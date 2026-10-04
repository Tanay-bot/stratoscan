"""Public share links for backtest results (Day 6: shareable proof).

Two pure functions, no I/O:

- :func:`build_share_summary` turns the engine's full ``BacktestResponse`` into
  the ONLY data a share link ever exposes. It copies named numeric fields one
  by one — it never passes the result dict through — so nothing the owner did
  not choose to publish (an email, a user id, a job id, an anonymous session
  id, a trade log) can ride along. If a future field is added to the engine's
  response, it stays private until someone adds it here on purpose.

- :func:`render_share_page` turns that summary into a complete, server-rendered
  HTML page: headline metrics, an inline-SVG equity curve, the risk disclaimer
  next to the numbers, and one call to action. Server-rendered on purpose: a
  client-rendered page shows link-preview crawlers an empty shell (Day 7 adds
  the preview image and Open Graph tags on top of this page).

The query text is the owner's own words, published by their explicit choice.
It is length-capped and always HTML-escaped; it is never auto-linked, and the
page is ``noindex`` so a share link cannot be used to plant text in search
results under this domain.
"""

from __future__ import annotations

import html
import math
import urllib.parse
from typing import Any, Dict, List, Optional

# Characters of the owner's query kept on a public page.
MAX_SHARE_QUERY_CHARS = 300
# Symbols listed on a share page (the rest are summarised as a count).
MAX_SHARE_SYMBOLS = 20
# Points in the shared equity curve (the engine already caps at 300).
MAX_SHARE_CURVE_POINTS = 300

DISCLAIMER = (
    "Simulated results on historical data, including fees and slippage. Past "
    "performance does not predict future results. This is not investment advice."
)


