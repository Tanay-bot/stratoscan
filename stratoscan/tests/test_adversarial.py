"""Day 13 — someone actively trying to break it. Hermetic: fake DB, faked
model, in-memory rate-limit store; no network.

  A. Authorisation: EVERY authenticated route — found from the app itself —
     called with no token, a tampered payload, a forged signature, an
     expired token, a token for a deleted account, and purpose tokens
     (trial / unsubscribe) used as sessions. Admin routes and the dashboard's
     data endpoints called by a normal user.
  B. IDOR: another user's jobs, saved strategies, share links, explanations
     and trial jobs.
  C. Privilege escalation: extra fields in register, an is_admin claim in a
     token, self-demotion.
  D. Injection: the filter builder attacked directly and THROUGH THE
     INTERPRETER (an LLM answer naming hostile columns and values; free text
     on the deterministic path); sort keys.
  E. XSS: markup in a query is escaped everywhere it is rendered — the public
     share page above all; the unsubscribe page never reflects its token.
  F. Rate-limit bypass: forged forwarding headers, token cycling, preflight
     abuse, the trusted client-IP header, and the rolling-window edge in BOTH
     stores (Redis emulated) giving identical answers.

Run: python -m tests.test_adversarial
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("JWT_SECRET", "adversarial-dummy-secret")
os.environ["ANALYTICS_ENABLED"] = "false"

from unittest import mock  # noqa: E402

from src.database import STOCK_MASTER_COLUMNS  # noqa: E402
from src.database import PostgresDB as RealPostgresDB  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import src.api as api  # noqa: E402
from src import auth  # noqa: E402

client = TestClient(api.app)
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
checks = 0


def ok(cond, msg):
    global checks
    assert cond, msg
    checks += 1


def account(email):
    r = client.post("/auth/register", json={"email": email, "password": "correct-horse-1"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"], fake_db.get_user_by_email(email)["id"]


alice_tok, alice_id = account("alice@x.test")
bob_tok, bob_id = account("bob@x.test")
admin_tok, admin_id = account("root@x.test")
fake_db.set_user_admin(admin_id, True)


def b64(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def tampered(tok):
    """Same signature, payload rewritten to another account and admin."""
    h, p, s = tok.split(".")
    claims = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    claims.update({"sub": str(admin_id), "is_admin": True})
    return f"{h}.{b64(claims)}.{s}"


def forged_alg_none(tok):
    h, p, _ = tok.split(".")
    return f"{b64({'alg': 'none', 'typ': 'JWT'})}.{p}."


expired = auth._sign_payload({"sub": str(alice_id), "ver": 0, "iat": 1, auth._TOKEN_TTL: int(time.time()) - 5})
ghost_tok, ghost_id = account("ghost@x.test")
fake_db.delete_user(ghost_id, "ghost@x.test")
BAD_TOKENS = {
    "tampered payload": tampered(alice_tok),
    "alg=none": forged_alg_none(alice_tok),
    "garbage": "not.a.token",
    "expired": expired,
    "deleted account": ghost_tok,
    "trial token as session": auth.create_anon_token()["anon_token"],
    "unsubscribe token as session": auth.create_unsubscribe_token(alice_id),
}

# ═══════════════════════════════════════════════════════════════════════════
# A. Authorisation — every authenticated route, from the app
# ═══════════════════════════════════════════════════════════════════════════


def classify(route):
    calls = set()
    stack = list(route.dependant.dependencies)
    while stack:
        d = stack.pop()
        calls.add(d.call)
        stack.extend(d.dependencies)
    if any(c is auth.require_admin for c in calls):
        return "admin"
    if any(c is auth.get_current_user or getattr(c, "entitlement_feature", None) for c in calls):
        return "bearer"
    return None


def url(path):
    return re.sub(r"\{[^}]+\}", "1", path)


guarded = []
for route in api.app.routes:
    if isinstance(route, APIRoute):
        kind = classify(route)
        if kind:
            for method in sorted(route.methods - {"HEAD"}):
                guarded.append((method, route.path, kind))
ok(len(guarded) >= 25, f"found {len(guarded)} guarded routes")

for method, path, kind in guarded:
    r = client.request(method, url(path))
    ok(r.status_code == 401, f"{method} {path} with no token -> {r.status_code}")
    for name, bad in BAD_TOKENS.items():
        r = client.request(method, url(path), headers=H(bad))
        ok(r.status_code == 401, f"{method} {path} with {name} -> {r.status_code}")
        ok("Traceback" not in r.text and "signature" not in r.text.lower(), f"{path} leaks detail")
    if kind == "admin":
        r = client.request(method, url(path), headers=H(alice_tok))
        ok(r.status_code == 403, f"{method} {path} by a normal user -> {r.status_code}")
print(f"✅ authorisation: {len(guarded)} guarded routes × (no token + {len(BAD_TOKENS)} bad tokens) → 401; "
      f"{sum(1 for g in guarded if g[2] == 'admin')} admin routes → 403 for a normal user")

# Routes that take EITHER a bearer or a trial token (resolved in the handler).
EITHER = [("POST", "/results/share", {"job_id": "xxx"}), ("GET", "/results/shares", None),
          ("DELETE", "/results/share/" + "a" * 22, None), ("POST", "/explain", {"job_id": "xxx"})]
for method, path, body in EITHER:
    ok(client.request(method, path, json=body).status_code == 401, f"{path} anonymous")
    r = client.request(method, path, json=body, headers=H(tampered(alice_tok)))
    ok(r.status_code == 401, f"{path}: a bad bearer is a 401, never a silent fallback to trial")
# The HTML dashboard is public (so the login form renders) but carries no data.
page = client.get("/admin")
ok(page.status_code == 200 and "alice@x.test" not in page.text, "dashboard shell carries no user data")
print("✅ bearer-or-trial routes refuse a forged bearer; the dashboard shell is data-free")

# ═══════════════════════════════════════════════════════════════════════════
# B. IDOR
# ═══════════════════════════════════════════════════════════════════════════
RESULT = {"query": "alice's secret idea", "strategy": {"buy_rules": ["rsi < 30"], "sell_rules": ["rsi > 70"]},
          "results": {"AAA": {"symbol": "AAA", "total_return_pct": 5.0, "total_trades": 3, "trades": [],
                              "equity_curve": [{"date": "2020-01-02", "value": 100000.0}]}}}
trial_a, trial_b = auth.create_anon_token(), auth.create_anon_token()
with api._BACKTEST_JOBS_LOCK:
    api._BACKTEST_JOBS["job_alice"] = {"job_id": "job_alice", "status": "done", "user_email": "alice@x.test",
                                       "result": RESULT}
    api._BACKTEST_JOBS["job_trial"] = {"job_id": "job_trial", "status": "done",
                                       "user_email": api._anon_owner(trial_a["anon_id"]), "result": RESULT}
share = client.post("/results/share", headers=H(alice_tok), json={"job_id": "job_alice"}).json()
strat = client.post("/me/strategies", headers=H(alice_tok), json={"job_id": "job_alice"}).json()["strategy"]

for method, path, body, why in [
    ("GET", "/backtest/async/job_alice", None, "poll another user's job"),
    ("POST", "/results/share", {"job_id": "job_alice"}, "share another user's job"),
    ("DELETE", f"/results/share/{share['share_id']}", None, "revoke another user's link"),
    ("POST", "/explain", {"job_id": "job_alice"}, "explain another user's job"),
    ("POST", "/me/strategies", {"job_id": "job_alice"}, "follow another user's job"),
    ("DELETE", f"/me/strategies/{strat['id']}", None, "unfollow another user's strategy"),
    ("POST", "/explain", {"job_id": "job_trial"}, "an account reading a trial's job"),
]:
    r = client.request(method, path, json=body, headers=H(bob_tok))
    ok(r.status_code == 404, f"IDOR ({why}) -> {r.status_code} {r.text[:80]}")
    ok("secret idea" not in r.text, f"IDOR ({why}) leaked content")
ok(client.get("/me/strategies", headers=H(bob_tok)).json()["strategies"] == [], "bob sees none of alice's strategies")
ok(client.get("/results/shares", headers=H(bob_tok)).json()["shares"] == [], "bob sees none of alice's links")
ok(client.get("/try/backtest/job_trial", headers={"X-Anon-Token": trial_b["anon_token"]}).status_code == 404,
   "another trial session cannot poll this one's job")
ok(client.get("/try/backtest/job_alice", headers={"X-Anon-Token": trial_a["anon_token"]}).status_code == 404,
   "a trial cannot poll an account's job")
ok(client.get("/r/job_alice").status_code == 404, "a job id is not a share id: unshared results stay private")
ok(client.get(f"/r/{share['share_id']}.json").status_code == 200, "the shared one is public, by choice")
print("✅ IDOR: jobs, strategies, share links, explanations and trial jobs are owner-only (404, nothing leaked)")

# ═══════════════════════════════════════════════════════════════════════════
# C. Privilege escalation
# ═══════════════════════════════════════════════════════════════════════════
r = client.post("/auth/register", json={"email": "sneaky@x.test", "password": "correct-horse-1",
                                        "is_admin": True, "plan": "pro", "email_verified": True})
ok(r.status_code == 200 and r.json()["user"]["is_admin"] is False, "register ignores is_admin in the body")
sneaky = fake_db.get_user_by_email("sneaky@x.test")
ok(not sneaky["is_admin"] and sneaky["plan"] == "free", "register ignores plan in the body")
forged_admin = auth._sign_payload({"sub": str(alice_id), "ver": 0, "is_admin": True, "iat": int(time.time()),
                                   auth._TOKEN_TTL: int(time.time()) + 600})
ok(client.get("/admin/users", headers=H(forged_admin)).status_code == 403,
   "an is_admin claim in a validly-signed token grants nothing (the row decides)")
ok(client.patch(f"/admin/users/{alice_id}", headers=H(alice_tok), json={"plan": "pro"}).status_code == 403,
   "a user cannot set their own plan")
ok(client.patch(f"/admin/users/{admin_id}", headers=H(admin_tok), json={"is_admin": False}).status_code == 400,
   "an admin cannot demote themselves")
print("✅ privilege escalation: body fields and token claims grant nothing; plans only change by an admin")

# ═══════════════════════════════════════════════════════════════════════════
# D. Injection — the filter builder, directly and through the interpreter
# ═══════════════════════════════════════════════════════════════════════════
HOSTILE = ["pe_ratio; DROP TABLE users--", "1=1", "sector) OR (1=1", "market_cap/**/OR/**/1=1",
           "symbol' --", "updated_at; SELECT pg_sleep(10)"]


class _Cursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def fetchone(self):
        return (0,)

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, log):
        self.log = log

    def cursor(self, *a, **k):
        return _Cursor(self.log)

    def commit(self):
        pass


class CapturingPG(RealPostgresDB):
    def __init__(self):
        self.log = []

    @contextmanager
    def _get_conn(self):
        yield _Conn(self.log)


pg = CapturingPG()
for col in HOSTILE:
    try:
        pg._build_filter_where({col: ("lt", 5)})
        ok(False, f"a hostile LHS column was accepted: {col}")
    except ValueError:
        ok(True, "")
    try:
        pg._build_filter_where({"current_price": ("gt_col", col)})
        ok(False, f"a hostile RHS column was accepted: {col}")
    except ValueError:
        ok(True, "")
where, params = pg._build_filter_where({"sector": "x' OR '1'='1", "pe_ratio": ("lt; DROP", "5; DROP TABLE users")})
ok("DROP" not in where and "'" not in where and where.count("%s") == 2, f"values are parameters: {where}")
ok("x' OR '1'='1" in params, "hostile values travel as parameters")
pg.query_stocks({}, sort_by="market_cap; DROP TABLE users--")
ok("DROP" not in pg.log[-1][0] and "ORDER BY market_cap DESC" in pg.log[-1][0], "a hostile sort key falls back")


def assert_closed(db_filters, where_from):
    for col, val in db_filters.items():
        ok(col in STOCK_MASTER_COLUMNS, f"{where_from}: LHS {col!r} outside the whitelist")
        vals = val if isinstance(val, list) else [val]
        for v in vals:
            if isinstance(v, tuple) and isinstance(v[0], str) and v[0].endswith("_col"):
                ok(v[1] in STOCK_MASTER_COLUMNS, f"{where_from}: RHS {v[1]!r} outside the whitelist")
    sql, _ = pg._build_filter_where(db_filters)
    for bad in ("DROP", "--", ";", "pg_sleep", "/*", "' OR"):
        ok(bad not in sql, f"{where_from}: {bad!r} reached the SQL text: {sql}")


from src import interpreter  # noqa: E402
from src.deterministic_interpreter import DeterministicInterpreter  # noqa: E402
from src.screener import StockScreener  # noqa: E402

screener = StockScreener(db=pg)
hostile_llm = json.dumps({
    "pe_ratio; DROP TABLE users--": 5, "max_pe_ratio": "1; DROP TABLE users", "min_market_cap": "0 OR 1=1",
    "sector": "x' OR '1'='1", "industry": "'; DELETE FROM users; --",
    "max_rsi": 30, "above_sma_50": "yes; DROP", "momentum": "bullish'; --",
    "sort_by": "pg_sleep(10)", "__class__": {"x": 1}, "1=1": True,
})
with mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter(hostile_llm)), \
        mock.patch.object(interpreter.settings, "use_deterministic_interpreter", False):
    interpreter._interpret_cache.clear()
    iq = interpreter.interpret_query("find stocks that will drop the users table please")
ok(iq.error is None, f"interpreter error: {iq.error}")
ok(all(c.field in STOCK_MASTER_COLUMNS for c in iq.filters), [c.field for c in iq.filters])
db_filters, _, _ = screener._build_filter_set(iq, set())
assert_closed(db_filters, "LLM path")
for text in ("tech stocks with pe below 5; DROP TABLE users; --",
             "stocks where 1=1 OR sector = 'x' -- and rsi under 30",
             "small caps above their 50 day average'); DELETE FROM users; --"):
    q, _conf = DeterministicInterpreter().parse(text)
    f, _, _ = screener._build_filter_set(q, set())
    assert_closed(f, f"deterministic path: {text[:30]}")
r = client.post("/screen", json={"query": "tech", "sort_by": "market_cap; DROP TABLE users--"})
ok(r.status_code in (200, 422, 503) and "Traceback" not in r.text, f"/screen hostile sort_by -> {r.status_code}")
print("✅ injection: the whitelist is closed at the SQL writer (LHS and RHS); hostile LLM output and free "
      "text reach SQL only as parameters; hostile sort keys fall back")

# ═══════════════════════════════════════════════════════════════════════════
# E. XSS
# ═══════════════════════════════════════════════════════════════════════════
XSS = "<script>alert(1)</script><img src=x onerror=alert(2)>\"'><svg/onload=alert(3)>"
with api._BACKTEST_JOBS_LOCK:
    api._BACKTEST_JOBS["job_xss"] = {"job_id": "job_xss", "status": "done", "user_email": "alice@x.test",
                                     "result": {**RESULT, "query": XSS}}
sid = client.post("/results/share", headers=H(alice_tok), json={"job_id": "job_xss"}).json()["share_id"]
page = client.get(f"/r/{sid}")
ok(page.status_code == 200, page.status_code)
for raw in ("<script>alert", "<img src=x", "<svg/onload", "onerror=alert(2)>"):
    ok(raw not in page.text, f"share page reflects raw markup: {raw}")
ok("&lt;script&gt;" in page.text, "the query is shown, escaped")
csp = page.headers.get("content-security-policy", "")
ok("script-src" in csp or "default-src 'none'" in csp, f"share page CSP: {csp}")
ok("'unsafe-inline'" not in csp.split("script-src")[-1].split(";")[0] if "script-src" in csp else True,
   "no inline scripts allowed on the share page")
js = client.get(f"/r/{sid}.json")
ok(js.headers["content-type"].startswith("application/json"), "the JSON form is served as JSON, not HTML")
for evil in ['"><script>alert(1)</script>', "javascript:alert(1)"]:
    u = client.get("/email/unsubscribe", params={"token": evil})
    ok(u.status_code == 400 and "<script>alert" not in u.text and "javascript:" not in u.text,
       "the unsubscribe page never reflects a bad token")
dash = (ROOT / "src" / "admin_dashboard.py").read_text()
for field in ("u.email", "u.plan"):
    raw = re.findall(r"\+\s*" + re.escape(field) + r"\s*\+", dash)
    ok(not raw, f"admin dashboard renders {field} without esc(): {raw}")
print("✅ XSS: markup in a query is escaped on the public share page (script-free CSP), never reflected by "
      "the unsubscribe page, and escaped in the admin dashboard")

# ═══════════════════════════════════════════════════════════════════════════
# F. Rate-limit bypass
# ═══════════════════════════════════════════════════════════════════════════
from src import redis_ratelimit as rr  # noqa: E402

rr.reset_rate_limit_store()
REG_CAP = api.RATE_LIMITS["register"]["burst"]
with mock.patch.object(api.settings, "rate_limit_enabled", True):
    codes = []
    for i in range(REG_CAP + 2):
        forged = {"X-Forwarded-For": f"10.0.{i}.{i}", "X-Real-IP": f"10.1.{i}.1",
                  "Forwarded": f"for=10.2.{i}.1", "Fly-Client-IP": f"10.3.{i}.1"}
        codes.append(client.post("/auth/register", headers=forged,
                                 json={"email": f"rl{i}@x.test", "password": "correct-horse-1"}).status_code)
    ok(codes[:REG_CAP] == [200] * REG_CAP and codes[REG_CAP:] == [429, 429],
       f"forged forwarding headers buy no fresh bucket: {codes}")
    rr.reset_rate_limit_store()
    for _ in range(20):
        ok(client.options("/auth/register", headers={"Origin": "http://localhost:5173",
                                                     "Access-Control-Request-Method": "POST"}).status_code != 429,
           "preflight is never limited")
    ok(client.post("/auth/register", json={"email": "after@x.test", "password": "correct-horse-1"}).status_code == 200,
       "and preflight spends nothing")
    rr.reset_rate_limit_store()
    cap = api.RATE_LIMITS["anon_backtest"]["burst"]
    got = [client.post("/try/backtest", headers={"X-Anon-Token": auth.create_anon_token()["anon_token"]},
                       json={"query": "rsi oversold"}).status_code for _ in range(cap + 1)]
    ok(got[-1] == 429, f"cycling trial tokens does not reset the per-IP trial scope: {got}")
    # The trusted client-IP header — only when configured, only a valid IP.
    rr.reset_rate_limit_store()
    with mock.patch.object(api.settings, "client_ip_header", "Fly-Client-IP"):
        per_visitor = [client.post("/auth/register", headers={"Fly-Client-IP": f"203.0.113.{i}"},
                                   json={"email": f"v{i}@x.test", "password": "correct-horse-1"}).status_code
                       for i in range(REG_CAP + 2)]
        ok(per_visitor == [200] * (REG_CAP + 2), f"behind Fly, each visitor has their own bucket: {per_visitor}")
        junk = [client.post("/auth/register", headers={"Fly-Client-IP": f"not-an-ip-{i}"},
                            json={"email": f"j{i}@x.test", "password": "correct-horse-1"}).status_code
                for i in range(REG_CAP + 1)]
        ok(junk[-1] == 429, f"a junk header value falls back to the socket peer: {junk}")
rr.reset_rate_limit_store()


class SortedSetRedis:
    """Enough of Redis to run the Upstash store's rolling-window commands."""

    def __init__(self):
        self.z = {}

    def multi_exec(self, cmds):
        out = []
        for c in cmds:
            op, key = c[0], c[1]
            s = self.z.setdefault(key, [])
            if op == "ZREMRANGEBYSCORE":
                hi = float(c[3])
                self.z[key] = [(m, sc) for m, sc in s if not (sc <= hi)]  # bare bound = inclusive
                out.append({"result": "OK"})
            elif op == "ZADD":
                s.append((c[3], float(c[2])))
                out.append({"result": 1})
            elif op == "ZCARD":
                out.append({"result": len(self.z[key])})
            elif op == "ZRANGE":
                first = sorted(self.z[key], key=lambda t: t[1])[:1]
                out.append({"result": [first[0][0], str(first[0][1])] if first else []})
            else:
                out.append({"result": "OK"})
        return out


