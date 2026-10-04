"""Day 7 — share-link preview cards. Hermetic: no DB, no network.

A pasted share link should unfurl into a card with the curve on it. These
tests pin what makes that card trustworthy and robust:

  1. Every hostile summary renders a valid 1200×630 PNG: zero trades, a
     total loss, one point, a flat line, a 2,000-character query, an absurd
     return, NaN/inf, bidi overrides, control characters, unsupported
     scripts, garbage types, and None.
  2. The disclaimer fits on the image in full (never ellipsized away), and
     the sizes that must survive a thumbnail stay large.
  3. The headline is drawn in the colour of the result (no green losses).
  4. Output is deterministic, so caching by content hash is sound.
  5. The route: PNG + ETag + 304, unknown ids 404 without rendering, a
     revoked link's image dies with it, and the share page points
     og:image / twitter:image at the card.
  6. Dependency wiring: Pillow is hash-locked, fonts ship in the image.

Run: python -m tests.test_share_card
"""

from __future__ import annotations

import io
import math
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("JWT_SECRET", "share-card-dummy-secret")
os.environ["ANALYTICS_ENABLED"] = "false"

from unittest import mock  # noqa: E402

from PIL import Image  # noqa: E402

from src import share_card  # noqa: E402
from src.share_card import (  # noqa: E402
    CARD_DISCLAIMER,
    GAIN,
    HEIGHT,
    LOSS,
    WIDTH,
    _sans,
    card_key,
    clean_text,
    render_share_card,
    wrap,
)

ROOT = Path(__file__).resolve().parent.parent


def curve(fn, n=300):
    return [{"date": f"2016-01-{i % 28 + 1:02d}", "value": fn(i)} for i in range(n)]


def summary(**over):
    base = {
        "query": "oversold large-cap tech",
        "basis": "single",
        "symbols": ["AAPL"],
        "symbols_tested": 1,
        "headline": {"total_return_pct": 48.7, "benchmark_total_return_pct": 182.3,
                     "excess_return_pct": -133.6, "sharpe_ratio": 0.62,
                     "max_drawdown_pct": -23.4, "win_rate_pct": 58.0, "total_trades": 41},
        "period": {"start": "2016-01-04", "end": "2026-01-02"},
        "equity_curve": curve(lambda i: 100000 * (1 + 0.5 * i / 299)),
    }
    base.update(over)
    return base


def png_ok(data: bytes) -> Image.Image:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    img = Image.open(io.BytesIO(data))
    img.load()
    assert img.size == (WIDTH, HEIGHT), img.size
    return img


# ═══════════════════════════════════════════════════════════════════════════
# 1. Ugly cases all render
# ═══════════════════════════════════════════════════════════════════════════
H = summary()["headline"]
ugly = {
    "zero trades": summary(headline={**H, "total_return_pct": 0.0, "total_trades": 0,
                                     "sharpe_ratio": None, "win_rate_pct": None},
                           equity_curve=curve(lambda i: 100000.0)),
    "total loss": summary(headline={**H, "total_return_pct": -100.0, "max_drawdown_pct": -100.0},
                          equity_curve=curve(lambda i: max(0.0, 100000 * (1 - i / 150)))),
    "single point": summary(equity_curve=curve(lambda i: 100000.0, n=1)),
    "no curve": summary(equity_curve=[]),
    "2000-char query": summary(query="momentum " * 222),
    "absurd return": summary(headline={**H, "total_return_pct": 123456789.1, "total_trades": 10**9},
                             equity_curve=curve(lambda i: 100000 * math.exp(i / 20))),
    "nan / inf": summary(headline={k: float("nan") for k in H} | {"benchmark_total_return_pct": float("inf")},
                         equity_curve=[{"date": "x", "value": float("nan")},
                                       {"date": "y", "value": float("-inf")},
                                       {"date": "z", "value": "abc"}, "junk", None]),
    "bidi + control + scripts": summary(query="buy \u202eevil\u202c \u200b ש中 Ωmega café \x00\x07\ttab\nnew"),
    "wrong types": {"query": 12345, "headline": "x", "period": [], "equity_curve": "nope", "symbols": 5},
    "empty": {},
    "none": None,
}
for name, s in ugly.items():
    png_ok(render_share_card(s))
