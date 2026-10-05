"""Entitlements — the ONE place that decides what an account may do (Day 12).

Every gated route consults :func:`plan_for`. Scattered ``if user.plan ==``
checks are where billing bugs come from (access nobody paid for, or paid
access refused), so there are none: the routes ask this module, and this
module asks the billing provider.

Exactly four things are gated (the plan's list, nothing more):

    backtests per month · saved strategy count · CSV export · monitoring emails

Never gated, for anyone: accuracy. The robustness analyses (walk-forward,
Monte Carlo, bootstrap, the parameter grid), the "show the code" layer, the
explanation of a result and public share pages are the product's
credibility — a product that sells trust in tiers is selling something else.
``tests/test_entitlements.py`` pins that a free backtest is byte-for-byte the
same result as a Pro one.

Plan-change semantics (defined now, before any payment provider exists):

- **Upgrade** takes effect immediately.
- **Downgrade** takes effect at the end of the current period.
- **Revocation** for any reason (a failed payment, a refund, an admin) keeps
  access for a grace period first.

All three are one mechanism: the account's ``plan`` plus an optional
``plan_pending`` that applies at ``plan_change_at``. :func:`plan_for` reads
both from the user row on every request — no entitlement cache, so a plan set
by an admin applies on the very next request.

The first :data:`GRANDFATHER_LIMIT` accounts are Pro permanently
(``users.grandfathered_pro``, issued by an atomic counter at signup — see
``PostgresDB.create_user``), whatever the billing provider later says.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

FREE = "free"
PRO = "pro"
# Older rows (and the old admin UI) say "premium": the same tier.
PLAN_ALIASES = {"premium": PRO}

# The first hundred accounts are Pro for life — decided now, encoded here.
GRANDFATHER_LIMIT = 100
# Revocation for any reason keeps access this long first.
GRACE_DAYS = 7

# Feature names — the ONLY four gated capabilities.
BACKTESTS = "backtests"
SAVED_STRATEGIES = "saved_strategies"
CSV_EXPORT = "csv_export"
MONITORING_EMAILS = "monitoring_emails"
FEATURES = (BACKTESTS, SAVED_STRATEGIES, CSV_EXPORT, MONITORING_EMAILS)


@dataclass(frozen=True)
class PlanLimits:
    backtests_per_month: Optional[int]  # None = unlimited (rate limits still apply)
    saved_strategies: int
    csv_export: bool
    monitoring_emails: bool


PLANS: Dict[str, PlanLimits] = {
    FREE: PlanLimits(backtests_per_month=50, saved_strategies=3, csv_export=False, monitoring_emails=False),
    PRO: PlanLimits(backtests_per_month=None, saved_strategies=20, csv_export=True, monitoring_emails=True),
}
PLAN_LABELS = {FREE: "Free", PRO: "Pro"}


def normalize_plan(plan: Any) -> str:
    """A known tier id; anything unknown is Free (fail closed, never open)."""
    p = str(plan or FREE).strip().lower()
    p = PLAN_ALIASES.get(p, p)
    return p if p in PLANS else FREE


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def usage_period(now: Optional[datetime] = None) -> str:
    """The calendar month (UTC) usage is counted in, e.g. ``2026-10``.

    Computed ONCE per request and passed down, so a request that straddles
    midnight on the 1st is charged to a single month.
    """
    return (now or utcnow()).strftime("%Y-%m")


def period_end(now: Optional[datetime] = None) -> datetime:
    """End of the current billing period for the manual provider: the start
    of next month, UTC. (A payment provider supplies its own.)"""
    n = now or utcnow()
    year, month = (n.year + 1, 1) if n.month == 12 else (n.year, n.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Entitlement:
    plan: str                      # the tier in force right now
    source: str                    # "grandfathered" | the provider's name
    limits: PlanLimits
    pending_plan: Optional[str] = None
    change_at: Optional[datetime] = None
    in_grace: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def allows(self, feature: str) -> bool:
        """Boolean features only (counted ones are checked against usage)."""
        if feature == CSV_EXPORT:
            return self.limits.csv_export
        if feature == MONITORING_EMAILS:
            return self.limits.monitoring_emails
        raise ValueError(f"{feature} is a counted feature; use limit_for()")

    def limit_for(self, feature: str) -> Optional[int]:
        if feature == BACKTESTS:
            return self.limits.backtests_per_month
        if feature == SAVED_STRATEGIES:
            return self.limits.saved_strategies
        raise ValueError(f"{feature} is not a counted feature")

    def public(self) -> Dict[str, Any]:
        return {
            "plan": self.plan,
            "plan_label": PLAN_LABELS[self.plan],
            "source": self.source,
            "limits": asdict(self.limits),
            "pending_plan": self.pending_plan,
            "change_at": self.change_at.isoformat() if self.change_at else None,
            "in_grace": self.in_grace,
        }


def plan_for(user: Dict[str, Any], *, now: Optional[datetime] = None, provider: Any = None) -> Entitlement:
    """What this account may do RIGHT NOW. Pure: reads the user row only.

    ``user`` is the row ``get_current_user`` just loaded for this request,
    so there is no cache to go stale.
    """
    from src.billing import get_billing_provider

    now = now or utcnow()
    if user.get("grandfathered_pro"):
        return Entitlement(plan=PRO, source="grandfathered", limits=PLANS[PRO])

    prov = provider or get_billing_provider()
    sub = prov.subscription(user)
    plan = normalize_plan(sub.plan)
    pending = normalize_plan(sub.pending_plan) if sub.pending_plan else None
    change_at = _aware(sub.change_at)
    in_grace = False
    if pending and change_at:
        if now >= change_at:
            plan, pending, change_at = pending, None, None
        else:
            in_grace = bool(sub.grace)
    return Entitlement(
        plan=plan, source=prov.name, limits=PLANS[plan],
        pending_plan=pending, change_at=change_at, in_grace=in_grace,
    )


def grace_end(now: Optional[datetime] = None) -> datetime:
    return (now or utcnow()) + timedelta(days=GRACE_DAYS)
