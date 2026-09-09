# 003 — Make `build_backfill_history` injectable so `test_backfill` is genuinely hermetic

## Goal

`python -m tests.test_backfill` passes **without network access**, making the suite honest
about its own docstring.

`tests/test_backfill.py` claims "no network, no DB" and is hermetic in sections 1–5, which all
pass `fetch_fn=_fake_fetch` into `backfill_history`. Section 6 tests the CLI wrapper
`build_backfill_history` instead — and that wrapper exposes only
`symbols / years / limit / verify`, with no way to inject a fetcher. So it falls through to
`fetch_fn = fetch_fn or fetch_historical_data` (`src/backfill.py:226`) and calls **real
yfinance**. In any sandboxed or offline environment that request fails, NVDA yields no data,
`backfilled` is `0`, and `check("wrapper returns stats", stats6.get("backfilled") == 1)` fails.

This is a real violation of golden rule #7 (hermetic tests only), not just an environment
quirk — the suite has been network-dependent since the initial commit.

## Files

- `stratoscan/src/backfill.py`
- `stratoscan/tests/test_backfill.py`

Nothing else.

## Pattern

`backfill_history` **already** accepts `fetch_fn`, `store_fn` and `db`. `build_backfill_history`
simply fails to forward them. Add them as optional passthrough parameters with `None` defaults
so every existing caller is unaffected.

Before (`stratoscan/src/backfill.py:336`):

```python
def build_backfill_history(
    symbols: Optional[List[str]] = None,
    years: Optional[int] = None,
    limit: Optional[int] = None,
    verify: bool = True,
) -> Dict[str, Any]:
    ...
    return backfill_history(
        symbols=symbols,
        years=years or 10,
        limit=limit,
        verify=verify,
    )
```

After — add the three parameters, document them in the existing Args block in the same style
as the current entries, and forward them:

```python
def build_backfill_history(
    symbols: Optional[List[str]] = None,
    years: Optional[int] = None,
    limit: Optional[int] = None,
    verify: bool = True,
    fetch_fn: Optional[Callable[..., Any]] = None,
    store_fn: Optional[Callable[..., Any]] = None,
    db: Optional[Any] = None,
) -> Dict[str, Any]:
    ...
    return backfill_history(
        symbols=symbols,
        years=years or 10,
        limit=limit,
        verify=verify,
        fetch_fn=fetch_fn,
        store_fn=store_fn,
        db=db,
    )
```

These annotations are copied verbatim from `backfill_history` (`src/backfill.py:343-345`) —
use them exactly. `Callable`, `Any` and `Optional` are **already imported** at
`src/backfill.py:43`, so no import change is needed.

Then in `tests/test_backfill.py` section 6 (around line 213), pass the fake fetcher the same
way sections 1–5 already do (see `:123` and `:147` for the existing calls):

```python
stats6 = build_backfill_history(
    symbols=["NVDA"], years=2, verify=False, fetch_fn=_fake_fetch,
)
```

`_fake_fetch` is defined at `tests/test_backfill.py:69`. Do not rename or redefine it.

## Non-goals

- Do **not** change the behavior of `backfill_history` itself. Only the wrapper's signature
  and its forwarding call change.
- Do **not** change the defaults: with no arguments, `build_backfill_history` must still use
  the real `fetch_historical_data` and the real `BacktestDB`. Production behavior is unchanged.
- Do **not** weaken or delete the `check("wrapper returns stats", ...)` assertion — it must
  still require `backfilled == 1`.
- Do not touch `src/backtester.py`, and do not alter the `mock.patch.object(backfill,
  "BacktestDB", ...)` that section 6 already uses to assert the owned DB is closed.
- No reformatting or unrelated cleanup.

## Baseline

Already failing before you start, not yours:

- `tests.test_backfill` ← the one you are fixing
- `tests.test_api_dirty_data` (unless 002 has merged)
- `tests.test_cache` (unless 001 has merged)

Everything else passes.

## Verify

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret

python -m tests.test_backfill    # must exit 0 — and must do so with no network

ruff check .                     # must stay clean

for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
```

Prove the hermeticity claim, not just the pass. Disconnect the network (or block outbound
DNS) and re-run `python -m tests.test_backfill`. It must still exit 0. If it only passes while
online, the injection is not wired through and the task is not done.

## Done

Commit and push branch `task/003-backfill-fetch-fn-passthrough`. **Do not merge.**
