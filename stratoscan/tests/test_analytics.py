"""Hermetic tests for the Day-4 event funnel — no network, no DB.

Run: python -m tests.test_analytics

Covers the contract the funnel is only worth having if it holds:

  1. Vocabulary: every event declares its prop keys, and the browser's
     allowlist is a strict subset. The fifteen rows of the spec's table are
     sixteen names (returned_day_2 and returned_day_7 are two).
  2. ``track()``: unknown names write nothing; unknown PROP keys are dropped
     while allowed ones survive; an ``anon_id`` that is not a UUID is stored as
     NULL; a raising database never reaches the caller; and the whole thing is
     a no-op when ``analytics_enabled`` is False.
  3. Derived events: ``second_backtest`` fires on the 2nd completed backtest
     and not the 1st or 3rd; ``returned_day_2`` / ``returned_day_7`` fire once
     for the right gap and not for the wrong one.
  4. ``POST /events``: 204 + a row for a client event, 400 + NO row for a
     server-only name (the anti-forgery gate), optional Bearer attribution.
  5. ``/screen`` with the fake interpreter fires exactly one interpret_* and
     one screen_returned.
  6. Schema: the runtime constants, the init_schema wiring and the Alembic
     revision agree (both, not either), and the chain still has one head.

Every rejection is paired with the acceptance next to it — a whitelist test
that only proves the good case proves nothing.

Fully hermetic: FakeEventDB stands in for PostgresDB's event surface,
FakeQualityDB for the screener's, FakeLLMRouter for the LLM.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Hermetic: keep the API rate limiter OFF (in-memory AND Upstash) so this
# suite never touches the network or real rate-limit counters.
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
# /events attributes a Bearer token when one is present; the signing key only
# needs to be non-empty for that to be a realistic path.
os.environ.setdefault("JWT_SECRET", "test-analytics-jwt-secret")

from fastapi.testclient import TestClient  # noqa: E402

import src.analytics as analytics  # noqa: E402
from src.config import settings  # noqa: E402
from tests.fakes import FakeEventDB, FakeLLMRouter, FakeQualityDB  # noqa: E402

_ORIGINAL_ANALYTICS_ENABLED = settings.analytics_enabled
settings.analytics_enabled = True


def _fresh_ts(minutes_ago: int = 2) -> str:
    """A UTC ISO timestamp a few minutes old (inside the freshness guard)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Vocabulary
# ═══════════════════════════════════════════════════════════════════════════════
print("1. Event vocabulary...")

assert len(analytics.EVENTS) == 16, sorted(analytics.EVENTS)
for name in (
    "landing_view", "query_submitted", "interpret_deterministic", "interpret_llm",
    "screen_returned", "backtest_started", "backtest_completed", "backtest_failed",
    "backtest_duration", "template_used", "result_shared", "signup_started",
    "signup_completed", "second_backtest", "returned_day_2", "returned_day_7",
):
    assert name in analytics.EVENTS, f"missing event: {name}"

assert analytics.CLIENT_EVENTS == frozenset(
    {"landing_view", "query_submitted", "template_used", "result_shared", "signup_started"}
), sorted(analytics.CLIENT_EVENTS)
assert analytics.CLIENT_EVENTS <= set(analytics.EVENTS), "client events must be a subset"
assert analytics.DERIVED_EVENTS <= set(analytics.EVENTS)
# A client that could send backtest_completed could forge the conversion rate.
assert analytics.CLIENT_EVENTS.isdisjoint(analytics.DERIVED_EVENTS)

# The PII gate: no event may accept a key that would carry user content.
FORBIDDEN = {
    "query", "email", "password", "token", "error", "message", "trace",
    "headline", "url", "symbol_list", "tickers", "raw_input",
}
for event, keys in analytics.EVENTS.items():
    leaked = keys & FORBIDDEN
    assert not leaked, f"{event} allows PII-bearing prop(s): {sorted(leaked)}"

# The exact prop sets the spec pins.
assert analytics.EVENTS["query_submitted"] == frozenset({"surface"})
assert analytics.EVENTS["screen_returned"] == frozenset({"result_count", "total_matches"})
assert analytics.EVENTS["backtest_started"] == frozenset({"symbols", "mode"})
assert analytics.EVENTS["backtest_failed"] == frozenset({"mode", "reason"})
assert analytics.EVENTS["backtest_duration"] == frozenset({"duration_ms", "mode"})
assert analytics.EVENTS["landing_view"] == frozenset()
print(f"✅ {len(analytics.EVENTS)} events, {len(analytics.CLIENT_EVENTS)} client-reportable, no PII keys")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. track() — validation, whitelist, identity, failure isolation, master switch
# ═══════════════════════════════════════════════════════════════════════════════
print("2. track() contract...")

