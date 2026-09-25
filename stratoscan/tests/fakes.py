"""Shared test fakes — keep API smoke tests hermetic (zero live LLM calls).

The smoke tests hit /screen, /interpret, /enrich and /backtest, which all
route through ``src.llm_router.get_router().chat_completion()``. Without a
fake, every test-suite run burns real free-tier Z.ai quota and becomes
network-dependent. Patch ``src.llm_router.get_router`` to return an instance
of :class:`FakeLLMRouter` before constructing the TestClient.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class FakeLLMRouter:
    """Drop-in for LLMRouter that returns canned JSON instead of calling an API."""

    def __init__(self, json_str: str = '{"sector": "Technology", "max_pe_ratio": 15}'):
        self.json_str = json_str
        self.last_used: tuple | None = None
        self.total_attempts = 0
        self.total_failovers = 0
        self._calls: List[Dict[str, Any]] = []

    def chat_completion(self, *, messages: List[Dict[str, Any]], **kwargs: Any):
        """Record the call and return a fake OpenAI response object."""
        self._calls.append({"messages": messages, "kwargs": kwargs})
        self.total_attempts += 1

        class _Message:
            content = self.json_str

        class _Choice:
            message = _Message()
            finish_reason = "stop"

        class _Response:
            choices = [_Choice()]

        return _Response()

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def last_query(self) -> str:
        """Return the last user query sent to the (fake) LLM."""
        if not self._calls:
            return ""
        msgs = self._calls[-1]["messages"]
        return next((m["content"] for m in msgs if m.get("role") == "user"), "")


class FakeUserDB:
    """In-memory stand-in for ``PostgresDB`` user methods — auth tests run
    with zero database access.

    Mirrors the exact interface the auth endpoints use:
      - ``create_user(email, password_hash, plan)`` -> id
      - ``get_user_by_email(email)`` -> user dict or None
      - ``get_user_by_id(user_id)`` -> user dict or None

    Duplicate emails raise an error whose message contains "duplicate key"
    (the register endpoint maps that to a 409 Conflict).
    """

    def __init__(self) -> None:
        self._users: Dict[str, Dict[str, Any]] = {}
        self._personal: Dict[int, Dict[str, Any]] = {}
        self._next_id = 1

    def _store(
        self,
        email: str,
        password_hash: str,
        plan: str,
        email_verified: bool = False,
        verification_token: str | None = None,
        verification_token_expires: str | None = None,
        is_admin: bool = False,
    ) -> Dict[str, Any]:
        user = {
            "id": self._next_id,
            "email": email,
            "password_hash": password_hash,
            "plan": plan,
            "email_verified": email_verified,
            "verification_token": verification_token,
            "verification_token_expires": verification_token_expires,
            "is_admin": is_admin,
            # Session-revocation counter. It MUST be present: real rows carry
            # it, and get_current_user compares the token's ``ver`` claim
            # against it — a fake user without the key would silently take
            # the ``.get(..., 0)`` default and mask a broken comparison.
            "token_version": 0,
            "reset_token_hash": None,
            "reset_token_expires": None,
            "created_at": "2026-07-31T00:00:00Z",
        }
        self._next_id += 1
        self._users[email] = user
        return user

    def create_user(
        self,
        email: str,
        password_hash: str,
        plan: str = "free",
        email_verified: bool = False,
        verification_token: str | None = None,
        verification_token_expires: str | None = None,
        is_admin: bool = False,
    ) -> int:
        if email in self._users:
            raise ValueError(
                'duplicate key value violates unique constraint "users_email_key"'
            )
        return self._store(
            email, password_hash, plan,
            email_verified=email_verified,
            verification_token=verification_token,
            verification_token_expires=verification_token_expires,
            is_admin=is_admin,
        )["id"]

    def get_user_by_email(self, email: str) -> Dict[str, Any] | None:
        return self._users.get(email)

    def get_user_by_id(self, user_id: int) -> Dict[str, Any] | None:
        for user in self._users.values():
            if user["id"] == user_id:
                return user
        return None

    def set_user_verification_token(
        self, user_id: int, token: str, expires_at: str
    ) -> None:
        for user in self._users.values():
            if user["id"] == user_id:
                user["verification_token"] = token
                user["verification_token_expires"] = expires_at
                return

    def mark_email_verified(self, user_id: int) -> None:
        for user in self._users.values():
            if user["id"] == user_id:
                user["email_verified"] = True
                user["verification_token"] = None
                user["verification_token_expires"] = None
                return

    def get_user_by_verification_token(self, token: str) -> Dict[str, Any] | None:
        for user in self._users.values():
            if user.get("verification_token") == token:
                return {
                    "id": user["id"],
                    "email": user["email"],
                    "verification_token_expires": user.get("verification_token_expires"),
                }
        return None

    # ── Admin dashboard methods ───────────────────────────────────────

    def set_user_plan(self, user_id: int, plan: str) -> None:
        for user in self._users.values():
            if user["id"] == user_id:
                user["plan"] = plan
                return

    def set_user_admin(self, user_id: int, is_admin: bool) -> None:
        for user in self._users.values():
            if user["id"] == user_id:
                user["is_admin"] = bool(is_admin)
                return

    def bump_token_version(self, user_id: int) -> int:
        """Mirror PostgresDB.bump_token_version (same signature + return)."""
        for user in self._users.values():
            if user["id"] == user_id:
                user["token_version"] += 1
                return user["token_version"]
        return 0

    def set_password_reset_token(
        self, user_id: int, token_hash: str, expires_at: str
    ) -> None:
        """Mirror PostgresDB.set_password_reset_token (overwrites any prior hash)."""
        for user in self._users.values():
            if user["id"] == user_id:
                user["reset_token_hash"] = token_hash
                user["reset_token_expires"] = expires_at
                return

    def get_user_by_reset_token_hash(self, token_hash: str) -> Dict[str, Any] | None:
        """Mirror PostgresDB.get_user_by_reset_token_hash."""
        for user in self._users.values():
            if token_hash and user.get("reset_token_hash") == token_hash:
                return {
                    "id": user["id"],
                    "email": user["email"],
                    "reset_token_expires": user["reset_token_expires"],
                }
        return None

    def complete_password_reset(
        self, user_id: int, token_hash: str, password_hash: str
    ) -> int | None:
        """Mirror PostgresDB.complete_password_reset, including its single-use
        guard: the update only lands if the row STILL holds this hash."""
        for user in self._users.values():
            if user["id"] == user_id and user.get("reset_token_hash") == token_hash:
                user["password_hash"] = password_hash
                user["reset_token_hash"] = None
                user["reset_token_expires"] = None
                user["email_verified"] = True
                user["verification_token"] = None
                user["verification_token_expires"] = None
                user["token_version"] += 1
                return user["token_version"]
        return None

    def change_password(self, user_id: int, password_hash: str) -> int:
        """Mirror PostgresDB.change_password (also clears a pending reset)."""
        for user in self._users.values():
            if user["id"] == user_id:
                user["password_hash"] = password_hash
                user["reset_token_hash"] = None
                user["reset_token_expires"] = None
                user["token_version"] += 1
                return user["token_version"]
        return 0

    def delete_user(self, user_id: int, email: str) -> Dict[str, int]:
        """Mirror PostgresDB.delete_user (user_data goes with the user)."""
        removed = 0
        for key, user in list(self._users.items()):
            if user["id"] == user_id:
                del self._users[key]
                removed = 1
        self._personal.pop(user_id, None)
        return {"users": removed, "backtest_jobs": 0}

    def list_users(
        self, limit: int = 50, offset: int = 0, search: str | None = None
    ) -> List[Dict[str, Any]]:
        users = sorted(
            self._users.values(), key=lambda u: u["id"], reverse=True
        )
        if search:
            users = [u for u in users if search.strip().lower() in u["email"].lower()]
        return [
            {
                "id": u["id"],
                "email": u["email"],
                "plan": u["plan"],
                "email_verified": u["email_verified"],
                "is_admin": u["is_admin"],
                "created_at": u.get("created_at"),
            }
            for u in users[offset: offset + limit]
        ]

    def count_users(self, search: str | None = None) -> int:
        users = list(self._users.values())
        if search:
            users = [u for u in users if search.strip().lower() in u["email"].lower()]
        return len(users)

    def user_stats(self) -> Dict[str, Any]:
        users = list(self._users.values())
        plans: Dict[str, int] = {}
        for u in users:
            plans[u["plan"]] = plans.get(u["plan"], 0) + 1
        return {
            "total": len(users),
            "admins": sum(1 for u in users if u["is_admin"]),
            "verified": sum(1 for u in users if u["email_verified"]),
            "plans": plans,
        }

    # ── Per-user saved data (watchlist / screeners / backtests) ───────
    # Mirrors PostgresDB.get_user_data / upsert_user_data so the /me/personal
    # endpoints are fully hermetic in tests.

    def get_user_data(self, user_id: int) -> Dict[str, Any] | None:
        return self._personal.get(user_id)

    def upsert_user_data(
        self,
        user_id: int,
        watchlist=None,
        saved_screeners=None,
        recent_backtests=None,
    ) -> None:
        self._personal[user_id] = {
            "watchlist": list(watchlist or []),
            "saved_screeners": list(saved_screeners or []),
            "recent_backtests": list(recent_backtests or []),
        }

class FakeQualityDB:
    """In-memory PostgresDB stand-in with REAL filter semantics.

    Mirrors the EXACT filter semantics of ``database._build_filter_where``
    (ranges, column-compare ops incl. frac variants, IN, exact, NULL
    guards, and the multi-condition list form) over an in-memory list,
    plus ``aggregate_stats`` with real mean/std — so the screener's
    translation and composite-score math are genuinely exercised, not
    stubbed. Lives here (side-effect-free module) so both the quality test
    suite and headless probes can import it without executing test bodies.
    """

    def __init__(self, records: List[Dict[str, Any]]):
        self._records = list(records)

    # ── Filter semantics (mirrors src/database._column_conditions) ─────
    def _match(self, record: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        for col, value in filters.items():
            rv = record.get(col)
            # Multiple ANDed conditions on one column (list of tuples) —
            # mirrors database._column_conditions' multi-condition form.
            if isinstance(value, list) and value and all(isinstance(v, tuple) for v in value):
                for sub in value:
                    if not self._match_one(record, col, rv, sub):
                        return False
                continue
            if not self._match_one(record, col, rv, value):
                return False
        return True

    def _match_one(self, record, col, rv, value) -> bool:
        """Match a single filter value form against one column value."""
        # Column-to-column comparison (incl. frac variants)
        if isinstance(value, tuple) and len(value) >= 2 and isinstance(value[0], str) and value[0].endswith("_col"):
            operator, other = value[0], value[1]
            ov = record.get(other)
            if len(value) == 3 and value[0].endswith("frac_col"):
                factor = float(value[2])
                ov = (ov * factor) if ov is not None else None
            if rv is None or ov is None:
                return False
            # Faithful to database._column_conditions: gt/lt are strict,
            # gte/lte (incl. frac variants) are inclusive.
            if operator in ("gt_col",) and not (rv > ov):
                return False
            if operator in ("lt_col",) and not (rv < ov):
                return False
            if operator in ("gte_col", "gte_frac_col") and not (rv >= ov):
                return False
            if operator in ("lte_col", "lte_frac_col") and not (rv <= ov):
                return False
            return True
        # Range / exact tuple
        if isinstance(value, tuple) and len(value) == 2:
            operator, operand = value
            if operator == "neq" and operand is None:
                return rv is not None
            if operator == "eq" and operand is None:
                return rv is None
            if operator == "lt":
                return rv is not None and rv < operand
            if operator == "lte":
                return rv is not None and rv <= operand
            if operator == "gt":
                return rv is not None and rv > operand
            if operator == "gte":
                return rv is not None and rv >= operand
            if operator == "eq":
                return rv == operand
            if operator == "neq":
                return rv != operand
            return False
        # IN clause
        if isinstance(value, (list, tuple)):
            return rv in value
        # Exact equality
        return rv == value

    def count_stocks(self, filters: Optional[Dict[str, Any]] = None) -> int:
        filters = filters or {}
        return sum(1 for r in self._records if self._match(r, filters))

    def query_stocks(self, filters=None, limit=50, offset=0,
                     sort_by="market_cap", sort_desc=True):
        filters = filters or {}
        matched = [r for r in self._records if self._match(r, filters)]
        matched.sort(
            key=lambda r: r.get(sort_by) if r.get(sort_by) is not None else float("-inf"),
            reverse=sort_desc,
        )
        return matched[offset: offset + limit]

    def aggregate_stats(self, filters=None, columns=None):
        filters = filters or {}
        matched = [r for r in self._records if self._match(r, filters)]
        out: Dict[str, Dict[str, float]] = {}
        for col in columns or []:
            vals = [r[col] for r in matched if r.get(col) is not None]
            if len(vals) < 2:
                out[col] = {"mean": (vals[0] if vals else 0.0), "std": 0.0, "valid": len(vals)}
                continue
            mean = sum(vals) / len(vals)
            var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
            out[col] = {"mean": mean, "std": var ** 0.5, "valid": len(vals)}
        return out

    def data_freshness(self, max_age_minutes: int = 15):
        return {
            "oldest_age_minutes": 0.0,
            "max_updated_at": None,
            "stale_count": 0,
            "total_rows": len(self._records),
        }

    def column_coverage(self, columns=None):
        """Count non-null rows per column (mirrors PostgresDB.column_coverage).

        Powers the screener's 0-match auto-relax ordering: least-covered
        filters are dropped first.
        """
        out: Dict[str, int] = {}
        for col in columns or []:
            out[col] = sum(1 for r in self._records if r.get(col) is not None)
        return out
