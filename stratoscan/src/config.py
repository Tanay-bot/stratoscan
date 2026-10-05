"""Application configuration and environment variable management.

All API keys and sensitive settings are loaded from environment variables
via a .env file. Never hardcode secrets in source code.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import sys
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# .env Security — enforce restricted file permissions
# ═══════════════════════════════════════════════════════════════════════════════

_project_root = Path(__file__).resolve().parent.parent
_env_path = _project_root / ".env"


def _secure_env_permissions() -> None:
    """Check and enforce secure permissions on the ``.env`` file.

    On Unix systems (macOS, Linux), ensures the file is only readable
    by the file owner (``chmod 600``). Warns if permissions are loose
    and attempts to fix them.

    This is a defence-in-depth measure — ``.env`` should never be
    committed to version control (it's in ``.gitignore``), but even
    if someone gains filesystem access, the secrets should be protected.
    """
    if not _env_path.exists():
        return

    # Skip on Windows — ACL-based permissions are handled differently
    if sys.platform == "win32":
        return

    try:
        current_mode = _env_path.stat().st_mode

        # Check if file is readable by group (S_IRGRP) or others (S_IROTH)
        if current_mode & (stat.S_IRGRP | stat.S_IROTH):
            desired_mode = stat.S_IRUSR | stat.S_IWUSR  # 0o600
            logger.warning(
                "⚠️  .env has loose permissions (%o). "
                "Any user on this system can read your API keys. "
                "Fixing to %o...",
                current_mode & 0o777,
                desired_mode & 0o777,
            )
            os.chmod(_env_path, desired_mode)
            logger.info("✅ .env permissions locked to owner-read/write only")
    except Exception as e:
        logger.debug("Could not check .env permissions: %s", e)


# Load .env from the project root
if _env_path.exists():
    load_dotenv(_env_path)
    _secure_env_permissions()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Finnhub API Keys (10 keys for round-robin) ──────────────────────
    finnhub_key_01: str = ""
    finnhub_key_02: str = ""
    finnhub_key_03: str = ""
    finnhub_key_04: str = ""
    finnhub_key_05: str = ""
    finnhub_key_06: str = ""
    finnhub_key_07: str = ""
    finnhub_key_08: str = ""
    finnhub_key_09: str = ""
    finnhub_key_10: str = ""

    # ── LLM Keys (via ZenMux) ──────────────────────────────────────────
    # ZenMux is OpenAI-compatible. Base URL + model slug are configurable in
    # .env so you can switch providers without code changes.
    #
    # Free-tier model (verified working with zero account balance):
    #   z-ai/glm-4.7-flash-free  (GLM-4.7-Flash)
    #   z-ai/glm-4.6v-flash-free
    #
    # NOTE: many "-free" slugs (deepseek/*-free, inclusionai/ling-3.0-flash)
    # still return HTTP 402 reject_no_credit without a balance — only the
    # z-ai/glm-*.flash-free lines above actually run on a $0 account.
    llm_base_url: str = "https://zenmux.ai/api/v1"
    llm_model: str = "z-ai/glm-4.7-flash-free"
    screener_llm_key: str = ""          # Used by the NL interpreter (Layer 1)
    backtest_llm_key: str = ""          # Used by the backtesting engine (Layer 3)

    # ── Multi-provider LLM routing (instant failover) ────────────────────
    # Comma-separated provider names in priority order (first = primary).
    # Each provider configures its own vars in .env: <NAME>_BASE_URL,
    # <NAME>_MODEL, <NAME>_KEYS (comma-separated). See src/llm_router.py.
    llm_providers: str = "zai"            # e.g. "zai,zenmux,gemini"
    # Per-call timeout in seconds. Raised from 45s → 120s because free
    # flash models (GLM-4.5/4.7) legitimately take 42-135s on strict-JSON
    # prompts (measured live); a 45s cap caused spurious timeouts and
    # failovers that burned other providers' free-tier quota.
    # Override per provider with <NAME>_TIMEOUT in .env (see llm_router).
    llm_provider_timeout: float = 120.0

    # ── Hybrid interpreter toggle ──────────────────────────────────────
    # When True, interpret_query() tries the deterministic parser first.
    # If confidence >= 0.6 the result is used (sub-ms, zero cost).
    # If confidence < 0.6, falls back to the LLM.  Set to False to
    # force LLM-only mode (useful for A/B testing or debugging).
    use_deterministic_interpreter: bool = True

    # ── HTTP timeouts (background jobs / external data) ──────────────────
    # Finnhub: the python SDK defaults every request to 10s — too tight for
    # bulk refresh loops on slow networks. We set the SDK's DEFAULT_TIMEOUT
    # per client from this setting (see data_fetcher.initialize_client_pool).
    finnhub_request_timeout: float = 30.0
    # yfinance: 10 years of daily OHLCV is a big download; give it a real
    # budget instead of the library's implicit short default.
    yfinance_timeout: float = 60.0
    # Postgres connect timeout used by the /health probe. Neon is
    # serverless — a cold start (database suspended) can take >3s, so the
    # old hardcoded 3s produced false "unhealthy" alarms.
    db_connect_timeout: int = 15

    # ── PostgreSQL (Primary Screener Database) ──────────────────────────
    # Direct connection string for any PostgreSQL provider:
    #   Supabase: postgresql://postgres.qjdxapyxhpdkikzdtuzl:<pw>@aws-0-us-west-1.pooler.supabase.com:5432/postgres
    #   Neon:     postgresql://user:<pw>@ep-xxx.us-east-2.aws.neon.tech/neondb
    #   Railway:  postgresql://user:<pw>@containers-us-west-xxx.railway.app:5432/railway
    database_url: str = ""

    # ── Supabase (kept for backward compat — not used by the screener) ──
    # These fields still exist in case a future frontend needs the anon key
    # for direct Supabase client-side queries. The backend no longer uses them.
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_anon_key: str = ""

    # ── Backtesting Database (separate Neon project) ───────────────────
    # Historical OHLCV + backtest results were originally stored in a local
    # SQLite file (backtest_db_path) — fine for a single dev box, but not
    # production: SQLite is a single-writer embedded file, so multiple
    # server instances can't share it and it can't survive a redeploy.
    # BACKTEST_DATABASE_URL points at a DEDICATED Neon project (same pattern
    # as the news DB) so 10 years of yfinance OHLCV never bloats the
    # screener database. When unset, the engine falls back to SQLite for
    # local dev / tests only.
    backtest_database_url: str = ""

    # ── News Pipeline (dedicated key + separate news DB) ────────────────
    # Dedicated Finnhub key used ONLY for /company-news so the news fetch
    # never competes with the screener's quote/profile refresh on the main
    # 10 keys. If it hits a rate limit, the fetcher falls back to the shared
    # pool (see src/data_fetcher.fetch_stock_news).
    #
    # NEWS_DATABASE_URL is a SEPARATE Neon project/database that stores
    # news articles + sentiment scores, keeping the bloat off the screener
    # DB. Sentiment is scored by the free LLM pool (src/news.py) because
    # Finnhub's /news-sentiment endpoint is a paid-tier resource (403 on
    # free keys — verified live).
    news_finnhub_key: str = ""
    news_database_url: str = ""
    news_symbols_limit: int = 150        # how many top-by-market-cap stocks to refresh
    news_refresh_minutes: int = 120      # how often the news job runs
    # Provider to TRY FIRST for sentiment scoring (background batch job —
    # wants the fastest provider, not the primary). Z.ai GLM measured
    # 42-135s on the strict-JSON sentiment prompt (times out at the 45s
    # provider limit and relies on failover); Groq/Gemini answer in <2s.
    # The router still fails over to the normal priority order if the
    # preferred provider is cooling or errors.
    news_llm_prefer: str = "groq"

    # ── Logging ────────────────────────────────────────────────────────
    log_level: str = "INFO"               # DEBUG, INFO, WARNING, ERROR, CRITICAL
    log_format: str = "text"              # "text" (colored console) or "json" (structured)
    log_file: str = ""                     # Optional path to a log file (JSON format)

    # ── Authentication (JWT, stdlib-only) ──────────────────────────────
    # Zero-dependency auth: HMAC-SHA256 signed tokens (JWT HS256 format)
    # via hmac/hashlib, PBKDF2 password hashing (see src/auth.py).
    # JWT_SECRET must be set in .env for auth endpoints to work.
    jwt_secret: str = ""
    jwt_expire_hours: int = 24 * 7        # tokens valid for 7 days
    password_min_length: int = 8
    # Password-reset link lifetime. The plan's window is 30–60 minutes:
    # long enough to find the email, short enough that a link sitting in an
    # inbox for a day is dead.
    password_reset_token_ttl_minutes: int = 45
    # Sliding sessions: POST /auth/refresh re-issues a token while the user is
    # active, but never beyond this many days after they last entered a
    # password. Without a cap, a stolen token could be refreshed forever.
    session_max_age_days: int = 30

    # ── Email verification (SendGrid Web API) ─────────────────────────
    # Signup verification: every new account gets an emailed verification
    # link (SendGrid's REST /v3/mail/send, stdlib-only client in
    # src/emails.py — no SDK dependency). New users start email_verified
    # = False; GET /auth/verify-email?token=... flips it to True.
    #
    #   SENDGRID_API_KEY   — the "SG.xxx" Web API key.
    #   EMAIL_FROM_ADDRESS — a SENDER VERIFIED in the SendGrid dashboard;
    #     SendGrid rejects mail from unverified senders with 403.
    #   APP_BASE_URL       — public origin used to build the verification
    #     link (http://localhost:3000 in dev, https://app.stratoscan.com
    #     in prod). The link points at your frontend, which then calls
    #     GET /auth/verify-email with the token.
    #   EMAIL_VERIFICATION_REQUIRED — when True, unverified accounts are
    #     blocked from login (403 with a resend hint). Default False keeps
    #     existing accounts + dev flows working while the feature is new.
    #
    # Cost discipline: verification emails are sent ONLY at signup (and on
    # explicit resend), never per login — free-tier SendGrid (100/day)
    # covers thousands of signups.
    sendgrid_api_key: str = ""
    email_from_address: str = ""
    app_base_url: str = "http://localhost:3000"
    # Public origin that serves share links (GET /r/{share_id}). The API
    # renders those pages itself (server-side, for link-preview crawlers), so
    # this is the API's PUBLIC address — e.g. https://api.stratoscan.app.
    # Blank = the origin the request arrived on (fine locally).
    share_base_url: str = ""
    email_verification_required: bool = False
    email_verification_token_ttl_hours: int = 24  # link expires after 24h

    # ── SMTP transport (alternative to the SendGrid API) ─────────────
    # SMTP_HOST is the switch: when it is non-empty, src/emails.py sends
    # over SMTP via smtplib and SENDGRID_API_KEY is ignored entirely. The
    # caller-facing contract does not change (returns bool, never raises),
    # so nothing that calls send_email needs to know which transport ran.
    # A local mail catcher is the reason this exists: pointing SMTP_HOST at
    # one exercises the signup mail path end to end — including the token
    # round-trip — without a SendGrid account or a verified sender.
    #
    #   SMTP_HOST     — the switch. Blank = SendGrid API (production).
    #   SMTP_PORT     — mailpit listens on 1025; relays conventionally 587.
    #   SMTP_USERNAME — blank = no AUTH (mailpit, and most local relays).
    #   SMTP_PASSWORD — only read when SMTP_USERNAME is set.
    #   SMTP_STARTTLS — True upgrades the connection before AUTH; False is
    #     correct for a plain local relay on 1025.
    smtp_host: str = ""          # non-empty selects SMTP over the SendGrid API
    smtp_port: int = 1025
    smtp_username: str = ""      # blank = no AUTH (mailpit, and most local relays)
    smtp_password: str = ""
    smtp_starttls: bool = False

    # ── Google OAuth ("Continue with Google") ────────────────────────
    # Server-side authorization-code flow (src/google_oauth.py) — no OAuth
    # SDK and no new dependency: urllib + hmac + json, the same shape as
    # src/emails.py and src/redis_ratelimit.py. Create the client at
    # console.cloud.google.com → APIs & Services → Credentials, then put
    # the pair in .env (which is git-ignored, chmod 600).
    #
    #   GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET — the credential pair. Both
    #     blank (the default) leaves everything as it was: the button sends
    #     visitors to /login?oauth_error=unavailable.
    #   GOOGLE_REDIRECT_URI — must equal the Authorized redirect URI in the
    #     Google console CHARACTER FOR CHARACTER or the token exchange comes
    #     back redirect_uri_mismatch. The default targets the local API.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # ── Admin dashboard ──────────────────────────────────────────────
    # Comma-separated list of emails granted the admin role. The admin
    # role gates the /admin/* management API + dashboard (user management,
    # job monitoring, refresh triggers). Every email in this list is
    # auto-promoted at login (idempotent) so bootstrapping the first admin
    # is just: put your email in .env, log in, open /admin.
    admin_emails: str = ""

    # ── Rate Limiting ──────────────────────────────────────────────────
    # Master switch for the API rate limiter (slowapi). Disable only for
    # local development / heavy test suites — keep ON in production.
    rate_limit_enabled: bool = True
    # Comma-separated IPs EXEMPT from all rate limiting (per-IP AND global
    # buckets — a whitelisted client skips the limiter entirely). Escape
    # hatch for local dev / testing against a specific machine; keep empty
    # in production.
    #   RATE_LIMIT_BYPASS_IPS=127.0.0.1,::1,192.168.1.5
    rate_limit_bypass_ips: str = ""
    # The header carrying the REAL client IP when the API sits behind a
    # proxy (Day 13). Blank = the socket peer (correct locally and in
    # tests). Behind Fly the socket peer is Fly's proxy, so every visitor
    # shared ONE per-IP bucket; set this to "Fly-Client-IP", which Fly's
    # edge sets and overwrites. Never X-Forwarded-For: a client can prepend
    # anything to it. Only set it behind a proxy that overwrites the header.
    client_ip_header: str = ""
    # Per-IP DEFAULT caps for the generic "read" bucket — the budget every
    # request falls into unless an endpoint is explicitly scoped to a
    # tighter bucket (screen/enrich/backtest/refresh are cost shields and
    # keep their own caps in src/api.py RATE_LIMITS, unaffected by these).
    #   RATE_LIMIT_PER_MINUTE -> burst (60s window)
    #   RATE_LIMIT_PER_DAY    -> daily (24h window); weekly = 5x daily
    # Production defaults: 60/min · 1,000/day · 5,000/week per IP.
    rate_limit_per_minute: int = 60
    rate_limit_per_day: int = 1000

    # ── Upstash Redis (exact cross-worker rate limiting) ───────────────
    # slowapi's counters are in-memory and per-process: with multiple
    # uvicorn workers each worker counts independently (limits get looser,
    # never stricter) — safe at MVP scale, but not exact. Pointing the
    # limiter at Upstash Redis (REST API, stdlib-only client in
    # src/redis_ratelimit.py) makes every worker share ONE counter.
    #   REDIS_RATE_LIMIT_ENABLED=true + these two vars -> Redis backend
    #   (falls back to the in-memory store if Redis is unreachable, so a
    #   Redis outage never breaks the API).
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    redis_rate_limit_enabled: bool = False
    redis_rate_limit_timeout: float = 10.0  # seconds per REST call

    # ── Caching ────────────────────────────────────────────────────────
    # Master switch for the in-memory TTL caches (src/cache.py). Two caches:
    #   - interpret: query -> InterpretedQuery (the LLM call is the expensive
    #     part; long TTL so identical queries never burn a free LLM call)
    #   - screen:    full /screen responses keyed by query+limit+offset
    #     (short TTL so repeat hits are instant while staying fresh against
    #     the 10-minute universe refresh)
    cache_enabled: bool = True
    interpret_cache_ttl: int = 3600 * 24      # 24h — LLM output is deterministic per query
    interpret_cache_max_entries: int = 1024
    screen_cache_ttl: int = 60                # 60s — refresh cadence is 10 min, 60s stays fresh
    screen_cache_max_entries: int = 1024

    # ── Product analytics (src/analytics.py) ───────────────────────────
    # Master switch for the event funnel. When False, ``analytics.track()`` is
    # a no-op: nothing is written to the ``events`` table, and no derived event
    # (second_backtest / returned_day_2 / returned_day_7) is computed. Call
    # sites stay in place, so flipping this back on re-enables the funnel
    # without a code change. Default True — the first cohort teaches its
    # lesson exactly once, so the funnel is on unless deliberately disabled.
    analytics_enabled: bool = True

    # ── CORS (browser security) ─────────────────────────────────────────
    # Comma-separated list of origins allowed to call the API from a browser.
    # LOCKED DOWN from the old wildcard: with allow_credentials=True a "*"
    # origin is actually rejected by modern browsers anyway (CORS spec), so
    # explicit origins are both safer AND the only correct config. Dev
    # defaults cover local frontends (Vite 5173, Create React App 3000, incl.
    # the 127.0.0.1 spellings — browsers treat localhost and 127.0.0.1 as
    # DIFFERENT origins); production MUST set CORS_ALLOW_ORIGINS to the real
    # frontend domain(s).
    cors_allow_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )

    # ── Backtest Cache Warmer (Tier 4 pre-fetch) ───────────────────────
    # Background job that pre-fetches 10 years of yfinance OHLCV for the
    # screener's top-N stocks (by market cap) into the backtest DB, so a
    # premium backtest never waits on a cold yfinance download at request
    # time (the #1 latency complaint against backtesting engines).
    #   - backtest_cache_warmer_enabled: master switch (default ON).
    #   - backtest_cache_warmer_limit: how many top symbols to warm per
    #     cycle (default 100 — one full cycle fits inside a nightly window).
    #   - backtest_cache_warmer_stale_days: a symbol whose newest cached
    #     bar is older than this is refreshed (default 2 — history only
    #     changes at market close, so daily freshness is enough).
    #   - backtest_cache_warmer_delay: politeness pause between yfinance
    #     downloads so the warm cycle never hammers Yahoo (default 0.2s).
    #   - backtest_cache_warmer_time: HH:MM local time the job runs
    #     (default 03:30 — before the US market opens, so the cache is
    #     warm for the trading day).
    backtest_cache_warmer_enabled: bool = True
    backtest_cache_warmer_limit: int = 100
    backtest_cache_warmer_stale_days: int = 2
    backtest_cache_warmer_delay: float = 0.2
    backtest_cache_warmer_time: str = "03:30"

    # ── Weekly strategy digest (Day 10) ─────────────────────────────────
    # A daily pass that emails each opted-in user (at most once per ISO
    # week) what their saved strategies did since they saved them. Daily so
    # a provider outage on one day is retried the next without double
    # sending (digest_sends is the per-week ledger).
    #   - digest_max_sends_per_run: hard cap per pass — the SendGrid free
    #     tier is ~100/day and verification/reset mail shares it.
    digest_enabled: bool = True
    digest_time: str = "14:00"
    digest_max_sends_per_run: int = 60

    # ── Background Job Monitoring ───────────────────────────────────────
    # Records every scheduler job run (status, duration, record counts,
    # errors) + a scheduler heartbeat so a silently-dead refresh is never
    # missed. Exposed via GET /ops/jobs (see src/job_monitor.py).
    #
    # Heartbeat persistence throttle: the scheduler ticks every 30s, but
    # writing to Neon each tick would keep the serverless compute awake
    # 24/7 (auto-suspend needs ~5 min of idle) and blow the free tier.
    # The store persists a heartbeat at most every N minutes instead;
    # "scheduler alive" is judged against 3x that interval. In-memory
    # fallback when no database — monitoring never breaks a refresh job.
    job_monitor_enabled: bool = True
    job_heartbeat_persist_minutes: int = 15

    # ── Observability & Alerting ────────────────────────────────────────
    # Two delivery channels (src/alerts.py) — at least one must be set or
    # alerting is a no-op:
    #   ALERT_WEBHOOK_URL: a Slack/Discord/ntfy-compatible incoming webhook
    #     (preferred when set — delivered via stdlib urllib POST).
    #   ALERT_EMAIL: recipient for email alerts (delivered via the existing
    #     SendGrid client in src/emails.py, which needs SENDGRID_API_KEY +
    #     EMAIL_FROM_ADDRESS too). Used when no webhook is configured.
    # Failed background jobs and a stale/dead scheduler fire a one-line
    # alert through the configured channel. Fire-and-forget + per-key
    # throttling: an alert storm is worse than no alert, and alerting
    # NEVER blocks or breaks the job.
    alert_webhook_url: str = ""
    alert_email: str = ""
    alert_webhook_min_interval_seconds: int = 900  # min between alerts per key
    # Master switch for the Prometheus text-format /metrics endpoint
    # (src/metrics.py — stdlib-only, near-zero cost).
    metrics_enabled: bool = True

    # ── Deployment environment ──────────────────────────────────────────
    # "development" (default) or "production". In production the interactive
    # API docs (Swagger UI / ReDoc / OpenAPI schema) are DISABLED — they
    # expose the full route + model surface to anyone, fine for local dev
    # but an unnecessary attack surface in prod. Set APP_ENV=production in
    # the deployed environment (.env or platform env vars).
    app_env: str = "development"

    # ── Paths ───────────────────────────────────────────────────────────
    backtest_db_path: str = str(_project_root / "data" / "backtest_history.db")

    @property
    def finnhub_keys(self) -> List[str]:
        """Return the list of non-empty Finnhub API keys."""
        return [
            key.strip()
            for key in [
                self.finnhub_key_01,
                self.finnhub_key_02,
                self.finnhub_key_03,
                self.finnhub_key_04,
                self.finnhub_key_05,
                self.finnhub_key_06,
                self.finnhub_key_07,
                self.finnhub_key_08,
                self.finnhub_key_09,
                self.finnhub_key_10,
            ]
            if key.strip()
        ]

    @property
    def has_supabase_credentials(self) -> bool:
        """Check if the old Supabase REST API credentials are configured."""
        return bool(self.supabase_url and self.supabase_service_key)

    @property
    def has_database_url(self) -> bool:
        """Check if a PostgreSQL connection string is configured."""
        return bool(self.database_url)

    @property
    def has_news_database(self) -> bool:
        """Check if the separate news database is configured."""
        return bool(self.news_database_url)

    @property
    def has_backtest_database(self) -> bool:
        """Check if the dedicated backtesting database is configured.

        When set, the BacktestEngine stores historical OHLCV + results in
        PostgreSQL (Neon) instead of the local SQLite dev file.
        """
        return bool(self.backtest_database_url)

    @property
    def has_upstash_redis(self) -> bool:
        """Check if the Upstash Redis REST backend is fully configured."""
        return bool(self.upstash_redis_rest_url and self.upstash_redis_rest_token)

    @property
    def has_news_credentials(self) -> bool:
        """Check if the full news pipeline is configured (key + DB)."""
        return bool(self.news_finnhub_key and self.news_database_url)

    @property
    def finnhub_key_count(self) -> int:
        """Number of configured Finnhub API keys."""
        return len(self.finnhub_keys)

    @property
    def has_jwt_secret(self) -> bool:
        """Check if JWT auth is configured (JWT_SECRET set)."""
        return bool(self.jwt_secret)

    @property
    def has_google_oauth(self) -> bool:
        """Check if "Continue with Google" is configured (both halves of the pair).

        Both are required. An id without a secret can open the consent
        screen but can never exchange the returned code — the user would
        approve access and then land on an error page, which is the worst
        possible moment to fail. Checking one flag before the redirect
        keeps that from ever happening.
        """
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def mail_transport(self) -> str:
        """Which transport send_email will use: "smtp", "sendgrid", or "" (none).

        SMTP_HOST wins — src/emails.py dispatches on it directly, and this
        property must agree with that dispatch or the two will drift.
        """
        if self.smtp_host:
            return "smtp"
        if self.sendgrid_api_key:
            return "sendgrid"
        return ""

    @property
    def has_email_verification(self) -> bool:
        """Check if email verification is fully configured.

        Needs a transport AND a from-address. EMAIL_FROM_ADDRESS is required
        by both — SendGrid rejects mail from unverified senders with 403,
        and SMTP needs a From header.
        """
        return bool(self.mail_transport and self.email_from_address)

    @property
    def alerts_enabled(self) -> bool:
        """True when at least one alert delivery channel is configured.

        A webhook URL is enough on its own. Email needs the recipient, a
        configured transport (see mail_transport) and a from-address.
        """
        if self.alert_webhook_url:
            return True
        return bool(
            self.alert_email and self.mail_transport and self.email_from_address
        )

    @property
    def cors_origins(self) -> List[str]:
        """Parsed list of allowed browser origins (comma-separated setting)."""
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        """True when the app runs in a production environment.

        Gates prod-only hardening: interactive API docs are disabled when
        production, so the deployed surface is the API routes only.
        """
        return self.app_env.strip().lower() in ("production", "prod")

    @property
    def admin_email_list(self) -> List[str]:
        """Parsed list of admin emails (lowercased, trimmed)."""
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        # Provider-specific LLM vars (ZAI_*, GEMINI_*, ...) are read directly
        # from the environment by src/llm_router.py, so let them pass through
        # instead of rejecting them as unknown fields.
        extra = "allow"


# Singleton settings instance
settings = Settings()

# Provider names the LLM router can build (documented reference set —
# matches the <NAME>_BASE_URL / <NAME>_MODEL / <NAME>_KEYS blocks in
# .env.example). Used by validate_env_template so a provider added to
# LLM_PROVIDERS without a documented template block is caught.
KNOWN_LLM_PROVIDERS = ("ZAI", "ZENMUX", "GEMINI", "GROQ")


def _env_template_documented_keys() -> set:
    """All keys documented in ``.env.example``.

    Both active lines (``KEY=value``) and commented-out documentation lines
    (``# KEY=...``) count as documented — the point is that an operator
    opening the template can SEE every key the app might read.
    """
    env_example = _project_root / ".env.example"
    if not env_example.exists():
        return set()
    keys = set()
    for line in env_example.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=", line)
        if m:
            keys.add(m.group(1))
    return keys


def _app_read_env_keys() -> set:
    """Every env var the app reads: Settings fields + per-provider LLM vars."""
    keys = {name.upper() for name in Settings.model_fields}
    providers = set(
        p.strip().upper()
        for p in settings.llm_providers.split(",")
        if p.strip()
    )
    providers |= set(KNOWN_LLM_PROVIDERS)
    for p in providers:
        keys |= {f"{p}_BASE_URL", f"{p}_MODEL", f"{p}_KEYS", f"{p}_TIMEOUT"}
    return keys


def validate_env_template() -> List[str]:
    """Verify every env var the app reads is documented in ``.env.example``.

    Returns an empty list when the template documents everything the app
    can read, or a single warning naming the missing keys. Skips silently
    when ``.env.example`` is absent (e.g. a deployed container ships only
    the real ``.env`` — there is no template to validate).
    """
    documented = _env_template_documented_keys()
    if not documented:
        return []
    missing = sorted(_app_read_env_keys() - documented)
    if not missing:
        return []
    return [
        "Env var(s) read by the app are missing from .env.example: "
        + ", ".join(missing)
        + ". Add them to the template so every configurable key is documented."
    ]


def validate_config() -> List[str]:
    """Validate that all required configuration is present.

    Returns:
        A list of missing or invalid configuration warnings. Empty list means all good.
    """
    warnings: List[str] = []

    if not settings.finnhub_keys:
        warnings.append("No Finnhub API keys configured. Set at least FINNHUB_KEY_01.")

    if not settings.screener_llm_key:
        warnings.append("Screener LLM key is missing. Set SCREENER_LLM_KEY.")

    if not settings.backtest_llm_key:
        logger.debug("Backtest LLM key not set (optional — backtester uses rule-based logic)")

    if not settings.has_database_url:
        warnings.append(
            "DATABASE_URL is missing. The screener needs a PostgreSQL connection string. "
            "Set it in your .env file (e.g., DATABASE_URL=\"postgresql://...\")."
        )

    if not settings.has_backtest_database:
        # Non-fatal (local dev + hermetic tests intentionally use the SQLite
        # fallback), but a PRODUCTION deploy that forgets this env var would
        # silently run the backtester on a single-writer SQLite file — the
        # exact failure mode the Neon migration exists to prevent. Surface it
        # so operators can't miss it.
        warnings.append(
            "BACKTEST_DATABASE_URL is missing. The backtester will use the local "
            "SQLite dev fallback. In production, set it to a dedicated Neon "
            "database so historical OHLCV is shared across instances."
        )

    if settings.redis_rate_limit_enabled and not settings.has_upstash_redis:
        warnings.append(
            "REDIS_RATE_LIMIT_ENABLED=true but UPSTASH_REDIS_REST_URL / "
            "UPSTASH_REDIS_REST_TOKEN are missing — rate limiting will run "
            "on the per-process in-memory store instead of exact Redis."
        )

    if settings.job_monitor_enabled and not settings.has_database_url:
        # Non-fatal (dev + hermetic tests intentionally use the in-memory
        # fallback), but a PRODUCTION deploy without DATABASE_URL would
        # lose job history on every restart — surface it so operators can't
        # miss the silent degradation.
        warnings.append(
            "JOB_MONITOR_ENABLED=true but DATABASE_URL is missing — job "
            "history/heartbeat will be in-memory only (lost on restart). "
            "Set DATABASE_URL for persistent background-job monitoring."
        )

    if settings.digest_enabled and not (settings.share_base_url or "").strip():
        # The digest's unsubscribe link is an API route; the scheduler has no
        # request origin to fall back on, so it refuses to send without one.
        warnings.append(
            "DIGEST_ENABLED=true but SHARE_BASE_URL is unset — the weekly "
            "strategy email will not send (its unsubscribe link must point at "
            "the API's public origin). Set SHARE_BASE_URL or DIGEST_ENABLED=false."
        )

    # Every env var the app reads must be documented in the template.
    warnings.extend(validate_env_template())

    return warnings
