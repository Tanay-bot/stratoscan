"""Hermetic tests for the Redis-backed rate limiter — no network, no Upstash.

Run: python -m tests.test_redis_ratelimit

Covers the store layer that had ZERO direct coverage:

1. UpstashRedis wire format: execute() posts [CMD, args...]; multi_exec()
   posts the 2-D array; result() extracts/raises.
2. UpstashRateLimitStore: fixed-window INCR/EXPIRE-NX/TTL parsing,
   rolling-window sorted-set parsing, check_many batch parsing.
3. InMemoryRateLimitStore: fixed + rolling windows, retry_after semantics,
   window expiry — the fail-open fallback must behave identically to Redis.
4. get_rate_limit_store() factory: Redis when configured + pingable;
   in-memory fallback when Redis is unreachable (fail-open, cached, resettable).
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest import mock  # noqa: E402

from src import redis_ratelimit as rr  # noqa: E402


# ── 1. UpstashRedis wire format ─────────────────────────────────────────────
print("1. UpstashRedis wire format...")

client = rr.UpstashRedis("https://example.upstash.io", "token-123", timeout=5.0)

with mock.patch.object(client, "_post", return_value={"result": "PONG"}) as m_post:
    assert client.execute("PING") == {"result": "PONG"}
    # Single command: POST to base URL with [CMD, args...].
    args = m_post.call_args[0]
    assert args[0] == "", args
    assert args[1] == ["PING"], args

with mock.patch.object(client, "_post", return_value=[{"result": 1}]) as m_post:
    client.multi_exec([["INCR", "k"], ["EXPIRE", "k", 60, "NX"]])
    args = m_post.call_args[0]
    assert args[0] == "/multi-exec", args
    assert args[1] == [["INCR", "k"], ["EXPIRE", "k", 60, "NX"]], args

# result() extracts, and raises on an error entry.
assert rr.UpstashRedis.result({"result": 42}, "INCR") == 42
try:
    rr.UpstashRedis.result({"error": "WRONGTYPE"}, "INCR")
    raise AssertionError("error entry must raise")
except rr.UpstashRedisError as e:
    assert "WRONGTYPE" in str(e)

# ping() returns False on failure (never raises).
with mock.patch.object(client, "execute", side_effect=rr.UpstashRedisError("down")):
    assert client.ping() is False
print("✅ execute/multi_exec wire format, result() semantics, ping fail-safe")


# ── 2. Upstash store parsing ────────────────────────────────────────────────
print("2. UpstashRateLimitStore parsing...")

fake_redis = mock.Mock()
store = rr.UpstashRateLimitStore(client=fake_redis)

# Fixed window: INCR → EXPIRE NX → TTL. Count 3 of 5 → allowed, remaining 2.
fake_redis.multi_exec.return_value = [
    {"result": 3}, {"result": "OK"}, {"result": 59},
]
allowed, remaining, retry_after = store.check_and_increment("rl:burst:x", 5, 60)
assert allowed is True and remaining == 2 and retry_after == 59
# Verify the exact command sequence sent (atomicity contract).
cmds = fake_redis.multi_exec.call_args[0][0]
assert cmds == [["INCR", "rl:burst:x"], ["EXPIRE", "rl:burst:x", 60, "NX"], ["TTL", "rl:burst:x"]], cmds

# Over limit: count 6 of 5 → blocked, remaining 0.
fake_redis.multi_exec.return_value = [
    {"result": 6}, {"result": "OK"}, {"result": 42},
]
allowed, remaining, retry_after = store.check_and_increment("rl:burst:x", 5, 60)
assert allowed is False and remaining == 0 and retry_after == 42

# Rolling window: sorted-set commands; oldest hit controls retry_after.
fake_redis.multi_exec.return_value = [
    {"result": "OK"}, {"result": "OK"}, {"result": 4},
    [["ts", "1000.0"]], {"result": "OK"},
]
allowed, remaining, retry_after = store.check_and_increment_rolling(
    "rl:weekly:x", 10, 604800, now=1000.0
)
assert allowed is True and remaining == 6
cmds = fake_redis.multi_exec.call_args[0][0]
assert cmds[0][0] == "ZREMRANGEBYSCORE" and cmds[1][0] == "ZADD" and cmds[2][0] == "ZCARD"

# check_many batch: mixed fixed + rolling in ONE multi_exec call. Reset the
# mock first — the fixed/rolling checks above already accumulated calls, so
# assert_called_once would fail on the historical count, not the batch.
fake_redis.multi_exec.reset_mock()
fake_redis.multi_exec.return_value = [
    {"result": 2}, {"result": "OK"}, {"result": 58},   # fixed: count 2/5
    {"result": "OK"}, {"result": "OK"}, {"result": 9},
    [["ts", "500.0"]], {"result": "OK"},              # rolling: 9/10
]
results = store.check_many(
    [("rl:f", 5, 60, False), ("rl:r", 10, 600, True)], now=1000.0
)
assert results[0] == (True, 3, 58), results
assert results[1][0] is True and results[1][1] == 1, results
# ONE round trip for the whole batch.
fake_redis.multi_exec.assert_called_once()
print("✅ fixed/rolling parsing + batch check_many in one round trip")


# ── 3. In-memory store parity ───────────────────────────────────────────────
print("3. InMemoryRateLimitStore parity...")

mem = rr.InMemoryRateLimitStore()

# Fixed window: 3 of 5 within the window.
now = 1000.0
for _ in range(3):
    a, rem, retry = mem.check_and_increment("f1", 5, 60, now=now)
assert a is True and rem == 2
# Window expiry resets the counter.
a, rem, _ = mem.check_and_increment("f1", 5, 60, now=now + 61)
assert a is True and rem == 4, (a, rem)
# Over limit → blocked with remaining 0. The limit is inclusive (count <= 5
# is allowed), so the BLOCK happens on the 6th call.
for _ in range(5):
    a, rem, _ = mem.check_and_increment("f2", 5, 60, now=2000.0)
assert a is True and rem == 0, (a, rem)  # exactly at the limit: allowed, 0 left
for _ in range(5):
    a, rem, _ = mem.check_and_increment("f2", 5, 60, now=2000.0)
assert a is False and rem == 0, (a, rem)  # 6th+ call: blocked

# Rolling window: 6 hits within a 100s window, then a 7th — 7 of 10 used.
rnow = 5000.0
for _ in range(6):
    mem.check_and_increment_rolling("r1", 10, 100, now=rnow)
a, rem, retry = mem.check_and_increment_rolling("r1", 10, 100, now=rnow + 1)
assert a is True and rem == 3, (a, rem)  # 10 - 7 hits = 3 remaining
# Old hits (>= window) expire: 5 more hits at 5000+110 → oldest pruned.
for _ in range(5):
    mem.check_and_increment_rolling("r1", 10, 100, now=rnow + 110)
a, rem, _ = mem.check_and_increment_rolling("r1", 10, 100, now=rnow + 111)
# 6 hits at t=5000 (pruned) + 5 at 5110 + 1 at 5111 → rolling count should stay ≤ 10.
assert a is True, (a, rem)

# check_many parity: the batch is itself an additional hit, so it must
# return EXACTLY what a 4th individual call on the same key would return
# (both increment the shared counter under the same window).
mem2 = rr.InMemoryRateLimitStore()
for _ in range(3):
    mem2.check_and_increment("k", 3, 60, now=100.0)
fourth_individual = mem2.check_and_increment("k", 3, 60, now=100.0)
mem3 = rr.InMemoryRateLimitStore()
for _ in range(3):
    mem3.check_and_increment("k", 3, 60, now=100.0)
batch = mem3.check_many([("k", 3, 60, False)], now=100.0)
assert fourth_individual == batch[0], (fourth_individual, batch[0])
print("✅ fixed/rolling semantics, window expiry, check_many parity")


# ── 4. Store factory (Redis vs in-memory fallback) ──────────────────────────
print("4. get_rate_limit_store factory...")

rr.reset_rate_limit_store()

# Redis configured + pingable → Upstash store. NOTE: has_upstash_redis is a
# read-only pydantic PROPERTY (like finnhub_keys), so it must be patched at
# the CLASS level — instance-level mock.patch.object raises AttributeError on
# exit (pydantic __delattr__ rejects unknown attrs).
with mock.patch.object(rr.settings, "redis_rate_limit_enabled", True), \
     mock.patch.object(
         type(rr.settings), "has_upstash_redis", mock.PropertyMock(return_value=True)
     ), \
     mock.patch.object(rr, "UpstashRateLimitStore") as m_ustore:
    m_ustore.return_value._client.ping.return_value = True
    s = rr.get_rate_limit_store()
    assert s is m_ustore.return_value, "pingable Redis must select the Upstash store"

rr.reset_rate_limit_store()

# Redis configured but UNREACHABLE → in-memory fallback (fail-open).
with mock.patch.object(rr.settings, "redis_rate_limit_enabled", True), \
     mock.patch.object(
         type(rr.settings), "has_upstash_redis", mock.PropertyMock(return_value=True)
     ), \
     mock.patch.object(rr, "UpstashRateLimitStore") as m_ustore:
    m_ustore.return_value._client.ping.return_value = False
    s = rr.get_rate_limit_store()
    assert isinstance(s, rr.InMemoryRateLimitStore), "unreachable Redis must fall back"

rr.reset_rate_limit_store()

# Not enabled → in-memory without ever constructing the Redis client.
with mock.patch.object(rr.settings, "redis_rate_limit_enabled", False), \
     mock.patch.object(rr, "UpstashRateLimitStore") as m_ustore:
    s = rr.get_rate_limit_store()
    assert isinstance(s, rr.InMemoryRateLimitStore)
    m_ustore.assert_not_called()

rr.reset_rate_limit_store()
print("✅ Redis when pingable; in-memory fallback; disabled → never touches Redis")


mock.patch.stopall()
print()
print("🎯 All Redis rate-limit tests pass!")
