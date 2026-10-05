"""The conformance suite every BillingProvider must pass (Day 12).

Written while the requirements are fresh, BEFORE any payment provider exists.
``ManualBillingProvider`` passes it today (``tests/test_entitlements.py``
runs it); a payment adapter must pass the same function with a test double
for its API — that is the contract, not the adapter's own tests.

Usage::

    from tests.billing_conformance import run_conformance
    run_conformance(make_provider=lambda db: MyAdapter(db_factory=lambda: db),
                    make_db=FakeUserDB)

``make_db()`` returns a store with the user methods of ``FakeUserDB``
(create_user, get_user_by_id, set_plan_state); ``make_provider(db)`` returns
the provider under test, wired to that store.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.billing import BillingProvider, Subscription, change_plan
from src.entitlements import FREE, GRACE_DAYS, GRANDFATHER_LIMIT, PRO, period_end, plan_for

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


def _fresh(db: Any, email: str) -> int:
    # Exhaust grandfathering so the account's plan comes from the provider.
    if hasattr(db, "grandfather_issued"):
        db.grandfather_issued = GRANDFATHER_LIMIT
    return db.create_user(email, "h", "free")


def run_conformance(*, make_provider: Callable[[Any], BillingProvider], make_db: Callable[[], Any]) -> int:
    """Raises AssertionError on the first failure; returns the check count."""
    checks = 0

    def ok(cond: bool, msg: str) -> None:
        nonlocal checks
        assert cond, msg
        checks += 1

    db = make_db()
    prov = make_provider(db)
    ok(isinstance(prov, BillingProvider), "implements the BillingProvider protocol")
    ok(isinstance(prov.name, str) and prov.name and prov.name != "grandfathered",
       "has a name (shown as the entitlement source)")

    def ent(uid: int, now: datetime = T0):
        return plan_for(db.get_user_by_id(uid), now=now, provider=prov)

    # A new account is Free.
    uid = _fresh(db, "c1@x.test")
    sub = prov.subscription(db.get_user_by_id(uid))
    ok(isinstance(sub, Subscription) and sub.plan == FREE and sub.pending_plan is None,
       "a new account's subscription is Free with nothing pending")

    # Upgrade: immediate.
    prov.upgrade(uid, PRO, now=T0)
    ok(ent(uid).plan == PRO, "upgrade takes effect immediately")
    prov.upgrade(uid, PRO, now=T0)
    ok(ent(uid).plan == PRO and ent(uid).pending_plan is None, "upgrading twice is idempotent")

    # Downgrade: at period end, not before.
    prov.downgrade(uid, FREE, now=T0)
    before = ent(uid, T0 + timedelta(days=1))
    ok(before.plan == PRO and before.pending_plan == FREE and not before.in_grace,
       "a downgrade keeps Pro until the period ends, with the change visible")
    end = before.change_at
    ok(end is not None and end > T0, "a downgrade is scheduled in the future")
    ok(end == period_end(T0) or prov.name != "manual", "the manual provider's period ends at month end")
    ok(ent(uid, end - timedelta(seconds=1)).plan == PRO, "still Pro one second before the period ends")
    ok(ent(uid, end).plan == FREE, "Free from the moment the period ends")

    # Upgrade while a downgrade is pending cancels it.
    uid2 = _fresh(db, "c2@x.test")
    prov.upgrade(uid2, PRO, now=T0)
    prov.downgrade(uid2, FREE, now=T0)
    prov.upgrade(uid2, PRO, now=T0)
    ok(ent(uid2, T0 + timedelta(days=60)).plan == PRO, "re-upgrading cancels a pending downgrade")

    # Revocation for any reason: grace first.
    uid3 = _fresh(db, "c3@x.test")
    prov.upgrade(uid3, PRO, now=T0)
    prov.revoke(uid3, reason="payment_failed", now=T0)
    during = ent(uid3, T0 + timedelta(days=GRACE_DAYS) - timedelta(minutes=1))
    ok(during.plan == PRO and during.in_grace, f"a revoked account keeps access for {GRACE_DAYS} days")
    ok(ent(uid3, T0 + timedelta(days=GRACE_DAYS)).plan == FREE, "and loses it when the grace period ends")

    # An admin's hand: immediate in both directions, clears anything pending.
    uid4 = _fresh(db, "c4@x.test")
    prov.upgrade(uid4, PRO, now=T0)
    prov.downgrade(uid4, FREE, now=T0)
    prov.set_plan(uid4, FREE)
    ok(ent(uid4).plan == FREE and ent(uid4).pending_plan is None, "set_plan(free) is immediate and clears pending")
    prov.set_plan(uid4, "premium")
    ok(ent(uid4).plan == PRO, "set_plan accepts the old tier name 'premium' as Pro")

    # change_plan picks the timing from the tier order.
    uid5 = _fresh(db, "c5@x.test")
    ok(change_plan(prov, db.get_user_by_id(uid5), PRO, now=T0) == "upgrade", "Free → Pro is an upgrade")
    ok(change_plan(prov, db.get_user_by_id(uid5), FREE, now=T0) == "downgrade", "Pro → Free is a downgrade")
    ok(change_plan(prov, db.get_user_by_id(uid5), "pro", now=T0) == "unchanged", "same tier is unchanged")

    # subscription() must not do I/O per request: it reads the row it is given.
    row = dict(db.get_user_by_id(uid5))
    real = {m: getattr(db, m) for m in ("get_user_by_id", "set_plan_state") if hasattr(db, m)}

    def _boom(*a, **k):
        raise RuntimeError("subscription() touched the store")

    try:
        for m in real:
            setattr(db, m, _boom)
        prov.subscription(row)
        ok(True, "subscription() reads only the row it is given")
    finally:
        for m, fn in real.items():
            setattr(db, m, fn)
    return checks