LIMIT, WINDOW, T = 3, 100, 1_000.0
# Every attempt (allowed or not) is a hit in a sliding log, in both stores.
TIMELINES = {
    "a hit exactly WINDOW old is outside": [T, T + 1, T + 2, T + 100.0],
    "a hit a millisecond younger is inside": [T, T + 1, T + 2, T + 99.999],
    "denied attempts count too": [T, T + 1, T + 2, T + 50, T + 100.0],
    "a long quiet gap resets": [T, T + 1, T + 2, T + 3, T + 500],
}
EXPECTED_LAST = {"a hit exactly WINDOW old is outside": True, "a hit a millisecond younger is inside": False,
                 "denied attempts count too": False, "a long quiet gap resets": True}
for name, times in TIMELINES.items():
    redis_store = rr.UpstashRateLimitStore(client=SortedSetRedis())
    mem_store = rr.InMemoryRateLimitStore()
    r_ans = [redis_store.check_and_increment_rolling("k", LIMIT, WINDOW, now=t)[0] for t in times]
    m_ans = [mem_store.check_and_increment_rolling("k", LIMIT, WINDOW, now=t)[0] for t in times]
    ok(r_ans == m_ans, f"{name}: redis={r_ans} memory={m_ans}")
    ok(r_ans[-1] is EXPECTED_LAST[name], f"{name}: {r_ans}")
print("✅ rate limits: forged forwarding headers and cycled trial tokens buy nothing; preflight is free and "
      "spends nothing; Fly-Client-IP only when configured; rolling-window edge identical in Redis and memory")

mock.patch.stopall()
print(f"\nALL ADVERSARIAL CHECKS PASSED ({checks} checks)")