def _num(value: Any, ndigits: int = 2) -> Optional[float]:
    """A finite float rounded to ``ndigits``, or None for anything else."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, ndigits)


def _int(value: Any) -> Optional[int]:
    f = _num(value, 0)
    return int(f) if f is not None else None


def _curve(points: Any) -> List[Dict[str, Any]]:
    """Copy an equity curve as ``[{date, value}]``, dropping malformed points."""
    out: List[Dict[str, Any]] = []
    if not isinstance(points, list):
        return out
    for pt in points:
        if not isinstance(pt, dict):
            continue
        value = _num(pt.get("value"))
        date = pt.get("date")
        if value is None or not isinstance(date, str):
            continue
        out.append({"date": date[:10], "value": value})
    if len(out) > MAX_SHARE_CURVE_POINTS:
        step = len(out) / MAX_SHARE_CURVE_POINTS
        sampled = [out[int(i * step)] for i in range(MAX_SHARE_CURVE_POINTS)]
        sampled[-1] = out[-1]
        out = sampled
    return out


def build_share_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    """The public, whitelisted summary of one finished backtest.

    Uses the portfolio figures when the run covered several symbols (that is
    the book the user actually saw), else the single symbol's. Trade count is
    summed; win rate is trade-weighted across symbols, so a symbol with one
    lucky trade cannot dominate it.
    """
    results = result.get("results") if isinstance(result, dict) else None
    results = results if isinstance(results, dict) else {}
    items = [r for r in results.values() if isinstance(r, dict)]
    portfolio = result.get("portfolio") if isinstance(result.get("portfolio"), dict) else None

    trades = sum(_int(r.get("total_trades")) or 0 for r in items)
    wins = 0.0
    weighted = 0
    for r in items:
        rate, n = _num(r.get("win_rate_pct")), _int(r.get("total_trades")) or 0
        if rate is not None and n > 0:
            wins += rate / 100.0 * n
            weighted += n
    win_rate = round(wins / weighted * 100, 2) if weighted else None
    # SPY over each symbol's own window. A portfolio gets a benchmark only
    # when every symbol was measured over the same window (identical SPY
    # figure); otherwise "vs SPY" would compare against a period the book
    # did not trade, so none is shown rather than a wrong one.
    benchmarks = {
        b for b in (_num(r.get("benchmark_total_return_pct")) for r in items)
        if b is not None
    }

    if portfolio and len(items) > 1:
        basis = "portfolio"
        benchmark = next(iter(benchmarks)) if len(benchmarks) == 1 else None
        total_return = _num(portfolio.get("total_return_pct"))
        sharpe = _num(portfolio.get("sharpe_ratio"), 3)
        max_dd = _num(portfolio.get("max_drawdown_pct"))
        curve = _curve(portfolio.get("equity_curve"))
    elif items:
        basis = "single"
        top = items[0]
        benchmark = _num(top.get("benchmark_total_return_pct"))
        total_return = _num(top.get("total_return_pct"))
        sharpe = _num(top.get("sharpe_ratio"), 3)
        max_dd = _num(top.get("max_drawdown_pct"))
        curve = _curve(top.get("equity_curve"))
    else:
        basis = "none"
        total_return = sharpe = max_dd = benchmark = None
        curve = []

    excess = (
        round(total_return - benchmark, 2)
        if total_return is not None and benchmark is not None else None
    )
    symbols = sorted(str(s)[:12] for s in results.keys())
    query = str(result.get("query") or "").strip()[:MAX_SHARE_QUERY_CHARS]

    return {
        "query": query,
        "basis": basis,
        "symbols": symbols[:MAX_SHARE_SYMBOLS],
        # Symbols that produced a result (``total_symbols_tested`` can include
        # names that had no price data, which the page must not imply ran).
        "symbols_tested": len(symbols),
        "headline": {
            "total_return_pct": total_return,
            "benchmark_total_return_pct": benchmark,
            "excess_return_pct": excess,
            "sharpe_ratio": sharpe,
            "max_drawdown_pct": max_dd,
            "win_rate_pct": win_rate,
            "total_trades": trades,
        },
        "period": {
            "start": curve[0]["date"] if curve else None,
            "end": curve[-1]["date"] if curve else None,
        },
        "equity_curve": curve,
    }


# ── rendering ────────────────────────────────────────────────────────────────

def _fmt_pct(v: Optional[float], signed: bool = True) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}%" if signed else f"{v:.1f}%"


def _fmt_num(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def equity_svg(curve: List[Dict[str, Any]], width: int = 720, height: int = 240) -> str:
    """An inline SVG polyline of the equity curve (empty-state text if <2 points)."""
    if len(curve) < 2:
        return (
            f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="No equity curve">'
            f'<text x="50%" y="50%" text-anchor="middle" fill="#8b8b9a" '
            f'font-size="14">Not enough data to draw a curve</text></svg>'
        )
    values = [p["value"] for p in curve]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pad = 8
    n = len(values) - 1
    pts = " ".join(
        f"{pad + (width - 2 * pad) * i / n:.1f},"
        f"{height - pad - (height - 2 * pad) * (v - lo) / span:.1f}"
        for i, v in enumerate(values)
    )
    up = values[-1] >= values[0]
    color = "#16c784" if up else "#ea3943"
    first, last = curve[0]["date"], curve[-1]["date"]
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Equity curve from {html.escape(first)} to {html.escape(last)}, '
        f'{"up" if up else "down"} overall">'
        f'<polyline fill="none" stroke="{color}" stroke-width="2.2" '
        f'stroke-linejoin="round" points="{pts}"/></svg>'
    )


def render_share_page(
    summary: Dict[str, Any],
    *,
    page_url: str,
    app_url: str,
    image_url: Optional[str] = None,
) -> str:
    """A complete server-rendered HTML page for one shared result.

    ``image_url`` (Day 7) adds Open Graph / Twitter Card tags pointing at the
    preview image; without it the page still renders fully.
    """
    q = summary.get("query") or "A Stratoscan strategy"
    h = summary.get("headline") or {}
    period = summary.get("period") or {}
    ret = h.get("total_return_pct")
    spy = h.get("benchmark_total_return_pct")

    years = ""
    if period.get("start") and period.get("end"):
        years = f" ({period['start'][:4]}–{period['end'][:4]})"
    title = (
        f"{_fmt_pct(ret)} backtested{years} — {q[:70]}"
        if ret is not None else f"Backtest — {q[:80]}"
    )
    desc = (
        f"Strategy {_fmt_pct(ret)} vs SPY {_fmt_pct(spy)}. "
        f"{h.get('total_trades', 0)} simulated trades. {DISCLAIMER}"
    )
    run_url = f"{app_url.rstrip('/')}/try?q={urllib.parse.quote(q)}"
    try_url = f"{app_url.rstrip('/')}/try"

    meta_image = ""
    if image_url:
        e_img = html.escape(image_url, quote=True)
        meta_image = (
            f'<meta property="og:image" content="{e_img}">'
            f'<meta property="og:image:width" content="1200">'
            f'<meta property="og:image:height" content="630">'
            f'<meta name="twitter:card" content="summary_large_image">'
            f'<meta name="twitter:image" content="{e_img}">'
        )
    else:
        meta_image = '<meta name="twitter:card" content="summary">'

    def tile(label: str, value: str, tone: str = "") -> str:
        return (
            f'<div class="tile"><div class="label">{html.escape(label)}</div>'
            f'<div class="value {tone}">{html.escape(value)}</div></div>'
        )

    tone = "gain" if (ret or 0) >= 0 else "loss"
    symbols = summary.get("symbols") or []
    extra = summary.get("symbols_tested", len(symbols)) - len(symbols)
    sym_text = ", ".join(symbols) + (f" +{extra} more" if extra > 0 else "")

    e = html.escape
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{e(title)} · Stratoscan</title>
<meta name="description" content="{e(desc, quote=True)}">
<link rel="canonical" href="{e(page_url, quote=True)}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Stratoscan">
<meta property="og:title" content="{e(title, quote=True)}">
<meta property="og:description" content="{e(desc, quote=True)}">
<meta property="og:url" content="{e(page_url, quote=True)}">
<meta name="twitter:title" content="{e(title, quote=True)}">
<meta name="twitter:description" content="{e(desc, quote=True)}">
{meta_image}
<style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box}}
body{{margin:0;background:#0a0a0f;color:#e8e8f0;font:16px/1.5 Inter,system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:780px;margin:0 auto;padding:32px 16px 48px}}
.brand{{color:#00d4ff;font-weight:700;letter-spacing:.02em;text-decoration:none}}
h1{{font-size:20px;font-weight:600;margin:18px 0 4px;word-wrap:break-word}}
.sub{{color:#8b8b9a;font-size:13px;margin:0 0 20px}}
.card{{background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:18px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:14px}}
.tile .label{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#8b8b9a}}
.tile .value{{font:600 20px/1.3 'JetBrains Mono',ui-monospace,monospace;font-variant-numeric:tabular-nums}}
.gain{{color:#16c784}} .loss{{color:#ea3943}}
svg{{width:100%;height:auto;display:block}}
.disclaimer{{margin-top:14px;padding:10px 12px;border-radius:10px;background:rgba(251,191,36,.08);border:1px solid rgba(251,191,36,.25);font-size:13px;color:#d6d6e0}}
.disclaimer a{{color:#00d4ff}}
.cta{{display:flex;flex-wrap:wrap;gap:10px;margin-top:22px}}
.btn{{display:inline-block;padding:11px 18px;border-radius:10px;font-weight:600;text-decoration:none}}
.primary{{background:#00d4ff;color:#0a0a0f}} .ghost{{border:1px solid rgba(255,255,255,.18);color:#e8e8f0}}
.syms{{color:#8b8b9a;font-size:13px;margin-top:10px;word-wrap:break-word}}
</style>
</head><body><main>
<a class="brand" href="{e(app_url, quote=True)}">Stratoscan</a>
<h1>{e(q)}</h1>
<p class="sub">Backtested {e(period.get('start') or '—')} → {e(period.get('end') or '—')} · {e(str(len(symbols) + max(extra, 0)))} symbol(s) · simulated</p>
<section class="card" aria-label="Result">
<div class="grid">
{tile("Strategy return", _fmt_pct(ret), tone)}
{tile("SPY (buy & hold)", _fmt_pct(spy))}
{tile("Sharpe ratio", _fmt_num(h.get("sharpe_ratio")))}
{tile("Max drawdown", _fmt_pct(h.get("max_drawdown_pct"), signed=False))}
{tile("Win rate", _fmt_pct(h.get("win_rate_pct"), signed=False))}
{tile("Trades", str(h.get("total_trades") or 0))}
</div>
{equity_svg(summary.get("equity_curve") or [])}
<p class="syms">{e(sym_text)}</p>
<div class="disclaimer" role="note">{e(DISCLAIMER)} <a href="{e(app_url.rstrip('/') + '/risk', quote=True)}">Read the risk disclaimer</a>.</div>
</section>
<div class="cta">
<a class="btn primary" href="{e(run_url, quote=True)}">Run this strategy yourself</a>
<a class="btn ghost" href="{e(try_url, quote=True)}">Try your own idea — free, no signup</a>
</div>
</main></body></html>"""
