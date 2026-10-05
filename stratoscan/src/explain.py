"""Explain a finished backtest in plain English (Day 11).

A Sharpe of 0.3 and a 40% drawdown mean nothing to most people. This module
turns a stored result into a reading of what happened — in two layers:

1. **Findings** — deterministic, always present, computed here from numbers
   the engine already produced. These carry the uncomfortable conclusions
   (out-of-sample disagreement → probably fitted; two trades out of nine made
   the profit → luck; fewer than 30 trades → not statistically meaningful)
   and the reasons a losing strategy lost. They never depend on a model, so
   they are there even when every LLM provider is down.
2. **Narrative** — an optional LLM-written paragraph over the SAME facts.
   It is only shown when it passes two automatic checks:
   - the compliance guard (:data:`COMPLIANCE_PATTERNS`): no recommendation,
     no prediction, no "should" — descriptive and past tense only;
   - the number guard (:func:`unsupported_numbers`): every number in the
     text must be one the model was given, so it cannot invent a figure.
   A narrative that fails either is withheld (and logged), never repaired.

Facts are a whitelist (:func:`extract_facts`): named numbers copied one by
one, like the share page — no email, job id, query text or trade log reaches
the model. The explanation is cached by :func:`result_hash` (facts + prompt
version), in memory and in the main DB, so one backtest always gets one
reading.

Nothing here is investment advice, and the module's job is to keep it that
way: the guard runs on the findings too (``tests/test_explain.py`` runs it
over fifty varied results).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Bump when the prompt, the facts or the findings change: cached readings
# made under the old version are then never served for new results.
PROMPT_VERSION = "explain-v2"

DISCLAIMER = (
    "A description of a simulation on historical data, including fees and "
    "slippage. It is not investment advice and says nothing about the future."
)

# The engine's own bar for statistical meaning (backtester.MIN_TRADES_FOR_CONFIDENCE).
MIN_TRADES_FOR_CONFIDENCE = 30
# Thresholds for the findings. Each is a judgement, written down once.
CONCENTRATION_SHARE = 0.75       # top 2 trades made ≥ 75% of the net profit
STOP_OUT_SHARE = 0.40            # ≥ 40% of exits were stop-losses
DEEP_DRAWDOWN_PCT = 30.0         # peak-to-trough ≥ 30%
FRAGILE_PROB_POSITIVE = 0.60     # < 60% of trade reshuffles ended positive
MAX_SYMBOLS_IN_FACTS = 10

# ═══════════════════════════════════════════════════════════════════════════
# The compliance guard
# ═══════════════════════════════════════════════════════════════════════════
# Any match fails the reading. Case-insensitive, word-bounded where it
# matters. Wide on purpose: a false positive costs one withheld paragraph
# (the findings still show); a false negative is advice with our name on it.
COMPLIANCE_PATTERNS: Tuple[str, ...] = (
    r"\bshould\b",
    r"\bought to\b",
    r"\bmust (?:buy|sell|hold|invest)\b",
    r"\bwe recommend\b",
    r"\brecommend(?:s|ed|ation|ations)?\b",
    r"\b(?:i|we) (?:would )?(?:suggest|advise)\b",
    r"\badvi[cs]e\b",
    r"\bwill\b",
    r"\bgoing to\b",
    r"\bexpect(?:s|ed)? (?:it|this|the strategy|returns?|to)\b",
    r"\bexpect it to\b",
    r"\blikely to\b",
    r"\bpoised\b",
    r"\bforecast",
    r"\bpredict",
    r"\boutlook\b",
    r"\bin the future\b",
    r"\bgoing forward\b",
    r"\bnext (?:year|month|week|quarter)\b",
    r"\bwill (?:likely )?outperform\b",
    r"\bis a (?:good|great|solid|safe|smart|bad) (?:investment|buy|bet|pick)\b",
    r"\b(?:good|great|safe|smart) investment\b",
    r"\bshould (?:buy|sell|hold)\b",
    r"\b(?:buy|sell) (?:now|signal|rating|today)\b",
    r"\bstrong buy\b",
    r"\b(?:consider|worth) (?:buying|selling|investing|holding)\b",
    r"\bworth (?:a )?(?:buy|look|try|investing)\b",
    r"\bguarantee",
    r"\brisk[- ]free\b",
    r"\bcan'?t lose\b",
    r"\bsure (?:thing|bet)\b",
    r"\bprice target\b",
    r"\byou (?:can|could) (?:make|earn)\b",
    r"\bupside\b",
    r"\bopportunity\b",
)
_COMPLIANCE_RE = [re.compile(p, re.IGNORECASE) for p in COMPLIANCE_PATTERNS]


def compliance_violations(text: str) -> List[str]:
    """The patterns ``text`` matches — empty means it passes."""
    return [p.pattern for p in _COMPLIANCE_RE if p.search(text or "")]


# ═══════════════════════════════════════════════════════════════════════════
# Facts — the only numbers the model ever sees
# ═══════════════════════════════════════════════════════════════════════════

def _num(value: Any, ndigits: int = 2) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return round(f, ndigits) if math.isfinite(f) else None


def _pct(value: Any) -> Optional[float]:
    """A decimal fraction (0.123) as a percentage (12.3)."""
    f = _num(value, 6)
    return round(f * 100, 1) if f is not None else None


def _items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = result.get("results") if isinstance(result, dict) else None
    if not isinstance(results, dict):
        return []
    return [r for r in results.values() if isinstance(r, dict)]


def _closed_trades(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for it in items:
        for t in it.get("trades") or []:
            if isinstance(t, dict) and _num(t.get("pnl")) is not None and t.get("sell_date"):
                out.append(t)
    return out


def _trade_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    pnls = [float(t["pnl"]) for t in trades]
    pcts = [(_num(t.get("pnl_pct"), 6) or 0.0) for t in trades]
    wins = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p <= 0]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    net = sum(pnls)
    top2 = sum(sorted((p for p in pnls if p > 0), reverse=True)[:2])
    worst2 = -sum(sorted(p for p in pnls if p < 0)[:2])
    exits: Dict[str, int] = {}
    for t in trades:
        reason = str(t.get("exit_reason") or "signal")
        exits[reason] = exits.get(reason, 0) + 1
    return {
        "closed_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "avg_win_pct": round(sum(wins) / len(wins) * 100, 1) if wins else None,
        "avg_loss_pct": round(sum(losses) / len(losses) * 100, 1) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        # Share of the NET profit made by the two best trades (only meaningful
        # when there was a net profit).
        "top2_profit_share_pct": round(top2 / net * 100, 1) if net > 0 and len(trades) >= 3 else None,
        # Share of the gross losses from the two worst trades.
        "worst2_loss_share_pct": round(worst2 / gross_loss * 100, 1) if gross_loss > 0 and len(trades) >= 3 else None,
        "exits": dict(sorted(exits.items())),
    }


# A point within this fraction of its running peak counts as AT the peak.
# The curves are downsampled (≤ 300 points over ten years, ~8 trading days
# apart), so the day of a new high is usually not a sample; with a zero
# tolerance nearly every sample sat a hair under an unsampled peak and a
# steadily rising account read as "below its peak for 90 months" (found by
# the Day 13 end-to-end run).
UNDERWATER_TOLERANCE = 0.01


def _dated(curve: Any) -> List[Tuple[date, float]]:
    pts: List[Tuple[date, float]] = []
    if not isinstance(curve, list):
        return pts
    for p in curve:
        if isinstance(p, dict) and isinstance(p.get("date"), str):
            v = _num(p.get("value"), 6)
            if v is not None:
                try:
                    pts.append((date.fromisoformat(p["date"][:10]), v))
                except ValueError:
                    continue
    return pts


def _drawdown_duration(equity: Any, drawdown: Any) -> Tuple[Optional[float], Optional[bool]]:
    """Longest stretch more than 1% below a previous peak, in whole months,
    and whether the account was back within 1% of its peak at the end.

    Measured on the sampled EQUITY curve against its own running peak (so a
    peak and the dip after it are judged on the same samples); the drawdown
    curve is the fallback when no equity curve is long enough.
    """
    eq = _dated(equity)
    if len(eq) >= 20:
        under: List[Tuple[date, bool]] = []
        peak = eq[0][1]
        for d, v in eq:
            peak = max(peak, v)
            under.append((d, peak > 0 and v < peak * (1 - UNDERWATER_TOLERANCE)))
    else:
        dd = _dated(drawdown)
        if len(dd) < 2:
            return None, None
        under = [(d, v < -UNDERWATER_TOLERANCE) for d, v in dd]
    longest = 0
    start: Optional[date] = None
    for d, is_under in under:
        if is_under:
            start = start or d
            longest = max(longest, (d - start).days)
        else:
            start = None
    return round(longest / 30.44), not under[-1][1]


def _walk_forward(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """In-sample (first 70%) vs out-of-sample (last 30%) across symbols."""
    rows = []
    for it in items:
        wf = it.get("walk_forward")
        if isinstance(wf, dict) and wf.get("available"):
            ins = wf.get("in_sample") or {}
            oos = wf.get("out_of_sample") or {}
            rolling = wf.get("rolling") or {}
            rows.append({
                "symbol": str(it.get("symbol") or "")[:12],
                "in_sample_return_pct": _pct(ins.get("total_return")),
                "out_of_sample_return_pct": _pct(oos.get("total_return")),
                "positive_windows": rolling.get("positive_windows"),
                "total_windows": rolling.get("total_windows"),
            })
    if not rows:
        return None
    disagree = [
        r["symbol"] for r in rows
        if (r["in_sample_return_pct"] or 0) > 0 and (r["out_of_sample_return_pct"] or 0) < 0
    ]
    return {"symbols": rows[:MAX_SYMBOLS_IN_FACTS], "gained_in_sample_lost_out_of_sample": disagree}


def _monte_carlo(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    rows = []
    for it in items:
        mc = it.get("monte_carlo")
        if isinstance(mc, dict) and mc.get("available"):
            rows.append({
                "symbol": str(it.get("symbol") or "")[:12],
                "p5_return_pct": _pct(mc.get("p5")),
                "median_return_pct": _pct(mc.get("median")),
                "p95_return_pct": _pct(mc.get("p95")),
                "share_positive_pct": _pct(mc.get("prob_positive")),
            })
    return {"symbols": rows[:MAX_SYMBOLS_IN_FACTS]} if rows else None


def extract_facts(result: Dict[str, Any]) -> Dict[str, Any]:
    """The whitelisted numbers one explanation is built from.

    Portfolio figures when several symbols ran (the book the user saw),
    else the single symbol's — the same basis as the share page.
    """
    items = _items(result)
    portfolio = result.get("portfolio") if isinstance(result.get("portfolio"), dict) else None
    multi = bool(portfolio) and len(items) > 1
    top = portfolio if multi else (items[0] if items else {})

    benchmarks = {
        b for b in (_num(r.get("benchmark_total_return_pct")) for r in items) if b is not None
    }
    benchmark = (next(iter(benchmarks)) if len(benchmarks) == 1 else None) if multi else (
        _num(top.get("benchmark_total_return_pct")) if items else None
    )
    total_return = _num(top.get("total_return_pct"))
    curve = top.get("equity_curve") if isinstance(top.get("equity_curve"), list) else []
    dates = [p.get("date") for p in curve if isinstance(p, dict) and isinstance(p.get("date"), str)]
    duration, recovered = _drawdown_duration(top.get("equity_curve"), top.get("drawdown_curve"))
    trades = _closed_trades(items)
    strategy = result.get("strategy") if isinstance(result.get("strategy"), dict) else {}

    facts: Dict[str, Any] = {
        "basis": "portfolio" if multi else ("single" if items else "none"),
        "symbols": sorted(str(r.get("symbol") or "")[:12] for r in items)[:MAX_SYMBOLS_IN_FACTS],
        "symbols_tested": len(items),
        "period": {"start": dates[0][:10] if dates else None, "end": dates[-1][:10] if dates else None},
        "rules": {
            "buy_rules": [str(x)[:80] for x in (strategy.get("buy_rules") or [])][:8],
            "sell_rules": [str(x)[:80] for x in (strategy.get("sell_rules") or [])][:8],
        },
        "total_return_pct": total_return,
        "benchmark_total_return_pct": benchmark,
        "excess_return_pct": (
            round(total_return - benchmark, 2)
            if total_return is not None and benchmark is not None else None
        ),
        "sharpe_ratio": _num(top.get("sharpe_ratio"), 2),
        "max_drawdown_pct": _num(top.get("max_drawdown_pct"), 1),
        "longest_drawdown_months": duration,
        "recovered_by_end": recovered,
        "alpha_pct": None if multi else _num(top.get("alpha_pct")),
        "beta": None if multi else _num(top.get("beta")),
        "per_symbol": [
            {
                "symbol": str(r.get("symbol") or "")[:12],
                "total_return_pct": _num(r.get("total_return_pct")),
                "trades": int(_num(r.get("total_trades"), 0) or 0),
            }
            for r in items
        ][:MAX_SYMBOLS_IN_FACTS] if multi else [],
        **_trade_stats(trades),
        "walk_forward": _walk_forward(items),
        "monte_carlo": _monte_carlo(items),
    }
    facts["win_rate_pct"] = (
        round(facts["winning_trades"] / facts["closed_trades"] * 100, 1)
        if facts["closed_trades"] else None
    )
    return facts


def result_hash(facts: Dict[str, Any]) -> str:
    blob = json.dumps({"v": PROMPT_VERSION, "facts": facts}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# Findings — deterministic, always present
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Finding:
    code: str
    tone: str  # "caution" | "info" | "positive"
    text: str


def _p(v: float) -> str:
    return f"{v:+.1f}%"


def findings(facts: Dict[str, Any]) -> List[Finding]:
    """What happened, in order of importance. Past tense, descriptive."""
    out: List[Finding] = []
    ret = facts.get("total_return_pct")
    bench = facts.get("benchmark_total_return_pct")
    excess = facts.get("excess_return_pct")
    n = facts.get("closed_trades") or 0

    if facts.get("basis") == "none" or ret is None:
        return [Finding("no_result", "info", "There was no result to describe: no stock produced a backtest.")]

    if n == 0:
        out.append(Finding(
            "no_trades", "caution",
            "The rules never completed a trade over the period, so the return reflects no trading activity.",
        ))

    # Headline. Name the basis: on a multi-stock run the figures are the
    # equal-weighted portfolio's, which is not any single stock's number the
    # page may be showing next to it (found by the Day 13 end-to-end run).
    portfolio = facts.get("basis") == "portfolio"
    subject = f"The {facts.get('symbols_tested')}-stock portfolio" if portfolio else "The strategy"
    if ret < 0:
        out.append(Finding("lost_money", "caution", f"{subject} lost money: {_p(ret)} over the period."))
    else:
        out.append(Finding("made_money", "info", f"{subject} returned {_p(ret)} over the period."))
    if bench is not None and excess is not None:
        if excess < 0:
            out.append(Finding(
                "lagged_benchmark", "caution",
                f"It finished {abs(excess):.1f} points behind SPY, which returned {_p(bench)} over the same window.",
            ))
        else:
            out.append(Finding(
                "beat_benchmark", "positive",
                f"It finished {excess:.1f} points ahead of SPY, which returned {_p(bench)} over the same window.",
            ))

    # Why it lost — the drivers, strongest first.
    avg_win, avg_loss = facts.get("avg_win_pct"), facts.get("avg_loss_pct")
    exits = facts.get("exits") or {}
    stops = int(exits.get("stop_loss", 0)) + int(exits.get("trailing_stop", 0))
    if ret < 0 and n:
        if avg_win is not None and avg_loss is not None and abs(avg_loss) > avg_win:
            out.append(Finding(
                "losers_bigger", "caution",
                f"Losing trades were larger than winning ones: the average loser was {avg_loss:.1f}% "
                f"against an average winner of +{avg_win:.1f}%.",
            ))
        elif avg_win is None:
            out.append(Finding("no_winners", "caution", f"None of the {n} closed trades made money."))
        wr = facts.get("win_rate_pct")
        if wr is not None and wr < 40 and avg_win is not None:
            out.append(Finding(
                "low_win_rate", "caution",
                f"Only {facts['winning_trades']} of {n} trades won ({wr:.0f}%), too few for the winners to cover the losers.",
            ))
        worst = facts.get("worst2_loss_share_pct")
        if worst is not None and worst >= CONCENTRATION_SHARE * 100:
            out.append(Finding(
                "few_big_losers", "caution",
                f"Two trades accounted for {worst:.0f}% of all the money lost.",
            ))
    if n and stops / n >= STOP_OUT_SHARE:
        out.append(Finding(
            "stopped_out", "caution",
            f"{stops} of {n} trades ended at a stop-loss, so exits were mostly forced by price drops.",
        ))

    # Was it luck?
    top2 = facts.get("top2_profit_share_pct")
    if top2 is not None and top2 >= CONCENTRATION_SHARE * 100:
        out.append(Finding(
            "concentrated", "caution",
            f"Two trades out of {n} produced {top2:.0f}% of the net profit. A result that rests on two "
            "trades is closer to luck than to a repeatable edge.",
        ))
    if 0 < n < MIN_TRADES_FOR_CONFIDENCE:
        out.append(Finding(
            "few_trades", "caution",
            f"There were only {n} closed trades — fewer than {MIN_TRADES_FOR_CONFIDENCE}, so the win rate "
            "and averages are not statistically meaningful.",
        ))

    # Was it fitted?
    wf = facts.get("walk_forward") or {}
    disagree = wf.get("gained_in_sample_lost_out_of_sample") or []
    if disagree:
        who = "the strategy" if facts.get("basis") == "single" else f"{len(disagree)} of the symbols"
        out.append(Finding(
            "fitted", "caution",
            f"For {who}, the rules made money on the first 70% of the history but lost on the last 30%. "
            "When out-of-sample results disagree with the full sample, the rules most likely fitted "
            "past noise rather than found an edge.",
        ))
    mc_rows = (facts.get("monte_carlo") or {}).get("symbols") or []
    if ret > 0 and mc_rows:
        shares = [r["share_positive_pct"] for r in mc_rows if r.get("share_positive_pct") is not None]
        if shares and min(shares) < FRAGILE_PROB_POSITIVE * 100:
            out.append(Finding(
                "fragile", "caution",
                ("For at least one of the stocks, reshuffling" if portfolio else "Reshuffling")
                + f" the same trades in random order ended positive only {min(shares):.0f}% of the "
                "time, so the gain depended on the order the trades happened to come in.",
            ))

    # How painful was it?
    dd = facts.get("max_drawdown_pct")
    if dd is not None and abs(dd) >= DEEP_DRAWDOWN_PCT:
        months = facts.get("longest_drawdown_months")
        tail = ""
        if facts.get("recovered_by_end") is False:
            tail = " and it had not recovered by the end of the period"
        elif months:
            tail = f"; the longest stretch below a previous peak lasted about {months:.0f} months"
        out.append(Finding(
            "deep_drawdown", "caution",
            f"At its worst the account was {abs(dd):.1f}% below its peak{tail}.",
        ))
    return out


# ═══════════════════════════════════════════════════════════════════════════
# The narrative (LLM) and its guards
# ═══════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You describe the result of a historical backtest simulation in plain English for a non-expert.

Hard rules — breaking any one makes your answer unusable:
- Past tense only. Describe what happened in the simulation. Never say anything about the future.
- Descriptive only. Never recommend, advise, suggest, or judge whether anyone should do anything.
- Never use the words "should", "will", "recommend", "expect", "likely", "opportunity", "upside" or "predict".
- Use ONLY numbers that appear in the FACTS. Do not compute new numbers, do not round to new figures, do not invent dates.
- Every caution listed in FINDINGS must be reflected in your summary or points. Do not soften them.
- If the out-of-sample result disagrees with the in-sample result, say plainly that the rules probably fitted past data.
- If a couple of trades produced most of the profit, say plainly the result looks like luck.

Reply with JSON only: {"summary": "<2-3 sentences>", "points": ["<one sentence>", ...]} with at most 4 points."""


