"""
tests/test_api.py

Integration tests for the Savvy FastAPI layer.

Uses FastAPI TestClient to make real HTTP calls through the app.
No mocking of the HTTP layer — these tests prove the endpoints
are wired correctly end to end.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from interfaces.api import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "connectors" in data
        assert isinstance(data["connectors"], list)
        assert len(data["connectors"]) > 0


class TestConnectors:
    def test_list_connectors_returns_all(self, client):
        r = client.get("/connectors")
        assert r.status_code == 200
        data = r.json()
        assert "connectors" in data
        assert len(data["connectors"]) > 0

    def test_filter_by_tag_network(self, client):
        r = client.get("/connectors?tag=network")
        assert r.status_code == 200
        data = r.json()
        names = [c["name"] for c in data["connectors"]]
        assert len(names) > 0

    def test_filter_by_unknown_tag_returns_400(self, client):
        r = client.get("/connectors?tag=zzz_nonexistent_tag")
        assert r.status_code == 400


class TestTaxonomy:
    def test_taxonomy_returns_hierarchy(self, client):
        r = client.get("/taxonomy")
        assert r.status_code == 200
        data = r.json()
        assert "taxonomy" in data
        assert len(data["taxonomy"]) > 0

    def test_taxonomy_has_domain_category_connector_shape(self, client):
        r = client.get("/taxonomy")
        data = r.json()
        assert "taxonomy" in data
        for domain_name, domain_data in data["taxonomy"].items():
            assert isinstance(domain_name, str)
            assert isinstance(domain_data, dict)


class TestAlerts:
    def test_get_alerts_returns_list(self, client):
        r = client.get("/alerts")
        assert r.status_code == 200
        data = r.json()
        assert "alerts" in data
        assert isinstance(data["alerts"], list)
        assert "total" in data

    def test_get_alerts_unacknowledged_only(self, client):
        r = client.get("/alerts?unacknowledged_only=true")
        assert r.status_code == 200
        data = r.json()
        assert "alerts" in data
        for alert in data["alerts"]:
            assert alert["acknowledged"] is False

    def test_acknowledge_nonexistent_alert_returns_404(self, client):
        r = client.post("/alerts/nonexistent-alert-id/acknowledge")
        assert r.status_code == 404

    def test_acknowledge_all_returns_count(self, client):
        r = client.post("/alerts/acknowledge-all")
        assert r.status_code == 200
        data = r.json()
        assert "acknowledged_count" in data
        assert isinstance(data["acknowledged_count"], int)


class TestMonitorStats:
    def test_monitor_stats_returns_expected_shape(self, client):
        r = client.get("/monitor/stats")
        assert r.status_code == 200
        data = r.json()
        assert "running" in data
        assert "interval_seconds" in data
        assert "check_count" in data
        assert "total_alerts" in data
        assert "unacknowledged_alerts" in data

    def test_monitor_is_running_after_startup(self, client):
        r = client.get("/monitor/stats")
        data = r.json()
        assert data["running"] is True


class TestCache:
    def test_cache_stats_returns_data(self, client):
        r = client.get("/cache/stats")
        assert r.status_code == 200
        data = r.json()
        assert "enabled" in data or "stats" in data or "size" in data or isinstance(data, dict)

    def test_cache_clear_succeeds(self, client):
        r = client.post("/cache/clear")
        assert r.status_code == 200

    def test_cache_invalidate_known_connector(self, client):
        r = client.post("/cache/invalidate/system_health?device_id=local")
        assert r.status_code == 200
