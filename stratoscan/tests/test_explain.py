"""Day 11 — explain the result, don't just return it. Hermetic: a faked model,
a fake DB, synthetic prices; no network, no quota.

  A. Facts: a whitelist of numbers — no query text, email or trade log.
  B. Findings: deterministic, always present; a losing strategy is told WHY
     it lost; luck (two trades), fitting (in- vs out-of-sample) and too few
     trades are named, not softened.
  C. The compliance guard: 30+ advice/prediction phrasings fail it; the
     findings over FIFTY varied engine results pass it (release-gate test).
  D. The narrative: a model that recommends, predicts or invents a number is
     withheld; every provider down → the numbers without the narrative
     (failure drill); the same result hash gives the same reading twice,
     even after a "restart" when the model would now answer differently.
  E. The API: owner-only (account or trial), 409/422/401, its own rate-limit
     scope and global bucket, the narrative degrades without failing.

Run: python -m tests.test_explain
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("JWT_SECRET", "explain-dummy-secret")
os.environ["ANALYTICS_ENABLED"] = "false"

from unittest import mock  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src import explain as ex  # noqa: E402
from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures: real engine output in the stored (API) shape
# ═══════════════════════════════════════════════════════════════════════════
fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

import src.api as api  # noqa: E402
from src.backtester import compute_portfolio, run_backtest, walk_forward_analysis  # noqa: E402


def prices(seed: int, drift: float, vol: float = 0.018, n: int = 1500) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    idx = pd.bdate_range("2019-01-02", periods=n)
    return pd.DataFrame({"Open": np.r_[close[0], close[:-1]], "High": close * 1.01,
                         "Low": close * 0.99, "Close": close, "Volume": 5_000_000}, index=idx)


def stored(rules, frames, query="test strategy") -> dict:
    """What a finished async job holds: the API response, JSON-dumped."""
    bt = {}
    for sym, df in frames.items():
        start, end = str(df.index[0].date()), str(df.index[-1].date())
        bt[sym] = run_backtest(sym, rules, start, end, df=df)
        # BacktestEngine.backtest attaches walk-forward the same way.
        bt[sym].walk_forward = walk_forward_analysis(df, rules, symbol=sym, start_date=start, end_date=end)
    portfolio = compute_portfolio(bt) if len(bt) > 1 else None
    resp = api._build_backtest_response(
        api.BacktestRequest(query=query), rules, bt, None, portfolio)
    return json.loads(resp.model_dump_json())


def rules(buy, sell, **extra):
    return {"buy_rules": buy, "sell_rules": sell, "initial_capital": 100000.0,
            "fee_rate": 0.001, "slippage": 0.0005, **extra}


DIP = rules(["rsi < 35"], ["rsi > 65"])
TREND = rules(["price > sma_50", "sma_50 > sma_200"], ["price < sma_200"])

# ═══════════════════════════════════════════════════════════════════════════
# A. Facts
# ═══════════════════════════════════════════════════════════════════════════
single = stored(DIP, {"UPCO": prices(3, 0.0005)}, query="secret query me@x.test")
f = ex.extract_facts(single)
blob = json.dumps(f)
assert f["basis"] == "single" and f["symbols"] == ["UPCO"]
assert "secret query" not in blob and "@" not in blob, "no query text or email reaches the model"
assert "trades" not in f and "equity_curve" not in blob, "no trade log or curve — numbers only"
assert f["closed_trades"] > 0 and f["win_rate_pct"] is not None
assert f["walk_forward"] and f["monte_carlo"], "robustness numbers are part of the facts"
assert f["rules"]["buy_rules"] == ["rsi < 35"]
multi = stored(TREND, {"AAA": prices(4, 0.0006), "BBB": prices(5, -0.0002)})
fm = ex.extract_facts(multi)
assert fm["basis"] == "portfolio" and len(fm["per_symbol"]) == 2
head = ex.findings(fm)[0].text
assert head.startswith("The 2-stock portfolio"), f"a portfolio reading must name its basis: {head}"
assert fm["total_return_pct"] == multi["portfolio"]["total_return_pct"]
print("✅ facts are a whitelist of numbers (no query, email, trade log); portfolio basis when ≥2 symbols")

# ═══════════════════════════════════════════════════════════════════════════
# B. Findings
# ═══════════════════════════════════════════════════════════════════════════


def hand(trades, ret, *, bench=None, wf=None, mc=None, dd=-12.0, curve=None):
    """A hand-built stored result with known trades (decimal pnl_pct)."""
    tl = [{"symbol": "X", "buy_date": "2020-01-02", "buy_price": 10, "sell_date": "2020-02-03",
           "sell_price": 10, "shares": 100, "pnl": p * 1000, "pnl_pct": p, "side": "long",
           "exit_reason": why} for p, why in trades]
    return {"query": "q", "strategy": DIP, "results": {"X": {
        "symbol": "X", "total_return_pct": ret, "benchmark_total_return_pct": bench,
        "sharpe_ratio": -0.4 if ret < 0 else 0.8, "max_drawdown_pct": dd, "total_trades": len(tl),
        "trades": tl, "drawdown_curve": curve or [],
        "equity_curve": [{"date": "2020-01-02", "value": 100000.0}, {"date": "2021-06-30", "value": 100000.0}], "walk_forward": wf, "monte_carlo": mc}}}


def codes(result):
    return [x.code for x in ex.findings(ex.extract_facts(result))]


# A loser whose losers were bigger than its winners, and who won rarely.
loser = hand([(0.05, "signal")] * 3 + [(-0.08, "signal")] * 7, -38.5, bench=60.0)
c = codes(loser)
assert c[0] == "lost_money" and "lagged_benchmark" in c, c
assert "losers_bigger" in c and "low_win_rate" in c, c
texts = " ".join(x.text for x in ex.findings(ex.extract_facts(loser)))
assert "-8.0%" in texts and "+5.0%" in texts and "3 of 10" in texts, texts
# A loser that was stopped out.
stopped = hand([(0.03, "signal")] * 4 + [(-0.05, "stop_loss")] * 6, -14.0)
assert "stopped_out" in codes(stopped) and "lost_money" in codes(stopped)
# A loser with a couple of disasters.
blowup = hand([(0.02, "signal")] * 6 + [(-0.01, "signal")] * 2 + [(-0.30, "signal")] * 2, -45.0)
assert "few_big_losers" in codes(blowup), codes(blowup)
# Never a winner at all.
assert "no_winners" in codes(hand([(-0.02, "signal")] * 4, -8.0))
print("✅ a losing strategy is told why: bigger losers, low win rate, stop-outs, a few disasters")

# Luck: two trades out of nine made the profit.
lucky = hand([(0.60, "signal"), (0.45, "signal")] + [(-0.01, "signal")] * 7, 95.0, bench=40.0)
c = codes(lucky)
assert "concentrated" in c and "beat_benchmark" in c and "few_trades" in c, c
# Fitted: made money in-sample, lost out-of-sample.
fitted = hand([(0.04, "signal")] * 40, 50.0, wf={
    "available": True, "in_sample": {"total_return": 0.9}, "out_of_sample": {"total_return": -0.12},
    "rolling": {"positive_windows": 2, "total_windows": 4}})
assert "fitted" in codes(fitted)
# Fragile: reshuffled trades rarely end positive.
fragile = hand([(0.04, "signal")] * 35, 20.0, mc={
    "available": True, "p5": -0.2, "median": 0.01, "p95": 0.3, "prob_positive": 0.52})
assert "fragile" in codes(fragile) and "few_trades" not in codes(fragile)
# Deep, unrecovered drawdown.
curve = [{"date": "2020-01-01", "value": 0.0}, {"date": "2020-06-01", "value": -0.35},
         {"date": "2021-01-01", "value": -0.2}]
deep = hand([(0.01, "signal")] * 31, 3.0, dd=-35.0, curve=curve)
d = [x for x in ex.findings(ex.extract_facts(deep)) if x.code == "deep_drawdown"]
assert d and "had not recovered" in d[0].text
assert ex.findings(ex.extract_facts({"results": {}}))[0].code == "no_result"
# A steadily rising account sampled every ~8 days (peaks fall between samples)
# was read as "below its peak for 90 months" before Day 13 — never again.
rise = [{"date": str((pd.Timestamp("2016-01-04") + pd.Timedelta(days=12 * i)).date()),
         "value": 100000 * (1.0004 ** (8 * i)) * (0.995 if i % 2 else 1.0)} for i in range(300)]
months, recovered = ex._drawdown_duration(rise, [{"date": r["date"], "value": -0.004} for r in rise])
assert months == 0 and recovered is True, (months, recovered)
crash = rise[:100] + [{"date": r["date"], "value": rise[99]["value"] * 0.7} for r in rise[100:200]]
months, recovered = ex._drawdown_duration(crash, [])
assert 35 <= months <= 41 and recovered is False, (months, recovered)
print("✅ luck (two trades), fitting (IS vs OOS), fragility, few trades and deep drawdowns are named")

# ═══════════════════════════════════════════════════════════════════════════
# C. The compliance guard
# ═══════════════════════════════════════════════════════════════════════════
BAD = [
    "You should buy this stock.", "We recommend this strategy.", "This will outperform the market.",
    "It is a good investment.", "Expect it to keep rising.", "The strategy is likely to do well.",
    "Investors ought to hold it.", "Consider buying more on dips.", "This looks like a buy now moment.",
    "It's a strong buy.", "Returns are guaranteed.", "A risk-free way to beat SPY.",
    "The upside is large.", "A clear opportunity in tech.", "Our forecast is positive.",
    "The model predicts gains.", "Going forward the edge remains.", "Next year looks strong.",
    "Prices are going to climb.", "The outlook is bright.", "In the future it may recover.",
    "Our price target is 120.", "You can make money with this.", "Worth buying at these levels.",
    "I suggest holding.", "We advise selling.", "This is a great pick... a great investment.",
    "It is poised to rally.", "You can't lose with this.", "A sure bet.",
    "It is a safe bet.", "Must buy before earnings.", "The stock will rise.",
]
missed = [b for b in BAD if not ex.compliance_violations(b)]
assert not missed, f"the guard missed: {missed}"
OK = [
    "The strategy lost 12.0% over the period.", "Two trades produced most of the profit.",
    "The rules made money on the first 70% of the history but lost on the last 30%.",
    "At its worst the account was 35.0% below its peak.",
]
assert not [o for o in OK if ex.compliance_violations(o)], "descriptive sentences must pass"
print(f"✅ compliance guard: {len(BAD)} advice/prediction phrasings fail, descriptive text passes "
      f"({len(ex.COMPLIANCE_PATTERNS)} patterns)")

# Fifty varied real results: every finding passes both guards.
RULESETS = [
    DIP, TREND, rules(["rsi < 30"], ["rsi > 70"]), rules(["rsi < 45"], ["rsi > 55"], stop_loss=0.05),
    rules(["price > sma_50"], ["price < sma_50"], take_profit=0.15),
]
REGIMES = [(0.0007, 0.015), (-0.0006, 0.02), (0.0, 0.025), (0.0003, 0.01), (-0.0002, 0.03)]
varied = []
for i, rs in enumerate(RULESETS):
    for j, (drift, vol) in enumerate(REGIMES):
        varied.append(stored(rs, {"S": prices(100 + i * 10 + j, drift, vol, 900)}))
        if j % 2:  # every other regime as a two-symbol portfolio
            varied.append(stored(rs, {"P": prices(7 + i, drift, vol, 900),
                                      "Q": prices(70 + j, -drift, vol, 900)}))
        else:
            varied.append(stored(rs, {"T": prices(500 + i * 10 + j, drift, vol * 1.5, 900)}))
assert len(varied) == 50
seen_codes, losers = set(), 0
for res in varied:
    facts = ex.extract_facts(res)
    found = ex.findings(facts)
    assert found, "every result gets findings"
    for fd in found:
        assert not ex.compliance_violations(fd.text), (fd.code, fd.text)
        assert not ex.unsupported_numbers(fd.text, facts, found), (fd.code, fd.text)
    seen_codes |= {fd.code for fd in found}
    losers += facts["total_return_pct"] < 0
assert losers >= 5 and {"lost_money", "made_money"} <= seen_codes, (losers, seen_codes)
print(f"✅ findings over 50 varied engine results pass the compliance and number guards "
      f"({losers} losers; {len(seen_codes)} kinds of finding)")

# ═══════════════════════════════════════════════════════════════════════════
# D. The narrative
# ═══════════════════════════════════════════════════════════════════════════


class Model:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def __call__(self, messages):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def say(summary, *points):
    return json.dumps({"summary": summary, "points": list(points)})


lf = ex.extract_facts(loser)
good = Model("```json\n" + say(
    f"The strategy lost {abs(lf['total_return_pct'])}% while SPY gained {lf['benchmark_total_return_pct']}%.",
    f"Only {lf['winning_trades']} of {lf['closed_trades']} trades won in 2020.",
    f"The average loser was {lf['avg_loss_pct']}% against an average winner of +{lf['avg_win_pct']}%.",
) + "\n```")
cache = ex.ExplanationCache(db_factory=lambda: fake_db)
r1 = ex.explain_result(loser, chat=good, cache=cache)
assert r1["narrative_status"] == "ok" and r1["narrative"]["summary"].startswith("The strategy lost")
assert len(r1["narrative"]["points"]) == 2 and r1["findings"][0]["code"] == "lost_money"
assert "not investment advice" in r1["disclaimer"]
sent = good.calls
r2 = ex.explain_result(loser, chat=good, cache=cache)
assert good.calls == sent and r2["cached"] and r2["narrative"] == r1["narrative"]
# A "restart": empty memory, same DB, and a model that would now say something else.
restarted = ex.ExplanationCache(db_factory=lambda: fake_db)
drifted = Model(say("Something entirely different happened."))
r3 = ex.explain_result(loser, chat=drifted, cache=restarted)
assert drifted.calls == 0 and r3["narrative"] == r1["narrative"], "one result, one reading"
assert r3["result_hash"] == r1["result_hash"]
assert ex.result_hash(ex.extract_facts(lucky)) != r1["result_hash"]
with mock.patch.object(ex, "PROMPT_VERSION", "explain-v999"):
    assert ex.result_hash(lf) != r1["result_hash"], "a new prompt version never reuses old readings"
print("✅ a clean narrative is shown; the same result hash gives the same reading — even after a restart")

for reply, status, why in [
    (say("The strategy lost money and you should avoid it."), "withheld", "advice"),
    (say("It lost money.", "It will likely recover next year."), "withheld", "prediction"),
    (say("The strategy lost 42.7% in its worst month."), "withheld", "an invented number"),
    (say(f"It finished {lf['closed_trades'] + 17} trades in total."), "withheld", "an invented count"),
    (say("The strategy lost 6% in March."), "withheld", "a small invented percentage"),
    ("Sorry, I can't help with that.", "unavailable", "not JSON"),
    (RuntimeError("All LLM providers failed"), "unavailable", "every provider down"),
]:
    model = Model(reply)
    out = ex.explain_result(loser, chat=model, cache=ex.ExplanationCache())
    assert out["narrative"] is None and out["narrative_status"] == status, (why, out["narrative_status"])
    assert out["findings"] and out["findings"][0]["code"] == "lost_money", "the numbers always render"
print("✅ advice, predictions and invented numbers are withheld; provider down → findings without "
      "narrative (failure drill)")

# A withheld or failed reading is never cached: the next call asks again.
c2 = ex.ExplanationCache()
ex.explain_result(loser, chat=Model(say("You should buy.")), cache=c2)
retry = Model(say("The strategy lost money."))
assert ex.explain_result(loser, chat=retry, cache=c2)["narrative_status"] == "ok" and retry.calls == 1
# The prompt carries the hard constraints and only the facts.
msgs = ex.build_messages(lf, ex.findings(lf))
assert "Past tense only" in msgs[0]["content"] and "Never recommend" in msgs[0]["content"]
assert "secret" not in msgs[1]["content"]
print("✅ failed readings are not cached; the prompt carries the hard constraints and only the facts")

# ═══════════════════════════════════════════════════════════════════════════
# E. The API
# ═══════════════════════════════════════════════════════════════════════════
from fastapi.testclient import TestClient  # noqa: E402

from src import auth  # noqa: E402

client = TestClient(api.app)
H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
tok = client.post("/auth/register", json={"email": "ex@x.test", "password": "correct-horse-1"}).json()["access_token"]
other = client.post("/auth/register", json={"email": "ex2@x.test", "password": "correct-horse-1"}).json()["access_token"]
trial = auth.create_anon_token()
with api._BACKTEST_JOBS_LOCK:
    api._BACKTEST_JOBS["ex_done"] = {"job_id": "ex_done", "status": "done", "user_email": "ex@x.test", "result": loser}
    api._BACKTEST_JOBS["ex_run"] = {"job_id": "ex_run", "status": "running", "user_email": "ex@x.test", "result": None}
    api._BACKTEST_JOBS["ex_empty"] = {"job_id": "ex_empty", "status": "done", "user_email": "ex@x.test",
                                      "result": {**loser, "results": {}}}
    api._BACKTEST_JOBS["ex_trial"] = {"job_id": "ex_trial", "status": "done",
                                      "user_email": api._anon_owner(trial["anon_id"]), "result": lucky}

api._EXPLAIN_CACHE = ex.ExplanationCache()
r = client.post("/explain", json={"job_id": "ex_done"})
assert r.status_code == 401 and "explain" in r.json()["detail"]
assert client.post("/explain", headers=H(other), json={"job_id": "ex_done"}).status_code == 404
assert client.post("/explain", headers=H(tok), json={"job_id": "ex_run"}).status_code == 409
assert client.post("/explain", headers=H(tok), json={"job_id": "ex_empty"}).status_code == 422
assert client.post("/explain", headers=H(tok), json={"job_id": "ex_trial"}).status_code == 404

# The canned FakeLLMRouter reply is not a narrative → findings without one.
r = client.post("/explain", headers=H(tok), json={"job_id": "ex_done"})
assert r.status_code == 200, r.text
body = r.json()
assert body["findings"][0]["code"] == "lost_money" and body["narrative"] is None
assert body["narrative_status"] == "unavailable" and body["facts"]["closed_trades"] == 10
with mock.patch("src.llm_router.get_router", side_effect=RuntimeError("no providers")):
    down = client.post("/explain", headers=H(tok), json={"job_id": "ex_done"})
assert down.status_code == 200 and down.json()["narrative"] is None, "provider outage never fails it"
assert "no providers" not in down.text, "no raw exception text (golden rule #12)"
# A good model → narrative, through the real router seam.
lucky_f = ex.extract_facts(lucky)
router = FakeLLMRouter(say(f"Two trades out of {lucky_f['closed_trades']} produced most of the profit."))
with mock.patch("src.llm_router.get_router", return_value=router):
    t = client.post("/explain", headers={"X-Anon-Token": trial["anon_token"]}, json={"job_id": "ex_trial"})
assert t.status_code == 200 and t.json()["narrative_status"] == "ok", t.text
assert router._calls[0]["kwargs"]["temperature"] == 0.0
assert "concentrated" in [x["code"] for x in t.json()["findings"]]
print("✅ API: owner-only (account or trial), 401/404/409/422; provider down → 200 with findings, no narrative")

assert api._scope_for_path("/explain") == "explain"
assert api.SCOPE_GLOBAL["explain"] == "explain_llm" and "explain_llm" in api.GLOBAL_LIMITS
assert api.SCOPE_GLOBAL.get("screen") == "screener_llm", "explanations never draw on the screener's budget"
assert api._endpoint_auth("/explain", ["POST"]) == "trial"
mig = (ROOT / "alembic" / "versions" / "c7d8e9f0a1b2_explanations.py").read_text()
from src.database import EXPLANATIONS_SCHEMA  # noqa: E402

assert 'down_revision: Union[str, Sequence[str], None] = "b6c7d8e9f0a1"' in mig
assert "result_hash TEXT PRIMARY KEY" in mig and "result_hash TEXT PRIMARY KEY" in EXPLANATIONS_SCHEMA
print("✅ own rate-limit scope + global bucket (not the screener's); migration matches init_schema")

mock.patch.stopall()
print("\nALL EXPLAIN CHECKS PASSED")
