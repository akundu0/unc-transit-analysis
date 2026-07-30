"""Tests for the route-ID extraction logic in the poller."""

from ingestion.poller import _extract_route_id


class TestExtractRouteId:
    """Validate route parsing from Chapel Hill Transit trip_id formats."""

    def test_ns_route(self):
        assert _extract_route_id("NS_N_2220_Mon - Fri", None) == "NS"

    def test_fcx_route(self):
        assert _extract_route_id("FCX_E_0753_Mon - Fri", None) == "FCX"

    def test_single_char_route(self):
        assert _extract_route_id("S_E_1616_Mon - Fri", None) == "S"

    def test_numeric_suffix_route(self):
        assert _extract_route_id("400X_W_0800_Sat", None) == "400X"

    def test_explicit_route_id_takes_priority(self):
        """If the feed provides route_id directly, prefer it over parsing."""
        assert _extract_route_id("NS_N_2220_Mon - Fri", "OVERRIDE") == "OVERRIDE"

    def test_empty_route_id_falls_back_to_trip_id(self):
        assert _extract_route_id("NS_N_2220_Mon - Fri", "") == "NS"

    def test_none_trip_id_and_none_route_id(self):
        assert _extract_route_id(None, None) is None

    def test_unparseable_trip_id(self):
        """Trip IDs without underscores should return None."""
        assert _extract_route_id("no-underscores", None) is None
