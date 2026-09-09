# 001 — Fix `tests/test_cache.py` (stale test: hybrid parser bypasses the LLM)

## Goal

`python -m tests.test_cache` passes. The suite exists to prove **LLM-call caching** — that an
identical query does not re-call the LLM. It asserts `calls_after_first == 1` and gets `0`,
because the deterministic parser (added later, in `5824f3c`) answers
`"undervalued tech stocks"` at confidence **1.00** and returns at `src/interpreter.py:348`
before `chat_completion` is ever reached. The fake router's counter therefore stays at zero.

Two changes: pin the suite to the LLM path so its existing assertions test what they were
written to test, then add one new case so the deterministic short-circuit — the behavior that
silently broke this suite — is itself pinned from now on.

## Files

- `stratoscan/tests/test_cache.py`

Nothing else. This task changes **no product code**.

## Pattern

The suite already pins env before importing `src.config` (line 31). Add the flag the same way,
in the same place, with a comment saying why.

Before (`stratoscan/tests/test_cache.py:31`):

```python
os.environ["RATE_LIMIT_ENABLED"] = "false"
```

After:

```python
os.environ["RATE_LIMIT_ENABLED"] = "false"
# Pin the LLM path: this suite measures LLM-call caching. The deterministic
# parser answers these queries at confidence 1.00 and returns before any LLM
# call, which would make every assertion below vacuous.
os.environ["USE_DETERMINISTIC_INTERPRETER"] = "false"
```

Ordering matters: this must come **before** `from src.config import settings`, because
`settings` is a module-level singleton that reads env at instantiation.

Then add one new test case at the end of the file, following the style already used there
(plain asserts, a `print("✅ ...")` line on success). It must:

1. Re-enable the deterministic parser for that case only — set
   `settings.use_deterministic_interpreter = True` and restore it in a `finally`.
2. Clear the interpret cache first so the earlier runs' entries don't mask the behavior
   (`from src.interpreter import _interpret_cache; _interpret_cache.clear()`).
3. Assert that with a `FakeLLMRouter` patched in, `interpret_query("undervalued tech stocks")`
   leaves `total_attempts == 0` — i.e. the deterministic path really did short-circuit.
4. Assert the returned `InterpretedQuery` has a non-empty `filters` list, so a silently empty
   parse can't pass as a short-circuit.

## Non-goals

- Do **not** change `src/interpreter.py`, `src/deterministic_interpreter.py` or `src/config.py`.
  The product behavior is correct; only the test is stale.
- Do **not** change the default of `use_deterministic_interpreter` (it is `True` and must stay).
- Do not reformat, rename, reorder or "tidy" any other part of the file.
- Do not touch any other suite, even if you notice something.

## Baseline

Before you start, these three are already failing and are **not yours**:

- `tests.test_api_dirty_data`
- `tests.test_backfill`
- `tests.test_cache` ← the one you are fixing

Everything else passes: 36/39 backend. If a fourth suite goes red, that one *is* yours.

## Verify

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret

python -m tests.test_cache          # must exit 0

ruff check .                        # must stay clean

# No collateral damage — only the two known suites may fail:
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
# expected output, exactly:
#   FAIL tests/test_api_dirty_data.py
#   FAIL tests/test_backfill.py
```

## Done

Commit and push branch `task/001-fix-test-cache`. **Do not merge.** The diff will be reviewed.
