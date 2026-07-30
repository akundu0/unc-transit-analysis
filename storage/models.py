"""
SQLAlchemy ORM models for the UNC Transit event store.

Tables
------
vehicle_positions  — raw snapshot per vehicle per poll
trip_updates       — raw predicted arrival delay per stop per poll

Indices on (polled_at, route_id) are created for efficient time-window queries
used by the feature engineering layer.
"""

from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, DateTime, Index
from storage.db import Base


class VehiclePosition(Base):
    __tablename__ = "vehicle_positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_id = Column(String, nullable=False)
    trip_id = Column(String, nullable=True)
    route_id = Column(String, nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    bearing = Column(Float, nullable=True)
    speed = Column(Float, nullable=True)          # metres/second from GTFS-RT
    current_status = Column(Integer, nullable=True)  # GTFS VehicleStopStatus enum
    stop_id = Column(String, nullable=True)
    stop_sequence = Column(Integer, nullable=True)
    polled_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_vp_polled_route", "polled_at", "route_id"),
        Index("ix_vp_route_id", "route_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<VehiclePosition vehicle={self.vehicle_id} route={self.route_id} "
            f"polled_at={self.polled_at}>"
        )


class TripUpdate(Base):
    __tablename__ = "trip_updates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trip_id = Column(String, nullable=False)
    route_id = Column(String, nullable=True)
    stop_id = Column(String, nullable=True)
    stop_sequence = Column(Integer, nullable=True)
    predicted_arrival_delay_seconds = Column(Integer, nullable=True)
    predicted_departure_delay_seconds = Column(Integer, nullable=True)
    arrival_time = Column(DateTime, nullable=True)
    polled_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_tu_polled_route", "polled_at", "route_id"),
        Index("ix_tu_route_id", "route_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<TripUpdate trip={self.trip_id} route={self.route_id} "
            f"delay={self.predicted_arrival_delay_seconds}s polled_at={self.polled_at}>"
        )
