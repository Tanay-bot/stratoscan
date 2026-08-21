"""Upstash Redis-backed rate limiting (exact across workers).

Replaces slowapi's in-memory, per-process counters with a Redis backend so
every uvicorn worker shares ONE counter (multi-worker exactness — the README
roadmap item). Two windowing models are supported:

  - Fixed window   (burst/daily limits): INCR + EXPIRE-NX in one atomic
    ``multi-exec`` transaction. ``EXPIRE key seconds NX`` sets the TTL only
    when the key has no TTL yet — i.e. exactly once per window — so a
    sustained stream of requests never slides the window forward.
  - Rolling window (weekly limits): a sorted set of hit timestamps; each hit
    is ZADDed, entries older than the window are pruned, and the count is
    ZCARD. ``retry_after`` is derived from the oldest surviving hit, matching
    the pre-existing deque-based weekly middleware semantics.

The store is selected by ``get_rate_limit_store()``:

  - Upstash (REST) when ``REDIS_RATE_LIMIT_ENABLED=true`` and the REST URL +
    token are configured (and Redis answers a ping);
  - otherwise an in-memory store (thread-safe, per-process) — the exact
    behaviour Stratoscan shipped before Redis, kept as the hermetic dev/test
    default AND as the fail-safe fallback: if Redis is unreachable at runtime,
    the middleware catches the error and falls back to in-memory so a Redis
    outage never breaks the API (fail-open, logged).

The Upstash REST wire format (verified live): a single command is a POST to
the base URL with a JSON body ``["CMD", arg1, arg2, ...]``; atomic batches go
to ``/multi-exec`` with a 2-D array ``[["CMD", args...], ...]``; the response
is ``{"result": ...}`` / ``{"error": ...}`` (a batch returns a list of those).
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import certifi

from src.config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# Upstash REST client (stdlib-only + certifi for the CA bundle)
# ═══════════════════════════════════════════════════════════════════════════════

class UpstashRedisError(Exception):
    """Raised when an Upstash REST call fails (network, HTTP, or Redis error)."""


class UpstashRedis:
    """Minimal, stdlib-only client for the Upstash Redis REST API.

    Args:
        base_url: e.g. ``https://pleased-jaguar-136301.upstash.io``
        token:    the REST token (Authorization: Bearer).
        timeout:  per-request timeout in seconds.
    """

    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        # Upstash certificates chain to a public CA; this Python build can't
        # always find the system bundle, so pin certifi (already a requests
        # dependency) as the trust store.
        self._ctx = ssl.create_default_context(cafile=certifi.where())

    def _post(self, path: str, payload: Any) -> Any:
        """POST a JSON payload to ``path`` and return the parsed response."""
        req = urllib.request.Request(
            f"{self._base}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout, context=self._ctx) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            raise UpstashRedisError(f"Upstash HTTP {e.code}: {body}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise UpstashRedisError(f"Upstash unreachable: {e}") from e
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise UpstashRedisError(f"Upstash returned non-JSON: {raw[:200]!r}") from e

    def execute(self, command: str, *args: Any) -> Any:
        """Run a single Redis command via the body-array wire format."""
        return self._post("", [command, *args])

    def multi_exec(self, commands: List[List[Any]]) -> List[Dict[str, Any]]:
        """Run an atomic transaction (``/multi-exec``).

        Returns the per-command result dicts in order: ``[{"result": ...}, ...]``.
        """
        return self._post("/multi-exec", commands)

    def pipeline(self, commands: List[List[Any]]) -> List[Dict[str, Any]]:
        """Run a non-atomic batch (``/pipeline``)."""
        return self._post("/pipeline", commands)

    def ping(self) -> bool:
        """True when the Redis instance answers PING with 200."""
        try:
            self.execute("PING")
            return True
        except UpstashRedisError:
            return False

    @staticmethod
    def result(entry: Dict[str, Any], cmd_label: str) -> Any:
        """Extract ``result`` from a response entry, raising on ``error``."""
        if isinstance(entry, dict) and "error" in entry:
            raise UpstashRedisError(f"{cmd_label}: {entry['error']}")
        return entry.get("result") if isinstance(entry, dict) else entry


# ═══════════════════════════════════════════════════════════════════════════════
# Rate-limit store interface + implementations
# ═══════════════════════════════════════════════════════════════════════════════

class RateLimitStore(ABC):
    """Interface shared by the Redis and in-memory backends."""

    @abstractmethod
    def check_and_increment(
        self,
        key: str,
        limit: int,
        window_seconds: int,
        now: Optional[float] = None,
    ) -> Tuple[bool, int, int]:
        """Fixed-window check: record one hit, return (allowed, remaining, retry_after)."""

    @abstractmethod
    def check_and_increment_rolling(
        self,
        key: str,
        limit: int,
        window_seconds: int,
        now: Optional[float] = None,
    ) -> Tuple[bool, int, int]:
        """Rolling-window check: record one hit, return (allowed, remaining, retry_after)."""

    @abstractmethod
    def check_many(
        self,
        checks: List[Tuple[str, int, int, bool]],
        now: Optional[float] = None,
    ) -> List[Tuple[bool, int, int]]:
        """Run a batch of checks in ONE round trip.

        Each entry is ``(key, limit, window_seconds, rolling)``. Returns one
        ``(allowed, remaining, retry_after)`` per entry, in order. This is the
        hot path — one HTTP request runs burst + daily + weekly (+ global)
        checks, and a batch keeps that to a single Redis transaction.
        """


class UpstashRateLimitStore(RateLimitStore):
    """Redis-backed store. Every operation is ONE atomic ``multi-exec`` call.

    Fixed window:  ``INCR key`` then ``EXPIRE key <window> NX`` (TTL set only
    when absent — i.e. on the first hit of a window) then ``TTL key`` for the
    retry-after. Atomic, so concurrent workers can't double-count or slide the
    window.
    Rolling window: ``ZREMRANGEBYSCORE`` (prune old hits), ``ZADD`` (this hit,
    unique member), ``ZCARD`` (count), ``ZRANGE 0 0 WITHSCORES`` (oldest hit →
    retry-after), ``EXPIRE NX``.
    """

    def __init__(self, client: Optional[UpstashRedis] = None):
        self._client = client or UpstashRedis(
            settings.upstash_redis_rest_url,
            settings.upstash_redis_rest_token,
            settings.redis_rate_limit_timeout,
        )

    def check_and_increment(
        self,
        key: str,
        limit: int,
        window_seconds: int,
        now: Optional[float] = None,
    ) -> Tuple[bool, int, int]:
        results = self._client.multi_exec(
            [
                ["INCR", key],
                ["EXPIRE", key, window_seconds, "NX"],
                ["TTL", key],
            ]
        )
        count = int(UpstashRedis.result(results[0], "INCR"))
        ttl = int(UpstashRedis.result(results[2], "TTL"))
        allowed = count <= limit
        remaining = max(0, limit - count)
        retry_after = ttl if ttl > 0 else window_seconds
        return allowed, remaining, int(retry_after)

    def check_and_increment_rolling(
        self,
        key: str,
        limit: int,
        window_seconds: int,
        now: Optional[float] = None,
    ) -> Tuple[bool, int, int]:
        now = now if now is not None else time.time()
        member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
        results = self._client.multi_exec(
            [
                # A bare numeric max bound is INCLUSIVE in Redis — a hit
                # exactly window_seconds old is pruned, matching the
                # in-memory store's `>=` semantics so the fail-open fallback
                # behaves identically to Redis. (The "[" prefix form is
                # rejected by Upstash's REST arg parser — "min or max is not
                # a float" — so we use the default inclusive bare number.)
                ["ZREMRANGEBYSCORE", key, "-inf", now - window_seconds],
                ["ZADD", key, now, member],
                ["ZCARD", key],
                ["ZRANGE", key, 0, 0, "WITHSCORES"],
                ["EXPIRE", key, window_seconds, "NX"],
            ]
        )
        count = int(UpstashRedis.result(results[2], "ZCARD"))
        allowed = count <= limit
        remaining = max(0, limit - count)
        oldest = UpstashRedis.result(results[3], "ZRANGE")
        if oldest:
            try:
                oldest_ts = float(oldest[1])
            except (ValueError, TypeError, IndexError):
                oldest_ts = now
        else:
            oldest_ts = now
        retry_after = int(max(1, window_seconds - (now - oldest_ts)))
        return allowed, remaining, retry_after

    def check_many(
        self,
        checks: List[Tuple[str, int, int, bool]],
        now: Optional[float] = None,
    ) -> List[Tuple[bool, int, int]]:
        """Batch all checks into ONE atomic ``multi_exec`` transaction.

        Fixed-window checks contribute ``INCR / EXPIRE NX / TTL``; rolling
        checks contribute the 5 sorted-set commands. Results are parsed back
        per-check in the same order the commands were appended.
        """
        now = now if now is not None else time.time()
        commands: List[List[Any]] = []
        for key, limit, window_seconds, rolling in checks:
            if rolling:
                member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
                # Bare numeric max = INCLUSIVE bound — see
                # check_and_increment_rolling. Must stay identical so Redis
                # matches the in-memory fallback.
                commands.extend(
                    [
                        ["ZREMRANGEBYSCORE", key, "-inf", now - window_seconds],
                        ["ZADD", key, now, member],
                        ["ZCARD", key],
                        ["ZRANGE", key, 0, 0, "WITHSCORES"],
                        ["EXPIRE", key, window_seconds, "NX"],
                    ]
                )
            else:
                commands.extend(
                    [
                        ["INCR", key],
                        ["EXPIRE", key, window_seconds, "NX"],
                        ["TTL", key],
                    ]
                )
        results = self._client.multi_exec(commands)

        out: List[Tuple[bool, int, int]] = []
        idx = 0
        for key, limit, window_seconds, rolling in checks:
            if rolling:
                count = int(UpstashRedis.result(results[idx + 2], "ZCARD"))
                oldest = UpstashRedis.result(results[idx + 3], "ZRANGE")
                if oldest:
                    try:
                        oldest_ts = float(oldest[1])
                    except (ValueError, TypeError, IndexError):
                        oldest_ts = now
                else:
                    oldest_ts = now
                retry_after = int(max(1, window_seconds - (now - oldest_ts)))
                idx += 5
            else:
                count = int(UpstashRedis.result(results[idx], "INCR"))
                ttl = int(UpstashRedis.result(results[idx + 2], "TTL"))
                retry_after = ttl if ttl > 0 else window_seconds
                idx += 3
            allowed = count <= limit
            remaining = max(0, limit - count)
            out.append((allowed, remaining, int(retry_after)))
        return out


class InMemoryRateLimitStore(RateLimitStore):
    """Thread-safe, per-process fallback — the pre-Redis behaviour.

    Mirrors the deque-based weekly middleware (rolling) and adds fixed-window
    counters so the SAME store interface serves both windowing models.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fixed: Dict[str, List[float]] = {}          # key -> [count, window_start]
        self._rolling: Dict[str, deque] = {}              # key -> deque[timestamps]

    def check_and_increment(
        self,
        key: str,
        limit: int,
        window_seconds: int,
        now: Optional[float] = None,
    ) -> Tuple[bool, int, int]:
        now = now if now is not None else time.time()
        with self._lock:
            state = self._fixed.get(key)
            if state is None or now - state[1] >= window_seconds:
                state = [0.0, now]
                self._fixed[key] = state
            state[0] += 1
            count = int(state[0])
            allowed = count <= limit
            remaining = max(0, limit - count)
            retry_after = int(max(1, window_seconds - (now - state[1])))
        return allowed, remaining, retry_after

    def check_and_increment_rolling(
        self,
        key: str,
        limit: int,
        window_seconds: int,
        now: Optional[float] = None,
    ) -> Tuple[bool, int, int]:
        now = now if now is not None else time.time()
        with self._lock:
            hits = self._rolling.setdefault(key, deque())
            # `>=`: a hit exactly window_seconds old is outside the window.
            # (The strict `>` version kept it, making every window effectively
            # one unit longer — the rolling tests lock the inclusive contract.)
            while hits and now - hits[0] >= window_seconds:
                hits.popleft()
            hits.append(now)
            count = len(hits)
            allowed = count <= limit
            remaining = max(0, limit - count)
            oldest = hits[0] if hits else now
            retry_after = int(max(1, window_seconds - (now - oldest)))
        return allowed, remaining, retry_after

    def check_many(
        self,
        checks: List[Tuple[str, int, int, bool]],
        now: Optional[float] = None,
    ) -> List[Tuple[bool, int, int]]:
        """Batch of checks under ONE lock acquisition (local, no I/O)."""
        now = now if now is not None else time.time()
        out: List[Tuple[bool, int, int]] = []
        with self._lock:
            for key, limit, window_seconds, rolling in checks:
                if rolling:
                    hits = self._rolling.setdefault(key, deque())
                    while hits and now - hits[0] >= window_seconds:
                        hits.popleft()
                    hits.append(now)
                    count = len(hits)
                    oldest = hits[0] if hits else now
                    retry_after = int(max(1, window_seconds - (now - oldest)))
                else:
                    state = self._fixed.get(key)
                    if state is None or now - state[1] >= window_seconds:
                        state = [0.0, now]
                        self._fixed[key] = state
                    state[0] += 1
                    count = int(state[0])
                    retry_after = int(max(1, window_seconds - (now - state[1])))
                allowed = count <= limit
                remaining = max(0, limit - count)
                out.append((allowed, remaining, retry_after))
        return out


