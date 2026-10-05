"""Day 12 — entitlements without a payment provider. Hermetic: fake DB, fake
engine; no network.

  A. plan_for(): the one function. Aliases, fail-closed on unknown plans,
     grandfathering beats everything, no cache (reads the row it is given).
  B. Transitions: upgrade now, downgrade at period end, revocation after a
     grace period, an admin's hand immediate — via the billing conformance
     suite every future provider must also pass.
  C. Grandfathering: exactly the first 100 accounts, ever.
  D. Every gated route, found from the app itself: a gate without a direct
     API test here fails the suite ("impossible by convention").
  E. A free account cannot reach a gated capability by calling the API
     directly; boundaries (exactly at the limit, month rollover); an admin's
     plan change applies on the next request in both directions.
  F. Never gated: accuracy. A free backtest is the same result as a Pro one;
     explain, share pages and robustness have no gate.
  G. Monitoring emails: the digest skips accounts not entitled.
  H. No price string in any API response.

Run: python -m tests.test_entitlements
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("JWT_SECRET", "entitlements-dummy-secret")
os.environ["ANALYTICS_ENABLED"] = "false"

from unittest import mock  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src import entitlements as en  # noqa: E402
from src.billing import ManualBillingProvider, set_billing_provider  # noqa: E402
from tests.billing_conformance import run_conformance  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

# ═══════════════════════════════════════════════════════════════════════════
# A. plan_for
# ═══════════════════════════════════════════════════════════════════════════
manual = ManualBillingProvider(db_factory=FakeUserDB)
P = lambda row: en.plan_for(row, now=NOW, provider=manual)  # noqa: E731
assert P({"plan": "free"}).plan == "free" and P({}).plan == "free"
assert P({"plan": "premium"}).plan == "pro", "premium is the old name for pro"
assert P({"plan": "PRO "}).plan == "pro"
assert P({"plan": "platinum"}).plan == "free", "an unknown plan fails CLOSED"
g = P({"plan": "free", "grandfathered_pro": True, "plan_pending": "free",
       "plan_change_at": NOW - timedelta(days=1)})
assert g.plan == "pro" and g.source == "grandfathered", "grandfathering beats anything pending"
assert P({"plan": "free"}).limits == en.PLANS["free"] and en.PLANS["free"].csv_export is False
assert set(en.FEATURES) == {"backtests", "saved_strategies", "csv_export", "monitoring_emails"}, \
    "exactly the four gated capabilities"
assert en.usage_period(datetime(2026, 10, 31, 23, 59, tzinfo=timezone.utc)) == "2026-10"
assert en.usage_period(datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)) == "2026-11"
assert en.period_end(datetime(2026, 12, 20, tzinfo=timezone.utc)) == datetime(2027, 1, 1, tzinfo=timezone.utc)
# The frontend's public copy of the plans (Pricing/Features) must match.
from pathlib import Path  # noqa: E402

ts = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "plans.ts").read_text()
for tier, key in (("free", "free"), ("pro", "pro")):
    block = re.search(key + r": \{(.*?)\n  \}", ts, re.S).group(1)
    lim = en.PLANS[tier]
    want = {"backtestsPerMonth": "null" if lim.backtests_per_month is None else str(lim.backtests_per_month),
            "savedStrategies": str(lim.saved_strategies), "csvExport": str(lim.csv_export).lower(),
            "monitoringEmails": str(lim.monitoring_emails).lower()}
    for k, v in want.items():
        assert re.search(rf"{k}: {v}\b", block), f"frontend/src/lib/plans.ts {tier}.{k} != {v}"
assert f"GRANDFATHER_LIMIT = {en.GRANDFATHER_LIMIT}" in ts
print("✅ plan_for: aliases, unknown plans fail closed, grandfathering wins, exactly four features; "
      "frontend plans.ts matches")

# ═══════════════════════════════════════════════════════════════════════════
# B. Transitions (the conformance suite a payment adapter must also pass)
# ═══════════════════════════════════════════════════════════════════════════
n = run_conformance(make_provider=lambda db: ManualBillingProvider(db_factory=lambda: db),
                    make_db=FakeUserDB)
print(f"✅ ManualBillingProvider passes the billing conformance suite ({n} checks): upgrade now, "
      f"downgrade at period end, revoke after {en.GRACE_DAYS} days' grace, admin set immediate")

# ═══════════════════════════════════════════════════════════════════════════
# C. Grandfathering
# ═══════════════════════════════════════════════════════════════════════════
gdb = FakeUserDB()
ids = [gdb.create_user(f"g{i}@x.test", "h", "free") for i in range(en.GRANDFATHER_LIMIT + 5)]
flags = [gdb.get_user_by_id(i)["grandfathered_pro"] for i in ids]
assert flags == [True] * en.GRANDFATHER_LIMIT + [False] * 5
gdb.delete_user(ids[0], "g0@x.test")
assert gdb.get_user_by_id(gdb.create_user("late@x.test", "h", "free"))["grandfathered_pro"] is False, \
    "deleting an early account never frees a slot"
assert en.plan_for(gdb.get_user_by_id(ids[1]), provider=manual).plan == "pro"
print(f"✅ grandfathering: exactly the first {en.GRANDFATHER_LIMIT} accounts, ever, are Pro for life")

# ═══════════════════════════════════════════════════════════════════════════
# D. Every gated route — from the app itself
# ═══════════════════════════════════════════════════════════════════════════
fake_db = FakeUserDB()
fake_db.grandfather_issued = en.GRANDFATHER_LIMIT  # accounts below are NOT grandfathered
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()
set_billing_provider(ManualBillingProvider(db_factory=lambda: fake_db))

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import src.api as api  # noqa: E402


def gates_in_app():
    found = {}
    for route in api.app.routes:
        if not isinstance(route, APIRoute):
            continue
        stack = list(route.dependant.dependencies)
        while stack:
            dep = stack.pop()
            feature = getattr(dep.call, "entitlement_feature", None)
            if feature:
                for method in route.methods:
                    found[(method, route.path)] = feature
            stack.extend(dep.dependencies)
    return found


EXPECTED_GATES = {
    ("POST", "/backtest"): "backtests",
    ("POST", "/backtest/async"): "backtests",
    ("POST", "/backtest/export"): "csv_export",
    ("POST", "/screen/export"): "csv_export",
    ("POST", "/me/strategies"): "saved_strategies",
}
assert gates_in_app() == EXPECTED_GATES, f"gated routes drifted: {gates_in_app()}"
covered = set(EXPECTED_GATES.values()) | {"monitoring_emails"}  # the digest, section G
assert covered == set(en.FEATURES)
print(f"✅ {len(EXPECTED_GATES)} gated routes found from the app match the table; every feature is gated somewhere")

# ═══════════════════════════════════════════════════════════════════════════
# E. Direct API calls by a free account
# ═══════════════════════════════════════════════════════════════════════════
client = TestClient(api.app)
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731


def account(email):
    tok = client.post("/auth/register", json={"email": email, "password": "correct-horse-1"}).json()["access_token"]
    return tok, fake_db.get_user_by_email(email)["id"]


admin_tok, admin_id = account("boss@x.test")
fake_db.set_user_admin(admin_id, True)
free_tok, free_id = account("free@x.test")
assert fake_db.get_user_by_id(free_id)["grandfathered_pro"] is False


def series(seed=5, n=1200):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.018, n)))
    idx = pd.bdate_range("2019-01-02", periods=n)
    return pd.DataFrame({"Open": np.r_[close[0], close[:-1]], "High": close * 1.01,
                         "Low": close * 0.99, "Close": close, "Volume": 5_000_000}, index=idx)


class _Engine:
    _last_screen_message = None
    _last_fill_note = None

    def backtest_top_results(self, interpreted, top_n=5, **kwargs):
        from src.backtester import run_backtest

        df = series()
        return {"EQ": run_backtest("EQ", {"buy_rules": ["rsi < 35"], "sell_rules": ["rsi > 65"]},
                                   "2019-01-02", str(df.index[-1].date()), df=df)}

    def backtest(self, *a, **k):
        return {}


engine = mock.patch("src.backtester.BacktestEngine", return_value=_Engine())
engine.start()
started = []
mock.patch.object(api, "_start_async_backtest",
                  side_effect=lambda *a, **k: started.append(1) or {"job_id": "bt_x", "status": "queued"}).start()


class _Report:
    results = []


mock.patch.object(api, "_run_screen", return_value=(None, _Report())).start()

BODY = {"query": "oversold large cap stocks"}  # no uppercase ticker: goes through the screen path
DIRECT_CALLS = {
    ("POST", "/backtest"): lambda t: client.post("/backtest", headers=H(t), json=BODY),
    ("POST", "/backtest/async"): lambda t: client.post("/backtest/async", headers=H(t), json=BODY),
    ("POST", "/backtest/export"): lambda t: client.post("/backtest/export", headers=H(t), json=BODY),
    ("POST", "/screen/export"): lambda t: client.post("/screen/export", headers=H(t), json={"query": "tech"}),
    ("POST", "/me/strategies"): lambda t: client.post("/me/strategies", headers=H(t), json={"job_id": "bt_done"}),
}
assert set(DIRECT_CALLS) == set(EXPECTED_GATES), "every gate has a direct-API test below"

# Put the free account AT each limit, then call every gated route directly.
period = en.usage_period()
FREE_LIMIT = en.PLANS["free"].backtests_per_month
fake_db.usage[(free_id, period, "backtests")] = FREE_LIMIT
for i in range(en.PLANS["free"].saved_strategies):
    fake_db.save_strategy(free_id, f"q{i}", {"buy_rules": [], "sell_rules": []}, ["X"], None)
with api._BACKTEST_JOBS_LOCK:
    api._BACKTEST_JOBS["bt_done"] = {"job_id": "bt_done", "status": "done", "user_email": "free@x.test",
                                     "result": {"query": "q", "strategy": {"buy_rules": ["rsi < 30"]},
                                                "results": {"EQ": {"total_return_pct": 1.0}}}}
for key, call in DIRECT_CALLS.items():
    r = call(free_tok)
    assert r.status_code == 403, (key, r.status_code, r.text)
    assert r.json()["upgrade_required"] == EXPECTED_GATES[key], (key, r.json())
    assert not re.search(r"[$€£]\s?\d", r.text), "refusals never quote a price"
assert not started, "a refused async backtest never starts"
assert fake_db.usage[(free_id, period, "backtests")] == FREE_LIMIT, "a refusal charges nothing"
assert client.post("/screen/export", json={"query": "tech"}).status_code == 401
print("✅ a free account at its limits is refused by EVERY gated route called directly (403 + upgrade_required)")

# Boundaries: one below the limit → allowed, and that use takes the last slot.
fake_db.usage[(free_id, period, "backtests")] = FREE_LIMIT - 1
assert client.post("/backtest/async", headers=H(free_tok), json=BODY).status_code == 200
assert fake_db.usage[(free_id, period, "backtests")] == FREE_LIMIT and len(started) == 1
assert client.post("/backtest/async", headers=H(free_tok), json=BODY).status_code == 403
fake_db.usage[(free_id, period, "backtests")] = FREE_LIMIT - 1
assert client.post("/backtest", headers=H(free_tok), json=BODY).status_code == 200
assert client.post("/backtest", headers=H(free_tok), json=BODY).status_code == 403
# The check and the charge both read the same month; a new month starts at zero.
with mock.patch.object(en, "usage_period", return_value="2099-01"):
    assert client.post("/backtest/async", headers=H(free_tok), json=BODY).status_code == 200
    assert fake_db.usage[(free_id, "2099-01", "backtests")] == 1
# The race for the last slot: the charge is atomic even if the check passed.
fake_db.usage[(free_id, period, "backtests")] = FREE_LIMIT - 1
real_check = api._check_entitlement
with mock.patch.object(api, "_check_entitlement", side_effect=lambda u, f: real_check(u, f)):
    fake_db.usage[(free_id, period, "backtests")] = FREE_LIMIT - 1
    with mock.patch.object(fake_db, "get_usage", return_value=0):  # a stale read lets the check pass
        fake_db.usage[(free_id, period, "backtests")] = FREE_LIMIT
        assert client.post("/backtest/async", headers=H(free_tok), json=BODY).status_code == 403
# The check alone (without the charge behind it) refuses exactly at the limit.
fake_db.usage[(free_id, period, "backtests")] = FREE_LIMIT
try:
    api._check_entitlement(fake_db.get_user_by_id(free_id), "backtests")
    raise AssertionError("the check must refuse at the limit")
except api.EntitlementError as e:
    assert e.entitlement_feature == "backtests"
fake_db.usage[(free_id, period, "backtests")] = FREE_LIMIT - 1
api._check_entitlement(fake_db.get_user_by_id(free_id), "backtests")
fake_db.strategies.pop(next(iter(fake_db.strategies)))
assert client.post("/me/strategies", headers=H(free_tok), json={"job_id": "bt_done"}).status_code == 200
assert client.post("/me/strategies", headers=H(free_tok), json={"job_id": "bt_done"}).status_code == 403
print("✅ boundaries: the last slot is usable once, then refused; a new month resets; a stale check can't beat the atomic charge")

# An admin's plan change applies on the NEXT request — both directions.
r = client.patch(f"/admin/users/{free_id}", headers=H(admin_tok), json={"plan": "pro"})
assert r.status_code == 200 and r.json()["plan"] == "pro", r.text
assert client.post("/screen/export", headers=H(free_tok), json={"query": "tech"}).status_code == 200
assert client.post("/backtest/export", headers=H(free_tok), json=BODY).status_code == 200
assert client.post("/backtest/async", headers=H(free_tok), json=BODY).status_code == 200, "Pro is unlimited"
ent = client.get("/me/entitlements", headers=H(free_tok)).json()
assert ent["plan"] == "pro" and ent["limits"]["backtests_per_month"] is None and ent["source"] == "manual"
r = client.patch(f"/admin/users/{free_id}", headers=H(admin_tok), json={"plan": "free"})
assert r.json()["plan"] == "free"
assert client.post("/screen/export", headers=H(free_tok), json={"query": "tech"}).status_code == 403
# Scheduled: a downgrade waits for the period end (and the admin sees it).
client.patch(f"/admin/users/{free_id}", headers=H(admin_tok), json={"plan": "pro"})
r = client.patch(f"/admin/users/{free_id}", headers=H(admin_tok), json={"plan": "free", "plan_change": "scheduled"})
assert r.json()["plan"] == "pro" and r.json()["pending_plan"] == "free" and r.json()["plan_change_at"], r.json()
assert client.post("/screen/export", headers=H(free_tok), json={"query": "tech"}).status_code == 200
with mock.patch.object(en, "utcnow", return_value=en.period_end() + timedelta(seconds=1)):
    assert client.post("/screen/export", headers=H(free_tok), json={"query": "tech"}).status_code == 403
    listed = {u["id"]: u for u in client.get("/admin/users", headers=H(admin_tok)).json()["users"]}
    assert listed[free_id]["plan"] == "free", "the admin list shows the plan in force, not the raw column"
print("✅ admin plan changes apply on the next request (up and down); a scheduled downgrade waits for period end")

me = client.get("/me/entitlements", headers=H(free_tok)).json()
assert {"plan", "plan_label", "source", "limits", "usage", "period", "resets_at", "grandfathered"} <= set(me)
assert me["usage"]["saved_strategies"] == 3 and me["grandfathered"] is False
gf_tok = client.post("/auth/login", json={"email": "boss@x.test", "password": "correct-horse-1"}).json()["access_token"]
assert client.get("/me/entitlements", headers=H(gf_tok)).json()["plan"] == "free"
assert client.get("/me/entitlements").status_code == 401
print("✅ /me/entitlements: plan, source, limits, this month's usage, reset date")

# ═══════════════════════════════════════════════════════════════════════════
# F. Never gated: accuracy
# ═══════════════════════════════════════════════════════════════════════════
pro_tok, pro_id = account("pro@x.test")
fake_db.set_user_plan(pro_id, "pro")
low_tok, low_id = account("low@x.test")


def run_sync(tok):
    body = client.post("/backtest", headers=H(tok), json=BODY).json()
    return json.dumps(body["results"], sort_keys=True)


assert run_sync(low_tok) == run_sync(pro_tok), "a Free backtest is the same result as a Pro one"
res = json.loads(run_sync(low_tok))["EQ"]
assert res["monte_carlo"] and res["confidence_intervals"], "robustness is there for Free accounts"
ungated = {p for (_, p) in EXPECTED_GATES}
for path in ("/explain", "/results/share", "/r/{share_id}", "/r/{share_id}.json", "/backtest/async/{job_id}",
             "/screen", "/me/strategies/{strategy_id}"):
    assert path not in ungated, f"{path} must never be gated"
print("✅ accuracy is never gated: same result on Free and Pro; explain, share pages and robustness have no gate")

# ═══════════════════════════════════════════════════════════════════════════
# G. Monitoring emails (the digest)
# ═══════════════════════════════════════════════════════════════════════════
from src import digest as dg  # noqa: E402

ddb = FakeUserDB()
ddb.grandfather_issued = en.GRANDFATHER_LIMIT
f_id = ddb.create_user("f@x.test", "h", "free", email_verified=True)
p_id = ddb.create_user("p@x.test", "h", "free", email_verified=True)
ddb.set_user_plan(p_id, "pro")
# A Pro account whose downgrade has already taken effect: the SQL prefilter
# still sees plan='pro' (it is a superset), so plan_for must be what skips it.
d_id = ddb.create_user("d@x.test", "h", "free", email_verified=True)
ddb.set_plan_state(d_id, plan="pro", pending="free", change_at=en.utcnow() - timedelta(days=1),
                    reason="downgrade")
for uid in (f_id, p_id, d_id):
    ddb.save_strategy(uid, "q", {"buy_rules": ["rsi < 30"], "sell_rules": ["rsi > 70"]}, ["X"], None)
sent = []
prov = ManualBillingProvider(db_factory=lambda: ddb)


def entitled(uid):
    return en.plan_for(ddb.get_user_by_id(uid), provider=prov).allows(en.MONITORING_EMAILS)


report = dg.run_digest(
    ddb, send=lambda to, *a, **k: sent.append(to) or True,
    evaluate=lambda s: dg.StrategyUpdate(strategy_id=s["id"], query=s["query"], saved_on=None, status="too_early"),
    now=NOW, max_sends=10, app_url="https://app", entitled=entitled,
    links=lambda uid: {"unsubscribe": "u", "unsubscribe_one_click": "u", "preferences": "p"},
)
assert sent == ["p@x.test"], sent
assert [c["id"] for c in ddb.digest_candidates("2026-W50", 10)] == [p_id, d_id], \
    "free accounts are not even candidates; a lapsed Pro is (superset) — and skipped above"
print("✅ monitoring emails: only entitled accounts get the weekly digest")

# ═══════════════════════════════════════════════════════════════════════════
# H. No prices anywhere in the API
# ═══════════════════════════════════════════════════════════════════════════
PRICE = re.compile(r"[$€£]\s?\d|\b\d+(?:\.\d+)?\s?(?:USD|EUR|GBP)\b|/\s?mo(?:nth)?\b|per month")
for path, headers in (("/meta", {}), ("/me/entitlements", H(free_tok)), ("/admin/users", H(admin_tok))):
    text = client.get(path, headers=headers).text
    assert not PRICE.search(text), (path, PRICE.search(text))
print("✅ no price string in /meta, /me/entitlements or the admin API")

set_billing_provider(None)
mock.patch.stopall()
print("\nALL ENTITLEMENT CHECKS PASSED")
