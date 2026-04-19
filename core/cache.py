"""
core/cache.py

Snapshot cache with TTL-based expiry and background refresh.

Instead of fetching live data on every query, the cache keeps
snapshots warm in memory. Queries respond instantly from cache.
Background threads refresh snapshots before they expire.

This is critical for fleet-scale deployments where fetching
200 machines synchronously on every query is too slow.

Usage:
    from core.cache import SnapshotCache
    cache = SnapshotCache(ttl_seconds=300)
    cache.warm("system_health", connector, device_id="local")
    snapshot = cache.get("system_health", "local")
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from core.schema import DiagnosticSnapshot


@dataclass
class CacheEntry:
    """A single cached snapshot with metadata."""
    snapshot: DiagnosticSnapshot
    cached_at: float = field(default_factory=time.time)
    refresh_count: int = 0
    last_error: Optional[str] = None

    def age_seconds(self) -> float:
        return time.time() - self.cached_at

    def is_expired(self, ttl_seconds: float) -> bool:
        return self.age_seconds() > ttl_seconds


class SnapshotCache:
    """
    Thread-safe in-memory snapshot cache with TTL expiry.

    Keys are (connector_name, device_id) tuples.
    Background refresh threads keep snapshots warm automatically.
    """

    def __init__(self, ttl_seconds: float = 300) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[tuple[str, str], CacheEntry] = {}
        self._lock = threading.RLock()
        self._refresh_threads: dict[tuple[str, str], threading.Thread] = {}

    def get(
        self,
        connector_name: str,
        device_id: str,
    ) -> Optional[DiagnosticSnapshot]:
        """
        Return a cached snapshot if available and not expired.
        Returns None if cache miss or expired.
        """
        key = (connector_name, device_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.is_expired(self._ttl):
                return None
            return entry.snapshot

    def set(
        self,
        connector_name: str,
        device_id: str,
        snapshot: DiagnosticSnapshot,
    ) -> None:
        """Store a snapshot in the cache."""
        key = (connector_name, device_id)
        with self._lock:
            existing = self._entries.get(key)
            refresh_count = (existing.refresh_count + 1) if existing else 0
            self._entries[key] = CacheEntry(
                snapshot=snapshot,
                cached_at=time.time(),
                refresh_count=refresh_count,
            )

    def warm(
        self,
        connector_name: str,
        fetch_fn: Callable[[str], DiagnosticSnapshot],
        device_id: str = "local",
        background: bool = False,
    ) -> Optional[DiagnosticSnapshot]:
        """
        Fetch and cache a snapshot.

        Parameters
        ----------
        connector_name:
            Name of the connector, used as part of the cache key.
        fetch_fn:
            Callable that takes a device_id and returns a snapshot.
        device_id:
            Device identifier to fetch.
        background:
            If True, fetch in a background thread and return None immediately.
            If False, fetch synchronously and return the snapshot.
        """
        if background:
            key = (connector_name, device_id)
            if key in self._refresh_threads:
                t = self._refresh_threads[key]
                if t.is_alive():
                    return None

            def _refresh():
                try:
                    snapshot = fetch_fn(device_id)
                    self.set(connector_name, device_id, snapshot)
                except Exception as exc:
                    with self._lock:
                        entry = self._entries.get(key)
                        if entry:
                            entry.last_error = str(exc)

            t = threading.Thread(target=_refresh, daemon=True)
            with self._lock:
                self._refresh_threads[key] = t
            t.start()
            return None
        else:
            try:
                snapshot = fetch_fn(device_id)
                self.set(connector_name, device_id, snapshot)
                return snapshot
            except Exception:
                raise

    def get_or_fetch(
        self,
        connector_name: str,
        fetch_fn: Callable[[str], DiagnosticSnapshot],
        device_id: str = "local",
    ) -> DiagnosticSnapshot:
        """
        Return cached snapshot if fresh, otherwise fetch and cache.
        This is the primary access pattern for most use cases.
        """
        cached = self.get(connector_name, device_id)
        if cached is not None:
            return cached
        return self.warm(connector_name, fetch_fn, device_id, background=False)

    def invalidate(self, connector_name: str, device_id: str) -> None:
        """Remove a specific entry from the cache."""
        key = (connector_name, device_id)
        with self._lock:
            self._entries.pop(key, None)

    def invalidate_all(self) -> None:
        """Clear the entire cache."""
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict:
        """Return cache statistics."""
        with self._lock:
            entries = list(self._entries.values())
        return {
            "total_entries": len(entries),
            "fresh_entries": sum(
                1 for e in entries if not e.is_expired(self._ttl)
            ),
            "expired_entries": sum(
                1 for e in entries if e.is_expired(self._ttl)
            ),
            "ttl_seconds": self._ttl,
            "entries": [
                {
                    "connector": k[0],
                    "device_id": k[1],
                    "age_seconds": round(e.age_seconds(), 1),
                    "refresh_count": e.refresh_count,
                    "expired": e.is_expired(self._ttl),
                    "last_error": e.last_error,
                }
                for k, e in self._entries.items()
            ],
        }


# Global cache instance with 5 minute TTL
snapshot_cache = SnapshotCache(ttl_seconds=300)