db = FakeEventDB()
ANON = str(uuid.uuid4())

# Unknown event name → nothing written.
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("not_a_real_event", user_id=1)
assert db.events == [], "an unknown event name must write nothing"

# Known name → exactly one row, with a default empty props object.
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("landing_view", anon_id=ANON)
assert len(db.events) == 1, db.events
assert db.events[0]["name"] == "landing_view"
assert db.events[0]["props"] == {}
assert db.events[0]["user_id"] is None
assert db.events[0]["anon_id"] == ANON
print("✅ unknown name writes nothing; known name writes one row")

# The prop whitelist drops disallowed keys and keeps allowed ones. This is the
# PII gate: a caller passing the query text must not be able to record it.
db = FakeEventDB()
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track(
        "query_submitted",
        user_id=3,
        props={
            "surface": "screener",     # whitelisted -> kept
            "query": "my secret idea",  # NOT whitelisted -> dropped
            "email": "alice@x.com",    # NOT whitelisted -> dropped
            "user_id": 99,             # NOT whitelisted -> dropped
        },
    )
assert db.events[0]["props"] == {"surface": "screener"}, db.events[0]["props"]

# Value-shaped gate: non-scalars, None and over-long strings are dropped, and a
# 64-char string is the boundary that survives.
db = FakeEventDB()
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("screen_returned", props={
        "result_count": 20,                    # int -> kept
        "total_matches": "x" * 65,             # too long -> dropped
    })
    analytics.track("backtest_duration", props={"duration_ms": 12.5, "mode": "x" * 64})
    analytics.track("interpret_deterministic", props={"confidence": {"nested": 1}})
assert db.events[0]["props"] == {"result_count": 20}, db.events[0]["props"]
assert db.events[1]["props"] == {"duration_ms": 12.5, "mode": "x" * 64}, db.events[1]["props"]
assert db.events[2]["props"] == {}, "a non-scalar prop value must be dropped"
print("✅ prop whitelist drops PII keys and non-scalar / over-long values")

# anon_id must be a UUID: anything else is stored as NULL rather than trusted
# (it is an identity key, and a client controls the header it arrives in).
db = FakeEventDB()
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("landing_view", anon_id="not-a-uuid")
    analytics.track("landing_view", anon_id="")
    analytics.track("landing_view", anon_id=None)
    analytics.track("landing_view", anon_id=ANON)
assert [e["anon_id"] for e in db.events] == [None, None, None, ANON]
print("✅ non-UUID anon_id stored as NULL; a UUID is kept")

# A broken database never reaches the caller — not from the insert, and not
# from the derived-event queries that run after it.
db = FakeEventDB()
with mock.patch.object(analytics, "_db", side_effect=RuntimeError("db is down")):
    analytics.track("landing_view", user_id=1)  # must not raise

with mock.patch.object(analytics, "_db", return_value=db), mock.patch.object(
    db, "insert_event", side_effect=RuntimeError("write failed")
):
    analytics.track("landing_view", user_id=1)  # must not raise

failing = FakeEventDB()
with mock.patch.object(analytics, "_db", return_value=failing), mock.patch.object(
    failing, "first_event_at", side_effect=RuntimeError("read failed")
):
    analytics.track("backtest_completed", user_id=1, props={"symbols": 1, "mode": "sync"})
assert len(failing.events) == 1, "the primary event must survive a derived-event failure"
print("✅ track() swallows insert AND derived-event failures")

# Master switch.
db = FakeEventDB()
with mock.patch.object(settings, "analytics_enabled", False), mock.patch.object(
    analytics, "_db", return_value=db
):
    analytics.track("landing_view", user_id=1)
    analytics.track("backtest_completed", user_id=1, props={"symbols": 1, "mode": "sync"})
assert db.events == [], db.events
print("✅ analytics_enabled=False writes nothing (including derived events)")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Derived events
# ═══════════════════════════════════════════════════════════════════════════════
print("3. Derived events...")

