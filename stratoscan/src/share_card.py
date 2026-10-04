"""Share-link preview cards (Day 7: make a shared link worth clicking).

One pure function, :func:`render_share_card`, turns a share summary (the
whitelist built by :func:`src.share_page.build_share_summary`) into a
1200×630 PNG — the image Discord, X, Slack and iMessage show when a share
link is pasted. Rendered in Python with Pillow from the curve the engine
already downsamples to ≤300 points; no browser, no external service.

The card carries the plain-English query, the headline return, where SPY
ended over the same window, the equity curve, and the risk disclaimer on
the image itself — the image travels to places the terms page never will,
and it is the thing people screenshot.

Every input is hostile until proven otherwise. The summary is already
whitelisted and its numbers are finite (``share_page._num``), but this
module re-checks anyway and handles the ugly cases on purpose: no curve,
one point, a flat line, a total loss, an absurd return, a 2,000-character
query, control and bidi-override characters, and scripts the fonts cannot
draw. Text is laid out with Pillow's BASIC engine, so output never depends
on whether the host has libraqm.

Output is deterministic for a given summary (and Pillow version), so the
API caches it under :func:`card_key`.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from src.share_page import _fmt_num, _fmt_pct

# Bump when the layout changes: it is part of the cache key.
CARD_VERSION = "1"
WIDTH, HEIGHT = 1200, 630
PAD = 56

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

BG = (10, 10, 15)
PANEL = (20, 20, 31)
TEXT_HI = (242, 244, 251)
TEXT_MID = (154, 163, 184)
TEXT_LOW = (124, 134, 160)
NEON = (0, 212, 255)
GAIN = (22, 199, 132)
LOSS = (234, 57, 67)
AMBER = (251, 191, 36)
GRID = (60, 62, 78)

CARD_DISCLAIMER = (
    "Simulated on historical data, including fees and slippage. Past performance "
    "does not predict future results. Not investment advice."
)

# Headline sizes tried largest-first; the floor keeps a return readable at
# thumbnail size (~1/3 scale in an X or Discord card).
HEADLINE_SIZES = (104, 92, 80, 68, 56, 48)
DISCLAIMER_FONT_SIZE = 23

# An unassigned code point: every font renders it as .notdef, so any glyph
# that renders identically is one the font does not have.
_NOTDEF_PROBE = "\u0378"


@lru_cache(maxsize=None)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(
        str(FONT_DIR / name), size, layout_engine=ImageFont.Layout.BASIC
    )


def _sans(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return _font("Inter-Bold.ttf" if bold else "Inter-Regular.ttf", size)


@lru_cache(maxsize=4096)
def _has_glyph(font_name: str, ch: str) -> bool:
    font = _font(font_name, 32)
    return bytes(font.getmask(ch)) != bytes(font.getmask(_NOTDEF_PROBE)) or ch.isspace()


def clean_text(text: Any, font_name: str = "Inter-Regular.ttf", limit: int = 400) -> str:
    """Make arbitrary user text safe and drawable.

    NFC-normalizes, removes control and format characters, folds
    whitespace, and replaces each run of characters the font cannot draw
    with a single space (a row of tofu boxes reads as a rendering bug).

    Format characters (category ``Cf``) include the bidi overrides and
    isolates (U+202A-E, U+2066-9) and the zero-width joiners: invisible
    themselves, they change how the text around them DISPLAYS, so a query
    could otherwise make the card read differently from what was typed.
    """
    s = unicodedata.normalize("NFC", str(text or ""))[: limit * 2]
    out: List[str] = []
    for ch in s:
        cat = unicodedata.category(ch)
        if ch in "\t\n\r" or cat in ("Zl", "Zp"):
            out.append(" ")
        elif cat in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        elif _has_glyph(font_name, ch):
            out.append(ch)
        else:
            out.append(" ")
    return " ".join("".join(out).split())[:limit]


def _longest_fit(text: str, font: ImageFont.FreeTypeFont, width: float, suffix: str = "") -> int:
    """Length of the longest prefix of ``text`` that fits with ``suffix``.

    Binary search: a FreeType measurement costs ~1 ms, so a character-by-
    character scan of a 2,000-character query would take seconds.
    """
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.getlength(text[:mid] + suffix) <= width:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _ellipsize(text: str, font: ImageFont.FreeTypeFont, width: float) -> str:
    if font.getlength(text) <= width:
        return text
    return text[: _longest_fit(text, font, width, "…")].rstrip() + "…"


def wrap(text: str, font: ImageFont.FreeTypeFont, width: float, max_lines: int) -> List[str]:
    """Greedy word wrap; over-long words are broken by character; the last
    line is ellipsized when text remains."""
    words = text.split()
    lines: List[str] = []
    cur = ""
    i = 0
    while i < len(words) and len(lines) < max_lines:
        w = words[i]
        trial = f"{cur} {w}" if cur else w
        if font.getlength(trial) <= width:
            cur = trial
            i += 1
            continue
        if not cur:  # a single word wider than the line: hard-break it
            cut = max(1, _longest_fit(w, font, width))
            lines.append(w[:cut])
            words[i] = w[cut:]
            continue
        lines.append(cur)
        cur = ""
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if i < len(words) and lines:
        rest = " ".join([lines[-1]] + words[i:])
        lines[-1] = _ellipsize(rest, font, width)
    return lines


def _finite(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _curve_values(curve: Any) -> List[float]:
    if not isinstance(curve, list):
        return []
    vals = [_finite(p.get("value")) for p in curve if isinstance(p, dict)]
    return [v for v in vals if v is not None]


def card_key(summary: Dict[str, Any]) -> str:
    """Stable cache key: same summary + same layout version ⇒ same PNG."""
    blob = json.dumps(summary, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{CARD_VERSION}:{blob}".encode("utf-8")).hexdigest()


def _fit(text: str, max_width: float, sizes: Sequence[int]) -> ImageFont.FreeTypeFont:
    """The largest bold size at which ``text`` fits (an absurd return like
    +123456.7% shrinks instead of running into the SPY block)."""
    for size in sizes:
        font = _sans(size, bold=True)
        if font.getlength(text) <= max_width:
            return font
    return _sans(sizes[-1], bold=True)


def _draw_chart(
    values: List[float], spy_end: Optional[float], box: Tuple[int, int, int, int], color
) -> Image.Image:
    """The equity curve on a 2× supersampled layer (Pillow lines are not
    anti-aliased), downscaled into ``box``."""
    x0, y0, x1, y1 = box
    w, h = (x1 - x0), (y1 - y0)
    S = 2
    layer = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    levels = values + ([spy_end] if spy_end is not None else [])
    lo, hi = min(levels), max(levels)
    span = hi - lo
    if span <= 0 or not math.isfinite(span):
        lo, hi, span = lo - 1.0, hi + 1.0, 2.0
    margin = 10 * S
    right_gutter = 70 * S if spy_end is not None else 0

    def y_of(v: float) -> float:
        return margin + (h * S - 2 * margin) * (1 - (v - lo) / span)

    usable_w = w * S - right_gutter
    n = len(values) - 1
    pts = [(usable_w * i / n, y_of(v)) for i, v in enumerate(values)]

    # Start-value baseline (dashed): "above this line = made money".
    base_y = y_of(values[0])
    for xs in range(0, int(usable_w), 24 * S):
        d.line([(xs, base_y), (min(xs + 12 * S, usable_w), base_y)], fill=GRID + (255,), width=2 * S)

    fill = color + (40,)
    d.polygon(pts + [(pts[-1][0], h * S), (pts[0][0], h * S)], fill=fill)
    d.line(pts, fill=color + (255,), width=4 * S, joint="curve")

    if spy_end is not None:
        sy = y_of(spy_end)
        sx = usable_w + 18 * S
        r = 7 * S
        d.ellipse([sx - r, sy - r, sx + r, sy + r], fill=TEXT_MID + (255,))
        label = _sans(18 * S, bold=True)
        d.text((sx + r + 6 * S, sy), "SPY", font=label, fill=TEXT_MID + (255,), anchor="lm")

    return layer.resize((w, h), Image.Resampling.LANCZOS)


def render_share_card(summary: Dict[str, Any]) -> bytes:
    """Render one share summary as a 1200×630 PNG (bytes)."""
    summary = summary if isinstance(summary, dict) else {}
    headline = summary.get("headline") if isinstance(summary.get("headline"), dict) else {}
    period = summary.get("period") if isinstance(summary.get("period"), dict) else {}

    ret = _finite(headline.get("total_return_pct"))
    spy = _finite(headline.get("benchmark_total_return_pct"))
    sharpe = _finite(headline.get("sharpe_ratio"))
    max_dd = _finite(headline.get("max_drawdown_pct"))
    trades = _finite(headline.get("total_trades"))
    values = _curve_values(summary.get("equity_curve"))
    symbols = summary.get("symbols") if isinstance(summary.get("symbols"), list) else []
    n_symbols = int(_finite(summary.get("symbols_tested")) or len(symbols))

    tone = TEXT_MID if ret is None else (GAIN if ret >= 0 else LOSS)
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)

    # ── top row: brand + "simulated" tag ─────────────────────────────────
    d.text((PAD, 44), "Stratoscan", font=_sans(30, bold=True), fill=NEON)
    tag = "SIMULATED BACKTEST"
    tag_font = _sans(19, bold=True)
    tw = tag_font.getlength(tag)
    tx1 = WIDTH - PAD
    tx0 = tx1 - tw - 28
    d.rounded_rectangle([tx0, 42, tx1, 80], radius=19, outline=AMBER, width=2)
    d.text(((tx0 + tx1) / 2, 61), tag, font=tag_font, fill=AMBER, anchor="mm")

    # ── headline return + context block to its right ─────────────────────
    ret_text = _fmt_pct(ret)
    ret_font = _fit(ret_text, 520, HEADLINE_SIZES)
    d.text((PAD - 4, 92), ret_text, font=ret_font, fill=tone)
    ctx_x = PAD + ret_font.getlength(ret_text) + 36
    ctx_w = WIDTH - PAD - ctx_x
    spy_line = f"vs SPY {_fmt_pct(spy)}" if spy is not None else "No SPY comparison"
    d.text((ctx_x, 114), _ellipsize(spy_line, _sans(32, bold=True), ctx_w),
           font=_sans(32, bold=True), fill=TEXT_HI)
    years = ""
    start, end = period.get("start"), period.get("end")
    if isinstance(start, str) and isinstance(end, str) and start[:4].isdigit() and end[:4].isdigit():
        years = f"{start[:4]}–{end[:4]} · "
    n_trades = int(trades) if trades is not None else 0
    meta_lines = (
        f"{years}{n_symbols} symbol{'s' if n_symbols != 1 else ''} · "
        f"{n_trades} trade{'s' if n_trades != 1 else ''}",
        f"Sharpe {_fmt_num(sharpe)} · Max drawdown {_fmt_pct(max_dd, signed=False)}",
    )
    for i, line in enumerate(meta_lines):
        d.text((ctx_x, 158 + i * 29), _ellipsize(line, _sans(22), ctx_w),
               font=_sans(22), fill=TEXT_LOW)

    # ── the query, in the owner's words ──────────────────────────────────
    q_font = _sans(30)
    query = clean_text(summary.get("query")) or "A Stratoscan strategy"
    q_lines = wrap(f"“{query}”", q_font, WIDTH - 2 * PAD, max_lines=2)
    for i, line in enumerate(q_lines):
        d.text((PAD, 222 + i * 40), line, font=q_font, fill=TEXT_HI)

    # ── equity curve ─────────────────────────────────────────────────────
    chart_box = (PAD, 312, WIDTH - PAD, 532)
    if len(values) >= 2:
        spy_end = values[0] * (1 + spy / 100.0) if spy is not None else None
        if spy_end is not None and not math.isfinite(spy_end):
            spy_end = None
        layer = _draw_chart(values, spy_end, chart_box, tone)
        img.paste(layer, chart_box[:2], layer)
    else:
        cx = (chart_box[0] + chart_box[2]) / 2
        cy = (chart_box[1] + chart_box[3]) / 2
        d.text((cx, cy), "Not enough data to draw a curve", font=_sans(26),
               fill=TEXT_LOW, anchor="mm")

    # ── disclaimer band — on the image itself, legible at full size ──────
    d.rectangle([0, 548, WIDTH, HEIGHT], fill=PANEL)
    disc_font = _sans(DISCLAIMER_FONT_SIZE)
    for i, line in enumerate(wrap(CARD_DISCLAIMER, disc_font, WIDTH - 2 * PAD, max_lines=2)):
        d.text((PAD, 558 + i * 31), line, font=disc_font, fill=(226, 228, 238))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
