"""Product analytics — the ONE entry point for every event the app records.

Why this module exists
----------------------
The first cohort teaches its lesson exactly once: without a funnel you cannot
tell whether signup loses people at the landing page, the screener, or the
backtest. So every event, server-side or browser-side, lands in one `events`
table (main DB) through one function — :func:`track`. Swapping in a hosted
tool later is then a one-function change rather than a hunt through routes.

What this module refuses to do
------------------------------
**No PII, ever.** Query text is user content and may be personal, emails are
identifiers, exception messages can carry credentials. So the vocabulary below
declares, per event, the ONLY prop keys that event may carry (`EVENTS`), and
:func:`track` silently drops anything else. A caller passing ``{"query": ...}``
records nothing for that key — the whitelist is the enforcement, not a comment.
For the same reason `backtest_failed` carries a fixed reason code and
`query_submitted` carries only the surface name.

Three further properties, each of which the tests pin:

  1. ``track()`` **never raises**. Analytics is telemetry: a broken event write
     must not fail a screen or a signup. The whole body is wrapped.
  2. The browser may only send the names in :data:`CLIENT_EVENTS`. Everything
     else is server-derived, so a script cannot forge the funnel
     (``POST /events`` answers 400 for a server-only name).
  3. Writing is a no-op when ``settings.analytics_enabled`` is False, so an
     operator can turn the whole thing off without removing call sites.

Derived events
--------------
``second_backtest``, ``returned_day_2`` and ``returned_day_7`` are never sent
by a caller: :func:`track` computes them from the identity's own history right
after a successful insert (see :func:`_fire_derived`). Identity is the
``user_id`` when there is one, else the browser's ``anon_id`` — which is what
lets pre-signup activity join the signed-in funnel.

"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from src.config import settings

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# The event vocabulary
# ═══════════════════════════════════════════════════════════════════════════════
# name -> the ONLY prop keys that event is allowed to carry. An event value of
# an empty frozenset means "carries no props at all" — which is the honest
# description of ``landing_view`` and is how the whitelist stays total: there
# is no event whose props are unrestricted.
EVENTS: Dict[str, FrozenSet[str]] = {
    # ── Browser-reported ────────────────────────────────────────────────
    "landing_view": frozenset(),
    # Only WHICH surface was submitted — never the query text.
    "query_submitted": frozenset({"surface"}),  # surface: screener | backtest
    "template_used": frozenset({"template"}),   # the chip's id/index, not its text
    "result_shared": frozenset(),
    "signup_started": frozenset({"method"}),    # method: password | google
    # ── Server-reported ─────────────────────────────────────────────────
    "interpret_deterministic": frozenset({"confidence"}),
    "interpret_llm": frozenset(),
    "screen_returned": frozenset({"result_count", "total_matches"}),
    "backtest_started": frozenset({"symbols", "mode"}),   # mode: sync | async
    "backtest_completed": frozenset({"symbols", "mode"}),
    "backtest_failed": frozenset({"mode", "reason"}),     # reason: a FIXED code
    "backtest_duration": frozenset({"duration_ms", "mode"}),
    "signup_completed": frozenset({"method"}),
    # ── Derived (never sent by a caller) ────────────────────────────────
    "second_backtest": frozenset(),
    "returned_day_2": frozenset(),
    "returned_day_7": frozenset(),
}

# The subset the browser is allowed to POST to /events. Everything else is
# server-derived: if a client could send ``backtest_completed`` it could forge
# the conversion rate, which is the number the funnel exists to report.
CLIENT_EVENTS: FrozenSet[str] = frozenset({
    "landing_view",
    "query_submitted",
    "template_used",
    "result_shared",
    "signup_started",
})

# Computed by track() from the identity's history — never accepted from a
# caller, and never re-triggering the computation that produced them.
DERIVED_EVENTS: FrozenSet[str] = frozenset({
    "second_backtest",
    "returned_day_2",
    "returned_day_7",
})

#: Longest string prop value kept. Anything longer is dropped: fields that
#: want to carry free text would otherwise become a PII channel by accident.
MAX_STR_PROP_CHARS = 64


# ═══════════════════════════════════════════════════════════════════════════════
# Validation / sanitising
# ═══════════════════════════════════════════════════════════════════════════════

def _clean_props(name: str, props: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep only the prop keys whitelisted for ``name``, with simple values.

    ``props`` arrives from callers and, on the HTTP path, from the browser —
    so it is treated as untrusted. Two independent gates:

      * the key must be in ``EVENTS[name]`` (the PII gate), and
      * the value must be a bool, a number, or a string of at most
        :data:`MAX_STR_PROP_CHARS` characters (the junk gate — dicts, lists,
        ``None`` and unbounded strings are dropped, which also keeps the row
        cheap and the column queryable).

    Args:
        name: An event name already known to be in :data:`EVENTS`.
        props: The caller's props, or None.

    Returns:
        A fresh dict with only the surviving pairs (possibly empty).
    """
    allowed = EVENTS[name]
    if not props or not isinstance(props, dict):
        return {}
    clean: Dict[str, Any] = {}
    for key, value in props.items():
        if key not in allowed:
            continue
        # bool first: it is a subclass of int, and keeping the distinction
        # means JSONB round-trips True rather than 1.
        if isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, (int, float)):
            clean[key] = value
        elif isinstance(value, str):
            if len(value) <= MAX_STR_PROP_CHARS:
                clean[key] = value
    return clean


