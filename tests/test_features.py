"""Unit tests for the feature engineering layer."""

import unittest.mock as mock
from datetime import datetime, timezone

import pandas as pd

from features.engineering import (
    FEATURE_NAMES,
    compute_features,
    to_vector,
)


class TestToVector:
    """Verify to_vector produces the correct ordered list."""

    def test_order_matches_feature_names(self):
        features = {name: float(i) for i, name in enumerate(FEATURE_NAMES)}
        vec = to_vector(features)
        assert vec == [float(i) for i in range(len(FEATURE_NAMES))]

    def test_length(self):
        features = {name: 0.0 for name in FEATURE_NAMES}
        assert len(to_vector(features)) == 7


class TestComputeFeatures:
    """Test compute_features with mocked DB queries."""

    def _mock_features(self, delays_15, delays_60, vehicle_count, stop_seq=None):
        """Helper: patch DB calls and return computed features."""
        as_of = datetime(2026, 7, 30, 14, 0, 0, tzinfo=timezone.utc)
        df_15 = pd.DataFrame({"delay": delays_15}) if delays_15 else pd.DataFrame({"delay": []})
        df_60 = pd.DataFrame({"delay": delays_60}) if delays_60 else pd.DataFrame({"delay": []})

        with mock.patch("features.engineering._query_trip_updates") as mock_tu, \
             mock.patch("features.engineering._query_vehicle_count") as mock_vc:
            mock_tu.side_effect = [df_15, df_60]
            mock_vc.return_value = vehicle_count
            return compute_features("NS", stop_sequence=stop_seq, as_of=as_of)

    def test_returns_all_feature_names(self):
        feats = self._mock_features([30, 60], [30, 60, 90], 2)
        assert set(feats.keys()) == set(FEATURE_NAMES)

    def test_mean_delay_15m(self):
        feats = self._mock_features([20, 40], [10], 1)
        assert feats["mean_delay_route_15m"] == 30.0

    def test_std_delay_15m_with_multiple(self):
        feats = self._mock_features([10, 30], [10, 30], 1)
        assert feats["std_delay_route_15m"] == 10.0  # std([10,30], ddof=0) = 10

    def test_std_delay_15m_single_value(self):
        """std should be 0 when only 1 data point."""
        feats = self._mock_features([42], [42], 1)
        assert feats["std_delay_route_15m"] == 0.0

    def test_empty_data_returns_zeros(self):
        feats = self._mock_features([], [], 0)
        assert feats["mean_delay_route_15m"] == 0.0
        assert feats["std_delay_route_15m"] == 0.0
        assert feats["mean_delay_route_60m"] == 0.0
        assert feats["vehicle_count_route_15m"] == 0.0

    def test_hour_and_day(self):
        feats = self._mock_features([], [], 0)
        assert feats["hour_of_day"] == 14.0   # 2 PM UTC
        assert feats["day_of_week"] == 3.0     # Wednesday (2026-07-30)

    def test_stop_sequence_normalisation(self):
        feats = self._mock_features([], [], 0, stop_seq=25)
        assert feats["stop_sequence_norm"] == 0.5  # 25/50

    def test_stop_sequence_none(self):
        feats = self._mock_features([], [], 0, stop_seq=None)
        assert feats["stop_sequence_norm"] == 0.0
