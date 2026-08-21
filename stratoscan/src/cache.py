"""Thread-safe TTL cache — stdlib only, zero dependencies.

Stratoscan caches two things:

  1. LLM interpretations (query -> InterpretedQuery) — the expensive part
     of every screen. Long TTL; identical queries never burn an LLM call.
  2. Full /screen responses (query + limit + offset -> response) — short
     TTL so repeat hits (e.g. re-clicking a preset) are instant while the
     data stays fresh against the 10-minute universe refresh.

This module provides the shared primitive: a lock-guarded dict with
per-entry expiry timestamps, lazy pruning, and LRU-ish eviction when the
cache grows past ``max_entries`` (oldest-written entries are dropped).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """A small thread-safe cache with per-entry TTL and bounded size.

    Args:
        ttl_seconds: Default seconds an entry lives before being dropped.
        max_entries: Hard cap — when exceeded, the oldest entries are
            evicted (LRU-ish: insertion order, refreshed on access).
        name: Optional label used in debug logs.
    """

    def __init__(
        self,
        ttl_seconds: float = 60.0,
        max_entries: int = 1024,
        name: str = "cache",
    ) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self.name = name
        self._store: "OrderedDict[Any, tuple[float, T]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: Any) -> Optional[T]:
        """Return the cached value for ``key``, or None if absent/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.time():
                # Expired — drop it now so the key doesn't linger
                del self._store[key]
                return None
            # Refresh recency so hot keys survive eviction
            self._store.move_to_end(key)
            return value

    def set(self, key: Any, value: T, ttl_seconds: Optional[float] = None) -> None:
        """Store ``value`` under ``key`` (thread-safe, bounded).

        Args:
            key: Cache key (must be hashable).
            value: The value to cache.
            ttl_seconds: Optional per-entry TTL override; defaults to the
                cache-wide TTL.
        """
        ttl = self.ttl_seconds if ttl_seconds is None else max(0.0, float(ttl_seconds))
        expires_at = time.time() + ttl
        with self._lock:
            self._store[key] = (expires_at, value)
            self._store.move_to_end(key)
            # Evict the oldest entries beyond the cap (insertion order,
            # refreshed on access in get/set). OrderedDict is non-empty here
            # because we just inserted, so popitem(last=False) is safe.
            while len(self._store) > self.max_entries:
                self._store.popitem(last=False)
            # Opportunistic sweep of expired entries so a cache that shrank
            # doesn't keep a pile of dead keys around.
            now = time.time()
            expired = [k for k, (exp, _) in self._store.items() if exp < now]
            for k in expired:
                del self._store[k]

    def clear(self) -> None:
        """Drop every entry (used by tests and hot-reload)."""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        """Number of entries currently stored (including expired-but-lazy)."""
        with self._lock:
            return len(self._store)

    def __contains__(self, key: Any) -> bool:
        """True if ``key`` maps to a live (unexpired) entry."""
        return self.get(key) is not None