# ═══════════════════════════════════════════════════════════════════════════════
# Store factory
# ═══════════════════════════════════════════════════════════════════════════════

_store_lock = threading.Lock()
_cached_store: Optional[RateLimitStore] = None
_store_resolved = False


def get_rate_limit_store() -> RateLimitStore:
    """Return the process-wide rate-limit store.

    Upstash Redis when ``REDIS_RATE_LIMIT_ENABLED=true`` + creds configured
    AND Redis answers PING; otherwise the in-memory store. The resolution is
    cached so we don't ping Redis on every request; a runtime Redis failure is
    handled by the middleware's per-request fallback (fail-open).
    """
    global _cached_store, _store_resolved
    with _store_lock:
        if _store_resolved:
            return _cached_store
        store: RateLimitStore = InMemoryRateLimitStore()
        if settings.redis_rate_limit_enabled and settings.has_upstash_redis:
            candidate = UpstashRateLimitStore()
            if candidate._client.ping():
                store = candidate
                logger.info("Rate limiting: Upstash Redis backend (exact across workers)")
            else:
                logger.warning(
                    "REDIS_RATE_LIMIT_ENABLED=true but Upstash did not answer PING — "
                    "falling back to the in-memory store."
                )
        _cached_store = store
        _store_resolved = True
        return store


def reset_rate_limit_store() -> None:
    """Clear the cached store (used by tests to force re-resolution)."""
    global _cached_store, _store_resolved
    with _store_lock:
        _cached_store = None
        _store_resolved = False
