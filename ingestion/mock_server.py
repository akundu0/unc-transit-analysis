"""
Lightweight mock GTFS-RT feed server for local end-to-end testing.

Runs on port 8999 and responds with synthetic protobuf data at:
    GET /mock/vehicle_positions
    GET /mock/trip_updates

Start with:
    python -m ingestion.mock_server

Then start the poller with the default placeholder URLs pointing here.
"""

import time
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import Response
from google.transit import gtfs_realtime_pb2

app = FastAPI(title="GTFS-RT Mock Server")

_ROUTES = ["400X", "CM", "J", "NS", "S"]


def _build_vehicle_positions_feed() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(time.time())

    for i, route_id in enumerate(_ROUTES):
        entity = feed.entity.add()
        entity.id = f"vehicle_{i + 1}"
        entity.vehicle.vehicle.id = f"BUS-{100 + i}"
        entity.vehicle.trip.trip_id = f"trip_{route_id}_{datetime.now(tz=timezone.utc).strftime('%H%M')}"
        entity.vehicle.trip.route_id = route_id
        entity.vehicle.position.latitude = 35.9049 + i * 0.001
        entity.vehicle.position.longitude = -79.0469 + i * 0.001
        entity.vehicle.position.speed = 8.0 + i * 0.5
        entity.vehicle.current_stop_sequence = i + 1

    return feed.SerializeToString()


def _build_trip_updates_feed() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(time.time())

    delays = [30, -15, 0, 60, 120]

    for i, route_id in enumerate(_ROUTES):
        entity = feed.entity.add()
        entity.id = f"tu_{i + 1}"
        entity.trip_update.trip.trip_id = f"trip_{route_id}_{datetime.now(tz=timezone.utc).strftime('%H%M')}"
        entity.trip_update.trip.route_id = route_id

        for stop_num in range(1, 4):
            stu = entity.trip_update.stop_time_update.add()
            stu.stop_id = f"stop_{route_id}_{stop_num}"
            stu.arrival.delay = delays[i] + (stop_num - 1) * 10
            stu.arrival.time = int(time.time()) + 300 * stop_num

    return feed.SerializeToString()


@app.get("/mock/vehicle_positions")
def vehicle_positions():
    return Response(
        content=_build_vehicle_positions_feed(),
        media_type="application/octet-stream",
    )


@app.get("/mock/trip_updates")
def trip_updates():
    return Response(
        content=_build_trip_updates_feed(),
        media_type="application/octet-stream",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8999)
