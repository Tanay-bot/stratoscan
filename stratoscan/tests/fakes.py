"""Shared test fakes — keep API smoke tests hermetic (zero live LLM calls).

The smoke tests hit /screen, /interpret, /enrich and /backtest, which all
route through ``src.llm_router.get_router().chat_completion()``. Without a
fake, every test-suite run burns real free-tier Z.ai quota and becomes
network-dependent. Patch ``src.llm_router.get_router`` to return an instance
of :class:`FakeLLMRouter` before constructing the TestClient.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
        # Day 6: backtest_jobs owner rows and shared_results, by primary key.
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.shares: Dict[str, Dict[str, Any]] = {}
        self._share_seq = 0
        # Day 10: saved strategies, the per-week digest ledger, the run lock.
        self.strategies: Dict[int, Dict[str, Any]] = {}
        self.digest_sends: Dict[tuple, Dict[str, Any]] = {}
        self._strategy_seq = 0
        self.lock_held = False
        # Day 12: entitlements. Grandfather slots issued (never decremented,
        # like app_counters) and metered usage by (user_id, period, kind).
        # Tests that need a FREE account exhaust the slots first:
        # ``db.grandfather_issued = GRANDFATHER_LIMIT``.
        self.grandfather_issued = 0
        self.usage: Dict[tuple, int] = {}
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
            # Google identity ("Continue with Google"). Real rows carry this
            # key (NULL for email/password accounts) — the merge rule reads
            # it, so the fake must have it too.
            "google_sub": None,
            # Day 10 opt-out — real rows default TRUE.
            "weekly_digest": True,
            # Day 12 plan state — real rows carry these columns.
            "plan_pending": None,
            "plan_change_at": None,
            "plan_change_reason": None,
            "grandfathered_pro": False,
            "created_at": "2026-07-31T00:00:00Z",
        }
        from src.entitlements import GRANDFATHER_LIMIT

        if self.grandfather_issued < GRANDFATHER_LIMIT:
            self.grandfather_issued += 1
            user["grandfathered_pro"] = True
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
                # Token kept, like PostgresDB: a second click is recognised.
                user["email_verified"] = True
                return

    def get_user_by_verification_token(self, token: str) -> Dict[str, Any] | None:
        for user in self._users.values():
            if user.get("verification_token") == token:
                return {
                    "id": user["id"],
                    "email": user["email"],
                    "email_verified": user.get("email_verified", False),
                    "verification_token_expires": user.get("verification_token_expires"),
                }
        return None

    # ── Admin dashboard methods ───────────────────────────────────────

    def set_user_plan(self, user_id: int, plan: str) -> None:
        self.set_plan_state(user_id, plan=plan, pending=None, change_at=None, reason=None)

    # ── Entitlements (Day 12) — mirrors PostgresDB ────────────────────
    def set_plan_state(self, user_id: int, *, plan, pending, change_at, reason) -> bool:
        user = self.get_user_by_id(user_id)
        if not user:
            return False
        user.update({"plan": plan, "plan_pending": pending, "plan_change_at": change_at,
                     "plan_change_reason": reason})
        return True

    def consume_usage(self, user_id: int, period: str, kind: str, limit) -> Optional[int]:
        if limit is not None and limit <= 0:
            return None
        key = (user_id, period, kind)
        current = self.usage.get(key, 0)
        if limit is not None and current >= limit:
            return None
        self.usage[key] = current + 1
        return current + 1

    def get_usage(self, user_id: int, period: str, kind: str) -> int:
        return self.usage.get((user_id, period, kind), 0)

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

    # ── Google OAuth ("Continue with Google") ─────────────────────────
    # Same signatures and returns as PostgresDB, minus the SQL.

    def get_user_by_google_sub(self, sub: str) -> Dict[str, Any] | None:
        """Mirror PostgresDB.get_user_by_google_sub."""
        for user in self._users.values():
            if sub and user.get("google_sub") == sub:
                return {
                    "id": user["id"],
                    "email": user["email"],
                    "is_admin": user.get("is_admin", False),
                    "token_version": user.get("token_version", 0),
                }
        return None

    def link_google_identity(self, user_id: int, sub: str) -> None:
        """Mirror PostgresDB.link_google_identity."""
        for user in self._users.values():
            if user["id"] == user_id:
                user["google_sub"] = sub
                return

    def take_over_for_google(self, user_id: int, sub: str, unusable_hash: str) -> int:
        """Mirror PostgresDB.take_over_for_google — including the point of it:
        the password is replaced and every session is revoked."""
        for user in self._users.values():
            if user["id"] == user_id:
                user["password_hash"] = unusable_hash
                user["google_sub"] = sub
                user["email_verified"] = True
                user["verification_token"] = None
                user["verification_token_expires"] = None
                user["reset_token_hash"] = None
                user["reset_token_expires"] = None
                user["token_version"] += 1
                self._personal.pop(user_id, None)  # squatter's saved state goes
                self._drop_shares(owner_user_id=user_id)
                for sid in [k for k, r in self.strategies.items() if r["user_id"] == user_id]:
                    del self.strategies[sid]
                return user["token_version"]
        return 0

    def create_google_user(self, email: str, sub: str, unusable_hash: str) -> int:
        """Mirror PostgresDB.create_google_user (verified, no usable password)."""
        if email in self._users:
            raise ValueError(
                'duplicate key value violates unique constraint "users_email_key"'
            )
        user = self._store(email, unusable_hash, "free", email_verified=True)
        user["google_sub"] = sub
        return user["id"]

    def delete_user(self, user_id: int, email: str) -> Dict[str, int]:
        """Mirror PostgresDB.delete_user (user_data goes with the user)."""
        removed = 0
        for key, user in list(self._users.items()):
            if user["id"] == user_id:
                del self._users[key]
                removed = 1
        self._personal.pop(user_id, None)
        self._drop_shares(owner_user_id=user_id)  # ON DELETE CASCADE
        for sid in [k for k, r in self.strategies.items() if r["user_id"] == user_id]:
            del self.strategies[sid]
        for key in [k for k in self.digest_sends if k[0] == user_id]:
            del self.digest_sends[key]
        for key in [k for k in self.usage if k[0] == user_id]:
            del self.usage[key]
        jobs = [j for j, row in self.jobs.items() if row["user_email"] == email]
        for job_id in jobs:
            del self.jobs[job_id]
        return {"users": removed, "backtest_jobs": len(jobs)}

    # ── Anonymous trials & shared results (Day 6) ───────────────────
    # Mirrors the PostgresDB methods of the same names, including the owner
    # scoping that lives in their WHERE clauses.

    def _owned(self, row: Dict[str, Any], owner_user_id: Optional[int],
               owner_anon: Optional[str]) -> bool:
        if owner_user_id is not None:
            return row["owner_user_id"] == owner_user_id
        if owner_anon:
            return row["owner_anon"] == owner_anon
        return False

    def _drop_shares(self, *, owner_user_id: int) -> None:
        for share_id in [k for k, r in self.shares.items()
                         if r["owner_user_id"] == owner_user_id]:
            del self.shares[share_id]

    # ── Saved strategies + weekly digest (Day 10) ───────────────────
    # Mirrors the PostgresDB methods, including the claim's conflict rule.

    def _user(self, user_id: int) -> Dict[str, Any] | None:
        return next((u for u in self._users.values() if u["id"] == user_id), None)

    def count_strategies(self, user_id: int) -> int:
        return sum(1 for r in self.strategies.values() if r["user_id"] == user_id)

    def save_strategy(self, user_id, query, rules, symbols, saved_return_pct, saved_at=None):
        import copy
        from datetime import datetime, timezone

        self._strategy_seq += 1
        row = {
            "id": self._strategy_seq, "user_id": user_id, "query": query,
            "rules": copy.deepcopy(rules), "symbols": list(symbols),
            "saved_return_pct": saved_return_pct,
            "saved_at": saved_at or datetime.now(timezone.utc),
        }
        self.strategies[row["id"]] = row
        return {k: v for k, v in row.items() if k != "user_id"}

    def list_strategies(self, user_id: int) -> List[Dict[str, Any]]:
        rows = [r for r in self.strategies.values() if r["user_id"] == user_id]
        rows.sort(key=lambda r: (r["saved_at"], r["id"]), reverse=True)
        return [{k: v for k, v in r.items() if k != "user_id"} for r in rows]

    def delete_strategy(self, user_id: int, strategy_id: int) -> bool:
        row = self.strategies.get(strategy_id)
        if row and row["user_id"] == user_id:
            del self.strategies[strategy_id]
            return True
        return False

    def get_weekly_digest(self, user_id: int):
        u = self._user(user_id)
        return None if u is None else bool(u.get("weekly_digest", True))

    def set_weekly_digest(self, user_id: int, enabled: bool) -> bool:
        u = self._user(user_id)
        if u is None:
            return False
        u["weekly_digest"] = bool(enabled)
        return True

    def digest_candidates(self, week_key: str, limit: int) -> List[Dict[str, Any]]:
        from src.database import DIGEST_MAX_ATTEMPTS

        out = []
        for u in sorted(self._users.values(), key=lambda u: u["id"]):
            if not (u.get("weekly_digest", True) and u["email_verified"]):
                continue
            # Day 12: same superset prefilter as the SQL.
            if not (u.get("grandfathered_pro") or u.get("plan", "free") != "free"):
                continue
            if not self.count_strategies(u["id"]):
                continue
            row = self.digest_sends.get((u["id"], week_key))
            if row and (row["status"] != "failed" or row["attempts"] >= DIGEST_MAX_ATTEMPTS):
                continue
            out.append({"id": u["id"], "email": u["email"]})
        return out[:limit]

    def claim_digest(self, user_id: int, week_key: str) -> bool:
        from src.database import DIGEST_MAX_ATTEMPTS

        row = self.digest_sends.get((user_id, week_key))
        if row is None:
            self.digest_sends[(user_id, week_key)] = {"status": "sending", "attempts": 1}
            return True
        if row["status"] == "failed" and row["attempts"] < DIGEST_MAX_ATTEMPTS:
            row["status"], row["attempts"] = "sending", row["attempts"] + 1
            return True
        return False

    def mark_digest(self, user_id: int, week_key: str, status: str) -> None:
        row = self.digest_sends.get((user_id, week_key))
        if row is not None:
            row["status"] = status

    def digest_lock(self):
        from contextlib import contextmanager

        @contextmanager
        def _lock():
            if self.lock_held:
                yield False
                return
            self.lock_held = True
            try:
                yield True
            finally:
                self.lock_held = False

        return _lock()

    def count_owner_backtests(self, owner: str) -> int:
        return sum(1 for r in self.jobs.values()
                   if r["user_email"] == owner and r.get("status") != "error")

    def create_share(self, share_id, job_id, owner_user_id, owner_anon, query, summary) -> None:
        if share_id in self.shares:
            raise ValueError('duplicate key value violates unique constraint "shared_results_pkey"')
        self._share_seq += 1
        self.shares[share_id] = {
            "share_id": share_id, "job_id": job_id, "owner_user_id": owner_user_id,
            "owner_anon": owner_anon, "query": query, "summary": summary,
            "created_at": self._share_seq,
        }

    # Day 11: the explanation cache (first writer wins, like the real table).
    def get_explanation(self, result_hash: str) -> Optional[Dict[str, Any]]:
        row = getattr(self, "explanations", {}).get(result_hash)
        return dict(row) if row else None

    def save_explanation(self, result_hash: str, payload: Dict[str, Any]) -> None:
        if not hasattr(self, "explanations"):
            self.explanations = {}
        self.explanations.setdefault(result_hash, dict(payload))

    def find_share_for_job(self, job_id, owner_user_id, owner_anon) -> Optional[str]:
        rows = [r for r in self.shares.values()
                if r["job_id"] == job_id and self._owned(r, owner_user_id, owner_anon)]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows[0]["share_id"] if rows else None

    def get_share(self, share_id: str) -> Optional[Dict[str, Any]]:
        row = self.shares.get(share_id)
        if not row:
            return None
        return {k: row[k] for k in ("share_id", "query", "summary", "created_at")}

    def delete_share(self, share_id, owner_user_id, owner_anon) -> bool:
        row = self.shares.get(share_id)
        if row and self._owned(row, owner_user_id, owner_anon):
            del self.shares[share_id]
            return True
        return False

    def list_shares(self, owner_user_id, owner_anon, limit: int = 100) -> List[Dict[str, Any]]:
        rows = [r for r in self.shares.values() if self._owned(r, owner_user_id, owner_anon)]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [{k: r[k] for k in ("share_id", "query", "summary", "created_at")}
                for r in rows[:limit]]

    def claim_anonymous_work(self, anon_id: str, user_id: int, email: str) -> Dict[str, int]:
        jobs = shares = 0
        for row in self.jobs.values():
            if row["user_email"] == "anon:" + anon_id:
                row["user_email"] = email
                jobs += 1
        for row in self.shares.values():
            if row["owner_anon"] == anon_id:
                row["owner_user_id"], row["owner_anon"] = user_id, None
                shares += 1
        return {"jobs": jobs, "shares": shares}

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
                # Day 12: the real SELECT carries USER_PLAN_COLUMNS.
                "plan_pending": u.get("plan_pending"),
                "plan_change_at": u.get("plan_change_at"),
                "plan_change_reason": u.get("plan_change_reason"),
                "grandfathered_pro": u.get("grandfathered_pro", False),
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


class FakeEventDB:
    """In-memory stand-in for PostgresDB's event + error-log methods.

    Mirrors the exact method surface ``src.analytics`` and the API's generic
    exception handler use, so a suite can exercise the REAL ``track()`` — the
    prop whitelist, the UUID check, the derived events — with zero database
    access. Patch it in as ``src.analytics._db`` (and as the API handler's
    ``PostgresDB``) before calling.

    ``now`` is settable on purpose: the day-2 / day-7 derivations compare UTC
    CALENDAR DATES, so a test has to be able to place an event on "yesterday"
    without waiting a day. Move ``now``, insert, and the arithmetic is real.
    """

    def __init__(self, now: Optional[datetime] = None) -> None:
        self.events: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.now: datetime = now or datetime.now(timezone.utc)

    # ── helpers ─────────────────────────────────────────────────────────
    def _matches(self, row: Dict[str, Any], user_id: Optional[int],
                 anon_id: Optional[str]) -> bool:
        """Identity rule, identical to PostgresDB._event_identity_where."""
        if user_id is not None:
            return row["user_id"] == user_id
        if anon_id:
            return row["anon_id"] == anon_id
        return False

    @property
    def names(self) -> List[str]:
        """Recorded event names in insertion order — the funnel walk."""
        return [e["name"] for e in self.events]

    def names_for(self, *, user_id: Optional[int] = None,
                  anon_id: Optional[str] = None) -> List[str]:
        """One identity's event names in insertion order."""
        return [
            e["name"] for e in self.events
            if self._matches(e, user_id, anon_id)
        ]

    def seed(self, name: str, *, user_id: Optional[int] = None,
             anon_id: Optional[str] = None, occurred_at: Optional[datetime] = None,
             props: Optional[Dict[str, Any]] = None) -> None:
        """Insert a row directly, bypassing track() (a past event)."""
        self.events.append({
            "name": name,
            "user_id": user_id,
            "anon_id": anon_id,
            "props": props or {},
            "occurred_at": occurred_at or self.now,
        })

    # ── PostgresDB event surface ────────────────────────────────────────
    def insert_event(self, name: str, user_id: Optional[int] = None,
                     anon_id: Optional[str] = None,
                     props: Optional[Dict[str, Any]] = None) -> None:
        self.seed(name, user_id=user_id, anon_id=anon_id, props=props)

    def count_events(self, name: str, user_id: Optional[int] = None,
                     anon_id: Optional[str] = None) -> int:
        return sum(
            1 for e in self.events
            if e["name"] == name and self._matches(e, user_id, anon_id)
        )

    def first_event_at(self, user_id: Optional[int] = None,
                       anon_id: Optional[str] = None) -> Optional[datetime]:
        times = [
            e["occurred_at"] for e in self.events
            if self._matches(e, user_id, anon_id)
        ]
        return min(times) if times else None

    def has_event(self, name: str, user_id: Optional[int] = None,
                  anon_id: Optional[str] = None) -> bool:
        return self.count_events(name, user_id, anon_id) > 0

    def list_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Newest first, matching PostgresDB.list_events' ordering."""
        return list(reversed(self.events))[:limit]

    # ── PostgresDB error-log surface ────────────────────────────────────
    def insert_error(self, method: str, route: str, error_type: str,
                     trace: str) -> None:
        self.errors.append({
            "occurred_at": self.now,
            "method": method,
            "route": route,
            "error_type": error_type,
            "trace": trace,
        })

    def list_errors(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(reversed(self.errors))[:limit]
