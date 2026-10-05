"""Day 14 — no public claim contradicts the system.

Public copy (the marketing pages, the verification page, index.html, the
READMEs) states numbers that live in code: Monte Carlo runs, walk-forward
windows, fees, the free plan's allowance, the ETF count, the split example.
This suite reads both and fails when they drift apart, and it bans the stale
claims Day 14 removed so they cannot creep back.

Run: python -m tests.test_public_claims
"""

from __future__ import annotations

import io
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("JWT_SECRET", "claims-dummy-secret")

from src import backtester  # noqa: E402
from src.entitlements import PLANS  # noqa: E402
from src.etfs import ETFS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FE = ROOT / "frontend"
if not FE.is_dir():  # the backend-only deploy image has no frontend to check
    print("⏭  frontend/ not present — public-claims check skipped")
    sys.exit(0)


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


PUBLIC_FILES = sorted(
    [p for p in (FE / "src" / "pages").glob("*.tsx") if ".test." not in p.name]
    + [p for p in (FE / "src" / "components" / "landing").glob("*.tsx") if ".test." not in p.name]
    + [FE / "index.html", ROOT / "README.md", ROOT / "stratoscan" / "README.md"]
)
public = {p: p.read_text(encoding="utf-8") for p in PUBLIC_FILES}
landing, about = read("frontend/src/pages/Landing.tsx"), read("frontend/src/pages/About.tsx")
verification = read("frontend/src/pages/Verification.tsx")

# ── 1. Banned: the claims Day 14 removed ────────────────────────────────────
BANNED = [
    (r"1,?400\+?\s*(US )?stocks|target: 1400", "universe size is ~5,000 (STAGING.md: 4,953 live)"),
    (r"24\s*/\s*7|suffix=\"/7\"", "US daily data, not round-the-clock coverage"),
    (r"real[- ]time", "prices refresh every 15 minutes; nothing streams"),
    (r"fully free|no subscription", "Pro exists (Day 12); the numbers are free, the plan isn't the only one"),
    (r"brokerage integration|broker connection|connect your broker", "there is no broker integration"),
    (r"\b\d{2,4} tests\b|lines of code", "stale test / code counts"),
    (r"trains on the past|fold trains", "walk-forward runs the same rules; nothing is trained"),
    (r"sparklines", "watchlist sparklines render in demo mode only"),
    (r"no gaps", "history gaps are possible (yfinance)"),
    (r"/day['\"]?,\s*label: 'backtests", "the free allowance is monthly (Day 12)"),
]
for path, text in public.items():
    for line in text.splitlines():
        if line.lstrip().startswith("- [ ]"):  # README roadmap items are plans, not claims
            continue
        for pattern, why in BANNED:
            assert not re.search(pattern, line, re.I), f"{path.relative_to(ROOT)}: {line.strip()!r} — {why}"
print(f"✅ no banned claim in {len(public)} public files")

# ── 2. Numbers that must match the code ─────────────────────────────────────
assert backtester.MONTE_CARLO_SIMULATIONS == 1000
assert "<CountUp target={1000} />" in landing and "Monte Carlo replays" in landing
assert "{ target: 1000, suffix: '', label: 'Monte Carlo replays per backtest' }" in about
assert backtester.WALK_FORWARD_SPLITS == 4
assert "{ target: 4, suffix: '', label: 'walk-forward windows per backtest' }" in about
assert "four windows" in read("frontend/src/components/landing/BacktestLoop.tsx")
assert "Monte Carlo reshuffles the trades 1,000 times" in read("frontend/src/components/landing/BacktestLoop.tsx")
assert PLANS["free"].backtests_per_month == 50, PLANS["free"]
assert "{ target: 50, suffix: '/mo', label: 'backtests on the free plan' }" in about
n_etfs = len(ETFS)
for path, text in public.items():
    for m in re.finditer(r"(\d+) (plain )?ETFs", text):
        assert int(m.group(1)) == n_etfs, f"{path.relative_to(ROOT)} says {m.group(0)}; the set has {n_etfs}"
assert backtester.DEFAULT_FEE_RATE == 0.001 and backtester.DEFAULT_SLIPPAGE == 0.0005
assert "a 0.1% fee and 0.05%" in re.sub(r"\s+", " ", verification)
assert backtester.DEFAULT_INITIAL_CAPITAL == 100_000 and "$100,000" in verification
print("✅ Monte Carlo, walk-forward, free allowance, ETF count, fees and capital match the code")

# ── 3. The split example on the page is the one the test computes ──────────
with redirect_stdout(io.StringIO()):
    from tests import test_corporate_actions as ca
flat = re.sub(r"\s+", " ", verification)
assert f"${ca.NAIVE_FINAL:,}" in flat and f"${ca.OURS_FINAL:,}" in flat, (ca.NAIVE_FINAL, ca.OURS_FINAL)
assert f"&minus;{abs(ca.NAIVE_PCT)}%" in flat and f"&minus;{abs(ca.OURS_PCT)}%" in flat
assert f"&minus;{abs(ca.NAIVE_PCT)}%" in re.sub(r"\s+", " ", landing)  # the landing teaser too
print(f"✅ split example: page shows {ca.NAIVE_PCT}% / {ca.OURS_PCT}%, exactly what the simulator returns")

# ── 4. The accuracy-loop line and the drill count agree with their sources ──
readme = re.sub(r"\s+", " ", read("README.md"))
m = re.search(r"Last run: (\d{4}-\d{2}-\d{2}) — ([\d,]+) / ([\d,]+) checks passed on (\d+) symbol results from (\d+) of the 30", readme)
assert m, "README accuracy line missing or reworded"
date, passed, total, results, queries = m.groups()
months = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
y, mo, d = date.split("-")
assert f"Last run: {int(d)} {months[int(mo) - 1]} {y} &mdash; {passed} of {total} checks passed on {results} results from {queries} of the 30" in flat, \
    "verification page accuracy line disagrees with README"
drills_on_page = len(re.findall(r"^\s+n: \d+,$", verification, re.M))
drill_doc = read("stratoscan/FAILURE_DRILLS.md")
hermetic = read("stratoscan/tests/test_failure_drills.py")
documented = len(re.findall(r"^## Drill \d+ ", drill_doc, re.M))
hermetic_drills = len(re.findall(r"^  \d\. ", hermetic.split('"""')[1], re.M))
every_deploy = verification.count("how: 'every deploy'")
assert drills_on_page == documented == 7, (drills_on_page, documented)
assert every_deploy == hermetic_drills == 3, (every_deploy, hermetic_drills)
assert "7 failure drills" in re.sub(r"\s+", " ", landing) or "<CountUp target={7} />" in landing
print(f"✅ accuracy line ({passed}/{total}, {date}) and {drills_on_page} drills match README / FAILURE_DRILLS / the suite")

print("\nALL PUBLIC-CLAIM CHECKS PASSED")
