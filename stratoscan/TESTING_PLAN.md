# Stratoscan Backtester Testing Plan

> **Goal:** Prove that what the screen shows is what actually happened — every metric,
> curve, and trade in the UI is verifiable against the raw data and the simulation math.
> **Scope:** Backtester first (this doc), then screener, auth, enrichment, and UI display.
> **Principle:** Never trust the engine's own numbers. Every phase recomputes the answer
> independently from the raw response or raw data, and diffs against what the engine claims.

---

## Test Harness Inventory (already built)

| Artifact | What it does | Where |
|---|---|---|
| `tests/test_backtester.py` | Hermetic simulation math: rules, indicators, stops, shorts, equity, drawdown | `tests/` |
| `tests/test_backtest_fillin.py` | Candidate fill-in (delisted symbols promoted) + message forwarding | `tests/` |
| `tests/test_backtest_tier4.py` | CSV export, robustness fields, strategy echo fidelity | `tests/` |
| `tests/test_backtest_db.py` | BacktestDB/SQLiteDB interface parity + Neon DDL | `tests/` |
| `tests/test_api_dirty_data.py` | Dirty-data /backtest over the real HTTP surface (hermetic) | `tests/` |
| `accuracy_loop.py` | 30 hard queries → recompute PnL/cash/return/drawdown/win-rate from JSON | root |
| `rule_execution_loop.py` | 30 queries → verify every trade fired on a real indicator state; hermetic reference simulator | root |
| `verify_trade_pnl.py` / `verify_backtest_accuracy.py` | Trade-level PnL + headline-accuracy invariants | root |

**Run command (project interpreter, all suites are script-style):**
```bash
PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14
for f in test_backtester test_backtest_fillin test_backtest_tier4 test_backtest_db test_api_dirty_data; do
  $PY tests/$f.py && echo "$f OK"
done
```

---

## Phase 1 — Hermetic Correctness (no network, deterministic)

**Goal:** The simulation engine obeys its documented contract on synthetic data where
the expected answer is known by construction.

**Procedure:**
1. Run the 5 hermetic suites above. Pass = exit 0 on all.
2. Known-state oracle: hand-computed price series (sawtooth, trending, flat, gap-up/down)
   where signal states are known → assert trades fire exactly at the documented bar
   (signal at close → fill at next open; stops intra-bar).
3. Reference-simulator diff: an *independent* simulator written from the documented
   contract, diffed trade-by-trade against the engine (penny-exact on zero-fee regimes).

**Pass criteria:** 100% of checks; reference and engine agree on every trade.

---

## Phase 2 — Live Math Accuracy (invariants over real responses)

**Goal:** Every number in a real `/backtest` response is internally consistent.

**Procedure** (`accuracy_loop.py`, ~25 min for 30 queries):
1. Register a fresh account; run 30 diverse queries (momentum, value, dividend, growth,
   oversold, short, date-ranges, single/multi-symbol, garbage input).
2. For each response, independently recompute:
   - **Per-trade PnL**: `(sell−buy)×shares − fees − slippage` on both legs == `pnl`
   - **Cash conservation**: equity final == initial capital + Σ realized PnL
   - **Return↔equity↔win-rate**: total return matches curve endpoints; win rate matches
     profitable-trade count
   - **Max drawdown** sanity vs the daily series
   - **Date/share sanity**: buys precede sells; shares positive; no future dates
   - **Strategy echo**: initial_capital / fee_rate / slippage / rules match the request
3. **Honest-failure checks**: relax/fill-in `message` is surfaced when it fires.

**Pass criteria:** 100% of ~13,600 invariant checks; HTTP 200 on all queries.

---

## Phase 3 — Rule-Execution Ground Truth ("do trades actually fire?")

**Goal:** Trades only happen when the strategy's stated rules are true — the deepest
accuracy layer (a trade existing is not the same as a trade being *correct*).

**Procedure** (`rule_execution_loop.py`, ~35 min):
1. Layer A: known-state oracle on synthetic regimes (see Phase 1).
2. Layer B: reference simulator vs engine across long/short/stop/take-profit regimes.
3. Layer C: for every trade in a real response, **re-fetch the symbol's real OHLCV**,
   recompute indicators (RSI/SMA/MACD/Bollinger) on the exact same window, and assert
   the buy/sell rule was actually satisfied at the signal bar — long entries on
   `buy_rules` with fill `open + slippage`, short entries on `sell_rules` with
   `open − slippage`, covers inverted.

**Pass criteria:** 100% of ~14,600 checks. A `pe < 15` rule must NOT silently become
`price < 15` (regression locked: the engine now refuses price-proxy `pe` rules).

---

## Phase 4 — Displayed-vs-Actual Consistency ("what shows on screen is what happened")

**Goal:** The UI's chart and metric cards are byte-faithful to the JSON, and the JSON is
byte-faithful to the simulation.

**Procedure:**
1. **Equity curve == trades**: cumulative sum of realized PnL (point-by-point) equals the
   returned `equity_curve`; curve endpoint == `final_value` == initial + Σpnl.
2. **Stats recomputed from trades**: Sharpe, win rate, max drawdown recomputed from the
   trade list + equity curve match the returned fields (within float tolerance).
3. **Trade list completeness**: every trade the sim claims appears in `trades`; no
   phantom fills; `total_trades` == `len(trades)`.
4. **Per-trade fields**: buy/sell dates are actual trading days; prices exist on those
   bars; shares × price is consistent with the position model.