print(f"✅ {len(ugly)} hostile summaries all render a valid 1200×630 PNG")


# ═══════════════════════════════════════════════════════════════════════════
# 2. Text handling
# ═══════════════════════════════════════════════════════════════════════════
cleaned = clean_text("buy \u202eevil\u202c\u200b now \x00\x07 tab\there\nnew  line")
assert "\u202e" not in cleaned and "\u200b" not in cleaned and "\x00" not in cleaned
assert cleaned == "buy evil now tab here new line", repr(cleaned)
assert clean_text("ש中 only") == "only", "glyphs the font lacks are dropped, not drawn as tofu"
assert clean_text("café Ω — ✓") == "café Ω — ✓", "supported non-ASCII text is kept"
assert len(clean_text("x" * 5000)) <= 400
assert clean_text(None) == "" and clean_text(123) == "123"
print("✅ clean_text strips bidi overrides, zero-width and control chars; drops undrawable glyphs")

font = _sans(30)
lines = wrap("word " * 500, font, 1088, max_lines=2)
assert len(lines) == 2 and lines[-1].endswith("…"), lines
assert all(font.getlength(line) <= 1088 for line in lines)
lines = wrap("x" * 400, font, 1088, max_lines=2)
assert len(lines) == 2 and all(font.getlength(line) <= 1088 for line in lines), "long word hard-breaks"
assert wrap("short", font, 1088, max_lines=2) == ["short"]
print("✅ wrap: ≤ 2 lines, every line inside the card, ellipsis when text remains")

disc = wrap(CARD_DISCLAIMER, _sans(share_card.DISCLAIMER_FONT_SIZE), WIDTH - 2 * share_card.PAD,
            max_lines=2)
assert share_card.DISCLAIMER_FONT_SIZE >= 21, "smaller is not legible on the full-size card"
assert " ".join(disc) == CARD_DISCLAIMER and not disc[-1].endswith("…"), (
    "the disclaimer must appear on the card IN FULL — it travels where the terms page doesn't"
)
assert "Not investment advice" in CARD_DISCLAIMER and "Past performance" in CARD_DISCLAIMER
print("✅ the full disclaimer fits on the card (2 lines at 23px), never ellipsized")

# Thumbnail legibility: X/Discord show ~400–520px wide. Sizes that carry the
# message must stay ≥ ~16px there (scale 1/3 → ≥ 48px on the card).
assert share_card._fit("+48.7%", 520, share_card.HEADLINE_SIZES).size >= 92
assert share_card._fit("+123456789.1%", 520, share_card.HEADLINE_SIZES).size >= 48
assert min(share_card.HEADLINE_SIZES) >= 48
print("✅ headline stays ≥ 48px even for absurd returns (≥ 16px at thumbnail size)")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Colour follows the result
# ═══════════════════════════════════════════════════════════════════════════
def tone_pixels(img: Image.Image, rgb) -> int:
    region = img.convert("RGB").crop((40, 90, 560, 210))
    raw = region.tobytes()
    return sum(
        1 for i in range(0, len(raw), 3)
        if all(abs(raw[i + k] - rgb[k]) <= 12 for k in range(3))
    )


gain_img = png_ok(render_share_card(summary()))
loss_img = png_ok(render_share_card(summary(headline={**H, "total_return_pct": -37.5})))
assert tone_pixels(gain_img, GAIN) > 500 and tone_pixels(gain_img, LOSS) == 0
assert tone_pixels(loss_img, LOSS) > 500 and tone_pixels(loss_img, GAIN) == 0
print("✅ a gain is drawn green and a loss red — never a green loss")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Determinism and speed
# ═══════════════════════════════════════════════════════════════════════════
a, b = render_share_card(summary()), render_share_card(summary())
assert a == b, "same summary must give byte-identical PNGs (the cache relies on it)"
assert card_key(summary()) == card_key(summary())
assert card_key(summary()) != card_key(summary(query="different"))
k1 = card_key(summary())
with mock.patch.object(share_card, "CARD_VERSION", "999"):
    assert card_key(summary()) != k1, "a layout change must invalidate every cached card"
