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
import logging
import os
import re
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from google.transit import gtfs_realtime_pb2

from storage.db import SessionLocal, init_db
from storage.models import VehiclePosition, TripUpdate

load_dotenv()

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
            arrival_delay = stu.arrival.delay if stu.HasField("arrival") else None
            departure_delay = stu.departure.delay if stu.HasField("departure") else None
            arrival_time_ts = stu.arrival.time if stu.HasField("arrival") and stu.arrival.time else None
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
    log.info("Poller starting — interval=%ds", POLL_INTERVAL)
    log.info("  VehiclePositions → %s", VEHICLE_POSITIONS_URL)
    log.info("  TripUpdates      → %s", TRIP_UPDATES_URL)

    async with httpx.AsyncClient() as client:
        while True:
            await poll_once(client)
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_poller())