5. **Robustness blocks**: `monte_carlo`, `walk_forward`, `parameter_sensitivity` are
   present and internally consistent (never `None` on a successful run).
6. **Frontend rendering** (manual/browser): equity chart data points == `equity_curve`;
   metric cards == returned numbers; trades table rows == `trades`. No derived value is
   computed client-side in a way that diverges from the API.

**Pass criteria:** every equality holds to float tolerance; UI deviates nowhere.

---

## Phase 5 — Screen→Backtest Pipeline Consistency

**Goal:** The backtest's candidate set is exactly the screener's top results.

**Procedure:**
1. Run `/screen` on a query; record the ranked symbol list.
2. Run `/backtest` with the same query and `top_n`.
3. Assert: backtest symbols == first `top_n` screen results (with fill-in only when a
   screen symbol has no data, and then the `message` must say so).
4. Assert `total_symbols_tested <= top_n` and every tested symbol has a result.

**Pass criteria:** set equality or an explicit fill-in note; no silent shrinkage.

---

## Phase 6 — Determinism & Concurrency (post-parallel-engine regression)

**Goal:** The ThreadPoolExecutor change (symbols backtested in parallel, 4 workers)
never changed the *math* — only the wall-clock.

**Procedure:**
1. **Determinism**: same query twice → identical `total_return_pct`, `final_value`,
   `total_trades`, Sharpe, win rate, trade count, equity length. (PASS as of 2026-08-09.)
2. **Parallel == sequential**: run the same symbol set via `top_n=5` (parallel) and as 5
   single-symbol calls; per-symbol numbers must be identical.
3. **Thread safety**: no SQLite "database is locked" / cross-thread connection errors
   under load; SQLiteDB per-thread connections hold (registry `close()` closes all).

**Pass criteria:** byte-identical per-symbol results; zero lock errors.

---

## Phase 7 — Performance & Timeout SLO

**Goal:** A backtest completes well inside the client's timeout budget.

**Procedure:**
1. Cold cache, top_n=5, complex strategy → wall-clock must be ≤ ~5 min (pre-fix: 10–25+).
   Observed post-fix: **53.6s / 105s**.
2. Warm cache (repeat query) → ≤ ~60s.
3. Confirm the frontend's `LONG_TIMEOUT_MS` (20 min) covers the 95th percentile.
4. `/backtest/export` CSV endpoint obeys the same budget.

**Pass criteria:** p95 < LONG_TIMEOUT_MS with margin; honest "can take a few minutes"
loading copy present.

---

## Phase 8 — Edge & Failure Drills

**Goal:** Failures degrade honestly, never silently.

**Procedure:**
1. Delisted symbols in top-N → fill-in promotes next-ranked + `message` explains.
2. Screen-to-0 (contradictory filters) → auto-relax + honest `message`, not bare empty.
3. NaN/inf price rows → finite JSON, no 500 (locked by `test_api_dirty_data`).
4. Benchmark (SPY) fetch failure → warning surfaced; alpha/beta null, not fake.
5. LLM JSON failure → `data_quality.error` + `llm_error` echoed.
6. Rate-limit 429 → `Retry-After` respected; UI shows friendly wait.
7. Garbage input (`"asdfgh qwerty 12345"`) → graceful output, no crash.

**Pass criteria:** every drill has an explicit, surfaced, honest outcome.

---

## Phase 9 — Cross-System & Full-Suite Regression

**Goal:** No fix in one subsystem broke another.

**Procedure:** Run the full verification matrix after any engine change:
```bash
$PY verify_full_backend_behavior.py   # all sections
$PY verify_fixes_implemented.py       # 44 checks
$PY verify_trade_pnl.py
$PY verify_backtest_accuracy.py
$PY verify_backtest_edge_paths.py
```
Plus all script-style suites + `pytest tests/test_llm_router.py` (the one pytest-style file).

---

## Phase 10 — UI Display Verification (browser)

**Goal:** The rendered workspace reflects the JSON and the JSON reflects reality.

**Procedure:** In the running preview: register → run a backtest query → confirm the
equity chart, metric cards, and trades table match the API response; check the console
and network tab for errors; verify the loading state copy and the honest `message`
(relaxed/fill-in) renders.

---

## Status snapshot — 2026-08-09

> ⚠️ **This table is a point-in-time record, not live state.** It was last
> updated on the date in the heading and is not refreshed automatically; the
> `⏳` rows below were pending as of that date and may or may not still be.
> For current, machine-checked status run the suites themselves (§ "Run
> command" above) — they are the source of truth, not this table.

| Phase | Status |
|---|---|
| 1. Hermetic correctness | ✅ 5/5 suites pass |
| 2. Live math accuracy | ✅ 100% (13,658 checks, pre-parallel engine); re-run in progress on parallel engine |
| 3. Rule-execution ground truth | ✅ 100% (14,627 checks, pre-parallel engine) |
| 4. Displayed-vs-actual | ⏳ initial invariants passing; UI pass pending |
| 5. Screen→backtest pipeline | ⏳ pending |
| 6. Determinism & concurrency | ✅ determinism PASS on parallel engine |
| 7. Performance SLO | ✅ 53.6s/105s cold (was 10–25+ min) |
| 8. Edge & failure drills | ✅ covered by suites; live spot-checks done |
| 9. Cross-system regression | ✅ verify matrix green before latest change; re-run after parallel engine |
| 10. UI display | ⏳ pending |

**Next run order after any engine change:** Phase 1 → Phase 6 (fast) → Phase 2 (25 min) →
Phase 3 (35 min) → Phases 4–5 (medium) → Phase 9 matrix.
