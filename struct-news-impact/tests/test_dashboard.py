"""
Dashboard endpoint tests.
Verifies GET / returns the live trading dashboard HTML correctly.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch
import calendar_fetcher


@pytest.fixture
def client():
    os.environ["FINNHUB_API_KEY"] = "test_key_for_tests"
    import news_impact_server
    news_impact_server.app.config["TESTING"] = True
    calendar_fetcher.init("test_key_for_tests")
    calendar_fetcher._cache = []
    calendar_fetcher._last_refresh = 9999999999.0
    calendar_fetcher._last_error = None
    with news_impact_server.app.test_client() as c:
        yield c


class TestDashboard:
    def test_returns_200(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_content_type_is_html(self, client):
        r = client.get("/")
        assert "text/html" in r.content_type

    def test_contains_title(self, client):
        r = client.get("/")
        assert b"STRUCT.ai News Impact" in r.data

    def test_contains_pair_grid_element(self, client):
        r = client.get("/")
        assert b"pairsGrid" in r.data

    def test_contains_upcoming_section(self, client):
        r = client.get("/")
        assert b"upcomingSection" in r.data

    def test_contains_health_elements(self, client):
        r = client.get("/")
        assert b"eventsCount" in r.data
        assert b"lastRefresh" in r.data
        assert b"cacheAge" in r.data
        assert b"nextRefresh" in r.data

    def test_contains_force_refresh_button(self, client):
        r = client.get("/")
        assert b"forceRefresh" in r.data or b"Force Refresh" in r.data

    def test_contains_auto_refresh_script(self, client):
        r = client.get("/")
        assert b"fetchAll" in r.data
        assert b"setInterval" in r.data

    def test_contains_api_calls(self, client):
        r = client.get("/")
        assert b"/api/impact/health" in r.data
        assert b"/api/impact/now" in r.data
        assert b"/api/impact/upcoming" in r.data

    def test_no_external_dependencies(self, client):
        r = client.get("/")
        html = r.data.decode("utf-8")
        assert "cdn." not in html
        assert "googleapis.com" not in html
        assert "jsdelivr" not in html
        assert "unpkg.com" not in html

    def test_get_only_not_post(self, client):
        r = client.post("/")
        assert r.status_code == 405

    def test_dashboard_does_not_break_api_routes(self, client):
        with patch("calendar_fetcher.get_status") as ms:
            ms.return_value = {
                "events_cached": 10, "last_error": None,
                "last_refresh_utc": "2026-05-23 12:00 UTC",
                "cache_age_secs": 60, "next_refresh_secs": 3540,
                "api_key_set": True,
            }
            r = client.get("/api/impact/health")
            assert r.status_code == 200
            data = r.get_json()
            assert "status" in data

    def test_dashboard_html_is_self_contained(self, client):
        r = client.get("/")
        html = r.data.decode("utf-8")
        assert "<style>" in html
        assert "<script>" in html
        assert "<!DOCTYPE html>" in html

    def test_dashboard_color_classes_present(self, client):
        r = client.get("/")
        html = r.data.decode("utf-8")
        assert "card-clear" in html
        assert "card-caution" in html
        assert "card-blocked" in html

    def test_status_pill_element_present(self, client):
        r = client.get("/")
        assert b"statusPill" in r.data
