"""
Async polling service for UNC/Chapel Hill Transit GTFS-RT feeds.

Polls both VehiclePositions and TripUpdates endpoints concurrently every
POLL_INTERVAL_SECONDS seconds.  Each poll is wrapped in try/except so a
single failed request never kills the loop.

Environment variables (set in .env):
    GTFS_RT_VEHICLE_POSITIONS_URL  — VehiclePositions protobuf endpoint
    GTFS_RT_TRIP_UPDATES_URL       — TripUpdates protobuf endpoint
    POLL_INTERVAL_SECONDS          — seconds between polls (default 15)
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2

from storage.db import SessionLocal, init_db
from storage.models import VehiclePosition, TripUpdate

load_dotenv()

# ---------------------------------------------------------------------------
# Static GTFS schedule lookup for delay computation
# ---------------------------------------------------------------------------
# Maps "trip_id|stop_id" -> scheduled arrival as seconds since midnight.
# Used to compute delay when the feed only provides predicted arrival time
# (without an explicit delay field).
_SCHEDULE_LOOKUP: dict[str, int] = {}
_SCHEDULE_PATH = Path(__file__).resolve().parent.parent / "data" / "gtfs_static" / "schedule_lookup.json"
if _SCHEDULE_PATH.exists():
    with open(_SCHEDULE_PATH) as _f:
        _SCHEDULE_LOOKUP = json.load(_f)

# GTFS static times are in the agency's local timezone (agency.txt → agency_timezone)
_AGENCY_INFO_PATH = _SCHEDULE_PATH.parent / "agency_info.json"
_agency_tz_name = "America/New_York"  # fallback
if _AGENCY_INFO_PATH.exists():
    with open(_AGENCY_INFO_PATH) as _f:
        _agency_tz_name = json.load(_f).get("agency_timezone", _agency_tz_name)
_AGENCY_TZ = ZoneInfo(_agency_tz_name)

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

VEHICLE_POSITIONS_URL = os.getenv(
    "GTFS_RT_VEHICLE_POSITIONS_URL",
    "http://localhost:8999/mock/vehicle_positions",   # placeholder / mock
)
TRIP_UPDATES_URL = os.getenv(
    "GTFS_RT_TRIP_UPDATES_URL",
    "http://localhost:8999/mock/trip_updates",         # placeholder / mock
)
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "15"))
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "30"))
_RETENTION_INTERVAL = 6 * 3600  # run retention purge every 6 hours


# ---------------------------------------------------------------------------
# Route-ID extraction — real Chapel Hill Transit feeds leave route_id blank
# but encode the route abbreviation as the first token of trip_id, e.g.
#   "NS_N_2220_Mon - Fri" → "NS"
#   "FCX_E_0753_Mon - Fri" → "FCX"
# ---------------------------------------------------------------------------

_TRIP_ID_ROUTE_RE = re.compile(r"^([A-Za-z0-9]+)_")


def _extract_route_id(trip_id: str | None, route_id: str | None) -> str | None:
    """Return the best available route_id, falling back to parsing trip_id."""
    if route_id:
        return route_id
    if trip_id:
        m = _TRIP_ID_ROUTE_RE.match(trip_id)
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# Fetch + decode helpers
# ---------------------------------------------------------------------------

async def _fetch_feed(client: httpx.AsyncClient, url: str, label: str) -> gtfs_realtime_pb2.FeedMessage | None:
    """Fetch and decode a single GTFS-RT protobuf feed. Returns None on error."""
    try:
        resp = await client.get(url, timeout=10.0)
        resp.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)
        return feed
    except Exception as exc:
        log.error("[%s] fetch failed: %s", label, exc)
        return None


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def _write_vehicle_positions(feed: gtfs_realtime_pb2.FeedMessage, polled_at: datetime) -> int:
    """Persist VehiclePosition records. Returns count written."""
    entities = [e for e in feed.entity if e.HasField("vehicle")]
    if not entities:
        log.info("[vehicle_positions] empty entity list — off-hours or no active vehicles")
        return 0

    records = []
    for entity in entities:
        v = entity.vehicle
        pos = v.position if v.HasField("position") else None
        trip_id = v.trip.trip_id or None
        route_id = _extract_route_id(trip_id, v.trip.route_id or None)
        records.append(
            VehiclePosition(
                vehicle_id=v.vehicle.id if v.vehicle.id else entity.id,
                trip_id=trip_id,
                route_id=route_id,
                lat=pos.latitude if pos else None,
                lon=pos.longitude if pos else None,
                bearing=pos.bearing if pos and pos.HasField("bearing") else None,
                speed=pos.speed if pos and pos.HasField("speed") else None,
                current_status=v.current_status if v.HasField("current_status") else None,
                stop_id=v.stop_id if v.stop_id else None,
                stop_sequence=v.current_stop_sequence if v.HasField("current_stop_sequence") else None,
                polled_at=polled_at,
            )
        )

    with SessionLocal() as db:
        db.add_all(records)
        db.commit()

    log.info("[vehicle_positions] wrote %d records", len(records))
    return len(records)


def _write_trip_updates(feed: gtfs_realtime_pb2.FeedMessage, polled_at: datetime) -> int:
    """Persist TripUpdate records. Returns count written."""
    entities = [e for e in feed.entity if e.HasField("trip_update")]
    if not entities:
        log.info("[trip_updates] empty entity list — off-hours or no active trips")
        return 0

    records = []
    for entity in entities:
        tu = entity.trip_update
        trip_id = tu.trip.trip_id
        route_id = _extract_route_id(trip_id, tu.trip.route_id or None)

        for stu in tu.stop_time_update:
            # Prefer explicit delay field; fall back to computing from
            # predicted arrival time vs static schedule.
            has_arrival = stu.HasField("arrival")
            if has_arrival and stu.arrival.HasField("delay"):
                arrival_delay = stu.arrival.delay
            elif has_arrival and stu.arrival.time and _SCHEDULE_LOOKUP:
                sched_key = f"{trip_id}|{stu.stop_id}"
                sched_secs = _SCHEDULE_LOOKUP.get(sched_key)
                if sched_secs is not None:
                    pred_dt = datetime.fromtimestamp(stu.arrival.time, tz=timezone.utc)
                    local_dt = pred_dt.astimezone(_AGENCY_TZ)
                    pred_secs = local_dt.hour * 3600 + local_dt.minute * 60 + local_dt.second
                    # GTFS allows sched times > 24:00:00 for overnight trips.
                    # Align pred_secs to the same scale when schedule wraps past midnight.
                    if sched_secs >= 86400 and pred_secs < 21600:  # sched is >24h, pred is before 6 AM
                        pred_secs += 86400
                    arrival_delay = pred_secs - sched_secs
                else:
                    arrival_delay = None
            else:
                arrival_delay = None

            departure_delay = stu.departure.delay if stu.HasField("departure") and stu.departure.HasField("delay") else None
            arrival_time_ts = stu.arrival.time if has_arrival and stu.arrival.time else None
            arrival_time = (
                datetime.fromtimestamp(arrival_time_ts, tz=timezone.utc) if arrival_time_ts else None
            )
            records.append(
                TripUpdate(
                    trip_id=trip_id,
                    route_id=route_id,
                    stop_id=stu.stop_id if stu.HasField("stop_id") else None,
                    stop_sequence=stu.stop_sequence if stu.HasField("stop_sequence") else None,
                    predicted_arrival_delay_seconds=arrival_delay,
                    predicted_departure_delay_seconds=departure_delay,
                    arrival_time=arrival_time,
                    polled_at=polled_at,
                )
            )

    with SessionLocal() as db:
        db.add_all(records)
        db.commit()

    log.info("[trip_updates] wrote %d stop-time records", len(records))
    return len(records)


# ---------------------------------------------------------------------------
# Core polling loop
# ---------------------------------------------------------------------------

async def poll_once(client: httpx.AsyncClient) -> None:
    """Concurrently fetch both feeds and persist results."""
    polled_at = datetime.now(timezone.utc)

    vp_feed, tu_feed = await asyncio.gather(
        _fetch_feed(client, VEHICLE_POSITIONS_URL, "vehicle_positions"),
        _fetch_feed(client, TRIP_UPDATES_URL, "trip_updates"),
    )

    if vp_feed is not None:
        _write_vehicle_positions(vp_feed, polled_at)

    if tu_feed is not None:
        _write_trip_updates(tu_feed, polled_at)


async def run_poller() -> None:
    """Long-lived async loop. Runs forever; Ctrl-C to stop."""
    init_db()
    log.info("Poller starting — interval=%ds, retention=%dd", POLL_INTERVAL, RETENTION_DAYS)
    log.info("  VehiclePositions → %s", VEHICLE_POSITIONS_URL)
    log.info("  TripUpdates      → %s", TRIP_UPDATES_URL)

    last_retention = 0.0  # epoch — ensures first purge runs on startup

    async with httpx.AsyncClient() as client:
        while True:
            await poll_once(client)

            # Periodic data retention purge
            now = time.monotonic()
            if now - last_retention >= _RETENTION_INTERVAL:
                try:
                    from scripts.retention import purge_old_rows
                    result = purge_old_rows(retention_days=RETENTION_DAYS)
                    total = sum(result.values())
                    if total > 0:
                        log.info("[retention] Purged %d old rows", total)
                except Exception as exc:
                    log.warning("[retention] Purge failed: %s", exc)
                last_retention = now

            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_poller())