worst = [summary(query="q" * 2000), summary(query="momentum " * 222), summary()]
t = time.perf_counter()
for s in worst:
    render_share_card(s)
per = (time.perf_counter() - t) / len(worst)
assert per < 0.5, f"render too slow for a request path: {per:.2f}s"
print(f"✅ deterministic bytes, stable content-hash key, {per * 1000:.0f} ms per render")


# ═══════════════════════════════════════════════════════════════════════════
# 5. The route
# ═══════════════════════════════════════════════════════════════════════════
from fastapi.testclient import TestClient  # noqa: E402

from tests.fakes import FakeLLMRouter, FakeUserDB  # noqa: E402

fake_db = FakeUserDB()
mock.patch("src.database.PostgresDB", return_value=fake_db).start()
mock.patch("src.llm_router.get_router", return_value=FakeLLMRouter()).start()

import src.api as api  # noqa: E402

client = TestClient(api.app)
SID = "AbCdEfGhIjKlMnOpQrStUv"
fake_db.create_share(SID, "bt_x", None, "a" * 32, "oversold large-cap tech", summary())

with mock.patch("src.share_card.render_share_card", wraps=render_share_card) as spy:
    r = client.get(f"/r/{SID}.png")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "max-age" in r.headers["cache-control"]
    png_ok(r.content)
    etag = r.headers["etag"]
    assert client.get(f"/r/{SID}.png").content == r.content
    assert spy.call_count == 1, "the second fetch must come from the cache"
    assert client.get(f"/r/{SID}.png", headers={"If-None-Match": etag}).status_code == 304

    for bad in ("short", "x" * 70, "NoSuchShareIdHere12345"):
        assert client.get(f"/r/{bad}.png").status_code == 404
    assert spy.call_count == 1, "unknown ids must 404 before any rendering"
print("✅ /r/{id}.png: PNG + ETag + 304, cached after the first render, unknown ids never render")

page = client.get(f"/r/{SID}")
assert page.status_code == 200
img_url = f"http://testserver/r/{SID}.png"
for tag in (f'<meta property="og:image" content="{img_url}">',
            '<meta name="twitter:card" content="summary_large_image">',
            f'<meta name="twitter:image" content="{img_url}">',
            '<meta property="og:image:width" content="1200">'):
    assert tag in page.text, tag
assert re.search(r'og:image:alt" content="Equity curve of a simulated backtest', page.text)
print("✅ the share page points og:image and twitter:image at the card (summary_large_image)")

fake_db.delete_share(SID, None, "a" * 32)
assert client.get(f"/r/{SID}.png").status_code == 404, "a revoked link's image must die with it"
print("✅ revoking a link kills its image too (the cache is keyed by content, read after the DB check)")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Dependency wiring
# ═══════════════════════════════════════════════════════════════════════════
lock = (ROOT / "requirements-lock.txt").read_text(encoding="utf-8")
block = re.search(r"^pillow==([\d.]+) \\\n((?:    --hash=sha256:[0-9a-f]{64}(?: \\)?\n)+)", lock, re.M)
assert block, "pillow must be pinned with hashes in requirements-lock.txt"
assert block.group(2).count("--hash=") >= 20, "every release file's hash, as pip-compile emits"
for f in ("requirements.in", "requirements.txt", "pyproject.toml"):
    assert "pillow" in (ROOT / f).read_text(encoding="utf-8").lower(), f
docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
assert "COPY assets ./assets" in docker, "fonts must ship in the image or every card 500s"
for font_file in ("Inter-Regular.ttf", "Inter-Bold.ttf", "OFL-Inter.txt"):
    assert (ROOT / "assets" / "fonts" / font_file).is_file(), font_file
print("✅ Pillow hash-locked; fonts (OFL) present and copied into the container")

mock.patch.stopall()
print("\nALL SHARE-CARD CHECKS PASSED")