# second_backtest: exactly the 2nd completion, never the 1st or 3rd.
db = FakeEventDB()
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("backtest_completed", user_id=42, props={"symbols": 5, "mode": "sync"})
    assert "second_backtest" not in db.names, "must not fire on the 1st"
    analytics.track("backtest_completed", user_id=42, props={"symbols": 5, "mode": "sync"})
    assert db.names.count("second_backtest") == 1, db.names
    analytics.track("backtest_completed", user_id=42, props={"symbols": 5, "mode": "sync"})
    assert db.names.count("second_backtest") == 1, "must not fire again on the 3rd"
# A different identity's second run does not fire the first identity's event.
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("backtest_completed", user_id=43, props={"symbols": 5, "mode": "sync"})
assert db.names.count("second_backtest") == 1
print("✅ second_backtest fires on the 2nd completion only, per identity")

# returned_day_2: seeded first event yesterday, one event to come back with.
db = FakeEventDB()
yesterday = datetime.now(timezone.utc) - timedelta(days=1)
db.seed("landing_view", user_id=7, occurred_at=yesterday)
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("landing_view", user_id=7)
    assert db.names_for(user_id=7).count("returned_day_2") == 1, db.names_for(user_id=7)
    analytics.track("query_submitted", user_id=7, props={"surface": "screener"})
    assert db.names_for(user_id=7).count("returned_day_2") == 1, "fires at most once"
print("✅ returned_day_2 fires once when the first event was yesterday")

# ...and not when the first event was today.
db = FakeEventDB()
db.seed("landing_view", user_id=8, occurred_at=datetime.now(timezone.utc))
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("landing_view", user_id=8)
assert "returned_day_2" not in db.names, db.names

# ...and not at 2 days, where neither window applies.
db = FakeEventDB()
db.seed("landing_view", user_id=9,
        occurred_at=datetime.now(timezone.utc) - timedelta(days=2))
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("landing_view", user_id=9)
assert "returned_day_2" not in db.names and "returned_day_7" not in db.names, db.names
print("✅ neither day-2 nor day-7 fires for a same-day or 2-day-old first event")

# returned_day_7: the 6-8 day window.
for days, expected in ((5, False), (6, True), (7, True), (8, True), (9, False)):
    db = FakeEventDB()
    db.seed("landing_view", user_id=10,
            occurred_at=datetime.now(timezone.utc) - timedelta(days=days))
    with mock.patch.object(analytics, "_db", return_value=db):
        analytics.track("landing_view", user_id=10)
    fired = db.names_for(user_id=10).count("returned_day_7") == 1
    assert fired is expected, f"day {days}: expected day_7={expected}, got {fired}"
print("✅ returned_day_7 fires exactly inside the 6-8 day window")

# Anonymous identity works the same way (the pre-signup half of the funnel).
db = FakeEventDB()
db.seed("landing_view", anon_id=ANON,
        occurred_at=datetime.now(timezone.utc) - timedelta(days=1))
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("landing_view", anon_id=ANON)
assert db.names_for(anon_id=ANON).count("returned_day_2") == 1

# With no identity at all, nothing is derived (never a table-wide count).
db = FakeEventDB()
with mock.patch.object(analytics, "_db", return_value=db):
    analytics.track("landing_view")
    analytics.track("backtest_completed", props={"symbols": 1, "mode": "sync"})
assert db.names == ["landing_view", "backtest_completed"], db.names
print("✅ derived events use user_id, else anon_id, and skip a blank identity")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. HTTP surface: POST /events (+ /admin/events auth)
# ═══════════════════════════════════════════════════════════════════════════════
print("4. POST /events...")

