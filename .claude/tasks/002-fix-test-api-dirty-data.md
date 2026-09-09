# 002 — Fix `tests/test_api_dirty_data.py` (stale test: deterministic path bypasses the router mock)

## Goal

`python -m tests.test_api_dirty_data` passes. The suite exists to prove the `/backtest` HTTP
surface survives **dirty data** — NaN/inf price rows must produce finite JSON and never a 500.
It is failing for a reason unrelated to that purpose.

The suite patches `src.llm_router.get_router` to force a simple strategy
(`{"sector": "Technology", "max_rsi": 30}` → `buy_rules = ['rsi < 30']`) specifically so the
engine generates trades on its sawtooth fixture. The deterministic parser now answers
`"oversold stocks with rising momentum"` at confidence 1.00 and **never consults the router at
all**, so the mock is bypassed and the real rules become:

```
buy_rules  = ['rsi < 35', 'price > sma_50']     # AND-ed, backtester.py:1285
sell_rules = ['rsi > 70']
```

On the fixture — a pure sine, `50 + 30*sin(...) + linspace(0,20,n)` — RSI is low exactly when
price sits below its 50-day mean, so the two conditions are structurally disjoint:
**0 of 2600 bars satisfy both.** No trades fire, and `assert len(res["trades"]) > 0` at line
169 fails.

Fix: pin the suite to the LLM path so its router mock controls the strategy again, as intended.

## Files

- `stratoscan/tests/test_api_dirty_data.py`

Nothing else. This task changes **no product code** and **does not touch the fixture**.

## Pattern

Identical to task 001. The suite already pins env at lines 34–35; add the flag alongside them,
before `src.config` is imported.

Before (`stratoscan/tests/test_api_dirty_data.py:34`):

```python
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
```

After:

```python
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["REDIS_RATE_LIMIT_ENABLED"] = "false"
# Pin the LLM path so the get_router mock below actually controls the
# strategy. The deterministic parser answers this suite's query at
# confidence 1.00 without consulting the router, and the rules it emits
# ('rsi < 35' AND 'price > sma_50') are never both true on the sine
# fixture — so the suite would measure nothing.
os.environ["USE_DETERMINISTIC_INTERPRETER"] = "false"
```

## Non-goals

- Do **not** modify the price fixture (`_frame()` around line 74). Its shape is the point of
  the suite — NaN/inf resilience, not signal realism. Making trades fire by reshaping the data
  would delete the test's actual purpose.
- Do **not** change `src/backtester.py`, `src/interpreter.py` or
  `src/deterministic_interpreter.py`. In particular do not touch `_translate_to_rules` — the
  rules it emits are documented intended behavior (`backtester.py:284-285`), and it sits next
  to frozen backtest math (golden rule #6).
- Do **not** relax the assertion at line 169 or any other assertion.
- No reformatting, renaming or drive-by edits.

## Baseline

Already failing before you start, not yours:

- `tests.test_api_dirty_data` ← the one you are fixing
- `tests.test_backfill`
- `tests.test_cache`

Everything else passes: 36/39 backend.

Note: if task 001 has already merged, `tests.test_cache` will be green and only
`tests.test_backfill` should remain red. Either baseline is fine — just don't chase a suite
that was already red when you started.

## Verify

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret

python -m tests.test_api_dirty_data   # must exit 0

ruff check .                          # must stay clean

for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
# only tests/test_backfill.py (and tests/test_cache.py if 001 has not merged)
# may appear. Anything else is a regression you introduced.
```

## Done

Commit and push branch `task/002-fix-test-api-dirty-data`. **Do not merge.**