def _clean_anon_id(anon_id: Optional[str]) -> Optional[str]:
    """Return ``anon_id`` only when it parses as a UUID, else None.

    The value comes from a client-controlled header (``X-Anon-Id``), so it is
    validated rather than stored on trust: it is an identity key, and an
    arbitrary string there would let one client impersonate another's
    pre-signup funnel.
    """
    if not anon_id:
        return None
    try:
        uuid.UUID(str(anon_id))
    except (ValueError, AttributeError, TypeError):
        return None
    return str(anon_id)


def _clean_user_id(user_id: Any) -> Optional[int]:
    """Return ``user_id`` as an int, or None when it is not a usable id."""
    if user_id is None or isinstance(user_id, bool):
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def _identity(user_id: Optional[int],
              anon_id: Optional[str]) -> Optional[Tuple[str, List[Any]]]:
    """Build the identity predicate for the funnel queries.

    Identity is the ``user_id`` when there is one, otherwise the ``anon_id``.
    A signed-in user's events never mix with anonymous ones — that is what
    makes "this person's first event" well defined across the signup boundary.

    Returns:
        ``(where_fragment, params)``, or None when there is no identity at all
        (no user, no valid anon id) — in which case derived events are skipped
        rather than computed across every anonymous row in the table.
    """
    if user_id is not None:
        return "user_id = %s", [user_id]
    if anon_id:
        return "anon_id = %s", [anon_id]
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Storage
# ═══════════════════════════════════════════════════════════════════════════════

def _db():
    """Open a main-DB client.

    Imported lazily so ``src.analytics`` stays importable in contexts that
    never touch the database (and so a test can patch a single seam). Tests
    patch ``src.analytics._db`` with an in-memory recorder.
    """
    from src.database import PostgresDB

    return PostgresDB()


def _record(name: str, *, user_id: Optional[int], anon_id: Optional[str],
            props: Dict[str, Any]) -> None:
    """Write one row. The only place an event row is ever inserted."""
    _db().insert_event(name, user_id=user_id, anon_id=anon_id, props=props)


