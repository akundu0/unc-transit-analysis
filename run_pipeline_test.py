"""
End-to-end pipeline smoke test — single command, no separate terminals needed.

Starts the mock GTFS-RT server in a background thread, runs 5 poll cycles,
prints the DB row counts, then exits.

Usage:
    python run_pipeline_test.py
"""

import asyncio
import threading
import time

import httpx
import uvicorn


def _start_mock_server():
    """Run the mock feed server in a daemon thread."""
    from ingestion.mock_server import app
    config = uvicorn.Config(app, host="0.0.0.0", port=8999, log_level="warning")
    server = uvicorn.Server(config)
    server.run()


def _seed_and_verify(n_polls: int = 5):
    from storage.db import init_db, engine
    from storage.models import VehiclePosition, TripUpdate
    from ingestion.poller import poll_once
    from sqlalchemy import text

    init_db()

    async def _run():
        async with httpx.AsyncClient() as client:
            for i in range(n_polls):
                print(f"  Poll {i + 1}/{n_polls}…")
                await poll_once(client)

    asyncio.run(_run())

    with engine.connect() as conn:
        vp = conn.execute(text("SELECT COUNT(*) FROM vehicle_positions")).scalar()
        tu = conn.execute(text("SELECT COUNT(*) FROM trip_updates")).scalar()

    print(f"\n✅  DB state after {n_polls} polls:")
    print(f"    vehicle_positions : {vp} rows")
    print(f"    trip_updates      : {tu} rows")
    if vp == 0 or tu == 0:
        print("\n❌  No rows written — check log output above for errors.")
    else:
        print("\nPipeline OK — run `python -m model.train` when you have enough history.")


if __name__ == "__main__":
    print("Starting mock GTFS-RT server on port 8999…")
    t = threading.Thread(target=_start_mock_server, daemon=True)
    t.start()
    time.sleep(1.5)   # give uvicorn a moment to bind

    print("Running poll cycles…\n")
    _seed_and_verify(n_polls=5)
