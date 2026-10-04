"""Day 6 — no signup wall, and shareable proof. Hermetic: no DB, no network.

A stranger runs real backtests without an account, then shares a result as a
public link. Both are the first things a bot finds, so every property here is
asserted in the direction that matters for abuse as well as for use:

  1. Token kinds never cross: a trial token is not an account session, and an
     account token is not a trial session.
  2. Trial backtests run the same engine path, owned by the trial session;
     nobody else can poll them; the allowance (3) ends in a friendly 403 that
     an engine error does not spend, and that survives a server restart.
  3. Failed runs never hand the client raw exception text (golden rule #12).
  4. Sharing is opt-in, idempotent, owner-only, and publishes a server-built
     whitelist — no email, user id, job id, trial id or trade log.
  5. Share ids cannot be walked; a revoked link is dead for everyone; a
     non-owner cannot revoke.
  6. Signing up claims the trial's jobs and links; deleting the account kills
     the links.
  7. Rate limits: changing trial tokens does not reset the per-IP cap, and
     polling or reading a share never spends a backtest.

The real SQL behind the fake (``shared_results`` + ``backtest_jobs``) is
exercised against Postgres separately; ``FakeUserDB`` mirrors its contract.

Run: python -m tests.test_trial_and_sharing
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
os.environ["RATE_LIMIT_BYPASS_IPS"] = ""
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("JWT_SECRET", "trial-and-sharing-dummy-secret")
os.environ["SENDGRID_API_KEY"] = ""
os.environ["SMTP_HOST"] = ""
os.environ["ANALYTICS_ENABLED"] = "false"  # the funnel has its own suite

from unittest import mock  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

import src.api as api  # noqa: E402
from src import auth  # noqa: E402
from src.config import settings  # noqa: E402
from src.redis_ratelimit import reset_rate_limit_store  # noqa: E402

client = TestClient(api.app)
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
A = lambda t: {"X-Anon-Token": t}  # noqa: E731


# ── A fake engine: deterministic results, no yfinance, no LLM ────────────────
seen_payloads: list = []


def _fake_pipeline(payload, user, progress_cb=None):
    seen_payloads.append(payload)
    if "explode" in payload.query:
        raise RuntimeError("psycopg2 failed: postgres://admin:hunter2@db.internal/main")
    if "baddates" in payload.query:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")
    return {"buy": [], "sell": []}, {}, None, None


def _fake_build(payload, strategy_rules, bt_results, screen_message, portfolio):
    curve = [{"date": "2016-01-04", "value": 100000.0},
             {"date": "2021-01-04", "value": 130000.0},
             {"date": "2026-01-02", "value": 150000.0}]
    results = {} if "nomatch" in payload.query else {
        "AAPL": {
            "symbol": "AAPL", "initial_capital": 100000.0, "final_value": 150000.0,
            "total_return_pct": 50.0, "sharpe_ratio": 0.81, "max_drawdown_pct": -21.5,
            "win_rate_pct": 60.0, "total_trades": 10, "equity_curve": curve,
            "benchmark_total_return_pct": 120.0,
            "trades": [{"symbol": "AAPL", "buy_date": "2016-02-01", "buy_price": 1.0,
                        "sell_date": "2016-03-01", "sell_price": 2.0, "shares": 1,
                        "pnl": 1.0, "pnl_pct": 100.0, "side": "long"}],
        },
    }
    return api.BacktestResponse(
        query=payload.query, strategy=strategy_rules, results=results,
        total_symbols_tested=len(results),
    )


mock.patch.object(api, "_run_backtest_pipeline", side_effect=_fake_pipeline).start()
mock.patch.object(api, "_build_backtest_response", side_effect=_fake_build).start()


def new_trial() -> str:
    r = client.post("/try/session")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["allowance"] == api.ANON_BACKTEST_ALLOWANCE
    return body["anon_token"]


def wait(job_id: str, headers: dict, path: str = "/try/backtest/") -> dict:
    for _ in range(200):
        r = client.get(path + job_id, headers=headers)
        assert r.status_code == 200, r.text
        if r.json()["status"] in ("done", "error"):
            return r.json()
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} never finished")


def trial_run(token: str, query: str = "oversold large-cap tech", **extra) -> dict:
    r = client.post("/try/backtest", headers=A(token), json={"query": query, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def register(email: str, password: str = "correct-horse-1") -> str:
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ═══════════════════════════════════════════════════════════════════════════
# 0. Wiring pins
# ═══════════════════════════════════════════════════════════════════════════
assert api._scope_for_path("/try/session") == "auth"
assert api._scope_for_path("/try/backtest") == "anon_backtest"
assert api._scope_for_path("/try/backtest/bt_abc") == "backtest_poll"
assert api._scope_for_path("/try/claim") == "auth"
assert api._scope_for_path("/results/share") == "share"
assert api._scope_for_path("/results/share/abc") == "share"
assert api._scope_for_path("/r/abc") == "read"
assert api.SCOPE_GLOBAL["anon_backtest"] == "backtest_llm", "trials draw on the shared LLM budget"
assert api.RATE_LIMITS["anon_backtest"]["daily"] > api.ANON_BACKTEST_ALLOWANCE, (
    "the per-IP daily cap must sit above the allowance, or a visitor hits a bare "
    "429 before the friendly 'create an account' 403"
)
assert api.RATE_LIMITS["anon_backtest"]["daily"] < api.RATE_LIMITS["backtest"]["daily"], (
    "anonymous traffic is held tighter than signed-in traffic"
)
print("✅ scopes: /try/backtest → anon_backtest (→ backtest_llm), polls free, shares own scope")

# Migration and the self-healing schema agree on the table and its cascade.
from pathlib import Path  # noqa: E402

from src.database import SHARED_RESULTS_SCHEMA  # noqa: E402

mig = (Path(__file__).resolve().parent.parent / "alembic" / "versions"
       / "a5b6c7d8e9f0_create_shared_results.py").read_text(encoding="utf-8")
assert 'down_revision: Union[str, Sequence[str], None] = "f4a5b6c7d8e9"' in mig
for needle in ("CREATE TABLE IF NOT EXISTS shared_results",
               "owner_user_id BIGINT REFERENCES users(id) ON DELETE CASCADE"):
    assert needle in mig and needle in SHARED_RESULTS_SCHEMA, needle
assert "DROP TABLE IF EXISTS shared_results" in mig
print("✅ shared_results: migration and init_schema agree (CASCADE on account delete)")


# ═══════════════════════════════════════════════════════════════════════════
# 1. Token kinds never cross
# ═══════════════════════════════════════════════════════════════════════════
minted = auth.create_anon_token()
assert auth.verify_anon_token(minted["anon_token"]) == minted["anon_id"]
assert len(minted["anon_id"]) == 32 and "@" not in minted["anon_id"]
assert auth.verify_anon_token(None) is None and auth.verify_anon_token("") is None

acct = auth.create_access_token(1, "someone@x.test")
assert auth.verify_anon_token(acct) is None, "an account token must not pass as a trial token"

head, body, sig = minted["anon_token"].split(".")
assert auth.verify_anon_token(f"{head}.{body}.{sig[:-2]}AA") is None, "bad signature accepted"
expired = auth._sign_payload({"typ": "anon", "aid": "a" * 32, "iat": 1, "exp": 2})
assert auth.verify_anon_token(expired) is None, "expired trial token accepted"
no_aid = auth._sign_payload({"typ": "anon", "iat": int(time.time()),
                             "exp": int(time.time()) + 60})
assert auth.verify_anon_token(no_aid) is None

tok = new_trial()
assert client.get("/auth/me", headers=H(tok)).status_code == 401, (
    "a trial token used as a Bearer token must not resolve to any account"
)
assert client.post("/backtest/async", headers=H(tok), json={"query": "x"}).status_code == 401

# The typ claim is what separates the kinds, not the absence of a field: a
# trial token that also carried a real ``sub`` must still not log anyone in,
# and an account-shaped token that carried an ``aid`` must not be a trial.
victim_tok = register("victim@x.test")
victim_id = fake_db.get_user_by_email("victim@x.test")["id"]
now = int(time.time())
forged_anon = auth._sign_payload({"typ": "anon", "aid": "b" * 32, "sub": str(victim_id),
                                  "ver": 0, "iat": now, "exp": now + 600})
assert client.get("/auth/me", headers=H(forged_anon)).status_code == 401, (
    "a token typed 'anon' must never resolve to an account, even with a sub"
)
forged_acct = auth._sign_payload({"sub": str(victim_id), "aid": "c" * 32, "ver": 0,
                                  "iat": now, "exp": now + 600})
assert auth.verify_anon_token(forged_acct) is None, (
    "a token not typed 'anon' must never be a trial session, even with an aid"
)
print("✅ trial ↔ account tokens rejected in both directions; tampered/expired trial tokens rejected")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Trial backtests: same job flow, owner-only, clamped, allowance
# ═══════════════════════════════════════════════════════════════════════════
assert client.post("/try/backtest", json={"query": "x"}).status_code == 401
assert client.post("/try/backtest", headers=A(acct), json={"query": "x"}).status_code == 401

t1 = new_trial()
started = trial_run(t1, top_n=20)
assert started["status"] == "queued" and started["anon_remaining"] == 2, started
assert started["total_symbols"] == api.ANON_MAX_SYMBOLS, "top_n must be clamped for trials"
done = wait(started["job_id"], A(t1))
assert done["status"] == "done" and "AAPL" in done["result"]["results"], done
assert isinstance(done["result"], dict), "the stored result must be a JSON dict"
json.dumps(done["result"])  # serializable → the DB upsert can store it
print("✅ trial backtest: queued → done through the shared async path; top_n clamped to 5")

with api._BACKTEST_JOBS_LOCK:
    stored = api._BACKTEST_JOBS[started["job_id"]]
assert stored["user_email"] == "anon:" + auth.verify_anon_token(t1)
json.dumps(stored["result"])  # the pre-Day-6 code stored a Pydantic model here

seen_payloads.clear()
trial_run(new_trial(), symbols=["A", "B", "C", "D", "E", "F", "G", "H"])
assert len(seen_payloads[-1].symbols) == api.ANON_MAX_SYMBOLS, seen_payloads[-1].symbols
print("✅ an 8-symbol compare request runs on the first 5 only")

other = new_trial()
assert client.get(f"/try/backtest/{started['job_id']}", headers=A(other)).status_code == 404
assert client.get(f"/try/backtest/{started['job_id']}").status_code == 401
eve = register("eve@x.test")
assert client.get(f"/backtest/async/{started['job_id']}", headers=H(eve)).status_code == 404
print("✅ another trial session, no token, or an account → cannot poll someone's trial job")

# Errors don't spend the allowance — and never leak raw exception text.
t2 = new_trial()
err = wait(trial_run(t2, query="explode please")["job_id"], A(t2))
assert err["status"] == "error"
assert err["error"] == api._GENERIC_BACKTEST_ERROR, err["error"]
assert "postgres" not in json.dumps(err) and "hunter2" not in json.dumps(err)
bad = wait(trial_run(t2, query="baddates please")["job_id"], A(t2))
assert bad["error"] == "start_date must be before end_date", "a 4xx detail is written for users"
print("✅ failed runs: engine internals → generic sentence; a 4xx detail passes through")

for i in range(api.ANON_BACKTEST_ALLOWANCE):
    r = trial_run(t2)
    assert r["anon_remaining"] == api.ANON_BACKTEST_ALLOWANCE - i - 1, r
    wait(r["job_id"], A(t2))
r = client.post("/try/backtest", headers=A(t2), json={"query": "one more"})
assert r.status_code == 403, r.text
assert "free account" in r.json()["detail"] and "come with you" in r.json()["detail"]
print("✅ allowance: 2 errored runs cost nothing; 3 good runs, then a friendly 403")

# A restart empties memory — the allowance must come back from the DB count.
t3 = new_trial()
aid3 = auth.verify_anon_token(t3)
for n in range(api.ANON_BACKTEST_ALLOWANCE):
    fake_db.jobs[f"old-{n}"] = {"user_email": "anon:" + aid3, "status": "done"}
with mock.patch.object(settings, "database_url", "postgresql://fake/for-has-database-url"):
    r = client.post("/try/backtest", headers=A(t3), json={"query": "after restart"})
assert r.status_code == 403, f"allowance must survive a restart: {r.status_code} {r.text}"
print("✅ allowance survives a restart (counted from backtest_jobs, not only memory)")

assert api._public_backtest_error(HTTPException(422, "Use YYYY-MM-DD")) == "Use YYYY-MM-DD"
assert api._public_backtest_error(HTTPException(503, "pool exhausted")) == api._GENERIC_BACKTEST_ERROR
assert api._public_backtest_error(ValueError("secret")) == api._GENERIC_BACKTEST_ERROR


# ═══════════════════════════════════════════════════════════════════════════
# 3. Sharing: opt-in, owner-only, idempotent, whitelisted
# ═══════════════════════════════════════════════════════════════════════════
s_tok = new_trial()
s_aid = auth.verify_anon_token(s_tok)
xss = "<script>alert(1)</script> oversold \"quoted\" tech"
s_job = trial_run(s_tok, query=xss)["job_id"]
wait(s_job, A(s_tok))

assert not fake_db.shares, "nothing is public until the owner presses share"
assert client.post("/results/share", json={"job_id": s_job}).status_code == 401
assert client.post("/results/share", headers=A(other), json={"job_id": s_job}).status_code == 404
r = client.post("/results/share", headers={**H("garbage.token.here"), **A(s_tok)},
                json={"job_id": s_job})
assert r.status_code == 401, "a bad Bearer token must not silently fall back to the trial identity"

r = client.post("/results/share", headers=A(s_tok), json={"job_id": s_job})
assert r.status_code == 200, r.text
share_id = r.json()["share_id"]
assert api._SHARE_ID_RE.match(share_id) and len(share_id) >= 20, share_id
assert r.json()["url"].endswith(f"/r/{share_id}")
assert share_id != s_job and s_job not in share_id
again = client.post("/results/share", headers=A(s_tok), json={"job_id": s_job}).json()
assert again["share_id"] == share_id, "sharing the same job twice returns the same link"
assert len(fake_db.shares) == 1
print("✅ share: 401 without identity, 404 for a non-owner, opt-in, idempotent, random id")

with api._BACKTEST_JOBS_LOCK:
    api._BACKTEST_JOBS["bt_running01"] = {
        "job_id": "bt_running01", "status": "running", "user_email": "anon:" + s_aid,
        "result": None,
    }
assert client.post("/results/share", headers=A(s_tok),
                   json={"job_id": "bt_running01"}).status_code == 409
empty_job = trial_run(new_trial_tok := new_trial(), query="nomatch at all")["job_id"]
wait(empty_job, A(new_trial_tok))
assert client.post("/results/share", headers=A(new_trial_tok),
                   json={"job_id": empty_job}).status_code == 422
print("✅ an unfinished job → 409; a run that matched nothing → 422 (nothing to show)")

pub = client.get(f"/r/{share_id}.json")
assert pub.status_code == 200, pub.text
payload = pub.json()
assert set(payload) == {"share_id", "query", "basis", "symbols", "symbols_tested",
                        "headline", "period", "equity_curve"}, sorted(payload)
raw = json.dumps(payload)
for leak in (s_job, s_aid, "anon:", "user_email", "\"trades\"", "buy_price", "@"):
    assert leak not in raw, f"public share payload leaks {leak!r}"
assert payload["headline"]["total_return_pct"] == 50.0
assert payload["headline"]["benchmark_total_return_pct"] == 120.0
assert payload["headline"]["excess_return_pct"] == -70.0, "losing to SPY is shown, not hidden"
assert payload["period"] == {"start": "2016-01-04", "end": "2026-01-02"}
print("✅ public JSON: exactly the whitelist — no job id, trial id, email or trade log")

page = client.get(f"/r/{share_id}")
assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
html_text = page.text
assert "<script>alert(1)" not in html_text and "&lt;script&gt;alert(1)" in html_text
assert "not investment advice" in html_text, "the disclaimer sits next to the numbers"
assert '<meta name="robots" content="noindex">' in html_text
assert f'rel="canonical" href="http://testserver/r/{share_id}"' in html_text
assert "default-src 'none'" in page.headers["content-security-policy"]
assert page.headers["x-content-type-options"] == "nosniff"
assert "/try?q=" in html_text, "the CTA re-runs the same strategy"
for leak in (s_job, s_aid):
    assert leak not in html_text
print("✅ public page: server-rendered, escaped, disclaimer, noindex, script-free CSP")

with mock.patch.object(settings, "share_base_url", "https://api.stratoscan.example"):
    r = client.post("/results/share", headers=A(s_tok), json={"job_id": s_job})
    assert r.json()["url"] == f"https://api.stratoscan.example/r/{share_id}"
print("✅ SHARE_BASE_URL sets the public origin of share links")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Enumeration and revocation
# ═══════════════════════════════════════════════════════════════════════════
for probe in [secrets.token_urlsafe(16) for _ in range(25)] + [
    "short", "a" * 65, s_job, s_aid, "%2e%2e", "abc.def.ghi.jkl.mno.pqr"
]:
    assert client.get(f"/r/{probe}").status_code == 404, probe
    assert client.get(f"/r/{probe}.json").status_code == 404, probe
with mock.patch.object(fake_db, "get_share", wraps=fake_db.get_share) as spy:
    for malformed in ("short", "a" * 65, "abc.def.ghi.jkl.mno.pqr", "has space in it 123"):
        assert client.get(f"/r/{malformed}").status_code == 404
    assert spy.call_count == 0, "a malformed id must be refused before any database read"
    client.get(f"/r/{secrets.token_urlsafe(16)}")
    assert spy.call_count == 1
print("✅ 25 random ids, the job id, the trial id and malformed ids → all 404 "
      "(malformed ones never reach the DB)")

assert client.delete(f"/results/share/{share_id}", headers=A(other)).status_code == 404
assert client.delete(f"/results/share/{share_id}", headers=H(eve)).status_code == 404
assert client.delete(f"/results/share/{share_id}").status_code == 401
assert client.get(f"/r/{share_id}").status_code == 200, "a refused revoke must leave the link up"

listed = client.get("/results/shares", headers=A(s_tok)).json()["shares"]
assert [s["share_id"] for s in listed] == [share_id]
assert client.get("/results/shares", headers=A(other)).json()["shares"] == []

r = client.delete(f"/results/share/{share_id}", headers=A(s_tok))
assert r.status_code == 200, r.text
assert client.get(f"/r/{share_id}").status_code == 404
assert client.get(f"/r/{share_id}.json").status_code == 404
assert client.delete(f"/results/share/{share_id}", headers=A(s_tok)).status_code == 404
reshared = client.post("/results/share", headers=A(s_tok), json={"job_id": s_job}).json()
assert reshared["share_id"] != share_id, "re-sharing mints a new id; the old link stays dead"
assert client.get(f"/r/{share_id}").status_code == 404
print("✅ revoke: non-owners 404 (link stays up); owner revokes → dead for everyone; re-share = new id")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Claim on signup; deletion kills links
# ═══════════════════════════════════════════════════════════════════════════
live_share = reshared["share_id"]
new_acct = register("trial-then-signup@x.test")
assert client.post("/try/claim", headers=H(new_acct)).status_code == 401, "needs the trial token"
assert client.post("/try/claim", headers=A(s_tok)).status_code == 401, "needs the account token"
r = client.post("/try/claim", headers={**H(new_acct), **A(s_tok)})
assert r.status_code == 200, r.text
assert r.json()["claimed_backtests"] >= 1 and r.json()["claimed_shares"] == 1, r.json()

assert wait(s_job, H(new_acct), path="/backtest/async/")["status"] == "done", (
    "the account now owns the trial's job"
)
assert client.get(f"/try/backtest/{s_job}", headers=A(s_tok)).status_code == 404
mine = client.get("/results/shares", headers=H(new_acct)).json()["shares"]
assert [s["share_id"] for s in mine] == [live_share]
assert client.get("/results/shares", headers=A(s_tok)).json()["shares"] == []
assert client.get(f"/r/{live_share}").status_code == 200, "claiming must not break the link"
print("✅ claim: jobs and links move into the account; the trial token loses them")

r = client.post("/auth/delete-account", headers=H(new_acct),
                json={"password": "correct-horse-1"})
assert r.status_code == 200, r.text
assert client.get(f"/r/{live_share}").status_code == 404, "links die with the account"
print("✅ deleting the account kills its public links")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Rate limits with limiting ON
# ═══════════════════════════════════════════════════════════════════════════
reset_rate_limit_store()
with mock.patch.object(settings, "rate_limit_enabled", True):
    codes = [client.post("/try/backtest", headers=A(new_trial()),
                         json={"query": "burst"}).status_code for _ in range(3)]
    assert codes[2] == 429, f"a fresh trial token per request must not reset the IP cap: {codes}"
    print(f"✅ new trial token per request still hits the per-IP burst cap ({codes})")

    polls = [client.get("/try/backtest/bt_nope00000000", headers=A(other)).status_code
             for _ in range(30)]
    assert 429 not in polls, polls
    reads = [client.get(f"/r/{secrets.token_urlsafe(16)}").status_code for _ in range(30)]
    assert 429 not in reads, reads
    print("✅ 30 trial polls and 30 share-page reads in a minute → none rate-limited")
reset_rate_limit_store()

mock.patch.stopall()
print("\nALL TRIAL + SHARING CHECKS PASSED")
