"""
tests/test_cache.py

Unit tests for the snapshot cache.
"""

from __future__ import annotations

import time
import pytest

from core.cache import SnapshotCache, CacheEntry
from core.schema import DiagnosticSnapshot, Severity


def make_snapshot(connector: str = "test", device_id: str = "local") -> DiagnosticSnapshot:
    return DiagnosticSnapshot(
        source_connector=connector,
        device_id=device_id,
        captured_at="2026-04-18T00:00:00Z",
        overall_severity=Severity.OK,
    )


def make_fetch_fn(connector: str = "test"):
    def fetch(device_id: str) -> DiagnosticSnapshot:
        return make_snapshot(connector, device_id)
    return fetch


@pytest.fixture
def cache() -> SnapshotCache:
    return SnapshotCache(ttl_seconds=60)


class TestCacheBasics:
    def test_miss_returns_none(self, cache):
        result = cache.get("test", "local")
        assert result is None

    def test_set_and_get(self, cache):
        snapshot = make_snapshot()
        cache.set("test", "local", snapshot)
        result = cache.get("test", "local")
        assert result is not None
        assert result.source_connector == "test"

    def test_different_keys_independent(self, cache):
        snap_a = make_snapshot("connector_a", "device_a")
        snap_b = make_snapshot("connector_b", "device_b")
        cache.set("connector_a", "device_a", snap_a)
        cache.set("connector_b", "device_b", snap_b)
        assert cache.get("connector_a", "device_a").source_connector == "connector_a"
        assert cache.get("connector_b", "device_b").source_connector == "connector_b"

    def test_invalidate_removes_entry(self, cache):
        cache.set("test", "local", make_snapshot())
        cache.invalidate("test", "local")
        assert cache.get("test", "local") is None

    def test_invalidate_all_clears_cache(self, cache):
        cache.set("a", "local", make_snapshot("a"))
        cache.set("b", "local", make_snapshot("b"))
        cache.invalidate_all()
        assert cache.get("a", "local") is None
        assert cache.get("b", "local") is None


class TestTTL:
    def test_fresh_entry_returned(self):
        cache = SnapshotCache(ttl_seconds=60)
        cache.set("test", "local", make_snapshot())
        assert cache.get("test", "local") is not None

    def test_expired_entry_returns_none(self):
        cache = SnapshotCache(ttl_seconds=0.01)
        cache.set("test", "local", make_snapshot())
        time.sleep(0.02)
        assert cache.get("test", "local") is None

    def test_cache_entry_age(self):
        entry = CacheEntry(snapshot=make_snapshot())
        time.sleep(0.05)
        assert entry.age_seconds() >= 0.05

    def test_is_expired_false_when_fresh(self):
        entry = CacheEntry(snapshot=make_snapshot())
        assert entry.is_expired(60) is False

    def test_is_expired_true_when_old(self):
        entry = CacheEntry(snapshot=make_snapshot())
        assert entry.is_expired(0) is True


class TestWarmAndFetch:
    def test_warm_synchronous(self, cache):
        snapshot = cache.warm("test", make_fetch_fn(), "local")
        assert snapshot is not None
        assert snapshot.source_connector == "test"
        assert cache.get("test", "local") is not None

    def test_get_or_fetch_on_miss(self, cache):
        snapshot = cache.get_or_fetch("test", make_fetch_fn(), "local")
        assert snapshot is not None

    def test_get_or_fetch_returns_cached_on_hit(self, cache):
        call_count = [0]
        def counting_fetch(device_id):
            call_count[0] += 1
            return make_snapshot("test", device_id)

        cache.get_or_fetch("test", counting_fetch, "local")
        cache.get_or_fetch("test", counting_fetch, "local")
        assert call_count[0] == 1

    def test_warm_background_does_not_block(self, cache):
        import time
        def slow_fetch(device_id):
            time.sleep(0.1)
            return make_snapshot("test", device_id)

        start = time.time()
        cache.warm("test", slow_fetch, "local", background=True)
        elapsed = time.time() - start
        assert elapsed < 0.05


class TestStats:
    def test_stats_empty_cache(self, cache):
        stats = cache.stats()
        assert stats["total_entries"] == 0
        assert stats["fresh_entries"] == 0
        assert stats["expired_entries"] == 0

    def test_stats_with_entries(self, cache):
        cache.set("a", "local", make_snapshot("a"))
        cache.set("b", "local", make_snapshot("b"))
        stats = cache.stats()
        assert stats["total_entries"] == 2
        assert stats["fresh_entries"] == 2
        assert stats["expired_entries"] == 0

    def test_stats_refresh_count(self, cache):
        cache.set("test", "local", make_snapshot())
        cache.set("test", "local", make_snapshot())
        stats = cache.stats()
        assert stats["entries"][0]["refresh_count"] == 1
