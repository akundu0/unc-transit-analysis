"""
Integration tests — poll → store → feature engineering path.

Uses the mock GTFS-RT server and an in-memory SQLite DB to verify the
full data pipeline end-to-end without hitting external services.
"""

import asyncio
from datetime import datetime, timezone

import httpx
import pytest
import uvicorn
import threading
import time

from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker

from storage.db import Base
from storage.models import VehiclePosition, TripUpdate


@pytest.fixture(scope="module")
def mock_server():
    """Start the mock GTFS-RT server in a background thread for integration tests."""
    from ingestion.mock_server import app
    config = uvicorn.Config(app, host="127.0.0.1", port=8999, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    time.sleep(1.0)
    yield
    server.should_exit = True


@pytest.fixture()
def integration_engine():
    """Separate in-memory engine for integration tests."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


class TestPollAndStore:
    """Verify the poller writes correct data to the DB."""

    def test_mock_feed_returns_protobuf(self, mock_server):
        """Sanity check: mock server serves valid protobuf."""
        from google.transit import gtfs_realtime_pb2
        resp = httpx.get("http://127.0.0.1:8999/mock/vehicle_positions", timeout=5)
        assert resp.status_code == 200
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)
        assert len(feed.entity) > 0

    def test_poll_once_writes_vehicle_positions(self, mock_server, integration_engine):
        """poll_once should insert VehiclePosition rows into the DB."""
        import unittest.mock as mock
        from ingestion import poller

        Session = sessionmaker(bind=integration_engine)
        with mock.patch.object(poller, "SessionLocal", Session), \
             mock.patch.object(poller, "VEHICLE_POSITIONS_URL", "http://127.0.0.1:8999/mock/vehicle_positions"), \
             mock.patch.object(poller, "TRIP_UPDATES_URL", "http://127.0.0.1:8999/mock/trip_updates"):
            async def run():
                async with httpx.AsyncClient() as client:
                    await poller.poll_once(client)
            asyncio.run(run())

        with integration_engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM vehicle_positions")).scalar()
            assert count == 5  # mock server produces 5 routes

    def test_poll_once_writes_trip_updates(self, mock_server, integration_engine):
        """poll_once should insert TripUpdate rows into the DB."""
        import unittest.mock as mock
        from ingestion import poller

        Session = sessionmaker(bind=integration_engine)
        with mock.patch.object(poller, "SessionLocal", Session), \
             mock.patch.object(poller, "VEHICLE_POSITIONS_URL", "http://127.0.0.1:8999/mock/vehicle_positions"), \
             mock.patch.object(poller, "TRIP_UPDATES_URL", "http://127.0.0.1:8999/mock/trip_updates"):
            async def run():
                async with httpx.AsyncClient() as client:
                    await poller.poll_once(client)
            asyncio.run(run())

        with integration_engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM trip_updates")).scalar()
            assert count == 15  # 5 routes × 3 stops each

    def test_poll_writes_correct_route_ids(self, mock_server, integration_engine):
        """Route IDs from the mock server should be stored correctly."""
        import unittest.mock as mock
        from ingestion import poller

        Session = sessionmaker(bind=integration_engine)
        with mock.patch.object(poller, "SessionLocal", Session), \
             mock.patch.object(poller, "VEHICLE_POSITIONS_URL", "http://127.0.0.1:8999/mock/vehicle_positions"), \
             mock.patch.object(poller, "TRIP_UPDATES_URL", "http://127.0.0.1:8999/mock/trip_updates"):
            async def run():
                async with httpx.AsyncClient() as client:
                    await poller.poll_once(client)
            asyncio.run(run())

        with integration_engine.connect() as conn:
            routes = conn.execute(
                text("SELECT DISTINCT route_id FROM vehicle_positions ORDER BY route_id")
            ).scalars().all()
            assert set(routes) == {"400X", "CM", "J", "NS", "S"}


class TestPollToFeatures:
    """End-to-end: poll data, then compute features from it."""

    def test_features_after_polling(self, mock_server, integration_engine):
        """After polling, compute_features should return non-zero values."""
        import unittest.mock as mock
        from ingestion import poller
        from features.engineering import compute_features

        Session = sessionmaker(bind=integration_engine)

        # Poll twice to accumulate some data
        with mock.patch.object(poller, "SessionLocal", Session), \
             mock.patch.object(poller, "VEHICLE_POSITIONS_URL", "http://127.0.0.1:8999/mock/vehicle_positions"), \
             mock.patch.object(poller, "TRIP_UPDATES_URL", "http://127.0.0.1:8999/mock/trip_updates"):
            for _ in range(2):
                async def run():
                    async with httpx.AsyncClient() as client:
                        await poller.poll_once(client)
                asyncio.run(run())

        # Compute features using the integration engine
        with mock.patch("features.engineering.engine", integration_engine):
            feats = compute_features("NS", stop_sequence=2)

        assert feats["vehicle_count_route_15m"] >= 1.0
        assert feats["stop_sequence_norm"] == 2 / 50.0
        assert 0.0 <= feats["hour_of_day"] <= 23.0
