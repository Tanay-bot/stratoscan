# Stratoscan — agent working rules

> Codebuff/Freebuff reads `knowledge.md` → `AGENTS.md` → `CLAUDE.md` and uses the first it
> finds, so **this file is what you get**. It is short on purpose. `../CLAUDE.md` has the full
> architecture reference (1,100+ lines) — open the section you need, don't read it all.

---

## 1. Verify loop — run these before you say you are done

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret          # boot guard; endpoints 503 without it

ruff check .                                    # must be clean
python -m tests.test_docs_contract              # OpenAPI snapshots + probe table
for t in tests/test_*.py; do                    # the full hermetic suite
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
```

Frontend:

```bash
cd frontend
npm run typecheck && npm run lint && npm run format:check
npm test                       # 205 tests / 26 files
npm run verify:walkforward
npm run verify:headers
npm run build
```

If you changed a route, a request model or a response model:

```bash
cd stratoscan && python scripts/regen_openapi.py
```

## 2. Baseline — nothing is red. Measure it, never quote it.

**Every backend suite passes.** There is no known-red list any more: the three suites that
used to be here (`test_cache`, `test_api_dirty_data`, `test_backfill`) were fixed by tasks
001–003. So a red suite is a real failure, and almost certainly yours.

Do not trust a count written in prose — including any count written below. Measure it
before you start, and again before you push:

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
ruff check .
```

Prints nothing and "All checks passed!" = clean. Frontend: `npm test` in `frontend/`.

Your task spec's **Baseline** section states the number expected on the day it was written.
If the measurement disagrees with the spec, say so and stop — do not fix an unrelated suite
to make the number match, and do not assume the spec is right.

> This section was itself stale for several tasks: it claimed 36/39 with three suites red
> when the tree was at 41/41 and green. An executor following it would have dismissed a
> real failure as pre-existing. That is why it now tells you to run the command instead of
> reading a number.

## 3. Hard nevers

Each of these is here because it actually happened in this repo.

| Never | What went wrong |
|---|---|
| **Never document behavior you did not implement.** | `frontend/README.md` described a whole silent-refresh auth system — proactive timer, single-flight, multi-tab rotation — with zero backing code. It shipped and was believed for months. |
| **Never name a test, doc section or process you have not opened.** | `CLAUDE.md` credited "R8 (contract diff)" for keeping `openapi.json` in sync. There is no R8. The snapshot rotted precisely because everyone believed it. |
| **Never write a count in prose. Point at the source instead.** | The probe count appeared as 15, 17 and 18 — twice contradicting itself inside one file. Say "the probes listed in `run_probes.py`". |
| **Never hand-edit `openapi.json` / `openapi.pretty.json`.** | Run `python scripts/regen_openapi.py`. Both files went stale from the initial commit and had drifted apart from *each other*. `test_docs_contract` now fails if you skip it. |
| **Never assume a config convention transfers between hosts.** | `public/_headers` is a Cloudflare/Netlify format. This project deploys to Vercel, which ignores it — so production shipped with no CSP, no HSTS, no `X-Frame-Options` while two docs claimed it was hardened. Headers live in `frontend/vercel.json`. |
| **Never leave a shell or YAML path unexecuted and assume it works.** | The deploy healthcheck had `${VAR:-...` with a missing `}` — an unterminated expansion nobody could hit locally. If you write shell in a workflow, `bash -n` it. |
| **Never add a `Settings` field without adding the key to `.env.example`.** | `validate_env_template()` fails the boot. `USE_DETERMINISTIC_INTERPRETER` had already slipped through. |
| **When you change behavior, reconcile every test that encoded the old behavior.** | The hybrid-parser commit touched three source files and zero tests, leaving `test_cache` and `test_api_dirty_data` red for weeks — and §2 above then described that staleness as normal. |
| **Never skip, disable, xfail or delete a test to get green.** | Fix the cause or say you could not. |
| **Never widen the task.** | Do what the spec says and nothing else. No drive-by refactors, no reformatting, no renames, no "while I was in there". |

## 4. Money-paths — stop and ask before touching

These break the product or its cost model. Full text in `../CLAUDE.md` §2.

- **One scheduler process, ever.** `python -m src.main scheduler` runs exactly once. Never add
  code that spawns a second one; never "fix" scaling by adding machines.
- **Backtest math is frozen.** `tests/test_backtest_golden.py` pins exact numbers. Any change
  to fees, slippage, signal generation, position sizing or portfolio logic must update that
  file — and if you are changing those numbers you should be certain why.
- **Screener = 0 external API calls per query.** Never add a per-stock API call to a hot path.
- **SQL injection is impossible by construction.** Column names resolve against the
  `STOCK_MASTER_COLUMNS` whitelist before interpolation; values are always parameterized.
- **Rate-limit scopes are cost shields**, not UX knobs. Don't loosen `screen`/`enrich`/
  `backtest`/`refresh`/`register` because someone hit a limit.
- **No raw exception text in API responses.** Log the detail, return a generic message.

## 5. How work arrives

Tasks come as spec files in `.claude/tasks/NNN-slug.md`. A spec names the exact files you may
touch, an explicit list of non-goals, the baseline, and the commands that must exit 0.

Work only inside `Files`. When the `Verify` commands pass, push a branch named
`task/NNN-slug`. **Do not merge** — every change is reviewed first.

If the spec is wrong, ambiguous, or cannot be satisfied without touching a file outside
`Files`: stop and say so. Do not improvise a wider change.
