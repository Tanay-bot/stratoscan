"""London Strategic Edge vault as an alternative backtest history source.

Why this exists
---------------
The backtester's history comes from yfinance, which scrapes Yahoo and hands
back an ``Adj Close`` column for free. The LSE vault is an *official* API with
deeper history (US stocks to 2003), a free key, and a generous export budget
— but it serves **raw, unadjusted** candles. Dividends and splits are separate
event datasets. Nothing in the LSE client or docs mentions adjusted prices.

That matters more than it sounds. ``backtester._normalize_ohlcv`` derives the
simulator's price basis from the per-day ratio ``Adj Close / Close``; the sim
trades adjusted prices so a 2:1 split never looks like a -50% crash. Feed it
raw prices and every backtest is silently, plausibly wrong.

So this module's real job is not "call an API" — it is to **rebuild the
adjusted series from raw prices plus corporate actions**, and hand back a
frame shaped exactly like yfinance's. Everything downstream (normalize,
store, simulate) then works unchanged, and the frozen backtest math in
``tests/test_backtest_golden.py`` is untouched.

Licensing
---------
LSE's terms permit use "for your own research, trading and model training,
including for commercial purposes" but forbid redistributing the data or
making it available to third parties. Backfilling your own backtest database
is squarely the permitted use. Serving LSE prices to end users is not — do
not wire this into the screener or any user-facing price display.

Status
------
The HTTP layer has never been exercised against the live API from this repo
(no key committed, by design). ``verify_lse_vs_yfinance.py`` is the probe that
proves equivalence against yfinance before you trust it; run that with a real
key before switching any backfill over.

Pure stdlib HTTP (urllib), matching the house pattern in ``emails.py`` and
``redis_ratelimit.py`` — no new runtime dependency.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.config import settings

logger = logging.getLogger(__name__)

VAULT_URL = "https://api.londonstrategicedge.com/vault"

# The vault caps interactive reads at this many rows per call. Ten years of
# DAILY bars is ~2,520 rows, so one call covers a full symbol — no paging
# needed for the backtester's use. Intraday timeframes would need paging.
MAX_ROWS_PER_CALL = 5000

# LSE has never published its field names for these datasets and explicitly
# removed fixed limits/shapes from its client docstrings (see its CHANGELOG
# v0.13.0). Rather than guess one name, accept every plausible spelling and
# let verify_lse_vs_yfinance.py report the real shape on first live run.
_DIV_AMOUNT_KEYS = ("amount", "dividend", "cash_amount", "value", "dividend_amount")
_SPLIT_RATIO_KEYS = ("ratio", "split_ratio", "factor")
_SPLIT_NUM_KEYS = ("numerator", "to_factor", "split_to")
_SPLIT_DEN_KEYS = ("denominator", "for_factor", "split_from")
_DATE_KEYS = ("effective_date", "ex_date", "date", "timestamp")


class LSEError(RuntimeError):
    """An LSE vault request failed. Carries no response body (no leaks)."""


def is_configured() -> bool:
    """True when an LSE API key is present. Nothing here runs without one."""
    return bool(getattr(settings, "lse_api_key", ""))


def _get(path: str, params: List[Tuple[str, Any]]) -> List[dict]:
    """GET a vault endpoint and return its rows.

    Raises LSEError on any non-2xx or transport failure. The upstream body is
    logged, never propagated, so an API error can't leak into an API response
    (golden rule #12).
    """
    key = getattr(settings, "lse_api_key", "")
    if not key:
        raise LSEError("LSE_API_KEY is not configured")

    query = urllib.parse.urlencode([(k, v) for k, v in params if v is not None])
    url = f"{VAULT_URL}{path}?{query}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        timeout = float(getattr(settings, "lse_timeout", 60.0))
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        body = ""
        try:
            body = exc.read().decode("utf-8")[:400]
        except Exception:
            pass
        logger.error("LSE vault %s failed: HTTP %s %s", path, exc.code, body)
        raise LSEError(f"LSE vault request failed (HTTP {exc.code})") from None
    except Exception as exc:  # pragma: no cover - network path
        logger.error("LSE vault %s failed: %s", path, exc)
        raise LSEError("LSE vault request failed") from None

    # The vault returns either a bare list or {"rows": [...]}; accept both.
    if isinstance(payload, dict):
        rows = payload.get("rows") or payload.get("data") or []
    else:
        rows = payload
    return rows if isinstance(rows, list) else []


def _first_key(row: Dict[str, Any], keys: Tuple[str, ...]) -> Optional[Any]:
    """Return the first present, non-null value among ``keys``."""
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return None


def _row_date(row: Dict[str, Any]) -> Optional[pd.Timestamp]:
    raw = _first_key(row, _DATE_KEYS)
    if raw is None:
        return None
    try:
        return pd.Timestamp(str(raw)).tz_localize(None).normalize()
    except (ValueError, TypeError):
        return None


def _split_ratio(row: Dict[str, Any]) -> Optional[float]:
    """Split ratio as a multiplier: 2.0 for a 2-for-1 (share count doubles)."""
    direct = _first_key(row, _SPLIT_RATIO_KEYS)
    if direct is not None:
        try:
            val = float(direct)
            return val if val > 0 else None
        except (TypeError, ValueError):
            return None
    num, den = _first_key(row, _SPLIT_NUM_KEYS), _first_key(row, _SPLIT_DEN_KEYS)
    if num is None or den is None:
        return None
    try:
        num_f, den_f = float(num), float(den)
        return num_f / den_f if den_f else None
    except (TypeError, ValueError):
        return None


def build_adjusted_frame(
    candles: List[dict],
    dividends: Optional[List[dict]] = None,
    splits: Optional[List[dict]] = None,
) -> pd.DataFrame:
    """Rebuild a yfinance-shaped frame from raw candles + corporate actions.

    This is the load-bearing function. ``_normalize_ohlcv`` divides
    ``Adj Close`` by ``Close`` to get a per-day adjustment ratio, so
    ``Adj Close`` must be the split- and dividend-adjusted close on the same
    basis yfinance uses (CRSP-style back-adjustment).

    The method: each corporate action effective on bar ``i`` scales every bar
    STRICTLY BEFORE it.

      * a split with ratio ``r`` scales prior bars by ``1 / r`` — a 2-for-1
        halves the pre-split prices so the series is continuous
      * a cash dividend ``D`` with ex-date on bar ``i`` scales prior bars by
        ``1 - D / close[i-1]`` — the close of the bar BEFORE the ex-date,
        which is the convention yfinance follows

    The cumulative factor at bar ``i`` is the product of the event factors of
    every LATER bar, so the most recent bar always has factor 1.0 and
    ``Adj Close[-1] == Close[-1]``. That invariant is asserted in
    ``tests/test_lse_source.py`` against a hand-computed example.

    Args:
        candles: Raw vault rows (``timestamp``/``ts``, open, high, low,
            close, volume). Order does not matter; sorted here.
        dividends: Dividend event rows, or None.
        splits: Split event rows, or None.

    Returns:
        DataFrame indexed by date with Open/High/Low/Close/Adj Close/Volume/
        Dividends/Stock Splits — the exact shape
        ``backtester.fetch_historical_data`` returns. Empty frame if there
        are no usable candles.
    """
    rows: List[Dict[str, Any]] = []
    for c in candles or []:
        dt = _row_date(c)
        if dt is None:
            continue
        try:
            close = float(c.get("close"))
        except (TypeError, ValueError):
            continue
        if not np.isfinite(close):
            continue

        def _num(key: str, fallback: float) -> float:
            try:
                val = float(c.get(key))
                return val if np.isfinite(val) else fallback
            except (TypeError, ValueError):
                return fallback

        rows.append({
            "date": dt,
            "Open": _num("open", close),
            "High": _num("high", close),
            "Low": _num("low", close),
            "Close": close,
            "Volume": _num("volume", 0.0),
        })

    if not rows:
        return pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Adj Close", "Volume",
                     "Dividends", "Stock Splits"]
        )

    df = pd.DataFrame(rows).drop_duplicates(subset="date", keep="last")
    df = df.sort_values("date").set_index("date")

    # ── event factors, one per bar ────────────────────────────────────────
    n = len(df)
    closes = df["Close"].to_numpy(dtype=float)
    event_factor = np.ones(n, dtype=float)
    div_col = np.zeros(n, dtype=float)
    split_col = np.ones(n, dtype=float)
    pos = {ts: i for i, ts in enumerate(df.index)}

    for row in splits or []:
        dt, ratio = _row_date(row), _split_ratio(row)
        if dt is None or not ratio or dt not in pos:
            continue
        i = pos[dt]
        event_factor[i] /= ratio
        split_col[i] = ratio

    for row in dividends or []:
        dt = _row_date(row)
        raw_amt = _first_key(row, _DIV_AMOUNT_KEYS)
        if dt is None or raw_amt is None or dt not in pos:
            continue
        try:
            amount = float(raw_amt)
        except (TypeError, ValueError):
            continue
        i = pos[dt]
        div_col[i] = amount
        # The bar before the ex-date sets the basis. A dividend on the very
        # first bar has no prior close, so it cannot scale anything.
        if i == 0:
            continue
        prev_close = closes[i - 1]
        if prev_close > 0 and np.isfinite(prev_close):
            event_factor[i] *= max(0.0, 1.0 - amount / prev_close)

    # Cumulative product of every LATER bar's factor → newest bar is 1.0.
    # reverse → cumprod → shift by one → reverse back.
    rev = event_factor[::-1]
    cum_rev = np.cumprod(rev)
    factor_rev = np.concatenate(([1.0], cum_rev[:-1]))
    adj_factor = factor_rev[::-1]

    df["Adj Close"] = df["Close"].to_numpy(dtype=float) * adj_factor
    df["Dividends"] = div_col
    df["Stock Splits"] = split_col
    return df[["Open", "High", "Low", "Close", "Adj Close", "Volume",
               "Dividends", "Stock Splits"]]


def fetch_historical_data_lse(
    symbol: str,
    years: int = 10,
    start_date: Optional[str] = None,
) -> Optional[pd.DataFrame]:
    """Drop-in replacement for ``backtester.fetch_historical_data``.

    Same signature, same window semantics (fetch from the EARLIER of
    ``start_date`` and today − ``years``, so a requested window that starts
    before the anchor is genuinely covered), same returned shape. Returns
    None on failure rather than raising — the documented failure signal the
    backtester and the failure drills both rely on.
    """
    if not is_configured():
        logger.warning("LSE source requested but LSE_API_KEY is not set")
        return None

    end = datetime.now()
    anchor = end - timedelta(days=years * 365)
    if start_date:
        try:
            requested = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            requested = anchor
        fetch_start = min(requested, anchor)
    else:
        fetch_start = anchor
    start_iso = fetch_start.strftime("%Y-%m-%d")

    try:
        candles = _get("/candles", [
            ("symbol", symbol), ("timeframe", "1d"), ("order", "asc"),
            ("limit", MAX_ROWS_PER_CALL), ("start", start_iso),
        ])
        # Corporate actions are best-effort: without them the series is raw,
        # which would silently mis-price every split. Fail the fetch instead.
        dividends = _get("/dividends", [
            ("symbol", symbol), ("order", "asc"),
            ("limit", MAX_ROWS_PER_CALL), ("start", start_iso),
        ])
        splits = _get("/stock_splits", [
            ("symbol", symbol), ("order", "asc"),
            ("limit", MAX_ROWS_PER_CALL), ("start", start_iso),
        ])
    except LSEError as exc:
        logger.error("LSE fetch failed for %s: %s", symbol, exc)
        return None

    if not candles:
        logger.warning("LSE returned no candles for %s", symbol)
        return None

    if len(candles) >= MAX_ROWS_PER_CALL:
        # Daily bars can't hit this in 10 years; if they do, the window is
        # being silently truncated and the caller must page.
        logger.warning(
            "LSE returned the %d-row cap for %s — window may be truncated",
            MAX_ROWS_PER_CALL, symbol,
        )

    df = build_adjusted_frame(candles, dividends, splits)
    return df if not df.empty else None
