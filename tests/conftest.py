"""
Shared pytest fixtures.

Creates an isolated in-memory SQLite database for each test session so tests
never touch the real event store.
"""

import os
import pytest
from datetime import datetime, timezone

# Force an in-memory DB before any application module reads DATABASE_URL
os.environ["DATABASE_URL"] = "sqlite://"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from storage.db import Base
from storage.models import VehiclePosition, TripUpdate  # noqa: F401 — register models


@pytest.fixture(scope="session")
def db_engine():
    """Create a shared in-memory SQLite engine for the test session."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """Yield a DB session and roll back after each test for isolation."""
    connection = db_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def seed_data(db_session):
    """Insert realistic test data: 3 routes, multiple polls over 2 hours."""
    now = datetime(2026, 7, 30, 14, 0, 0, tzinfo=timezone.utc)
    routes = ["NS", "FCX", "S"]
    delays = {"NS": [30, 45, 60], "FCX": [-10, 0, 15], "S": [120, 90, 80]}
    vehicles = {"NS": ["v1", "v2"], "FCX": ["v3"], "S": ["v4", "v5"]}

    from datetime import timedelta

    for poll_offset_min in [0, 5, 10, 30, 60]:
        polled_at = now - timedelta(minutes=poll_offset_min)

        for route_id in routes:
            # Vehicle positions
            for vid in vehicles[route_id]:
                db_session.add(VehiclePosition(
                    vehicle_id=vid,
                    trip_id=f"{route_id}_N_1400_Mon - Fri",
                    route_id=route_id,
                    lat=35.905,
                    lon=-79.047,
                    speed=5.0,
                    polled_at=polled_at,
                ))

            # Trip updates
            for i, delay in enumerate(delays[route_id]):
                db_session.add(TripUpdate(
                    trip_id=f"{route_id}_N_1400_Mon - Fri",
                    route_id=route_id,
                    stop_id=f"stop_{route_id}_{i}",
                    stop_sequence=i + 1,
                    predicted_arrival_delay_seconds=delay + poll_offset_min,
                    polled_at=polled_at,
                ))

    db_session.commit()
    return now
