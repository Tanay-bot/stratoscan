# 004 — Populate `stock_master.volume` from data we already hold

## Goal

Volume filters are advertised to the UI and match nothing.

`GET /meta` publishes `min_volume` and `min_avg_volume / max_avg_volume` as filter
fields (`src/api.py:1302-1303`), and `volume` / `avg_volume` are both in
`screener.SORTABLE_COLUMNS`. So the frontend renders those filters and that sort
option. Every one of them returns zero rows, because `stock_master.volume` is NULL for
every stock — this Finnhub plan's `/quote` response omits the `v` field entirely
(documented in `stratoscan/SCHEDULER.md`).

The data is not missing. It is in the wrong table. `historical_prices.volume` is
`BIGINT NOT NULL` and is populated for every symbol the cache warmer has fetched.

Add a sync that reads volume out of the backtest database and writes it into
`stock_master`. **Zero external API calls** — this reads one database and writes
another, which keeps golden rule #2 intact.

Scope is the module, its test, and a CLI command. **Scheduler wiring is deliberately
not part of this task** (see Non-goals).

## Files

- `stratoscan/src/volume_sync.py` *(new)*
- `stratoscan/tests/test_volume_sync.py` *(new)*
- `stratoscan/src/main.py` *(one new CLI subcommand only)*

Nothing else. In particular do not touch `screener.py`, `api.py`, `scheduler.py` or
`job_monitor.py`.

## Pattern

### The module

Create `src/volume_sync.py` with exactly this public surface:

```python
def sync_volume_from_history(
    backtest_db: Any = None,
    screener_db: Any = None,
    symbols: Optional[List[str]] = None,
    avg_window_days: int = 30,
) -> Dict[str, Any]:
    """Copy volume from the backtest DB's bars into stock_master.

    Returns stats: {"candidates": int, "updated": int, "skipped": int,
                    "no_data": int}
    """
```

Both `db` parameters default to `None` and are constructed internally when not
supplied — the same ownership pattern `backfill.backfill_history` uses. **They must be
injectable**, because the test injects fakes and the suite has to stay hermetic
(golden rule #7).

Per symbol:

1. Read its bars from the backtest DB via `get_historical_prices(symbol)`.
2. `volume` = the volume of the **most recent** bar.
3. `avg_volume` = the mean volume over the last `avg_window_days` bars, rounded to
   an integer. If fewer bars exist, average what is there.
4. Skip the symbol entirely if it has no bars, or if every volume is null/zero.

Then write in batches with `PostgresDB.bulk_upsert_stocks(records)`, where each record
is exactly:

```python
{"symbol": "AAPL", "volume": 51234567, "avg_volume": 48200000}
```

That method builds its column list from each record's own keys, so a record with only
these three fields updates only these three columns and leaves everything else alone.
Verify that claim by reading `database.py:1045` before you rely on it.

**One documented gotcha you must handle.** `bulk_upsert_stocks` backfills
`company_name` from existing rows for records that omit it, and then *drops any record
still missing a name* — that is how genuinely-new symbols are skipped. For symbols
already in `stock_master` this is fine. But it means the count of rows you submit may
exceed the count actually written, so **do not report `updated` from `len(records)`**.
Count what you submitted as `candidates`, and determine `updated` by reading back, or
by intersecting your symbol list with the symbols already present in `stock_master`.

### The CLI command

`src/main.py` is an argparse dispatcher where each command imports its dependencies
lazily inside the handler. Follow the existing commands exactly:

```
python -m src.main sync-volume
python -m src.main sync-volume --symbols AAPL,MSFT --avg-window 60
```

### The test

`tests/test_volume_sync.py`, in this repo's script style: module-level asserts,
`print("✅ …")` per check, a `Run: python -m tests.test_volume_sync` line in the
docstring. See `tests/test_lse_source.py` for the shape. It must be hermetic — fake
both databases, no network, no real DB.

Pin at minimum:

- A symbol with known bars produces the **hand-computed** latest volume and window
  average. Use a series where the right answer is obvious, e.g. volumes
  `[100, 200, 300, 400]` with `avg_window_days=2` → `volume=400`, `avg_volume=350`.
- Fewer bars than the window averages what exists rather than erroring.
- A symbol with no bars is counted in `no_data` and never submitted.
- The records passed to `bulk_upsert_stocks` contain **only** the keys `symbol`,
  `volume`, `avg_volume` — assert this explicitly by capturing the call. This is the
  assertion that stops a future change from clobbering other columns.
- Every returned stat is an int and `candidates == updated + skipped + no_data`.

## Non-goals

- **Do not wire this into `scheduler.py` or `job_monitor.JOB_DEFINITIONS`.** That area
  is governed by golden rule #1 (exactly one scheduler process, ever) and the cadence
  constants live in two files that must stay in sync. It is a separate reviewed task.
- Do not change the screener, its filters, or `SORTABLE_COLUMNS`. They are already
  correct — they were only ever missing data.
- Do not call Finnhub, yfinance, or any other external API. The whole point is that
  this needs none.
- Do not alter `bulk_upsert_stocks`, `get_historical_prices`, or any schema.
- No reformatting, renaming or drive-by cleanup anywhere.

## Baseline

Before you start, everything is green. Confirm it first:

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
```

That should print nothing — **40/40 passing**. If a suite fails here, it failed before
you touched anything; say so and stop rather than fixing it.

## Verify

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret

python -m tests.test_volume_sync          # must exit 0

ruff check .                              # must stay clean

python -m src.main sync-volume --help     # command is registered

# No regressions — this must still print nothing:
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done

# The env template must still be complete (golden rule #4):
python -c "from src.config import validate_env_template as v; print(v() or 'complete')"
```

## Done

Commit and push branch `task/004-populate-volume`. **Do not merge.** Claude reviews the
diff first.