def _fire_derived(name: str, *, user_id: Optional[int],
                  anon_id: Optional[str]) -> None:
    """Record the derived events implied by the row just written.

    Called after a successful insert, at most once per identity:

      * ``second_backtest`` — a ``backtest_completed`` insert made the
        identity's completed-backtest count exactly 2. This is the retention
        signal that matters: one backtest is curiosity, the second is habit.
        Deliberately ``== 2`` and not ``>= 2``, so the event means "this is the
        moment they came back" and cannot re-fire on the tenth run.
      * ``returned_day_2`` — the identity's FIRST event was on the previous
        UTC calendar day and this one does not exist for it yet.
      * ``returned_day_7`` — same rule for a first event 6, 7 or 8 UTC days
        earlier (a window rather than a point, so a user who visits every
        other day still registers, and a week-long gap lands exactly once).

    Values are compared as UTC calendar dates, not elapsed hours: "came back
    the next day" is a day boundary, and an hour-based rule would call a visit
    23 hours later "day 1".

    Never raises — ``track`` has already swallowed everything by the time this
    runs, and a derived-event failure must not lose the primary event.
    """
    if _identity(user_id, anon_id) is None:
        # No identity: nothing to derive from, and a table-wide aggregate is
        # not what "at most once per identity" means.
        return
    db = _db()

    if name == "backtest_completed":
        completed = db.count_events("backtest_completed", **{
            "user_id": user_id, "anon_id": anon_id,
        })
        if completed == 2 and not db.has_event("second_backtest", **{
            "user_id": user_id, "anon_id": anon_id,
        }):
            _record_derived("second_backtest", db, user_id, anon_id)

    first = db.first_event_at(user_id=user_id, anon_id=anon_id)
    if first is None:
        return
    if isinstance(first, str):
        try:
            first = datetime.fromisoformat(first.replace("Z", "+00:00"))
        except ValueError:
            return
    if first.tzinfo is None:
        first = first.replace(tzinfo=timezone.utc)
    elapsed_days = (datetime.now(timezone.utc).date()
                    - first.astimezone(timezone.utc).date()).days

    if elapsed_days == 1:
        if not db.has_event("returned_day_2", user_id=user_id, anon_id=anon_id):
            _record_derived("returned_day_2", db, user_id, anon_id)
    elif 6 <= elapsed_days <= 8:
        if not db.has_event("returned_day_7", user_id=user_id, anon_id=anon_id):
            _record_derived("returned_day_7", db, user_id, anon_id)


def _record_derived(name: str, db, user_id: Optional[int],
                    anon_id: Optional[str]) -> None:
    """Insert a derived event through the same path as a primary one.

    Uses the already-open ``db`` rather than opening another client, so the
    derived write shares the connection the checks just used.
    """
    db.insert_event(name, user_id=user_id, anon_id=anon_id, props={})


# ═══════════════════════════════════════════════════════════════════════════════
# The public entry point
# ═══════════════════════════════════════════════════════════════════════════════

def track(name: str, *, user_id: Optional[int] = None,
          anon_id: Optional[str] = None,
          props: Optional[Dict[str, Any]] = None) -> None:
    """Record one event. Never raises; a failure is logged and swallowed.

    Args:
        name: The event name. Must be a key of :data:`EVENTS`; an unknown name
            logs a warning and writes nothing (a typo must not invent a new
            funnel step).
        user_id: The signed-in user's id, when there is one.
        anon_id: The browser's ``stratoscan_anon_id`` (a UUID). Anything that
            is not a UUID is stored as NULL.
        props: Event properties. Only the keys whitelisted for ``name``
            survive, and only with simple, short values.

    Returns:
        None. Always. Callers treat this as fire-and-forget.
    """
    try:
        if not settings.analytics_enabled:
            return
        if name not in EVENTS:
            logger.warning("Analytics: unknown event %r dropped", name)
            return
        clean_user_id = _clean_user_id(user_id)
        clean_anon_id = _clean_anon_id(anon_id)
        _record(
            name,
            user_id=clean_user_id,
            anon_id=clean_anon_id,
            props=_clean_props(name, props),
        )
        # Only after the row is genuinely on disk: a derived event that fired
        # on a failed insert would report a return that never happened.
        _fire_derived(name, user_id=clean_user_id, anon_id=clean_anon_id)
    except Exception as e:  # noqa: BLE001 — telemetry must never break a caller
        logger.warning("Analytics: dropped event %r (%s)", name, e)


def recent_events(limit: int = 100) -> List[Dict[str, Any]]:
    """Return the newest events first, for the admin read-out.

    The funnel is walked from this list, so it is deliberately the raw rows
    (name, time, identity, props) with no aggregation — the operator sees the
    order things actually happened in.
    """
    return _db().list_events(limit=limit)
