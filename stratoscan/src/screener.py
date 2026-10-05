"""Real-Time Stock Screener for Stratoscan.

Filters the local stock universe database based on criteria from the
Natural Language Interpreter. All queries hit the local database —
zero API calls per user screening.

The screener is free for all users and is the primary user acquisition
feature of Stratoscan.

Quality tiers implemented here:
  Tier 1 (credibility): trend filters (price vs SMA50/200, golden/death
    cross, 52w proximity) pushed into SQL; real momentum composite
    (price above SMA50, not just RSI); user-controlled sorting; the
    response echoes the exact filters applied + data-quality notes.
  Tier 3 (robustness): multi-factor composite score (value + momentum +
    quality, z-scored against the match set), stale-data flags, a
    minimum-completeness guard, and a per-stock "why did this match"
    breakdown.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.database import PostgresDB
from src.interpreter import (
    InterpretedQuery,
    format_filters_for_display,
)

logger = logging.getLogger(__name__)

# How stale a row must be (minutes) before it's flagged in results. The
# refresh cadence is 10 minutes, so 15 gives one missed cycle of slack.
STALE_AFTER_MINUTES = 15

# Rows older than this (days) are EXCLUDED from screening entirely, not
# just flagged. The scheduler refreshes only the exchange-listed universe
# every ~15 minutes, so a row this old is unlisted/delisted/pink-sheet data
# that will never be refreshed again — returning it would present dead
# data as live. 7 days also survives holiday weekends when the scheduler
# is paused (longest realistic gap: a 3-day weekend).
STALE_EXCLUSION_DAYS = 7

# When sorting by the composite score, fetch at most this many matches and
# rank them in Python — ranking is then global within the window (not a
# re-order of the market-cap top-N page). 2000 keeps a full query fast while
# covering any realistic match set; beyond the cap the ranking is
# best-effort (documented in the response via data_quality.window).
SCORE_SORT_MAX_WINDOW = 2000

# How many LLM-produced filters a 0-match screen may drop (auto-relax)
# before returning the nearest matches instead of an empty result. Guards
# (freshness / completeness) are never dropped; only the interpreter's own
# criteria. 2 keeps the rescue bounded while covering the common cases
# (a contradictory trend condition, then one sparse numeric column).
RELAX_MAX_FILTERS = 2

# Day 14: columns filled for only part of the universe. Volume comes from
# daily bars, which are held for the largest names only (technicals top 500 +
# the backtest cache), so a volume filter can only ever match those. When one
# is applied the report says how many stocks carry the column — a filter that
# quietly ignores most of the universe must say so.
PARTIAL_COVERAGE_COLUMNS = {"volume": "Volume", "avg_volume": "Average volume"}

# Sort keys the screener accepts (must be a subset of the DB whitelist).
SORTABLE_COLUMNS: frozenset = frozenset({
    "symbol", "market_cap", "current_price", "price_change_pct", "volume",
    "avg_volume", "pe_ratio", "eps_ttm", "dividend_yield", "beta", "rsi_14",
    "sma_50", "sma_200", "high_52w", "low_52w",
    "ps_ratio", "pb_ratio", "ev_ebitda", "roe_ttm", "roa_ttm",
    "gross_margin_ttm", "net_margin_ttm", "revenue_growth_ttm",
    "eps_growth_ttm", "atr_14", "score",  # "score" = composite (computed)
})

# Composite score factor weights (Tier 3). Sums to 1.0.
SCORE_WEIGHTS: Dict[str, float] = {
    "value": 0.30,      # low P/E, low P/S, low EV/EBITDA
    "momentum": 0.40,   # price above SMA50/200, RSI, daily change
    "quality": 0.30,    # ROE, margins, positive earnings, low beta
}

@dataclass
class ScreenerResult:
    """A single stock result from the screener."""

    symbol: str
    company_name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    current_price: Optional[float] = None
    price_change_pct: Optional[float] = None
    market_cap: Optional[int] = None
    pe_ratio: Optional[float] = None
    rsi_14: Optional[float] = None
    volume: Optional[int] = None
    dividend_yield: Optional[float] = None
    beta: Optional[float] = None
    # Tier 1: previously stored-but-unused fields now surfaced to the API
    avg_volume: Optional[int] = None
    eps_ttm: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    # Tier 2: valuation / profitability / growth / extra technicals
    ps_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    ev_ebitda: Optional[float] = None
    roe_ttm: Optional[float] = None
    roa_ttm: Optional[float] = None
    gross_margin_ttm: Optional[float] = None
    net_margin_ttm: Optional[float] = None
    revenue_growth_ttm: Optional[float] = None
    eps_growth_ttm: Optional[float] = None
    macd_12_26: Optional[float] = None
    atr_14: Optional[float] = None
    bollinger_upper: Optional[float] = None
    bollinger_lower: Optional[float] = None
    updated_at: Optional[Any] = None
    # Tier 1: derived display value — % off the 52-week high
    pct_off_52w_high: Optional[float] = None
    # Tier 3: composite score (0-100) + per-factor breakdown
    score: Optional[float] = None
    score_components: Dict[str, Any] = field(default_factory=dict)
    # Tier 3: why this stock matched (human-readable list)
    why_matched: List[str] = field(default_factory=list)
    # Tier 3: data-freshness guard
    is_stale: bool = False
    data_age_minutes: Optional[float] = None

    @classmethod
    def from_db_record(cls, record: Dict[str, Any]) -> "ScreenerResult":
        """Create a ScreenerResult from a database record dict."""
        result = cls(
            symbol=record.get("symbol", ""),
            company_name=record.get("company_name", ""),
            sector=record.get("sector"),
            industry=record.get("industry"),
            current_price=record.get("current_price"),
            price_change_pct=record.get("price_change_pct"),
            market_cap=record.get("market_cap"),
            pe_ratio=record.get("pe_ratio"),
            rsi_14=record.get("rsi_14"),
            volume=record.get("volume"),
            dividend_yield=record.get("dividend_yield"),
            beta=record.get("beta"),
            avg_volume=record.get("avg_volume"),
            eps_ttm=record.get("eps_ttm"),
            sma_50=record.get("sma_50"),
            sma_200=record.get("sma_200"),
            high_52w=record.get("high_52w"),
            low_52w=record.get("low_52w"),
            ps_ratio=record.get("ps_ratio"),
            pb_ratio=record.get("pb_ratio"),
            ev_ebitda=record.get("ev_ebitda"),
            roe_ttm=record.get("roe_ttm"),
            roa_ttm=record.get("roa_ttm"),
            gross_margin_ttm=record.get("gross_margin_ttm"),
            net_margin_ttm=record.get("net_margin_ttm"),
            revenue_growth_ttm=record.get("revenue_growth_ttm"),
            eps_growth_ttm=record.get("eps_growth_ttm"),
            macd_12_26=record.get("macd_12_26"),
            atr_14=record.get("atr_14"),
            bollinger_upper=record.get("bollinger_upper"),
            bollinger_lower=record.get("bollinger_lower"),
            updated_at=record.get("updated_at"),
        )
        result._derive_display_fields()
        return result

    def _derive_display_fields(self) -> None:
        """Compute derived display values from raw DB fields."""
        if self.high_52w and self.current_price and self.high_52w > 0:
            self.pct_off_52w_high = (
                (self.high_52w - self.current_price) / self.high_52w * 100.0
            )


@dataclass
class ScreeningReport:
    """The full result of a screening operation."""

    query: InterpretedQuery
    results: List[ScreenerResult] = field(default_factory=list)
    total_matches: int = 0
    limit: int = 50
    offset: int = 0
    has_more: bool = False
    sort_by: str = "market_cap"
    sort_desc: bool = True
    # Tier 1: the exact SQL filters that were applied (auditable echo)
    applied_filters: List[str] = field(default_factory=list)
    # Tier 3: data-quality notes (staleness, coverage)
    data_quality: Dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        """Generate a human-readable summary of the screening results."""
        page_note = (
            f" (page starting at offset {self.offset}, more available)"
            if self.has_more
            else ""
        )
        header = (
            f"Screening results for: \"{self.query.raw_input}\"\n"
            f"Filters: {format_filters_for_display(self.query)}\n"
            f"Matches: {self.total_matches} stocks{page_note}\n"
        )
        if not self.results:
            return header + "No stocks matched your criteria."

        table = ["\nSymbol   | Company                    | Price   | Change % | P/E  | RSI  | Mkt Cap (B)"]
        table.append("-" * 95)
        for r in self.results[:self.limit]:
            price = f"${r.current_price:.2f}" if r.current_price else "N/A"
            change = f"{r.price_change_pct:+.2f}%" if r.price_change_pct is not None else "N/A"
            pe = f"{r.pe_ratio:.1f}" if r.pe_ratio else "N/A"
            rsi = f"{r.rsi_14:.1f}" if r.rsi_14 else "N/A"
            cap = f"${r.market_cap / 1_000_000_000:.1f}B" if r.market_cap else "N/A"
            name = r.company_name[:24] if len(r.company_name) > 24 else r.company_name.ljust(24)
            table.append(f"{r.symbol:<8} | {name} | {price:>7} | {change:>8} | {pe:>4} | {rsi:>4} | {cap}")

        return header + "\n".join(table)


class StockScreener:
    """Filters the stock universe using criteria from the LLM interpreter.

    All filtering is done locally against the database. Zero API calls.
    """

    def __init__(self, db: Optional[PostgresDB] = None) -> None:
        self._db = db or PostgresDB()

    # ── Filter translation ────────────────────────────────────────────────

    def _apply_derived_filters(
        self,
        query: InterpretedQuery,
        db_filters: Dict[str, Any],
        applied: List[str],
        relaxable: Optional[List[Dict[str, Any]]] = None,
        skip_labels: Optional[set] = None,
    ) -> None:
        """Translate boolean/derived criteria into SQL column comparisons.

        Every RHS column is validated by the DB layer against its whitelist
        before interpolation, so the LLM can never inject SQL.

        Multiple price-direction criteria (SMA trend, 52w proximity,
        Bollinger) are ANDed together on ``current_price`` (the DB layer
        supports a list of conditions per column), and ONLY conditions that
        are actually enforced are echoed in ``applied`` — the echo is never
        allowed to claim a filter that didn't run.

        Args:
            query: The interpreted query with optional derived fields.
            db_filters: Filter dict being built (mutated in place).
            applied: Human-readable list of applied filters (mutated).
            relaxable: Optional list collecting {column, cond, label,
                coverage_col} entries for the 0-match auto-relax rescue
                (mutated). None = not relaxing.
            skip_labels: Optional set of filter labels to OMIT entirely
                (contradiction-resolved or auto-relaxed criteria).
        """
        skip_labels = skip_labels or set()
        price_conds: List[Any] = []

        def _record(col: str, cond: Any, label: str, cov_col: str) -> None:
            if relaxable is not None:
                relaxable.append({
                    "column": col, "cond": cond, "label": label,
                    "coverage_col": cov_col,
                })

        if query.above_sma_50 and not query.below_sma_50:
            label = "price > SMA50"
            if label not in skip_labels:
                price_conds.append(("gt_col", "sma_50"))
                _record("current_price", ("gt_col", "sma_50"), label, "sma_50")
                applied.append(label)
        elif query.below_sma_50 and not query.above_sma_50:
            label = "price < SMA50"
            if label not in skip_labels:
                price_conds.append(("lt_col", "sma_50"))
                _record("current_price", ("lt_col", "sma_50"), label, "sma_50")
                applied.append(label)

        if query.above_sma_200 and not query.below_sma_200:
            label = "price > SMA200"
            if label not in skip_labels:
                price_conds.append(("gt_col", "sma_200"))
                _record("current_price", ("gt_col", "sma_200"), label, "sma_200")
                applied.append(label)
        elif query.below_sma_200 and not query.above_sma_200:
            label = "price < SMA200"
            if label not in skip_labels:
                price_conds.append(("lt_col", "sma_200"))
                _record("current_price", ("lt_col", "sma_200"), label, "sma_200")
                applied.append(label)

        if query.near_52w_high and not query.near_52w_low:
            label = "price within 5% of 52w high"
            if label not in skip_labels:
                price_conds.append(("gte_frac_col", "high_52w", 0.95))
                _record("current_price", ("gte_frac_col", "high_52w", 0.95),
                        label, "high_52w")
                applied.append(label)
        elif query.near_52w_low and not query.near_52w_high:
            label = "price within 5% of 52w low"
            if label not in skip_labels:
                price_conds.append(("lte_frac_col", "low_52w", 1.05))
                _record("current_price", ("lte_frac_col", "low_52w", 1.05),
                        label, "low_52w")
                applied.append(label)

        if query.bollinger_breakout and not query.bollinger_oversold:
            label = "price > upper Bollinger"
            if label not in skip_labels:
                price_conds.append(("gt_col", "bollinger_upper"))
                _record("current_price", ("gt_col", "bollinger_upper"),
                        label, "bollinger_upper")
                applied.append(label)
        elif query.bollinger_oversold and not query.bollinger_breakout:
            label = "price < lower Bollinger"
            if label not in skip_labels:
                price_conds.append(("lt_col", "bollinger_lower"))
                _record("current_price", ("lt_col", "bollinger_lower"),
                        label, "bollinger_lower")
                applied.append(label)

        if price_conds:
            # Merge with any numeric current_price filter the LLM already
            # produced (e.g. min_price → ("gt", 100)) — a trend condition
            # must AND with it, never silently replace it. Both conditions
            # coexist via the DB layer's multi-condition list form.
            existing = db_filters.get("current_price")
            if existing is not None:
                if isinstance(existing, list):
                    for c in existing:
                        if c not in price_conds:
                            price_conds.append(c)
                elif existing not in price_conds:
                    price_conds.append(existing)
            # Deduplicate identical conditions (e.g. above_sma_50 + bullish
            # momentum would both be 'price > sma_50') before setting.
            uniq: List[Any] = []
            for c in price_conds:
                if c not in uniq:
                    uniq.append(c)
            db_filters["current_price"] = uniq[0] if len(uniq) == 1 else uniq

        if query.golden_cross and not query.death_cross:
            label = "SMA50 > SMA200 (golden cross)"
            if label not in skip_labels:
                db_filters["sma_50"] = ("gt_col", "sma_200")
                _record("sma_50", ("gt_col", "sma_200"), label, "sma_50")
                applied.append(label)
        elif query.death_cross and not query.golden_cross:
            label = "SMA50 < SMA200 (death cross)"
            if label not in skip_labels:
                db_filters["sma_50"] = ("lt_col", "sma_200")
                _record("sma_50", ("lt_col", "sma_200"), label, "sma_50")
                applied.append(label)

        if query.macd_bullish:
            label = "MACD > 0 (bullish)"
            if label not in skip_labels:
                db_filters["macd_12_26"] = ("gt", 0)
                _record("macd_12_26", ("gt", 0), label, "macd_12_26")
                applied.append(label)

    def _momentum_filters(
        self,
        query: InterpretedQuery,
        db_filters: Dict[str, Any],
        applied: List[str],
        relaxable: Optional[List[Dict[str, Any]]] = None,
        skip_labels: Optional[set] = None,
    ) -> None:
        """Add SQL filters for the momentum direction (real trend).

        Tier 1 upgrade: momentum is now a *trend composite* — bullish means
        price above its 50-day moving average (a sustained uptrend), not a
        one-day RSI reading. Bearish mirrors below. This runs in SQL so
        pagination counts stay exact.

        Momentum coexists with any derived price filter (both are ANDed on
        ``current_price`` via the DB layer's multi-condition support) and is
        ALWAYS enforced when requested — so echoing it is always honest.

        Args:
            query: The interpreted query.
            db_filters: Filter dict being built (mutated in place).
            applied: Human-readable applied-filter list (mutated).
            relaxable: Optional list collecting relaxable entries for the
                0-match auto-relax rescue (mutated).
            skip_labels: Optional set of labels to omit (relaxed criteria).
        """
        skip_labels = skip_labels or set()
        if query.momentum == "bullish":
            label = "momentum: price > SMA50"
            cond: Any = ("gt_col", "sma_50")
        elif query.momentum == "bearish":
            label = "momentum: price < SMA50"
            cond = ("lt_col", "sma_50")
        else:
            return
        if label in skip_labels:
            return
        applied.append(label)
        if relaxable is not None:
            relaxable.append({
                "column": "current_price", "cond": cond, "label": label,
                "coverage_col": "sma_50",
            })

        existing = db_filters.get("current_price")
        if existing is None:
            db_filters["current_price"] = cond
        elif isinstance(existing, list):
            if cond not in existing:
                existing.append(cond)
        else:
            if existing != cond:
                db_filters["current_price"] = [existing, cond]

    # ── Composite score (Tier 3) ──────────────────────────────────────────

    def _score_results(
        self,
        results: List[ScreenerResult],
        stats: Dict[str, Dict[str, float]],
    ) -> None:
        """Attach a 0-100 composite score to each result (in place).

        Factors are z-scored against the MATCH SET's own distribution
        (mean/std from ``aggregate_stats``), weighted by ``SCORE_WEIGHTS``,
        and mapped through a logistic so the output lands in 0-100.

        Args:
            results: Result list to mutate.
            stats: Per-column {mean, std, valid} over the match set.
        """
        for r in results:
            components: Dict[str, Any] = {}

            # Value — P/E, P/S, EV/EBITDA (lower is better)
            value_z: List[float] = []
            for col in ("pe_ratio", "ps_ratio", "ev_ebitda"):
                raw = getattr(r, col)
                s = stats.get(col)
                if raw is not None and s and s["std"] > 0:
                    z = (raw - s["mean"]) / s["std"]
                    value_z.append(-z)  # lower raw → higher score
            if value_z:
                components["value"] = round(sum(value_z) / len(value_z), 2)

            # Momentum — RSI, daily change, EPS growth
            mom_z: List[float] = []
            for col in ("rsi_14", "price_change_pct", "eps_growth_ttm"):
                raw = getattr(r, col)
                s = stats.get(col)
                if raw is not None and s and s["std"] > 0:
                    mom_z.append((raw - s["mean"]) / s["std"])
            # SMA trend adds a discrete momentum bonus
            if r.sma_50 and r.current_price:
                if r.current_price > r.sma_50:
                    mom_z.append(0.5)
                else:
                    mom_z.append(-0.5)
            if mom_z:
                components["momentum"] = round(sum(mom_z) / len(mom_z), 2)

            # Quality — ROE, net margin, low beta
            qual_z: List[float] = []
            for col in ("roe_ttm", "net_margin_ttm"):
                raw = getattr(r, col)
                s = stats.get(col)
                if raw is not None and s and s["std"] > 0:
                    qual_z.append((raw - s["mean"]) / s["std"])
            if r.beta is not None and stats.get("beta", {}).get("std", 0) > 0:
                qual_z.append(-(r.beta - stats["beta"]["mean"]) / stats["beta"]["std"])
            if qual_z:
                components["quality"] = round(sum(qual_z) / len(qual_z), 2)

            # Combine weighted → logistic(0-100)
            total_z = 0.0
            weight_used = 0.0
            for factor, w in SCORE_WEIGHTS.items():
                if factor in components:
                    total_z += components[factor] * w
                    weight_used += w
            if weight_used > 0:
                total_z /= weight_used  # normalize by actually-used weights
            # logistic: 50 + 50 * tanh(z/2) → ~(0,100), 50 = average
            r.score = round(50 + 50 * math.tanh(total_z / 2), 1)
            r.score_components = components

    # ── Why matched (Tier 3) ──────────────────────────────────────────────

    def _explain_match(
        self, r: ScreenerResult, applied: List[str]
    ) -> List[str]:
        """Return a human-readable list of why this stock matched."""
        why: List[str] = []
        for label in applied:
            why.append(label)
        # Note the numeric filters the record satisfies
        checks = [
            ("pe_ratio", r.pe_ratio, lambda v: f"P/E {v:.1f}"),
            ("rsi_14", r.rsi_14, lambda v: f"RSI {v:.1f}"),
            ("market_cap", r.market_cap, lambda v: f"Mkt cap ${v/1e9:.2f}B"),
            ("dividend_yield", r.dividend_yield, lambda v: f"Div yield {v:.2f}%"),
            ("current_price", r.current_price, lambda v: f"Price ${v:.2f}"),
        ]
        for col, val, fmt in checks:
            if val is None:
                continue
            why.append(f"{col} = {fmt(val)}")
        return why[:8]

    # ── Main entry ────────────────────────────────────────────────────────

    def screen(
        self,
        query: InterpretedQuery,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "market_cap",
        sort_desc: bool = True,
    ) -> ScreeningReport:
        """Execute a screening query against the stock database.

        Supports offset-based pagination: ``total_matches`` is the TRUE
        count of matching rows (via ``count_stocks`` — same WHERE clause
        as the page query, no LIMIT), and ``has_more`` tells the caller
        whether another page exists.

        Zero-match rescue (Tier 3): a screen that returns 0 is NOT left
        as a bare empty list. First, contradictory criteria are resolved
        (e.g. "oversold + rising momentum" — RSI≤X and price>SMA50 barely
        coexist), then up to ``RELAX_MAX_FILTERS`` least-covered filters
        are dropped and the screen re-runs. The final report carries
        ``data_quality.message`` (forwarded to the API's ``message`` field)
        explaining what was dropped and why.

        Args:
            query: The interpreted user query with filter criteria.
            limit: Maximum number of results to return.
            offset: Number of matching rows to skip (page start).
            sort_by: Sort key (see SORTABLE_COLUMNS). "score" sorts by the
                Tier 3 composite score (computed post-query).
            sort_desc: True = descending (largest first).

        Returns:
            A ScreeningReport with matching stocks.
        """
        # An LLM interpretation failure must NEVER silently fall through to
        # the completeness guard and return the whole universe — a garbled
        # prompt would otherwise dump 10,000 stocks as if it were a valid
        # screen. The interpreter already sets ``query.error``; surface it
        # (generic copy — VULN-002: no raw exception text in the response)
        # instead of pretending the query matched everything.
        if query.error:
            logger.warning(
                "Screen aborted — interpretation failed: %s", query.error
            )
            return ScreeningReport(
                query=query,
                results=[],
                total_matches=0,
                limit=limit,
                offset=offset,
                has_more=False,
                sort_by=sort_by,
                sort_desc=sort_desc,
                applied_filters=[],
                data_quality={
                    "error": "Could not interpret your query. Please try rephrasing."
                },
            )

        # Tier 3 #7: contradiction resolver — a query that is simultaneously
        # "oversold" (RSI ≤ X) and "rising momentum" (price > SMA50) asks for
        # two conditions that almost never coexist (oversold stocks sit
        # BELOW their average). Keep the stronger signal (the explicit
        # numeric RSI) and drop the trend condition, noting it in the
        # report so the user knows the screen was adjusted.
        skip_labels, contradiction_notes = self._resolve_contradictions(query)

        db_filters, applied, relaxable = self._build_filter_set(query, skip_labels)
        report = self._run_screen(
            query, db_filters, applied, limit, offset, sort_by, sort_desc
        )

        # Tier 3 #6: auto-relax — a 0-match screen drops the least-covered
        # filter and re-runs (up to RELAX_MAX_FILTERS), returning the
        # nearest matches instead of an empty list. Guards (freshness /
        # completeness) are never dropped — only the interpreter's own
        # criteria, ordered by live column coverage (least-covered first,
        # so a dead column like avg_volume is relaxed before a healthy one).
        relaxed_labels: List[str] = []
        if (
            report.total_matches == 0
            and relaxable
            and not report.data_quality.get("error")
        ):
            coverage: Dict[str, int] = {}
            try:
                cov = self._db.column_coverage(
                    columns=[r["coverage_col"] for r in relaxable]
                )
                coverage = {k: v for k, v in cov.items()}
            except Exception:
                coverage = {}
            ordered = sorted(
                relaxable, key=lambda r: coverage.get(r["coverage_col"], 0)
            )
            for entry in ordered:
                if len(relaxed_labels) >= RELAX_MAX_FILTERS:
                    break
                relaxed_labels.append(entry["label"])
                db_filters, applied, _ = self._build_filter_set(
                    query, skip_labels | set(relaxed_labels)
                )
                report = self._run_screen(
                    query, db_filters, applied, limit, offset, sort_by, sort_desc
                )
                if report.total_matches > 0:
                    break

        if relaxed_labels:
            report.data_quality["relaxed_from"] = relaxed_labels
            if report.total_matches > 0:
                relax_msg = (
                    f"Relaxed {len(relaxed_labels)} filter(s) "
                    f"({', '.join(relaxed_labels)}) to show the nearest matches."
                )
            else:
                relax_msg = (
                    f"Relaxed {len(relaxed_labels)} filter(s) "
                    f"({', '.join(relaxed_labels)}) but still found 0 stocks."
                )
            report.data_quality["message"] = relax_msg
        self._note_partial_coverage(report, db_filters)
        if contradiction_notes:
            report.data_quality["contradiction_resolved"] = contradiction_notes
            existing = report.data_quality.get("message")
            notes = "; ".join(contradiction_notes)
            report.data_quality["message"] = (
                f"{notes} {existing}" if existing else notes
            )

        return report

    def _note_partial_coverage(self, report: "ScreeningReport", db_filters: Dict[str, Any]) -> None:
        """Day 14: say how much of the universe a partial column covers."""
        cols = [c for c in PARTIAL_COVERAGE_COLUMNS if c in db_filters]
        if not cols or report.data_quality.get("error"):
            return
        try:
            cov = self._db.column_coverage(columns=cols + ["symbol"])
        except Exception:  # noqa: BLE001 — a note must never break a screen
            return
        total = int(cov.get("symbol") or 0)
        partial = {c: {"known": int(cov.get(c) or 0), "total": total}
                   for c in cols if total and int(cov.get(c) or 0) < total}
        if not partial:
            return
        report.data_quality["partial_coverage"] = partial
        notes = "; ".join(
            f"{PARTIAL_COVERAGE_COLUMNS[c]} is known for {v['known']:,} of {v['total']:,} stocks "
            f"(the largest), so only those can match"
            for c, v in partial.items()
        )
        existing = report.data_quality.get("message")
        report.data_quality["message"] = f"{existing} {notes}." if existing else f"{notes}."

    def _resolve_contradictions(
        self, query: InterpretedQuery
    ) -> tuple:
        """Tier 3 #7: resolve mutually-exclusive interpreter criteria.

        An oversold reading (RSI ≤ X) and a bullish trend (price > SMA50,
        via above_sma_50 or bullish momentum) cannot meaningfully coexist —
        oversold stocks sit below their averages, so the intersection is
        near-empty. The explicit numeric signal (RSI) is kept and the trend
        condition is skipped; the dropped labels are returned so the caller
        can explain them to the user.

        Returns:
            (skip_labels, notes): labels to omit from the filter set, and
            human-readable notes describing what was dropped and why.
        """
        skip: set = set()
        notes: List[str] = []

        rsi_low_criteria = [
            c for c in query.filters
            if c.field == "rsi_14" and c.operator.value in ("lt", "lte")
        ]
        if rsi_low_criteria:
            low_threshold = rsi_low_criteria[0].value
            if query.above_sma_50:
                skip.add("price > SMA50")
                notes.append(
                    f"dropped 'price > SMA50' — contradicts oversold RSI ≤ "
                    f"{low_threshold} (oversold stocks sit below their average)"
                )
            if query.momentum == "bullish":
                skip.add("momentum: price > SMA50")
                notes.append(
                    f"dropped 'momentum: price > SMA50' — contradicts "
                    f"oversold RSI ≤ {low_threshold}"
                )

        rsi_high_criteria = [
            c for c in query.filters
            if c.field == "rsi_14" and c.operator.value in ("gt", "gte")
        ]
        if rsi_high_criteria:
            high_threshold = rsi_high_criteria[0].value
            if query.below_sma_50:
                skip.add("price < SMA50")
                notes.append(
                    f"dropped 'price < SMA50' — contradicts overbought RSI ≥ "
                    f"{high_threshold}"
                )
            if query.momentum == "bearish":
                skip.add("momentum: price < SMA50")
                notes.append(
                    f"dropped 'momentum: price < SMA50' — contradicts "
                    f"overbought RSI ≥ {high_threshold}"
                )

        return skip, notes

    def _build_filter_set(
        self, query: InterpretedQuery, skip_labels: set
    ) -> tuple:
        """Translate a query into the SQL filter dict + applied echo.

        Also returns the ``relaxable`` list — every interpreter-produced
        criterion (numeric, derived, momentum) with its human label and the
        DB column whose coverage determines relaxation priority. Criteria
        whose label is in ``skip_labels`` (contradiction-resolved or
        auto-relaxed) are omitted entirely.

        Returns:
            (db_filters, applied, relaxable): the filter dict, the echo
            list, and the relaxable entries for the 0-match rescue.
        """
        db_filters: Dict[str, Any] = {}
        applied: List[str] = []
        relaxable: List[Dict[str, Any]] = []

        for criterion in query.filters:
            cond: Any = (criterion.operator.value, criterion.value)
            # The interpreter speaks market cap in BILLIONS (human-friendly:
            # "small cap" = 0.3-2.0), but the DB stores DOLLARS (canonical,
            # set at the fetch boundary). Convert once here so filters
            # compare dollars-to-dollars — matching the fetch + display
            # layers. Defensive: coerce str/float/int so an LLM that emits
            # "2" (string) still converts. NOTE: the `applied` echo below
            # keeps the original billions value for readability.
            if criterion.field == "market_cap":
                try:
                    cond = (criterion.operator.value, float(criterion.value) * 1_000_000_000)
                except (TypeError, ValueError):
                    logger.warning(
                        "Non-numeric market_cap filter value %r — leaving as-is",
                        criterion.value,
                    )

            op_symbol = {
                "lt": "<", "lte": "≤", "gt": ">", "gte": "≥",
                "eq": "=", "neq": "≠",
            }.get(criterion.operator.value, criterion.operator.value)
            label = f"{criterion.field} {op_symbol} {criterion.value}"
            if label in skip_labels:
                continue

            # Same-column criteria (e.g. min_pe + max_pe, or the min+max
            # market-cap bounds of "small cap") must ALL be enforced — the
            # DB layer's multi-condition list form ANDs them. A bare dict
            # assignment would silently drop all but the last bound.
            existing = db_filters.get(criterion.field)
            if existing is None:
                db_filters[criterion.field] = cond
            elif isinstance(existing, list):
                if cond not in existing:
                    existing.append(cond)
            else:
                if existing != cond:
                    db_filters[criterion.field] = [existing, cond]

            applied.append(label)
            relaxable.append({
                "column": criterion.field, "cond": cond, "label": label,
                "coverage_col": criterion.field,
            })

        # Add sector/industry if specified (the query's subject, never relaxed)
        if query.sector:
            db_filters["sector"] = query.sector
            applied.append(f"sector = {query.sector}")
        if query.industry:
            db_filters["industry"] = query.industry
            applied.append(f"industry = {query.industry}")

        # Tier 1: derived/boolean criteria → SQL expressions
        self._apply_derived_filters(
            query, db_filters, applied, relaxable, skip_labels
        )

        # Tier 1: momentum → real trend composite in SQL
        self._momentum_filters(
            query, db_filters, applied, relaxable, skip_labels
        )

        return db_filters, applied, relaxable

    def _run_screen(
        self,
        query: InterpretedQuery,
        db_filters: Dict[str, Any],
        applied: List[str],
        limit: int,
        offset: int,
        sort_by: str,
        sort_desc: bool,
    ) -> ScreeningReport:
        """Execute the already-built filter set against the DB.

        Adds the non-relaxable guards (freshness exclusion + minimum
        completeness), runs the count + page query, computes the composite
        score, and assembles the ScreeningReport. Shared by the main screen
        and every auto-relax re-run.
        """
        # Freshness guard — the flag-only ``is_stale`` marker is NOT enough:
        # it still returned the row (which is exactly how stale pink sheets
        # kept appearing in screens). Exclude stale rows in SQL so
        # ``count_stocks`` and the page share the SAME match set. The cutoff
        # is a UTC ISO string — the DB layer parameterizes it, and the test
        # fake compares ISO strings lexicographically, so both stay correct.
        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(days=STALE_EXCLUSION_DAYS)
        ).isoformat()
        if "updated_at" not in db_filters:
            db_filters["updated_at"] = ("gt", cutoff_iso)
        applied.append(
            f"data fresher than {STALE_EXCLUSION_DAYS} days (freshness guard)"
        )

        # Tier 3: minimum-completeness guard — rows missing BOTH price and
        # market cap can't be ranked or displayed meaningfully.
        has_price = "current_price" in db_filters
        if not has_price:
            db_filters["current_price"] = ("neq", None)
        db_filters.setdefault("market_cap", ("neq", None))
        applied.append("has price + market cap (completeness guard)")

        sort_in_db = sort_by if sort_by != "score" else "market_cap"
        if sort_by not in SORTABLE_COLUMNS:
            logger.warning("Unknown sort_by %r — falling back to market_cap", sort_by)
            sort_by, sort_in_db = "market_cap", "market_cap"

        score_sort = sort_by == "score"

        try:
            total_matches = self._db.count_stocks(filters=db_filters)

            # Tier 3: composite score is computed against the MATCH SET, so
            # ranking by it must be GLOBAL, not a re-order of the fetched
            # page. When sort_by="score" we fetch the whole match set (capped
            # by SCORE_SORT_MAX_WINDOW) and slice the page in Python — the DB
            # page for score-sort is taken AFTER ranking.
            if score_sort:
                window = min(total_matches, SCORE_SORT_MAX_WINDOW)
                records = self._db.query_stocks(
                    filters=db_filters,
                    limit=max(window, limit),
                    offset=0,
                    sort_by="market_cap",
                    sort_desc=True,
                )
            else:
                records = self._db.query_stocks(
                    filters=db_filters,
                    limit=limit,
                    offset=offset,
                    sort_by=sort_in_db,
                    sort_desc=sort_desc,
                )

            results = [
                ScreenerResult.from_db_record(record) for record in records
            ]

            # Tier 3: composite score — z-score against the match set
            score_cols = [
                "pe_ratio", "ps_ratio", "ev_ebitda", "rsi_14",
                "price_change_pct", "eps_growth_ttm", "roe_ttm",
                "net_margin_ttm", "beta",
            ]
            stats: Dict[str, Dict[str, float]] = {}
            if results:
                stats = self._db.aggregate_stats(filters=db_filters, columns=score_cols)
                self._score_results(results, stats)

            # Tier 3: why-matched + staleness
            for r in results:
                r.why_matched = self._explain_match(r, applied)
                age = self._row_age_minutes(r)
                r.data_age_minutes = age
                r.is_stale = age is not None and age > STALE_AFTER_MINUTES

            # Rank by score globally, then take the page.
            if score_sort:
                results.sort(
                    key=lambda r: (r.score if r.score is not None else -1),
                    reverse=sort_desc,
                )
                page = results[offset: offset + limit]
                # has_more reflects the RANKED set (true global paging)
                has_more = (offset + limit) < len(results)
            else:
                page = results
                # has_more: compare against the DB page SIZE (len(records),
                # NOT any post-filtered len(results)) so pagination stays
                # exact for DB-side sorting.
                has_more = (offset + len(records)) < total_matches

            data_quality = self._data_quality_note()
            data_quality["stale_exclusion_days"] = STALE_EXCLUSION_DAYS
            if score_sort and total_matches > SCORE_SORT_MAX_WINDOW:
                data_quality["score_window"] = SCORE_SORT_MAX_WINDOW
                data_quality["score_note"] = (
                    f"Composite ranking covers the top {SCORE_SORT_MAX_WINDOW} "
                    "matches by market cap (window cap); beyond that the "
                    "ranking is best-effort."
                )

            return ScreeningReport(
                query=query,
                results=page,
                total_matches=total_matches,
                limit=limit,
                offset=offset,
                has_more=has_more,
                sort_by=sort_by,
                sort_desc=sort_desc,
                applied_filters=applied,
                data_quality=data_quality,
            )

        except Exception as e:
            # VULN-002: data_quality.error is a designed response field the
            # public /screen endpoint forwards into the HTTP body — it must
            # never carry raw exception text. Log the real error server-side;
            # the client gets a generic note (same contract as the HTTP 502s).
            logger.error("Screening query failed: %s", e)
            return ScreeningReport(
                query=query,
                results=[],
                total_matches=0,
                limit=limit,
                offset=offset,
                has_more=False,
                sort_by=sort_by,
                sort_desc=sort_desc,
                applied_filters=applied,
                data_quality={"error": "Screening query failed internally."},
            )

    def _row_age_minutes(self, r: ScreenerResult) -> Optional[float]:
        """Minutes since this row's updated_at (None if unavailable)."""
        if r.updated_at is None:
            return None
        try:
            ts = r.updated_at
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0.0, (now - ts).total_seconds() / 60.0)
        except Exception:
            return None

    def _data_quality_note(self) -> Dict[str, Any]:
        """Best-effort data freshness note (never raises)."""
        try:
            return self._db.data_freshness(max_age_minutes=STALE_AFTER_MINUTES)
        except Exception as e:
            logger.debug("data_freshness unavailable: %s", e)
            return {"error": "freshness unavailable"}

    def screen_from_natural_language(
        self,
        raw_query: str,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "market_cap",
        sort_desc: bool = True,
    ) -> ScreeningReport:
        """Full pipeline: interpret NL → filter database → return results.

        This is the primary entry point for the Stratoscan screener.

        Args:
            raw_query: A plain-English stock screening request.
            limit: Maximum number of results to return.
            offset: Number of matching rows to skip (page start).
            sort_by: Sort key (see SORTABLE_COLUMNS).
            sort_desc: True = descending.

        Returns:
            A ScreeningReport with matching stocks.
        """
        from src.interpreter import interpret_query as interpret

        interpreted = interpret(raw_query)
        logger.info(
            "Interpreting query: '%s' → %d filters, sector=%s, derived=%s",
            raw_query,
            len(interpreted.filters),
            interpreted.sector,
            interpreted.has_derived_criteria,
        )

        return self.screen(
            interpreted,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_desc=sort_desc,
        )
