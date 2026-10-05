"""Billing providers — where a plan comes from (Day 12).

:func:`src.entitlements.plan_for` asks a :class:`BillingProvider` what
subscription an account has; nothing else in the app knows which provider is
behind it. Today there is exactly one implementation,
:class:`ManualBillingProvider`, which reads the plan from the ``users`` table
(an admin sets it by hand). A payment adapter becomes a second
implementation later — see ``BILLING.md`` for what it must implement — and
must pass the same conformance suite (``tests/billing_conformance.py``).

The provider owns *transitions*; the entitlement layer owns *meaning*:

- ``upgrade``    — effective immediately.
- ``downgrade``  — effective at the end of the current period.
- ``revoke``     — to Free after a grace period, whatever the reason.
- ``set_plan``   — an admin's hand: effective immediately, clears anything
  pending.

A provider must never be called over the network per request: ``subscription``
reads the row ``get_current_user`` already loaded. A real adapter keeps that
row in sync from its webhooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Protocol, runtime_checkable

from src.entitlements import (
    FREE,
    PLANS,
    grace_end,
    normalize_plan,
    period_end,
    utcnow,
)

# Higher = more access. Upgrade vs downgrade is decided by rank, never by name.
TIER_RANK = {FREE: 0, "pro": 1}


@dataclass(frozen=True)
class Subscription:
    plan: str                         # tier the account is on now
    pending_plan: Optional[str] = None  # tier it moves to at change_at
    change_at: Optional[datetime] = None
    grace: bool = False               # True when the pending change is a revocation


@runtime_checkable
class BillingProvider(Protocol):
    name: str

    def subscription(self, user: Dict[str, Any]) -> Subscription: ...

    def upgrade(self, user_id: int, plan: str, *, now: Optional[datetime] = None) -> None: ...

    def downgrade(self, user_id: int, plan: str, *, now: Optional[datetime] = None) -> None: ...

    def revoke(self, user_id: int, *, reason: str, now: Optional[datetime] = None) -> None: ...

    def set_plan(self, user_id: int, plan: str) -> None: ...


def change_plan(provider: BillingProvider, user: Dict[str, Any], plan: str,
                *, now: Optional[datetime] = None) -> str:
    """Move an account to ``plan`` with the right timing. Returns
    ``"upgrade"`` / ``"downgrade"`` / ``"unchanged"``."""
    from src.entitlements import plan_for

    target = normalize_plan(plan)
    current = plan_for(user, now=now, provider=provider).plan
    if TIER_RANK[target] > TIER_RANK[current]:
        provider.upgrade(int(user["id"]), target, now=now)
        return "upgrade"
    if TIER_RANK[target] < TIER_RANK[current]:
        provider.downgrade(int(user["id"]), target, now=now)
        return "downgrade"
    return "unchanged"


class ManualBillingProvider:
    """The plan lives on the user row; an admin changes it by hand.

    ``db_factory`` returns an object with ``set_plan_state(user_id, *, plan,
    pending, change_at, reason)`` — ``PostgresDB`` in production.
    """

    name = "manual"

    def __init__(self, db_factory: Optional[Callable[[], Any]] = None):
        self._db_factory = db_factory

    def _db(self):
        if self._db_factory:
            return self._db_factory()
        from src.database import PostgresDB

        return PostgresDB()

    def subscription(self, user: Dict[str, Any]) -> Subscription:
        return Subscription(
            plan=normalize_plan(user.get("plan")),
            pending_plan=user.get("plan_pending") or None,
            change_at=user.get("plan_change_at"),
            grace=(user.get("plan_change_reason") == "revoke"),
        )

    def upgrade(self, user_id: int, plan: str, *, now: Optional[datetime] = None) -> None:
        self._db().set_plan_state(user_id, plan=normalize_plan(plan), pending=None,
                                  change_at=None, reason=None)

    def downgrade(self, user_id: int, plan: str, *, now: Optional[datetime] = None) -> None:
        # Keep the current tier until the period ends; then the pending one.
        user = self._db().get_user_by_id(user_id) or {}
        self._db().set_plan_state(
            user_id, plan=normalize_plan(user.get("plan")), pending=normalize_plan(plan),
            change_at=period_end(now or utcnow()), reason="downgrade",
        )

    def revoke(self, user_id: int, *, reason: str, now: Optional[datetime] = None) -> None:
        user = self._db().get_user_by_id(user_id) or {}
        self._db().set_plan_state(
            user_id, plan=normalize_plan(user.get("plan")), pending=FREE,
            change_at=grace_end(now or utcnow()), reason="revoke",
        )

    def set_plan(self, user_id: int, plan: str) -> None:
        if normalize_plan(plan) not in PLANS:
            raise ValueError(plan)
        self._db().set_plan_state(user_id, plan=normalize_plan(plan), pending=None,
                                  change_at=None, reason=None)


_PROVIDER: Optional[BillingProvider] = None


def get_billing_provider() -> BillingProvider:
    """The configured provider. One today; a payment adapter is registered
    here later and nothing else changes."""
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = ManualBillingProvider()
    return _PROVIDER


def set_billing_provider(provider: Optional[BillingProvider]) -> None:
    """Tests (and a future adapter's wiring) swap the provider here."""
    global _PROVIDER
    _PROVIDER = provider
