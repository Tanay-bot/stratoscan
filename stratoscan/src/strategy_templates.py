"""The strategy template library (Day 8: nobody faces an empty box).

Blank-input paralysis is the main way a natural-language product loses
people: "describe a strategy" is an intimidating instruction to someone who
has never written one. A template is just a pre-filled query through the
pipeline that already exists — no engine work.

Every template here is held to three rules, enforced by
``tests/test_strategy_templates.py``:

1. **It costs nothing.** The deterministic parser reads it at confidence
   >= 0.6, so a template never spends an LLM call, however often it is used.
2. **What is simulated is what the title says.** ``buy_rules`` /
   ``sell_rules`` pin the exact rules the engine receives. A template whose
   idea is only a *screen* (the engine trades something else) does not ship.
3. **It does not churn.** On synthetic up, choppy and down markets, its
   median holding period is measured in weeks, not a day.

Momentum templates trade the Day 9 momentum rules: enter when price is above
its 50-day average inside an uptrend (50-day above the 200-day), exit when
price falls below the 200-day. (Before Day 9 the exit was "RSI above 70",
which re-entered and exited on consecutive bars in a trend.)

Still left out: golden cross and Bollinger ideas — they are screened, but
the engine then trades RSI timing, so the title would promise more than the
simulation tests. Volume-confirmed breakouts wait for the Day 14 volume fix.

The frontend mirrors this list in ``frontend/src/lib/templates.ts`` (the
template test fails if the two drift apart).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class StrategyTemplate:
    id: str
    title: str
    query: str
    idea: str  # "Tests the idea that …" — one line
    category: str
    buy_rules: Tuple[str, ...]
    sell_rules: Tuple[str, ...]


def _rsi(n: float) -> Tuple[str, ...]:
    # Pinned verbatim: the parser emits "35.0" for "RSI below 35" but "35"
    # when the value comes from the "oversold" synonym.
    return (f"rsi < {n}",)


_EXIT = ("rsi > 70",)
_TREND_ENTRY = ("price > sma_50", "sma_50 > sma_200")
_TREND_EXIT = ("price < sma_200",)

TEMPLATES: Tuple[StrategyTemplate, ...] = (
    StrategyTemplate(
        id="rsi-pullback-uptrend",
        title="RSI pullback in an uptrend",
        query="large cap stocks with RSI below 40 trading above their 200-day moving average",
        idea="Buying short dips inside a long-term uptrend beats chasing strength.",
        category="Trend + timing",
        buy_rules=_rsi(40.0), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="oversold-mean-reversion",
        title="Oversold mean reversion",
        query="oversold large cap stocks with RSI below 30",
        idea="Deeply oversold large caps tend to bounce.",
        category="Mean reversion",
        buy_rules=_rsi(30.0), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="52-week-high-momentum",
        title="52-week high momentum",
        query="large cap stocks near their 52-week high with bullish momentum",
        idea="Stocks near their highs keep climbing while the trend holds.",
        category="Momentum",
        buy_rules=_TREND_ENTRY, sell_rules=_TREND_EXIT,
    ),
    StrategyTemplate(
        id="large-cap-momentum",
        title="Large-cap momentum",
        query="large cap stocks with bullish momentum",
        idea="Riding big names in an uptrend beats buying their dips.",
        category="Momentum",
        buy_rules=_TREND_ENTRY, sell_rules=_TREND_EXIT,
    ),
    StrategyTemplate(
        id="tech-momentum",
        title="Tech momentum",
        query="technology stocks with bullish momentum",
        idea="The strongest sector's trend carries on until it breaks.",
        category="Momentum",
        buy_rules=_TREND_ENTRY, sell_rules=_TREND_EXIT,
    ),
    StrategyTemplate(
        id="low-volatility-value",
        title="Low-volatility value",
        query="large cap low beta value stocks with P/E below 15 and RSI below 35",
        idea="Cheap, steady stocks bought on a dip hold up on a risk-adjusted basis.",
        category="Value",
        buy_rules=_rsi(35.0), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="quality-reasonable-price",
        title="Quality at a reasonable price",
        query="profitable large cap stocks with ROE above 15% and P/E below 20 and RSI below 35",
        idea="Profitability plus a sane multiple beats either one alone.",
        category="Quality",
        buy_rules=_rsi(35.0), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="beaten-down-quality",
        title="Beaten-down quality",
        query="profitable large cap stocks near their 52-week low with ROE above 15% and RSI below 30",
        idea="Good businesses near a 52-week low recover.",
        category="Quality",
        buy_rules=_rsi(30.0), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="dividend-durability",
        title="Dividend durability",
        query="large cap stocks with dividend yield above 3% and beta below 1 and RSI below 35",
        idea="Steady dividend payers hold up through drawdowns.",
        category="Income",
        buy_rules=_rsi(35.0), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="utility-income-dip",
        title="Utility income on a dip",
        query="utilities with dividend yield above 3% and RSI below 35",
        idea="High-yield utilities bought when oversold pay you to wait.",
        category="Income",
        buy_rules=_rsi(35.0), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="mega-cap-defensive",
        title="Mega-cap defensive",
        query="mega cap stocks with beta below 0.8 and RSI below 40",
        idea="Size and stability cushion the worst quarters.",
        category="Defensive",
        buy_rules=_rsi(40.0), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="tech-pullback",
        title="Tech pullback",
        query="technology stocks with RSI below 40 trading above their 200-day moving average",
        idea="Tech leaders in an uptrend reward buying the dip.",
        category="Sector",
        buy_rules=_rsi(40.0), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="energy-pullback",
        title="Energy pullback",
        query="energy stocks with RSI below 40 trading above their 200-day moving average",
        idea="Dips in an energy uptrend are entries, not exits.",
        category="Sector",
        buy_rules=_rsi(40.0), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="healthcare-oversold",
        title="Oversold healthcare",
        query="oversold healthcare stocks with RSI below 35",
        idea="Defensive healthcare names snap back from oversold.",
        category="Sector",
        buy_rules=("rsi < 35",), sell_rules=_EXIT,
    ),
    StrategyTemplate(
        id="financials-oversold",
        title="Oversold financials",
        query="oversold financial stocks with RSI below 35",
        idea="Banks and insurers overshoot on bad news, then revert.",
        category="Sector",
        buy_rules=("rsi < 35",), sell_rules=_EXIT,
    ),
)


def _fmt_rule(rule: str) -> str:
    field, op, value = rule.split()
    names = {"rsi": "RSI(14)", "price": "price", "sma_50": "the 50-day average",
             "sma_200": "the 200-day average"}
    # RSI crosses thresholds; prices and averages sit above or below each other.
    words = (
        {"<": "drops below", ">": "rises above"} if field == "rsi"
        else {"<": "falls below", ">": "is above"}
    )
    try:
        v = float(value)
        shown = f"{v:g}"
    except ValueError:
        shown = names.get(value, value)
    return f"{names.get(field, field)} {words.get(op, op)} {shown}"


def simulates(t: StrategyTemplate) -> str:
    """One plain sentence on what the engine actually trades for ``t``."""
    buy = " and ".join(_fmt_rule(r) for r in t.buy_rules)
    sell = " or ".join(_fmt_rule(r) for r in t.sell_rules)
    return f"Buys when {buy}; sells when {sell}."


def templates_payload() -> List[Dict[str, object]]:
    """The public, JSON-ready list (served in ``GET /meta``)."""
    out = []
    for t in TEMPLATES:
        d = asdict(t)
        d["buy_rules"] = list(t.buy_rules)
        d["sell_rules"] = list(t.sell_rules)
        d["simulates"] = simulates(t)
        out.append(d)
    return out
