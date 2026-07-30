"""Tests for ORM models and DB schema integrity."""

from datetime import datetime, timezone

from sqlalchemy import text

from storage.models import VehiclePosition, TripUpdate


class TestVehiclePositionModel:

    def test_create_and_read(self, db_session):
        vp = VehiclePosition(
            vehicle_id="BUS-100",
            trip_id="NS_N_1400_Mon - Fri",
            route_id="NS",
            lat=35.905,
            lon=-79.047,
            bearing=180.0,
            speed=8.5,
            current_status=2,
            stop_id="stop_NS_1",
            stop_sequence=3,
            polled_at=datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc),
        )
        db_session.add(vp)
        db_session.flush()

        result = db_session.query(VehiclePosition).filter_by(vehicle_id="BUS-100").one()
        assert result.route_id == "NS"
        assert result.bearing == 180.0
        assert result.current_status == 2
        assert result.stop_id == "stop_NS_1"

    def test_nullable_fields(self, db_session):
        """All optional fields should accept None."""
        vp = VehiclePosition(
            vehicle_id="BUS-999",
            polled_at=datetime.now(timezone.utc),
        )
        db_session.add(vp)
        db_session.flush()
        assert vp.id is not None
        assert vp.route_id is None
        assert vp.lat is None


class TestTripUpdateModel:

    def test_create_with_all_fields(self, db_session):
        tu = TripUpdate(
            trip_id="FCX_E_0753_Mon - Fri",
            route_id="FCX",
            stop_id="stop_FCX_1",
            stop_sequence=5,
            predicted_arrival_delay_seconds=45,
            predicted_departure_delay_seconds=50,
            arrival_time=datetime(2026, 7, 30, 14, 30, tzinfo=timezone.utc),
            polled_at=datetime(2026, 7, 30, 14, 0, tzinfo=timezone.utc),
        )
        db_session.add(tu)
        db_session.flush()

        result = db_session.query(TripUpdate).filter_by(trip_id="FCX_E_0753_Mon - Fri").one()
        assert result.stop_sequence == 5
        assert result.predicted_departure_delay_seconds == 50

    def test_repr(self, db_session):
        tu = TripUpdate(
            trip_id="NS_N_1400_Mon - Fri",
            route_id="NS",
            predicted_arrival_delay_seconds=30,
            polled_at=datetime.now(timezone.utc),
        )
        r = repr(tu)
        assert "NS" in r
        assert "30s" in r
