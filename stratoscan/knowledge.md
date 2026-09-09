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

## 2. Baseline — these are ALREADY red. You did not cause them. Do not chase them.

| Suite | Why it fails today |
|---|---|
| `tests.test_cache` | asserts an LLM call for a query the deterministic parser now handles (conf 1.00) |
| `tests.test_api_dirty_data` | deterministic path bypasses the suite's router mock; the fixture never satisfies the resulting rules |
| `tests.test_backfill` | §6 calls real yfinance; fails without outbound network |

Everything else passes: **36/39 backend, 205/205 frontend, ruff clean.** If your change makes
that number go down, you broke something. Check the baseline before assuming a red suite is yours.

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
| **When you change behavior, reconcile every test that encoded the old behavior.** | The hybrid-parser commit touched three source files and zero tests, leaving two suites red — the two in the baseline above. |
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