def build_messages(facts: Dict[str, Any], found: List[Finding]) -> List[Dict[str, str]]:
    user = (
        "FACTS:\n" + json.dumps(facts, sort_keys=True)
        + "\n\nFINDINGS:\n" + "\n".join(f"- [{f.tone}] {f.text}" for f in found)
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def parse_narrative(content: str) -> Optional[Dict[str, Any]]:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    summary = data.get("summary") if isinstance(data, dict) else None
    points = data.get("points") if isinstance(data, dict) else None
    if not isinstance(summary, str) or not summary.strip():
        return None
    if not isinstance(points, list):
        points = []
    clean = [str(p).strip()[:400] for p in points if isinstance(p, str) and p.strip()][:4]
    return {"summary": summary.strip()[:900], "points": clean}


_NUM_RE = re.compile(r"(?<![\w.])[-+−]?\d[\d,]*(?:\.\d+)?")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _fact_numbers(obj: Any, out: Optional[List[float]] = None) -> List[float]:
    out = [] if out is None else out
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        if math.isfinite(float(obj)):
            out.append(float(obj))
    elif isinstance(obj, str):
        # A date contributes its year only — never "06" and "30" as numbers
        # the model may then use as percentages.
        if _DATE_RE.match(obj):
            out.append(float(obj[:4]))
            return out
        for m in _NUM_RE.finditer(obj):
            try:
                out.append(float(m.group().replace(",", "").replace("−", "-")))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _fact_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _fact_numbers(v, out)
    return out


# Numbers the model may use without them being facts: the 70/30 split and
# the confidence threshold the findings name, and small counts ("two
# trades") that read naturally as digits.
_STRUCTURAL = {70.0, 30.0, 100.0, MIN_TRADES_FOR_CONFIDENCE * 1.0}
_SMALL_COUNT_MAX = 10


def unsupported_numbers(text: str, facts: Dict[str, Any], found: List[Finding]) -> List[str]:
    """Numbers in ``text`` that are not in the facts or findings.

    A number passes when it equals a known value at the precision written
    (``12.3`` matches 12.34; ``12`` matches 12.4), in either sign.
    """
    known = _fact_numbers(facts) + _fact_numbers([f.text for f in found])
    known_abs = [abs(k) for k in known]
    bad = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group().replace(",", "").replace("−", "-").lstrip("+")
        try:
            x = abs(float(raw))
        except ValueError:
            continue
        # A percentage is always a claim about the result: it must be a fact,
        # however small. Bare small integers read as counts ("two trades").
        after = text[m.end():m.end() + 9].lstrip().lower()
        is_pct = after.startswith(("%", "percent", "per cent", "points"))
        if x in _STRUCTURAL or (not is_pct and x.is_integer() and x <= _SMALL_COUNT_MAX):
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        tol = 0.5 * 10 ** (-decimals) + 1e-9
        if not any(abs(x - k) <= tol for k in known_abs):
            bad.append(m.group())
    return bad


ChatFn = Callable[[List[Dict[str, str]]], str]


def _llm_chat(messages: List[Dict[str, str]]) -> str:
    from src.llm_router import get_router

    resp = get_router().chat_completion(messages=messages, temperature=0.0, max_tokens=2000)
    return resp.choices[0].message.content or ""


class ExplanationCache:
    """Memory first, then the main DB. Both are best-effort: a cache that is
    down only costs an LLM call, it never fails the request."""

    def __init__(self, db_factory: Optional[Callable[[], Any]] = None, max_entries: int = 500):
        from src.cache import TTLCache

        self._mem = TTLCache(ttl_seconds=30 * 24 * 3600, max_entries=max_entries)
        self._db_factory = db_factory

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        hit = self._mem.get(key)
        if hit is not None:
            return json.loads(json.dumps(hit))
        if self._db_factory:
            try:
                row = self._db_factory().get_explanation(key)
            except Exception as e:  # noqa: BLE001
                logger.warning("Explanation cache read failed: %s", e)
                row = None
            if row:
                self._mem.set(key, row)
                return json.loads(json.dumps(row))
        return None

    def put(self, key: str, value: Dict[str, Any]) -> None:
        self._mem.set(key, value)
        if self._db_factory:
            try:
                self._db_factory().save_explanation(key, value)
            except Exception as e:  # noqa: BLE001
                logger.warning("Explanation cache write failed: %s", e)


def explain_result(
    result: Dict[str, Any],
    *,
    chat: Optional[ChatFn] = None,
    cache: Optional[ExplanationCache] = None,
) -> Dict[str, Any]:
    """The full reading for one stored result. Never raises for model trouble.

    ``narrative_status``: ``ok`` (shown), ``unavailable`` (every provider
    down or unparseable — try later), ``withheld`` (it failed a guard).
    """
    facts = extract_facts(result)
    found = findings(facts)
    key = result_hash(facts)
    base = {
        "result_hash": key,
        "basis": facts["basis"],
        "facts": facts,
        "findings": [asdict(f) for f in found],
        "disclaimer": DISCLAIMER,
    }
    cached = cache.get(key) if cache else None
    if cached and cached.get("narrative"):
        return {**base, "narrative": cached["narrative"], "narrative_status": "ok", "cached": True}
    if facts["basis"] == "none":
        return {**base, "narrative": None, "narrative_status": "unavailable", "cached": False}

    try:
        content = (chat or _llm_chat)(build_messages(facts, found))
    except Exception as e:  # noqa: BLE001 — every provider down: numbers without narrative
        logger.warning("Explain: no narrative (LLM unavailable): %s", e)
        return {**base, "narrative": None, "narrative_status": "unavailable", "cached": False}

    narrative = parse_narrative(content)
    if narrative is None:
        logger.warning("Explain: unparseable narrative for %s", key[:12])
        return {**base, "narrative": None, "narrative_status": "unavailable", "cached": False}
    text = " ".join([narrative["summary"], *narrative["points"]])
    violations = compliance_violations(text)
    invented = unsupported_numbers(text, facts, found)
    if violations or invented:
        logger.warning(
            "Explain: narrative withheld for %s (compliance=%s, unsupported numbers=%s)",
            key[:12], violations[:3], invented[:5],
        )
        return {**base, "narrative": None, "narrative_status": "withheld", "cached": False}
    if cache:
        cache.put(key, {"narrative": narrative, "prompt_version": PROMPT_VERSION})
    return {**base, "narrative": narrative, "narrative_status": "ok", "cached": False}
