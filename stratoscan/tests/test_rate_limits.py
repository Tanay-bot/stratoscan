"""Rate-limit verification tests for the Stratoscan FastAPI layer.

Run: python -m tests.test_rate_limits

Fully hermetic — ZERO network, ZERO real Upstash calls:

1. Approved limit numbers locked against drift (RATE_LIMITS / GLOBAL_LIMITS /
   SCOPE_GLOBAL in src/api.py).
2. _scope_for_path maps request paths to the right bucket.
3. RateLimitMiddleware is registered on the app.
4. In-memory store: fixed-window + rolling-window semantics, and the
   check_many batch API.
5. Upstash store wire format (mock HTTP client): fixed-window commands
   (INCR / EXPIRE NX / TTL), rolling-window commands (sorted-set), and the
   batch ordering — so the real client is validated without network.
6. Behavioral middleware tests on a scratch app (tiny caps): 429 body,
   Retry-After, CORS echo for allowed origins, no CORS leak for disallowed
   origins, OPTIONS preflight never counted, RateLimit-* headers on allowed
   responses, fail-open when the store raises, and RATE_LIMIT_ENABLED=false
   pass-through.

The real Upstash round-trip lives in verify_upstash_ratelimit.py (a live
probe with cleanup), NOT here — this suite must stay network-independent.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: force the IN-MEMORY store even though .env enables Redis, so this
# suite never pings or writes to the real Upstash instance.
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"

from unittest import mock  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import src.api as api_module  # to patch module-global caps / store in tests  # noqa: E402
from src.api import (  # noqa: E402
    GLOBAL_LIMITS,
    RATE_LIMITS,
    SCOPE_GLOBAL,
    RateLimitMiddleware,
    _scope_for_path,
    app,
)
from src.redis_ratelimit import (  # noqa: E402
    InMemoryRateLimitStore,
    UpstashRateLimitStore,
    UpstashRedisError,
)

# ── 1. Lock the approved numbers against drift ────────────────────────────────
assert RATE_LIMITS == {
    "screen":    {"burst": 5,   "daily": 50,    "weekly": 250},
    "interpret": {"burst": 10,  "daily": 100,   "weekly": 500},
    "enrich":    {"burst": 3,   "daily": 20,    "weekly": 100},
    "backtest":  {"burst": 2,   "daily": 10,    "weekly": 50},
    "refresh":   {"burst": 1,   "daily": 10,    "weekly": 50},
    # The generic per-IP bucket: env-configurable via RATE_LIMIT_PER_MINUTE /
    # RATE_LIMIT_PER_DAY (defaults 60/min · 1000/day · 5000/week).
    "read":      {"burst": 60,  "daily": 1000,  "weekly": 5000},
    "auth":      {"burst": 10,  "daily": 50,    "weekly": 350},
    # VULN-002/003 fix: /auth/register gets a DEDICATED, tighter scope so
    # mass signup can't burn the SendGrid free tier (100/day) or enumerate
    # accounts at scale. Strictly tighter than the auth bucket.
    "register":  {"burst": 3,   "daily": 10,    "weekly": 30},
    # Day 3: every other email-sending auth action (password reset,
    # resend-verification) gets register's caps in its own scope.
    "auth_mail": {"burst": 3,   "daily": 10,    "weekly": 30},
    # Polling a running backtest is free work, so it has its own generous
    # scope and must never spend the backtest cost shield.
    "backtest_poll": {"burst": 120, "daily": 10000, "weekly": 50000},
}, f"RATE_LIMITS drifted: {RATE_LIMITS}"
assert GLOBAL_LIMITS == {
    "screener_llm": 900,
    "backtest_llm": 200,
    # VULN-001 fix: /refresh is admin-gated; this global daily cap is
    # defense-in-depth (each call fires thousands of Finnhub requests).
    "refresh_ops": 3,
    # VULN-002 fix: planet-wide daily cap on account creation — keeps the
    # whole planet under the SendGrid free tier (100/day) with headroom.
    "auth_register": 50,
}, f"GLOBAL_LIMITS drifted: {GLOBAL_LIMITS}"
assert SCOPE_GLOBAL == {
    "screen": "screener_llm",
    "interpret": "screener_llm",
    "enrich": "screener_llm",
    "backtest": "backtest_llm",
    "refresh": "refresh_ops",
    "register": "auth_register",
    # ...and draws on the same planet-wide email budget, not a second one.
    "auth_mail": "auth_register",
}, f"SCOPE_GLOBAL drifted: {SCOPE_GLOBAL}"
print("✅ Approved limit numbers locked in test")

# ── 2. Path → bucket mapping ──────────────────────────────────────────────────
scope_checks = {
    "/screen": "screen",
    "/interpret": "interpret",
    "/enrich": "enrich",
    "/backtest": "backtest",
    "/backtest/async": "backtest",
    "/backtest/async/0b6f2c1e-job": "backtest_poll",
    "/backtest/export": "backtest",
    "/refresh": "refresh",
    "/auth/register": "register",
    "/auth/password-reset/request": "auth_mail",
    "/auth/password-reset/confirm": "auth_mail",
    "/auth/resend-verification": "auth_mail",
    "/auth/change-password": "auth",
    "/auth/login": "auth",
    "/auth/me": "auth",
    "/stocks": "read",
    "/stocks/AAPL": "read",
    "/health": "read",
    "/docs": "read",
}
for path, expected in scope_checks.items():
    actual = _scope_for_path(path)
    assert actual == expected, f"_scope_for_path({path}) = {actual}, want {expected}"
print("✅ Scope mapping correct:", scope_checks)

# ── 3. Middleware registered on the real app ─────────────────────────────────
assert any(
    m.cls is RateLimitMiddleware for m in app.user_middleware
), "RateLimitMiddleware not registered on the app"
print("✅ RateLimitMiddleware registered on app")

# ── 4. In-memory store semantics ─────────────────────────────────────────────
mem = InMemoryRateLimitStore()
now = 1_000_000.0

# Fixed window: first hit allowed (remaining = limit - 1).
a, r, retry = mem.check_and_increment("k1", limit=3, window_seconds=60, now=now)
assert (a, r, retry) == (True, 2, 60), (a, r, retry)
# Second and third still allowed; fourth is DENIED.
mem.check_and_increment("k1", limit=3, window_seconds=60, now=now + 1)
mem.check_and_increment("k1", limit=3, window_seconds=60, now=now + 2)
a, r, retry = mem.check_and_increment("k1", limit=3, window_seconds=60, now=now + 3)
assert a is False and r == 0, (a, r)
# After the window elapses, the bucket resets.
a, r, retry = mem.check_and_increment("k1", limit=3, window_seconds=60, now=now + 61)
assert a is True and r == 2, (a, r)
print("✅ In-memory fixed-window semantics correct")

# Rolling window: 2 allowed of 3, third DENIED, pruned after window elapses.
a, _, _ = mem.check_and_increment_rolling("k2", limit=2, window_seconds=60, now=now)
b, _, _ = mem.check_and_increment_rolling("k2", limit=2, window_seconds=60, now=now + 1)
c, _, retry_c = mem.check_and_increment_rolling("k2", limit=2, window_seconds=60, now=now + 2)
assert a and b, "first two rolling hits should be allowed"
assert c is False, "third rolling hit must be denied"
assert retry_c >= 58, f"retry_after too small: {retry_c}"
a2, _, _ = mem.check_and_increment_rolling("k2", limit=2, window_seconds=60, now=now + 61)
assert a2 is True, "rolling window must prune after 60s"
print("✅ In-memory rolling-window semantics correct")

# check_many batch: mixed fixed + rolling under one call.
batch = mem.check_many(
    [("k3", 1, 60, False), ("k4", 1, 60, True)],
    now=now,
)
assert batch == [(True, 0, 60), (True, 0, 60)], batch
batch2 = mem.check_many(
    [("k3", 1, 60, False), ("k4", 1, 60, True)],
    now=now + 1,
)
assert batch2 == [(False, 0, 59), (False, 0, 59)], batch2
print("✅ In-memory check_many batch correct")

# ── 5. Upstash store wire format (mock HTTP — no network) ────────────────────
class FakeUpstashClient:
    """Records multi_exec payloads; returns canned responses in order."""

    def __init__(self, responses):
        self.calls = []
        self.responses = list(responses)

    def multi_exec(self, commands):
        self.calls.append(commands)
        if not self.responses:
            return [{"result": None} for _ in commands]
        return self.responses.pop(0)


def ok(v):
    return {"result": v}


# 5a. Fixed window: INCR / EXPIRE NX / TTL in one transaction.
fixed_client = FakeUpstashClient(
    [[ok(1), ok(1), ok(60)], [ok(4), ok(1), ok(57)]]
)
fixed_store = UpstashRateLimitStore(client=fixed_client)
a, r, retry = fixed_store.check_and_increment("rl:burst:read:1.2.3.4", limit=3, window_seconds=60)
assert a is True and r == 2 and retry == 60, (a, r, retry)
assert fixed_client.calls[0] == [
    ["INCR", "rl:burst:read:1.2.3.4"],
    ["EXPIRE", "rl:burst:read:1.2.3.4", 60, "NX"],
    ["TTL", "rl:burst:read:1.2.3.4"],
], fixed_client.calls[0]
a, r, retry = fixed_store.check_and_increment("rl:burst:read:1.2.3.4", limit=3, window_seconds=60)
assert a is False and r == 0 and retry == 57, (a, r, retry)
print("✅ Upstash fixed-window wire format correct (INCR/EXPIRE NX/TTL)")

# 5b. Rolling window: sorted-set commands in one transaction.
roll_client = FakeUpstashClient([[ok(2), ok(1), ok(1), ok(["m1", 100.0]), ok(1)]])
roll_store = UpstashRateLimitStore(client=roll_client)
a, r, retry = roll_store.check_and_increment_rolling(
    "rl:weekly:screen:1.2.3.4", limit=2, window_seconds=604800, now=100.0
)
assert a is True and r == 1, (a, r)  # limit=2, one hit → remaining 1
cmds = roll_client.calls[0]
assert cmds[0][0] == "ZREMRANGEBYSCORE" and cmds[0][1] == "rl:weekly:screen:1.2.3.4"
assert cmds[1][0] == "ZADD" and cmds[1][2] == 100.0
assert cmds[1][3].startswith("100.000000:") and len(cmds[1][3].split(":", 1)[1]) == 8
assert cmds[2][0] == "ZCARD" and cmds[3][0] == "ZRANGE" and cmds[4][0] == "EXPIRE"
assert cmds[4][3] == "NX"
print("✅ Upstash rolling-window wire format correct (sorted set)")

# 5c. check_many batch: fixed(3 cmds) then rolling(5 cmds) per entry, parsed back.
batch_client = FakeUpstashClient(
    [
        [ok(1), ok(1), ok(60),          # fixed check 0
         ok(0), ok(1), ok(1), ok(["m", 200.0]), ok(1)],  # rolling check 1
    ]
)
batch_store = UpstashRateLimitStore(client=batch_client)
res = batch_store.check_many(
    [("kf", 1, 60, False), ("kr", 1, 60, True)],
    now=200.0,
)
assert res == [(True, 0, 60), (True, 0, 60)], res
assert len(batch_client.calls[0]) == 8, batch_client.calls[0]
print("✅ Upstash check_many batch wire format correct (8 commands, 2 results)")

# 5d. Error entries raise UpstashRedisError (surfaced by the middleware as fail-open).
err_store = UpstashRateLimitStore(
    client=FakeUpstashClient([[{"error": "WRONGTYPE"}, ok(1), ok(60)]])
)
try:
    err_store.check_and_increment("k", limit=1, window_seconds=60)
    raise AssertionError("expected UpstashRedisError")
except UpstashRedisError:
    pass
print("✅ Upstash error entries raise UpstashRedisError")

# ── 6. Behavioral middleware tests on a scratch app ──────────────────────────
from src.config import settings as api_settings  # noqa: E402

allowed_origin = api_settings.cors_origins[0] if api_settings.cors_origins else None


def make_scratch(**rate_limits_overrides):
    """Scratch app + middleware with a FRESH in-memory store (isolation)."""
    scratch = FastAPI()
    scratch.add_middleware(RateLimitMiddleware)

    @scratch.get("/stocks/x")
    async def stub_stock(request: Request):
        """Cheap stub in the 'read' bucket."""
        return {"ok": True}

    store = InMemoryRateLimitStore()
    mock.patch.object(
        api_module,
        "get_rate_limit_store",
        return_value=store,
    ).start()
    if rate_limits_overrides:
        mock.patch.dict(api_module.RATE_LIMITS, rate_limits_overrides).start()
    return scratch


original_caps = api_module.RATE_LIMITS["read"]
try:
    # 6a. Tiny read caps → second request 429s with clean JSON + Retry-After.
    scratch = make_scratch(read={"burst": 1, "daily": 10, "weekly": 10})
    scw = TestClient(scratch, base_url="http://test.local")
    wk1 = scw.get("/stocks/x")
    wk2 = scw.get("/stocks/x", headers={"Origin": allowed_origin}) if allowed_origin else scw.get("/stocks/x")
    assert wk1.status_code == 200, f"first: {wk1.status_code}"
    assert wk2.status_code == 429, f"second: {wk2.status_code}"
    body = wk2.json()
    assert body.get("error") is True and body.get("status_code") == 429
    assert wk2.headers.get("retry-after"), "Missing Retry-After on 429"
    # CORS on the 429 echoes the request Origin only when on the allowlist.
    if allowed_origin:
        assert wk2.headers.get("access-control-allow-origin") == allowed_origin, (
            "429 must echo an ALLOWED origin (middleware runs outside CORS)"
        )
        assert wk2.headers.get("access-control-allow-credentials") == "true"
    wk3 = scw.get("/stocks/x", headers={"Origin": "https://evil.example.com"})
    assert wk3.headers.get("access-control-allow-origin") is None, (
        "429 must NOT leak CORS headers to disallowed origins"
    )
    print(f"✅ 429 works (burst): {wk2.status_code} {body.get('detail', '')[:40]}…")

    # 6b. OPTIONS preflight is never counted against the budget.
    scratch2 = make_scratch(read={"burst": 1, "daily": 10, "weekly": 10})
    sc2 = TestClient(scratch2, base_url="http://opt.test")
    o1 = sc2.options("/stocks/x")
    o2 = sc2.options("/stocks/x")
    assert o1.status_code in (200, 405) and o2.status_code in (200, 405)
    g1 = sc2.get("/stocks/x")
    g2 = sc2.get("/stocks/x")
    assert g1.status_code == 200, "GET after 2 OPTIONS should still pass burst=1"
    assert g2.status_code == 429, "second GET denied (OPTIONS never counted)"
    print("✅ OPTIONS preflight never counted against limits")

    # 6c. Allowed responses carry RateLimit-* headers (daily window, per-IP).
    scratch3 = make_scratch()
    sc3 = TestClient(scratch3, base_url="http://hdr.test")
    r3 = sc3.get("/stocks/x")
    assert r3.status_code == 200
    hdr = {k: v for k, v in r3.headers.items() if k.lower().startswith("ratelimit-")}
    assert "ratelimit-limit" in hdr and "ratelimit-remaining" in hdr and "ratelimit-reset" in hdr, hdr
    assert hdr["ratelimit-limit"] == str(RATE_LIMITS["read"]["daily"])
    print(f"✅ RateLimit-* headers present → {hdr}")

    # 6d. Fail-open: if the store raises, the middleware still serves the
    #     request via the in-memory fallback (never a 500).
    def _boom(*args, **kwargs):
        raise UpstashRedisError("simulated redis outage")

    scratch4 = make_scratch(read={"burst": 1, "daily": 10, "weekly": 10})
    mock.patch.object(api_module, "get_rate_limit_store", side_effect=_boom).start()
    sc4 = TestClient(scratch4, base_url="http://fo.test")
    f1 = sc4.get("/stocks/x")
    assert f1.status_code == 200, f"fail-open should serve: {f1.status_code}"
    print("✅ Fail-open: store outage → in-memory fallback, request served")

    # 6e. RATE_LIMIT_ENABLED=false → pass-through (no 429 even with tiny caps).
    scratch5 = make_scratch(read={"burst": 1, "daily": 10, "weekly": 10})
    with mock.patch.object(api_module.settings, "rate_limit_enabled", False):
        sc5 = TestClient(scratch5, base_url="http://off.test")
        p1 = sc5.get("/stocks/x")
        p2 = sc5.get("/stocks/x")
        p3 = sc5.get("/stocks/x")
        assert p1.status_code == p2.status_code == p3.status_code == 200, (
            f"rate limiting disabled must pass through: {p1.status_code},{p2.status_code},{p3.status_code}"
        )
    print("✅ RATE_LIMIT_ENABLED=false → pass-through (no throttling)")

    # 6f. RATE_LIMIT_BYPASS_IPS whitelist: a whitelisted client IP skips ALL
    #     limits (per-IP AND global buckets) — no 429 even past tiny caps;
    #     a non-whitelisted IP on the SAME app still hits the caps.
    with mock.patch.object(
        api_module.settings, "rate_limit_bypass_ips", "1.2.3.4,127.0.0.1"
    ):
        scratch6 = make_scratch(read={"burst": 1, "daily": 10, "weekly": 10})
        wl = TestClient(scratch6, base_url="http://wl.test", client=("1.2.3.4", 1000))
        for _ in range(5):
            wr = wl.get("/stocks/x")
            assert wr.status_code == 200, (
                f"whitelisted IP must never 429: {wr.status_code}"
            )
        other = TestClient(scratch6, base_url="http://wl.test", client=("9.9.9.9", 1000))
        o1 = other.get("/stocks/x")
        o2 = other.get("/stocks/x")
        assert o1.status_code == 200, f"non-whitelisted first request: {o1.status_code}"
        assert o2.status_code == 429, f"non-whitelisted must still 429: {o2.status_code}"

        # IPv4-mapped IPv6 (::ffff:127.0.0.1) must match a whitelisted
        # 127.0.0.1 after canonicalization — same address, different text.
        v6 = TestClient(scratch6, base_url="http://wl.test", client=("::ffff:127.0.0.1", 1000))
        for _ in range(3):
            vr = v6.get("/stocks/x")
            assert vr.status_code == 200, (
                f"IPv4-mapped IPv6 must match whitelist: {vr.status_code}"
            )
    print("✅ RATE_LIMIT_BYPASS_IPS: whitelisted IP unlimited, others still capped")

    # 6g. _canonical_ip: equivalent address spellings collapse to one key.
    assert api_module._canonical_ip(" ::ffff:127.0.0.1 ") == "127.0.0.1"
    assert api_module._canonical_ip("::1") == "::1"
    assert api_module._canonical_ip("unknown") == "unknown"
    print("✅ _canonical_ip normalizes IPv4-mapped IPv6, keeps non-IP passthrough")
finally:
    mock.patch.stopall()
    api_module.RATE_LIMITS["read"] = original_caps

print()
print("🎯 All rate-limit tests pass!")
