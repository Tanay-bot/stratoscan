"""Day 10 — saved strategies and the weekly digest. Hermetic: fake DB, fake
mail transport, synthetic prices; no network.

The plan's review prompt asks where this job can double-send, send to an
unsubscribed user, die on one malformed strategy, overlap itself, storm a
failing provider, or hold the database awake. Each is asserted here:

  A. The light re-run: real engine on synthetic prices, only since the save
     date; SPY over the same days; too-early / no-data / malformed handled.
  B. The email is honest: a strategy that trailed SPY says so in the subject;
     stocks that no longer screen in are named; zero trades are explained.
  C. The batch: one email per user per week; a second run sends nothing;
     unsubscribe honoured mid-batch; provider down -> circuit breaker, no
     storm, retried later without duplicates, attempts capped; a crash
     between claim and send never resends; one bad user can't kill the
     batch; the run lock; the per-run cap; dry run touches nothing.
  D. The API: save from an owned finished job only; caps; owner-scoped
     delete; preferences; unsubscribe GET changes nothing, POST stops sends.
  E. Transport: List-Unsubscribe headers reach SendGrid and SMTP; CR/LF in a
     header is dropped.

Run: python -m tests.test_weekly_digest
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("JWT_SECRET", "weekly-digest-dummy-secret")
os.environ["ANALYTICS_ENABLED"] = "false"
os.environ["SENDGRID_API_KEY"] = ""
os.environ["SMTP_HOST"] = ""

from unittest import mock  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src import digest as dg  # noqa: E402
from src.database import DIGEST_MAX_ATTEMPTS, MAX_SAVED_STRATEGIES  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)
SAVED = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
RULES_DIP = {"buy_rules": ["rsi < 45"], "sell_rules": ["rsi > 60"], "initial_capital": 100000.0,
             "fee_rate": 0.001, "slippage": 0.0005}


def series(drift: float, seed: int, vol: float = 0.02) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-02", TODAY.isoformat())
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, len(idx))))
    return pd.DataFrame({"Open": np.r_[close[0], close[:-1]], "High": close * 1.01,
                         "Low": close * 0.99, "Close": close, "Volume": 5_000_000}, index=idx)


PRICES = {"UPCO": series(0.002, 1), "DOWNCO": series(-0.003, 2), "FLAT": series(0.0, 3, 0.0005),
          "SPY": series(0.0015, 4, 0.008)}


def load_prices(sym, start, end):
    df = PRICES.get(sym)
    return None if df is None else df.loc[start:end]


def saved(symbols, rules=RULES_DIP, query="oversold large caps", sid=1, at=SAVED):
    return {"id": sid, "query": query, "rules": rules, "symbols": symbols, "saved_at": at}


def ev(s, screen=None, today=TODAY):
    return dg.evaluate_strategy(s, load_prices=load_prices, screen_now=screen, today=today)


# ═══════════════════════════════════════════════════════════════════════════
# A. The light re-run
# ═══════════════════════════════════════════════════════════════════════════
u = ev(saved(["UPCO", "DOWNCO"]), screen=lambda q: ["UPCO", "NEWCO"])
assert u.status == "ok", u
assert u.priced == ["DOWNCO", "UPCO"] and u.return_pct is not None and u.trades > 0
assert u.spy_pct is not None and u.spy_pct == dg._period_return(PRICES["SPY"], SAVED.date().isoformat())
assert u.best[0] == "UPCO" and u.worst[0] == "DOWNCO", (u.best, u.worst)
assert u.still_match == ["UPCO"] and u.no_longer_match == ["DOWNCO"] and u.new_matches == ["NEWCO"]
assert u.days == (TODAY - SAVED.date()).days
print(f"✅ re-run since the save date: avg {u.return_pct:+.1f}% vs SPY {u.spy_pct:+.1f}%, {u.trades} trades")

# Only the window since saving counts: the same rules saved on a later date
# report a different (shorter) result — pre-save bars are warm-up only.
later = ev(saved(["UPCO"], at=datetime(2026, 9, 1, tzinfo=timezone.utc)))
whole = ev(saved(["UPCO"], at=datetime(2025, 9, 1, tzinfo=timezone.utc)))
assert later.status == whole.status == "ok" and later.return_pct != whole.return_pct
print("✅ pre-save history is warm-up only — the window starts at the save date")

assert ev(saved(["UPCO"], at=NOW - timedelta(days=2))).status == "too_early"
assert ev(saved(["NOPE1", "NOPE2"])).status == "no_data"
for bad in (saved(["UPCO"], rules="x"), saved("UPCO"), saved(["UPCO"], rules={"buy_rules": "x"}),
            {"id": 9, "query": None}, saved(["UPCO"], at="not a date")):
    assert ev(bad).status == "error", bad
boom = dg.evaluate_strategy(saved(["UPCO"]), load_prices=lambda *a: 1 / 0, screen_now=None, today=TODAY)
assert boom.status == "error"
print("✅ too early / no prices / malformed / a crashing loader → an honest status, never an exception")


# ═══════════════════════════════════════════════════════════════════════════
# B. The email is honest
# ═══════════════════════════════════════════════════════════════════════════
LINKS = dict(app_url="https://app.example", unsubscribe_url="https://api.example/email/unsubscribe?token=T",
             preferences_url="https://app.example/app/settings#email")
bad = dg.StrategyUpdate(strategy_id=1, query="tech <b>dips</b>", saved_on="2026-07-01", days=96,
                        return_pct=-7.25, spy_pct=4.1, trades=6, priced=["A", "B"],
                        best=("A", -1.0), worst=("B", -13.5), screen_checked=True,
                        still_match=[], no_longer_match=["A", "B"], new_matches=["C"])
mail = dg.compose_email([bad], **LINKS)
assert "-7.2%" in mail["subject"] or "-7.3%" in mail["subject"], mail["subject"]
assert "behind SPY" in mail["subject"], "bad news belongs in the subject line, not buried"
assert "trailed the market by 11.3 points" in mail["text"], mail["text"]
assert "None of the stocks you saved match this strategy any more — today it picks C." in mail["text"]
assert "Worst: B -13.5%" in mail["text"]
assert "<b>dips</b>" not in mail["html"] and "&lt;b&gt;dips&lt;/b&gt;" in mail["html"]
for needle in (LINKS["unsubscribe_url"], LINKS["preferences_url"], "not investment advice",
               "https://app.example/app/backtest?q="):
    assert needle in mail["text"], needle
print("✅ a losing strategy says so in the subject; dropped stocks named; query escaped; links present")

cash = dg.StrategyUpdate(strategy_id=2, query="q", saved_on="2026-07-01", days=96, return_pct=0.0,
                         spy_pct=4.1, trades=0, priced=["A"])
assert "sat in cash" in "\n".join(dg.describe_update(cash))
early = dg.StrategyUpdate(strategy_id=3, query="q", saved_on="2026-10-03", days=2, status="too_early")
assert "too soon" in "\n".join(dg.describe_update(early))
two = dg.compose_email([bad, dg.StrategyUpdate(strategy_id=4, query="w", saved_on="2026-07-01",
                                               days=96, return_pct=3.0, spy_pct=1.0, priced=["X"])], **LINKS)
assert two["subject"] == "Your strategies this week: 1 up, 1 down", two["subject"]
print("✅ zero trades, too-early and multi-strategy subjects read plainly")


# ═══════════════════════════════════════════════════════════════════════════
# C. The batch
# ═══════════════════════════════════════════════════════════════════════════
class Mailer:
    def __init__(self, fail=False, on_send=None):
        self.sent, self.fail, self.on_send = [], fail, on_send

    def __call__(self, to, subject, text, html_body=None, headers=None):
        if self.on_send:
            self.on_send(to)
        if self.fail:
            return False
        self.sent.append({"to": to, "subject": subject, "headers": headers or {}})
        return True


def world(n_users=5, verified=True):
    db = FakeUserDB()
    for i in range(n_users):
        uid = db.create_user(f"u{i}@x.test", "h", "free")
        db._user(uid)["email_verified"] = verified
        db.save_strategy(uid, f"query {i}", RULES_DIP, ["UPCO"], 1.0, saved_at=SAVED)
    return db


def run(db, mailer, **kw):
    evaluate = kw.pop("evaluate", lambda s: ev(s))
    return dg.run_digest(
        db, send=mailer, evaluate=evaluate, now=kw.pop("now", NOW),
        max_sends=kw.pop("max_sends", 60), app_url="https://app.example",
        links=lambda uid: {"unsubscribe": f"U{uid}", "unsubscribe_one_click": f"U{uid}",
                           "preferences": "P"},
        **kw,
    )


db = world(4)
no_strat = db.create_user("nostrat@x.test", "h", "free")
db._user(no_strat)["email_verified"] = True
unverified = db.create_user("unverified@x.test", "h", "free")
db.save_strategy(unverified, "q", RULES_DIP, ["UPCO"], 1.0, saved_at=SAVED)
optout = db.create_user("optout@x.test", "h", "free")
db._user(optout)["email_verified"] = True
db.set_weekly_digest(optout, False)
db.save_strategy(optout, "q", RULES_DIP, ["UPCO"], 1.0, saved_at=SAVED)
m = Mailer()
r = run(db, m)
assert r.sent == 4 and r.failed == 0 and not r.aborted, r
assert sorted(x["to"] for x in m.sent) == [f"u{i}@x.test" for i in range(4)]
assert m.sent[0]["headers"]["List-Unsubscribe"] == "<U1>"
assert m.sent[0]["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
print("✅ one email per due user; no-strategy, unverified and opted-out users get none; one-click headers set")

again = run(db, m)
assert again.sent == 0 and len(m.sent) == 4, "a second run in the same week must send nothing"
next_week = run(db, m, now=NOW + timedelta(days=7))
assert next_week.sent == 4
print("✅ never twice in one ISO week; the next week sends again")

# Unsubscribe honoured mid-batch (it lands after the candidate list is built).
db = world(3)
m = Mailer(on_send=lambda to: db.set_weekly_digest(2, False) if to == "u0@x.test" else None)
r = run(db, m)
assert [x["to"] for x in m.sent] == ["u0@x.test", "u2@x.test"], m.sent
assert r.skipped == 1
assert run(db, Mailer(), now=NOW + timedelta(days=7)).sent == 2, "and on every later run"
print("✅ unsubscribe takes effect immediately — even mid-batch — and on the next run")

# Provider down: circuit breaker, no storm, retried later, no duplicates, capped.
db = world(6)
down = Mailer(fail=True)
r = run(db, down)
assert r.aborted and r.failed == dg.CIRCUIT_BREAKER_FAILURES and r.sent == 0, r
assert len(db.digest_sends) == dg.CIRCUIT_BREAKER_FAILURES, "untouched users get no ledger row"
assert all(v["status"] == "failed" for v in db.digest_sends.values())
up = Mailer()
r = run(db, up)  # provider back, same week
assert r.sent == 6 and len({x["to"] for x in up.sent}) == 6, r
assert all(v["status"] == "sent" for v in db.digest_sends.values())
assert run(db, Mailer()).sent == 0
print(f"✅ provider down: stops after {dg.CIRCUIT_BREAKER_FAILURES} failures (no storm); "
      "a later run sends each user exactly once")

db = world(1)
for _ in range(DIGEST_MAX_ATTEMPTS + 2):
    run(db, Mailer(fail=True))
assert db.digest_sends[(1, dg.iso_week_key(NOW))]["attempts"] == DIGEST_MAX_ATTEMPTS
assert run(db, Mailer()).sent == 0, "attempts are capped for the week"
print(f"✅ a failing address is tried at most {DIGEST_MAX_ATTEMPTS} times a week")

# Crash between claim and send: the row stays 'sending' and is never resent.
db = world(2)


def crash(to, *a, **k):
    if to == "u0@x.test":
        raise KeyboardInterrupt  # process dies mid-send


try:
    run(db, crash)
except KeyboardInterrupt:
    pass
db.lock_held = False  # the crashed process's session (and lock) is gone
assert db.digest_sends[(1, dg.iso_week_key(NOW))]["status"] == "sending"
m = Mailer()
run(db, m)
assert [x["to"] for x in m.sent] == ["u1@x.test"], "the in-flight user must not be re-sent"
print("✅ a crash mid-send leaves 'sending' — never re-sent (a missed email beats a duplicate)")

# One bad user cannot kill the batch.
db = world(3)
m = Mailer()


def picky(s):
    if s["query"] == "query 1":
        raise RuntimeError("corrupt strategy")
    return ev(s)


r = run(db, m, evaluate=picky)
assert r.errors == 1 and r.sent == 2 and [x["to"] for x in m.sent] == ["u0@x.test", "u2@x.test"]
assert (2, dg.iso_week_key(NOW)) not in db.digest_sends, "nothing claimed for the failed build"
print("✅ one user's broken strategy is isolated; everyone else still gets theirs")

# The lock, the cap, the dry run.
db = world(3)
db.lock_held = True
r = run(db, Mailer())
assert r.locked_out and r.sent == 0 and not db.digest_sends
db.lock_held = False
r = run(db, Mailer(), max_sends=2)
assert r.sent == 2, r
db = world(2)
m = Mailer()
r = run(db, m, dry_run=True)
assert len(r.previews) == 2 and not m.sent and not db.digest_sends
assert r.previews[0]["subject"] and "U1" in r.previews[0]["text"]
r = run(db, Mailer(), only_email="U1@X.TEST")
assert r.candidates == 1
# --user finds its address even when the queue is longer than the per-run cap.
db = world(5)
r = run(db, Mailer(), max_sends=2, only_email="u4@x.test")
assert r.candidates == 1 and r.sent == 1, r
print("✅ overlapping run locked out; per-run cap; dry run sends and records nothing; --user filter")


class Loose:
    """A store whose candidate list is stale or over-long — the guards inside
    run_digest must hold on their own, not lean on the SQL."""

    def __init__(self, db, rows):
        self.db, self.rows = db, rows

    def digest_candidates(self, week, limit):
        return self.rows

    def __getattr__(self, name):
        return getattr(self.db, name)


# The claim is the last word: a user already sent this week who still shows up
# in the list (a racing run, a stale read) gets nothing.
db = world(2)
run(db, Mailer())
m = Mailer()
r = run(Loose(db, [{"id": 1, "email": "u0@x.test"}, {"id": 2, "email": "u1@x.test"}]), m)
assert not m.sent and r.skipped == 2, r
# The per-run cap holds even when the store returns more rows than asked for.
db = world(5)
rows = [{"id": u, "email": f"u{u - 1}@x.test"} for u in range(1, 6)]
r = run(Loose(db, rows), Mailer(), max_sends=2)
assert r.sent == 2, r
# The breaker counts CONSECUTIVE failures: a success in between resets it.
db = world(6)
flaky = iter([False, False, True, False, False, True])


def sometimes(to, *a, **k):
    return next(flaky)


r = run(db, sometimes)
assert not r.aborted and r.sent == 2 and r.failed == 4, r
print("✅ the claim, the cap and the breaker each hold without help from the SQL")


# ═══════════════════════════════════════════════════════════════════════════
# D. The API
# ═══════════════════════════════════════════════════════════════════════════
from fastapi.testclient import TestClient  # noqa: E402

fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

import src.api as api  # noqa: E402
from src import auth  # noqa: E402

client = TestClient(api.app)
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
tok = client.post("/auth/register", json={"email": "saver@x.test", "password": "correct-horse-1"}).json()["access_token"]
other = client.post("/auth/register", json={"email": "other@x.test", "password": "correct-horse-1"}).json()["access_token"]
uid = fake_db.get_user_by_email("saver@x.test")["id"]

RESULT = {"query": "oversold large caps", "strategy": RULES_DIP, "total_symbols_tested": 2,
          "results": {"UPCO": {"total_return_pct": 12.5, "total_trades": 4, "equity_curve": []},
                      "DOWNCO": {"total_return_pct": -3.0, "total_trades": 2, "equity_curve": []}}}
with api._BACKTEST_JOBS_LOCK:
    api._BACKTEST_JOBS["bt_done"] = {"job_id": "bt_done", "status": "done", "user_email": "saver@x.test", "result": RESULT}
    api._BACKTEST_JOBS["bt_run"] = {"job_id": "bt_run", "status": "running", "user_email": "saver@x.test", "result": None}
    api._BACKTEST_JOBS["bt_empty"] = {"job_id": "bt_empty", "status": "done", "user_email": "saver@x.test",
                                      "result": {**RESULT, "results": {}}}

assert client.post("/me/strategies", json={"job_id": "bt_done"}).status_code == 401
assert client.post("/me/strategies", headers=H(other), json={"job_id": "bt_done"}).status_code == 404
assert client.post("/me/strategies", headers=H(tok), json={"job_id": "bt_run"}).status_code == 409
assert client.post("/me/strategies", headers=H(tok), json={"job_id": "bt_empty"}).status_code == 422
r = client.post("/me/strategies", headers=H(tok), json={"job_id": "bt_done"})
assert r.status_code == 200, r.text
s = r.json()["strategy"]
assert s["query"] == "oversold large caps" and sorted(s["symbols"]) == ["DOWNCO", "UPCO"]
assert s["rules"] == {"buy_rules": ["rsi < 45"], "sell_rules": ["rsi > 60"]}
stored = fake_db.strategies[s["id"]]
assert stored["rules"] == RULES_DIP, "the rules stored are the server's own strategy echo"
listed = client.get("/me/strategies", headers=H(tok)).json()["strategies"]
assert [x["id"] for x in listed] == [s["id"]]
assert client.get("/me/strategies", headers=H(other)).json()["strategies"] == []
print("✅ save only from your own finished job; rules and symbols come from the server's stored result")

assert client.delete(f"/me/strategies/{s['id']}", headers=H(other)).status_code == 404
assert client.delete(f"/me/strategies/{s['id']}", headers=H(tok)).status_code == 200
assert client.delete(f"/me/strategies/{s['id']}", headers=H(tok)).status_code == 404
for _ in range(MAX_SAVED_STRATEGIES):
    assert client.post("/me/strategies", headers=H(tok), json={"job_id": "bt_done"}).status_code == 200
assert client.post("/me/strategies", headers=H(tok), json={"job_id": "bt_done"}).status_code == 409
print(f"✅ owner-only delete; capped at {MAX_SAVED_STRATEGIES} strategies")

assert client.get("/me/email-preferences", headers=H(tok)).json()["weekly_digest"] is True
assert client.put("/me/email-preferences", headers=H(tok), json={"weekly_digest": False}).status_code == 200
assert fake_db.get_weekly_digest(uid) is False
client.put("/me/email-preferences", headers=H(tok), json={"weekly_digest": True})
print("✅ email preferences read and write")

ut = auth.create_unsubscribe_token(uid)
assert client.get("/auth/me", headers=H(ut)).status_code == 401, "an unsubscribe token is not a session"
assert auth.verify_unsubscribe_token(tok) is None, "a session token is not an unsubscribe token"
# The typ check stands on its own: a typed token that otherwise looks exactly
# like a session (right sub, right ver) is still refused.
import time as _t  # noqa: E402
lookalike = auth._sign_payload({"typ": "unsub", "sub": str(uid), "ver": 0,
                                "iat": int(_t.time()), auth._TOKEN_TTL: int(_t.time()) + 600})
assert client.get("/auth/me", headers=H(lookalike)).status_code == 401
assert client.get(f"/email/unsubscribe?token={tok}").status_code == 400, "GET refuses a wrong kind too"
page = client.get(f"/email/unsubscribe?token={ut}")
assert page.status_code == 200 and "<form" in page.text and 'method="post"' in page.text
assert fake_db.get_weekly_digest(uid) is True, "a GET (mail scanners prefetch links) must change nothing"
assert "form-action 'self'" in page.headers["content-security-policy"]
done = client.post(f"/email/unsubscribe?token={ut}", data={"List-Unsubscribe": "One-Click"})
assert done.status_code == 200 and "unsubscribed" in done.text
assert fake_db.get_weekly_digest(uid) is False
for badtok in ("", "garbage", tok, auth.create_anon_token()["anon_token"]):
    assert client.post(f"/email/unsubscribe?token={badtok}").status_code == 400, badtok
fake_db._user(uid)["email_verified"] = True
r = run(fake_db, Mailer())
assert "saver@x.test" not in [p for p in []] and r.candidates == 0, "unsubscribed → not even a candidate"
print("✅ unsubscribe: GET only confirms, POST stops sends at once; wrong token kinds refused")

# Account deletion takes strategies and the ledger with it.
fake_db.set_weekly_digest(uid, True)
run(fake_db, Mailer())
assert fake_db.strategies and fake_db.digest_sends
assert client.post("/auth/delete-account", headers=H(tok), json={"password": "correct-horse-1"}).status_code == 200
assert not fake_db.strategies and not [k for k in fake_db.digest_sends if k[0] == uid]
print("✅ deleting the account deletes its saved strategies and digest history")


# ═══════════════════════════════════════════════════════════════════════════
# E. Transport headers
# ═══════════════════════════════════════════════════════════════════════════
from src import emails  # noqa: E402
from src.config import settings  # noqa: E402

captured = {}


class _Resp:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    captured["payload"] = json.loads(req.data)
    return _Resp()


with mock.patch.object(settings, "sendgrid_api_key", "SG.x"), \
        mock.patch.object(settings, "smtp_host", ""), \
        mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
    assert emails.send_email("a@x.test", "s", "t", headers={
        "List-Unsubscribe": "<https://api/u>", "X-Evil": "a\r\nBcc: victim@x.test"})
assert captured["payload"]["headers"] == {"List-Unsubscribe": "<https://api/u>"}, captured["payload"]

sent_msgs = []


@contextmanager
def fake_smtp(*a, **k):
    class S:
        def send_message(self, msg):
            sent_msgs.append(msg)

        def starttls(self):
            pass

        def login(self, *a):
            pass

    yield S()


with mock.patch.object(settings, "smtp_host", "localhost"), mock.patch("smtplib.SMTP", fake_smtp):
    assert emails.send_email("a@x.test", "s", "t", headers={"List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
assert sent_msgs[0]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
print("✅ List-Unsubscribe headers reach SendGrid and SMTP; a header with CR/LF is dropped")


# ═══════════════════════════════════════════════════════════════════════════
# F. Wiring pins
# ═══════════════════════════════════════════════════════════════════════════
from src.database import DIGEST_SENDS_SCHEMA, SAVED_STRATEGIES_SCHEMA  # noqa: E402
from src.job_monitor import JOB_DEFINITIONS  # noqa: E402

mig = (ROOT / "alembic" / "versions" / "b6c7d8e9f0a1_saved_strategies_and_digest.py").read_text()
assert 'down_revision: Union[str, Sequence[str], None] = "a5b6c7d8e9f0"' in mig
for needle in ("user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE",
               "PRIMARY KEY (user_id, week_key)"):
    assert needle in mig and (needle in SAVED_STRATEGIES_SCHEMA or needle in DIGEST_SENDS_SCHEMA), needle
assert "weekly_digest BOOLEAN NOT NULL DEFAULT TRUE" in mig
assert "weekly_digest" in JOB_DEFINITIONS
sched = (ROOT / "src" / "scheduler.py").read_text()
assert "schedule.every().day.at(settings.digest_time).do(self.job_send_digest)" in sched
print("✅ migration matches init_schema; job monitored; scheduled daily")

# No SHARE_BASE_URL → no email: the unsubscribe link would point at the
# frontend and 404. The run refuses before touching the DB, and says so.
from src.config import settings as _settings, validate_config  # noqa: E402

with mock.patch.object(_settings, "share_base_url", ""), \
        mock.patch("src.database.PostgresDB", side_effect=AssertionError("must not connect")):
    r = dg.run_weekly_digest()
    assert r.misconfigured and r.sent == 0 and "SHARE_BASE_URL" in r.summary(), r
    assert any("SHARE_BASE_URL" in w for w in validate_config())
with mock.patch.object(_settings, "share_base_url", "https://api.example"):
    assert not any("SHARE_BASE_URL is unset" in w for w in validate_config())
print("✅ without SHARE_BASE_URL the digest sends nothing (no dead unsubscribe links) and config warns")

mock.patch.stopall()
print("\nALL WEEKLY-DIGEST CHECKS PASSED")