# The app's own DB seam is patched (src.database.PostgresDB), which is also
# what analytics._db() imports lazily — one fake for both.
api_db = FakeEventDB()
mock.patch("src.database.PostgresDB", return_value=api_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()
# Hermetic: /screen attaches stored sentiment with one batch read when the
# news database is configured (it is, in .env). Patch the function, not the
# property, so no connection is ever opened.
mock.patch("src.news.get_sentiment_for_screen", return_value={}).start()

from src.api import app  # noqa: E402  (imported after the patches above)

client = TestClient(app)

r = client.post("/events", json={"name": "landing_view"}, headers={"X-Anon-Id": ANON})
assert r.status_code == 204, (r.status_code, r.text)
assert len(api_db.events) == 1, api_db.events
assert api_db.events[0]["name"] == "landing_view"
assert api_db.events[0]["anon_id"] == ANON
assert r.content in (b"", b"null"), r.content

r = client.post("/events", json={"name": "query_submitted",
                                 "props": {"surface": "backtest", "query": "leak me"}})
assert r.status_code == 204, r.status_code
assert api_db.events[1]["props"] == {"surface": "backtest"}, api_db.events[1]["props"]

# A server-only name is REFUSED, not ignored — a script must not be able to
# forge the conversion rate.
before = len(api_db.events)
r = client.post("/events", json={"name": "backtest_completed",
                                 "props": {"symbols": 5, "mode": "sync"}})
assert r.status_code == 400, (r.status_code, r.text)
assert len(api_db.events) == before, "a rejected name must write no row"
r = client.post("/events", json={"name": "second_backtest"})
assert r.status_code == 400, r.status_code
assert len(api_db.events) == before
print("✅ client event → 204 + row; server-only name → 400 + no row")

# Optional Bearer attribution: a valid token sets user_id, a junk one does not
# 401 — the browser is fire-and-forget and must never see an auth error here.
from src.auth import create_access_token  # noqa: E402

token = create_access_token(4242, "walker@example.com", 0)
r = client.post("/events", json={"name": "result_shared"},
                headers={"Authorization": f"Bearer {token}"})
assert r.status_code == 204, r.status_code
assert api_db.events[-1]["user_id"] == 4242, api_db.events[-1]

r = client.post("/events", json={"name": "result_shared"},
                headers={"Authorization": "Bearer not-a-jwt"})
assert r.status_code == 204, (r.status_code, r.text)
assert api_db.events[-1]["user_id"] is None, api_db.events[-1]

r = client.post("/events", json={"name": "result_shared"})
assert r.status_code == 204, r.status_code
print("✅ Bearer token attributes the event; a bad/absent token still gets 204")

# /admin/events is admin-gated (401 without a token, 403 for a non-admin).
r = client.get("/admin/events")
assert r.status_code in (401, 403), (r.status_code, r.text)
print("✅ GET /admin/events is admin-gated")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. /screen fires exactly one interpret_* and one screen_returned
# ═══════════════════════════════════════════════════════════════════════════════
print("5. /screen event wiring...")

import src.interpreter as interp  # noqa: E402

RECORDS = [
    {
        "symbol": "AAA", "company_name": "AAA Corp", "sector": "Technology",
        "current_price": 50.0, "sma_50": 45.0, "sma_200": 40.0,
        "high_52w": 60.0, "low_52w": 30.0, "pe_ratio": 12.0, "rsi_14": 55.0,
        "market_cap": 1_500_000_000, "volume": 1_000_000,
        "updated_at": _fresh_ts(),
    },
]

# The screener's own DB is the FakeQualityDB (real filter + score semantics
# over an in-memory list); the event writes still go to FakeEventDB, because
# the two modules hold separate references to PostgresDB. Patching the module
# attributes (not the classes) means the import order cannot matter.
mock.patch("src.screener.PostgresDB",
           return_value=FakeQualityDB(RECORDS)).start()

# cache_enabled=False keeps the screen cache and the interpret cache out of the
# way, so "exactly one" is about the wiring rather than about a cache hit.
# (a) the LLM path
api_db.events.clear()
interp._interpret_cache.clear()
with mock.patch.object(settings, "cache_enabled", False), mock.patch.object(
    settings, "use_deterministic_interpreter", False
), mock.patch("src.llm_router.get_router",
              return_value=FakeLLMRouter('{"sector": "Technology"}')):
    r = client.post("/screen", json={"query": "some fuzzy idea please", "limit": 5})
assert r.status_code == 200, (r.status_code, r.text)
names = api_db.names
interp_events = [n for n in names if n.startswith("interpret_")]
assert interp_events == ["interpret_llm"], (names, r.text)
assert names.count("screen_returned") == 1, names
screen_props = next(e["props"] for e in api_db.events if e["name"] == "screen_returned")
assert screen_props["result_count"] == 1, (screen_props, r.text)
assert screen_props["total_matches"] >= 1, screen_props

# (b) the deterministic path — same two events, the other name
api_db.events.clear()
interp._interpret_cache.clear()
with mock.patch.object(settings, "cache_enabled", False), mock.patch.object(
    settings, "use_deterministic_interpreter", True
):
    r = client.post("/screen", json={"query": "undervalued small-cap tech stocks",
                                     "limit": 5})
assert r.status_code == 200, (r.status_code, r.text)
names = api_db.names
interp_events = [n for n in names if n.startswith("interpret_")]
assert interp_events == ["interpret_deterministic"], (names, r.text)
assert names.count("screen_returned") == 1, names
print("✅ /screen fires exactly one interpret_* and one screen_returned, on both paths")

# A failed interpretation records neither (there is no interpretation to
# report, and the screen aborted rather than returning a page).
api_db.events.clear()
with mock.patch.object(settings, "cache_enabled", False), mock.patch.object(
    settings, "use_deterministic_interpreter", False
), mock.patch("src.llm_router.get_router", side_effect=RuntimeError("provider down")):
    interp._interpret_cache.clear()
    r = client.post("/screen", json={"query": "anything at all here", "limit": 5})
assert r.status_code == 200, r.status_code
assert not [n for n in api_db.names if n.startswith("interpret_")], api_db.names
print("✅ a failed interpretation records no interpret_* event")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Schema + migration wiring (both, not either)
# ═══════════════════════════════════════════════════════════════════════════════
print("6. Schema + migration...")

from src.database import (  # noqa: E402
    ERROR_LOG_INDEXES,
    ERROR_LOG_SCHEMA,
    EVENTS_INDEXES,
    EVENTS_SCHEMA,
    _event_identity_where,
)

for fragment in ("CREATE TABLE IF NOT EXISTS events", "anon_id", "props", "JSONB"):
    assert fragment in EVENTS_SCHEMA, fragment
assert "REFERENCES users(id) ON DELETE SET NULL" in EVENTS_SCHEMA, \
    "deleting an account must anonymise its events, not delete them"
assert "CREATE TABLE IF NOT EXISTS error_log" in ERROR_LOG_SCHEMA
assert len(EVENTS_INDEXES) == 3 and len(ERROR_LOG_INDEXES) == 1

# The identity rule has to be shared, not re-implemented: analytics derives
# from it and so do the queries.
assert _event_identity_where(5, ANON) == ("user_id = %s", [5])
assert _event_identity_where(None, ANON) == ("anon_id = %s", [ANON])
assert _event_identity_where(None, None) == (None, [])

init_src = (Path(__file__).resolve().parent.parent / "src" / "database.py").read_text(
    encoding="utf-8"
)
init_body = init_src.split("def init_schema", 2)[1]
assert "EVENTS_SCHEMA" in init_body and "ERROR_LOG_SCHEMA" in init_body, \
    "init_schema must create both tables (the self-healing path)"

versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
migs = []
for f in sorted(versions.glob("*.py")):
    if f.name.startswith("__"):
        continue
    src = f.read_text(encoding="utf-8")
    rev = re.search(r'^revision: str = "([^"]+)"', src, re.M)
    down = re.search(
        r'^down_revision: Union\[str, Sequence\[str\], None\] = "([^"]+)"', src, re.M
    )
    assert rev, f.name
    migs.append((f.name, rev.group(1), down.group(1) if down else None))
revs = [r for _, r, _ in migs]
downs = [d for _, _, d in migs if d]
heads = [r for r in revs if r not in downs]
assert heads == ["f4a5b6c7d8e9"], heads
events_mig = next(m for m in migs if m[1] == "f4a5b6c7d8e9")
assert events_mig[2] == "e3f4a5b6c7d8", events_mig  # chains after google_sub
body = (versions / events_mig[0]).read_text(encoding="utf-8")
assert "CREATE TABLE IF NOT EXISTS events" in body
assert "CREATE TABLE IF NOT EXISTS error_log" in body
assert "DROP TABLE IF EXISTS events" in body  # downgrade reverses
assert "DROP TABLE IF EXISTS error_log" in body
assert "REFERENCES users(id) ON DELETE SET NULL" in body
print(f"✅ schema + init_schema + migration agree (single head {heads[0]})")


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Route registration
# ═══════════════════════════════════════════════════════════════════════════════
print("7. Routes...")

paths = {rt.path for rt in app.routes if hasattr(rt, "path")}
assert "/events" in paths, sorted(paths)
assert "/admin/events" in paths
# /events must stay in the default read bucket: a new scope would be a new
# bucket on every submitting surface, for a single cheap insert.
from src.api import _scope_for_path  # noqa: E402

assert _scope_for_path("/events") == "read", _scope_for_path("/events")
print("✅ /events + /admin/events registered; /events stays in the read scope")

settings.analytics_enabled = _ORIGINAL_ANALYTICS_ENABLED
mock.patch.stopall()

print()
print("🎯 All analytics tests pass!")
