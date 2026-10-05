# Billing — what the payment adapter must implement

> **Owner:** entitlements and the billing-provider contract. The rules for *who may do what* live
> in `src/entitlements.py`; *where a plan comes from* lives in `src/billing.py`. This file is the
> written note Day 12 asks for: exactly what a real payment provider has to add, so taking money
> is a day's adapter work rather than a refactor.

## Today

- **One entitlement function.** `plan_for(user)` reads the user row `get_current_user` already
  loaded on this request (no cache — an admin's change applies on the next request) and returns
  the tier in force, its limits and any pending change. Every gated route goes through
  `Depends(gate(FEATURE))` in `src/api.py`; the digest checks `MONITORING_EMAILS` per user.
- **Four gated capabilities, nothing else:** backtests per month, saved strategy count, CSV export,
  monitoring (weekly digest) emails. Numbers in `entitlements.PLANS` (mirrored for the public
  pages in `frontend/src/lib/plans.ts`, checked by `tests/test_entitlements.py`).
- **Never gated:** the history depth, fees/slippage, the SPY benchmark, Monte Carlo / bootstrap /
  walk-forward / parameter grid, the rules and code behind a result, `/explain`, share pages.
  `tests/test_entitlements.py` asserts a Free backtest equals a Pro one.
- **One provider:** `ManualBillingProvider` — the plan lives on `users` and an admin sets it from
  the dashboard (`PATCH /admin/users/{id}` `{plan, plan_change: "now"|"scheduled"}`).
- **Grandfathering:** the first 100 accounts ever (`app_counters.grandfathered_pro`, issued inside
  the signup transaction; deleting an account never frees a slot) are Pro for life, whatever any
  provider later says.
- **Plan-change semantics:** upgrade immediate · downgrade at period end · revocation for any
  reason after `GRACE_DAYS` (7) · admin set immediate. One mechanism: `plan` +
  `plan_pending` applying at `plan_change_at` (`plan_change_reason` = `downgrade` | `revoke`).
- **No prices anywhere** until `VITE_PRICING_ENABLED=true` and `VITE_PRO_PRICE` are both set
  (frontend); the API never returns a price.

## What a payment adapter must implement

A class satisfying `src.billing.BillingProvider`, registered in `get_billing_provider()`, that
passes `tests/billing_conformance.run_conformance(...)` with a test double for the provider's API.

| Method | Must do |
|---|---|
| `name` | short id shown as the entitlement source (e.g. `"stripe"`) |
| `subscription(user)` | build a `Subscription` **from the row only** — never call the provider per request (the conformance suite fails if it touches the store) |
| `upgrade(user_id, plan)` | start/upgrade the paid subscription; on success write `plan` immediately |
| `downgrade(user_id, plan)` | schedule at the provider's period end; write `plan_pending` + `plan_change_at` = that period end |
| `revoke(user_id, reason)` | payment failed / refund / fraud: `plan_pending=free`, `plan_change_at = now + GRACE_DAYS`, reason `revoke` |
| `set_plan(user_id, plan)` | an admin override — immediate; must also stop the provider from re-applying an old state on the next webhook |

Plus the parts the interface does not express yet — **the gaps to close before the first charge**:

1. **Customer identity.** A `users.billing_customer_id` (and subscription id) column, set once at
   checkout. Nothing maps an account to a provider customer today.
2. **Webhooks, idempotently.** An authenticated `POST /billing/webhook` that verifies the
   provider's signature, de-duplicates by event id (a `billing_events` table with a unique key —
   providers retry and reorder), and translates events into the four methods above. The row is
   the cache; webhooks are the only writer besides the admin.
3. **Out-of-order events.** Store the provider's own event timestamp / subscription version and
   ignore events older than the last applied one — otherwise a late "active" can undo a revoke.
4. **Period end comes from the provider.** `entitlements.period_end()` is the manual provider's
   calendar month; a paid subscription's period is the provider's `current_period_end`.
5. **Usage period vs billing period.** Metered backtests reset on the calendar month (UTC). Decide
   whether a paid plan's allowance should follow the subscription period instead; if so, the
   period key passed to `consume_usage` must come from the provider.
6. **Checkout and portal.** Routes that create a checkout session and a customer-portal link
   (cancel, change card). Neither exists.
7. **Trials, proration, coupons, tax, currency.** Not modelled. If any is offered, decide first
   whether it changes *entitlements* (then it belongs in `plan_for`) or only *money* (then it stays
   inside the adapter).
8. **Grandfathered accounts.** Never send them to checkout; `plan_for` ignores the provider for
   them, so a stray subscription would charge for nothing.
9. **Admin vs provider precedence.** Today the admin's hand is final. With a live provider, decide
   whether an admin override pins the plan (a `plan_locked` flag the webhook respects) or is
   overwritten by the next event.
10. **Prices.** Flip `VITE_PRICING_ENABLED` and set `VITE_PRO_PRICE` only after the checkout works
    end to end; `pricing.test.tsx` keeps them hidden until then.

## Boundaries already pinned (`tests/test_entitlements.py`, real-Postgres checks)

- Exactly at the limit: the last slot is usable once; the next call is refused (403,
  `upgrade_required`). The charge is one atomic `INSERT … ON CONFLICT … WHERE count < limit`, so
  two requests racing for the last slot cannot both win (verified with 50 concurrent callers).
- Month rollover mid-request: the period is computed once per request.
- Plan changed mid-session: no entitlement cache, so the next request sees it.
- A refused request charges nothing; a failed backtest that was accepted *does* count (it was
  started). Revisit if users report it.
