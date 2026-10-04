"""FastAPI REST API for Stratoscan.

Bridges the gap between the CLI backend and a web frontend. All core
Stratoscan features (screening, backtesting, enrichment, data refresh)
are exposed as RESTful JSON endpoints.

Run with:
    python -m src.main serve          # Via the CLI
    uvicorn src.api:app --reload      # Direct uvicorn
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

# (datetime imported above is used for model fields — Pydantic v2 serializes
#  datetime to ISO-8601 strings in responses automatically.)

from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from src.config import settings
from src.logging_config import configure_logging, configure_uvicorn_logging
from src.cache import TTLCache
from src.redis_ratelimit import (
    InMemoryRateLimitStore,
    get_rate_limit_store,
)
from src.metrics import MetricsMiddleware, render_metrics

# Auth dependencies (module-level: used in endpoint signatures via Depends)
from src.auth import get_current_user, require_admin

# Full /screen response cache — short TTL (settings.screen_cache_ttl, default
# 60s) so repeat hits (re-clicking a preset, paginating back) are instant while
# staying fresh against the 10-minute universe refresh. Keyed by the exact
# (query, limit, offset) triple. Skipped when settings.cache_enabled is False.
_screen_cache = TTLCache(
    ttl_seconds=settings.screen_cache_ttl,
    max_entries=settings.screen_cache_max_entries,
    name="screen",
)

# ── App Setup ────────────────────────────────────────────────────────────────

# Configure logging at import time so it's ready before any endpoint runs
configure_logging()
configure_uvicorn_logging()

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Stratoscan API",
    description=(
        "AI-powered stock screener and backtesting platform. "
        "Turn natural language investment ideas into actionable stock lists."
    ),
    version="0.1.0",
    # Security (attack-surface reduction): the interactive docs (Swagger UI,
    # ReDoc) and the OpenAPI schema expose the full route + model surface to
    # anyone who can reach the API. That's a dev convenience and a prod
    # liability — disabled in production (settings.is_production).
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url="/openapi.json" if not settings.is_production else None,
)

# CORS — locked down to the configured origins (settings.cors_allow_origins,
# comma-separated in .env). Note: with allow_credentials=True a wildcard "*"
# is rejected by browsers per the CORS spec, so explicit origins are the only
# correct config — production sets CORS_ALLOW_ORIGINS to the real frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════════
# Rate Limiting
# ═══════════════════════════════════════════════════════════════════════════════
#    # Cost model (see conversation): per-IP limits are generous for real humans —
    # a normal user does 5-10 screens and 1-2 enriches a day, so these caps are
    # invisible to them. The GLOBAL limits are the real shield: all users share one
    # bucket per LLM key, so a bad actor can never burn the key's daily budget.
    #
    #   Screen     = 1 free ZenMux LLM call + cheap DB read            (near-zero cost)
    #   Interpret  = 1 free ZenMux LLM call, no DB                     (pure free)
    #   Enrich     = 1 LLM call + 20 Finnhub calls                     (only real per-IP cost)
    #   Backtest   = 1 LLM call + heavy CPU (yfinance)                 (free — no paywall)
    #   Auth       = register/login/me — password hashing + DB write   (cheap)
    #   Refresh    = 1 call -> thousands of Finnhub calls              (thousands of calls!)
    #   GET reads  = zero LLM, zero Finnhub — just a DB read           (genuinely free)
#
# Format: per-scope caps, enforced by ONE middleware below. Tweak numbers
# here in ONE place — no endpoint code needs to change. Every cap runs
# against the shared store (Upstash Redis when configured, in-memory
# otherwise) so N uvicorn workers share ONE counter: exact cross-worker
# limiting — the README roadmap item, now done.

RATE_LIMITS = {
    # Per-IP caps: burst (per minute), daily (per 24h), weekly (rolling 7d)
    "screen":    {"burst": 5,   "daily": 50,    "weekly": 250},
    "interpret": {"burst": 10,  "daily": 100,   "weekly": 500},
    "enrich":    {"burst": 3,   "daily": 20,    "weekly": 100},
    "backtest":  {"burst": 2,   "daily": 10,    "weekly": 50},
    "refresh":   {"burst": 1,   "daily": 10,    "weekly": 50},
    # The generic per-IP bucket (every endpoint not explicitly scoped
    # above). Env-configurable for production: RATE_LIMIT_PER_MINUTE /
    # RATE_LIMIT_PER_DAY (src/config.py). Weekly = 5x the daily cap so
    # the 60/min · 1,000/day · 5,000/week profile stays proportional.
    # The tighter scopes above are cost shields and are NOT affected by
    # these two vars — they only move the per-IP default.
    "read":      {
        "burst": settings.rate_limit_per_minute,
        "daily": settings.rate_limit_per_day,
        "weekly": settings.rate_limit_per_day * 5,
    },
    "auth":      {"burst": 10,  "daily": 50,    "weekly": 350},
    # VULN-002/003 fix: /auth/register gets its OWN tighter scope instead of
    # sharing the auth bucket. Account creation is rare for legit users but
    # is the send-verification-email trigger — the old 10/min, 50/day caps
    # let a handful of IPs burn the SendGrid free tier (100 emails/day) and
    # enumerate accounts at scale. These caps are strictly tighter than auth.
    "register":  {"burst": 3,   "daily": 10,    "weekly": 30},
    # Every OTHER auth action that sends an email — password reset and
    # resend-verification — spends the same SendGrid free-tier budget that
    # registration does, so it gets the same caps. Its own scope (not
    # "register") so resending a link can't exhaust a signup allowance, and
    # vice versa. Resend used to sit in the generic auth bucket: 50 emails a
    # day per IP, outside the planet-wide budget below.
    "auth_mail": {"burst": 3,   "daily": 10,    "weekly": 30},
    # Job-status polls (see _scope_for_path). Generous because they are free,
    # but still capped per IP so a runaway client can't hammer the DB; feeds
    # no global bucket — there is no shared budget for them to protect.
    "backtest_poll": {"burst": 120, "daily": 10000, "weekly": 50000},
    # Day 6: a backtest with no account. Tighter than authenticated per IP and
    # feeding the same planet-wide backtest_llm bucket. The daily cap sits
    # above the 3-per-session allowance so the friendly "create an account"
    # prompt is what a visitor normally sees, while discarding the session
    # token to start over still runs into this per-IP cap.
    "anon_backtest": {"burst": 2, "daily": 6, "weekly": 20},
    # Creating, listing and revoking share links: cheap rows, but rows.
    "share": {"burst": 10, "daily": 100, "weekly": 400},
}

# Global aggregates (ALL users share one bucket, enforced in the same batch
# as the per-IP caps).
GLOBAL_LIMITS = {
    "screener_llm": 900,  # /screen + /interpret + /enrich together
    "backtest_llm": 200,  # all /backtest calls together
    # /refresh is ADMIN-GATED (see the route) — this global daily cap is
    # defense-in-depth against a compromised admin token or a botnet that
    # rotates past the per-IP caps: each /refresh fires THOUSANDS of
    # Finnhub calls, so the whole planet may trigger it at most 3x/day.
    "refresh_ops": 3,
    # VULN-002 fix: planet-wide daily cap on account creation. Even with
    # per-IP caps, a botnet rotating IPs could otherwise create hundreds of
    # accounts/day, each firing a verification email — this keeps the whole
    # planet under the SendGrid free tier (100/day) with headroom for legit
    # signups. Feeds from the "register" scope.
    "auth_register": 50,
}

# Which per-IP scope feeds which global bucket.
SCOPE_GLOBAL = {
    "screen": "screener_llm",
    "interpret": "screener_llm",
    "enrich": "screener_llm",
    "backtest": "backtest_llm",
    "refresh": "refresh_ops",
    "register": "auth_register",
    # auth_register is the planet-wide cap on email-sending auth actions
    # (it exists to keep the whole planet under SendGrid's 100/day). A reset
    # email costs exactly what a verification email does, so it draws from
    # the same bucket rather than getting a second, additive allowance.
    "auth_mail": "auth_register",
    # Anonymous backtests spend the same engine/LLM budget as signed-in ones.
    "anon_backtest": "backtest_llm",
}

_BURST_SECONDS = 60
_DAY_SECONDS = 24 * 60 * 60
_WEEK_SECONDS = 7 * 24 * 60 * 60


def _scope_for_path(path: str) -> str:
    """Map a request path to its limit bucket (anything else: read)."""
    for prefix, scope in (
        ("/screen", "screen"),
        ("/interpret", "interpret"),
        ("/enrich", "enrich"),
        # Polling a running job (GET /backtest/async/{job_id}) reads one
        # in-memory dict entry or one DB row — no LLM, no download. It must
        # NOT spend the backtest cost shield: the UI polls every 1.5s, so under
        # the 2/min backtest burst the third poll of every job 429'd, and each
        # poll also drew down the user's 10/day backtests and the planet-wide
        # backtest_llm bucket. Matched before "/backtest"; starting a job
        # (POST /backtest/async, no trailing segment) stays in "backtest".
        # Day 6 trial routes. Polls before starts, starts before the rest of
        # /try (session minting and claiming are ordinary auth traffic).
        ("/try/backtest/", "backtest_poll"),
        ("/try/backtest", "anon_backtest"),
        ("/try", "auth"),
        ("/results", "share"),
        ("/backtest/async/", "backtest_poll"),
        ("/backtest", "backtest"),
        ("/refresh", "refresh"),
        # VULN-002/003 fix: /auth/register must be matched BEFORE the generic
        # /auth prefix so it lands in its dedicated (tighter) bucket.
        ("/auth/register", "register"),
        # Email-sending auth actions, matched before the generic /auth prefix
        # for the same reason.
        ("/auth/password-reset", "auth_mail"),
        ("/auth/resend-verification", "auth_mail"),
        ("/auth", "auth"),
    ):
        if path.startswith(prefix):
            return scope
    return "read"


def _canonical_ip(raw: str) -> str:
    """Canonicalize an IP string so equivalent addresses match exactly.

    Uses ipaddress to collapse textual variants: IPv4-mapped IPv6 such as
    ``::ffff:127.0.0.1`` becomes ``127.0.0.1`` (``compressed`` alone would
    keep the ``::ffff:`` prefix), and IPv6 is returned in its compressed
    form. Non-IP values (e.g. the "unknown" client fallback) pass through
    unchanged.
    """
    text = raw.strip()
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        return text
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    return addr.compressed


class RateLimitMiddleware:
    """Unified rate limiting for every HTTP request.

    Replaces slowapi's per-endpoint decorators AND the old weekly middleware
    with a single pass that enforces all four layers in ONE store call:

      - per-IP burst   (fixed 60s window)   key: ``rl:burst:{scope}:{ip}``
      - per-IP daily   (fixed 24h window)   key: ``rl:daily:{scope}:{ip}``
      - per-IP weekly  (rolling 7d window)  key: ``rl:weekly:{scope}:{ip}``
      - global bucket  (fixed 24h window)   key: ``rl:global:{bucket}``

    One ``check_many`` call per request = one atomic Redis transaction when
    the Upstash backend is active, one lock acquisition in the in-memory
    fallback. If Redis is configured but unreachable at runtime the
    middleware fails OPEN to the in-memory store (logged), so a Redis outage
    never breaks the API.

    Runs OUTSIDE the CORS middleware (registered after it), so its 429
    bypasses CORSMiddleware — it therefore echoes allowed Origins itself,
    exactly like the weekly middleware it replaces.
    """

    def __init__(self, app):
        self.app = app
        self._fallback = InMemoryRateLimitStore()
        # Whitelisted IPs skip the limiter entirely (per-IP AND global
        # buckets). Parsed once at startup from RATE_LIMIT_BYPASS_IPS.
        # Entries are canonicalized so textual variants of the same
        # address (e.g. ::ffff:127.0.0.1 vs 127.0.0.1) all match.
        self._bypass_ips = {
            _canonical_ip(raw)
            for raw in settings.rate_limit_bypass_ips.split(",")
            if raw.strip()
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not settings.rate_limit_enabled:
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        # Never count CORS preflight against a user's budget.
        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        ip = request.client.host if request.client else "unknown"
        if _canonical_ip(ip) in self._bypass_ips:
            await self.app(scope, receive, send)
            return
        bucket = _scope_for_path(request.url.path)
        caps = RATE_LIMITS.get(bucket) or RATE_LIMITS["read"]

        checks = [
            (f"rl:burst:{bucket}:{ip}", caps["burst"], _BURST_SECONDS, False),
            (f"rl:daily:{bucket}:{ip}", caps["daily"], _DAY_SECONDS, False),
            (f"rl:weekly:{bucket}:{ip}", caps["weekly"], _WEEK_SECONDS, True),
        ]
        global_bucket = SCOPE_GLOBAL.get(bucket)
        if global_bucket:
            checks.append(
                (f"rl:global:{global_bucket}", GLOBAL_LIMITS[global_bucket], _DAY_SECONDS, False)
            )

        def _run_checks(checks):
            # get_rate_limit_store() resolves the backend ONCE per process —
            # and the Upstash resolution performs a blocking PING. Both that
            # and check_many (urllib over REST) are network I/O, so run them
            # off the event loop together; one slow Redis call never
            # serializes the whole API. The in-memory fallback is local and
            # stays synchronous.
            return get_rate_limit_store().check_many(checks)

        try:
            results = await asyncio.to_thread(_run_checks, checks)
        except Exception:
            # Fail-open: rate limiting must never take the API down.
            logger.warning(
                "Rate-limit store failed — falling back to in-memory",
                exc_info=True,
            )
            results = self._fallback.check_many(checks)

        denied = [(allowed, retry) for (allowed, _rem, retry) in results if not allowed]
        if denied:
            retry_after = max(r for _, r in denied)
            headers = {"Retry-After": str(retry_after)}
            request_origin = request.headers.get("origin")
            if request_origin in settings.cors_origins:
                # Match CORSMiddleware's credentialed-request output: the
                # ACAO header alone isn't enough — the Fetch spec also
                # requires Access-Control-Allow-Credentials for the body to
                # be readable by a credentialed browser fetch.
                headers["Access-Control-Allow-Origin"] = request_origin
                headers["Access-Control-Allow-Credentials"] = "true"
                # Without Access-Control-Expose-Headers, browser JS cannot
                # READ the Retry-After header (it is not a CORS-safelisted
                # response header) — the frontend's 429 countdown would
                # silently fall back to the body's retry_after.
                headers["Access-Control-Expose-Headers"] = "Retry-After"
            response = JSONResponse(
                status_code=429,
                content={
                    "error": True,
                    "detail": (
                        "Rate limit exceeded. Limits reset on a rolling "
                        "cycle — please slow down and try again later."
                    ),
                    "status_code": 429,
                    "retry_after": retry_after,
                },
                headers=headers,
            )
            await response(scope, receive, send)
            return

        # Allowed: surface the budget on the response (daily window, per-IP).
        _daily_remaining, _daily_reset = results[1][1], results[1][2]
        _reset_ts = int(time.time() + _daily_reset)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [
                    (b"ratelimit-limit", str(caps["daily"]).encode()),
                    (b"ratelimit-remaining", str(max(0, _daily_remaining)).encode()),
                    (b"ratelimit-reset", str(_reset_ts).encode()),
                ]
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(RateLimitMiddleware)

# /metrics — registered AFTER the rate limiter so it sits OUTERMOST and
# counts 429s from the rate limiter too (see src/metrics.py).
app.add_middleware(MetricsMiddleware)


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic Models
# ═══════════════════════════════════════════════════════════════════════════════


class HealthResponse(BaseModel):
    """Response model for the health check endpoint."""

    status: str = "ok"
    version: str = "0.1.0"
    finnhub_keys_configured: int = 0
    screener_llm_configured: bool = False
    backtest_llm_configured: bool = False
    database_configured: bool = False
    database_alive: Optional[bool] = None
    database_latency_ms: Optional[float] = None
    uptime_seconds: Optional[float] = None


class ScreenRequest(BaseModel):
    """Request body for the screening endpoint."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Natural language stock screening query (up to 4000 characters)",
        examples=["undervalued small-cap tech stocks with rising momentum"],
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of results to return per page",
    )
    offset: int = Field(
        default=0,
        ge=0,
        le=1_000_000,
        description=(
            "Number of matching results to skip (pagination). "
            "Fetch the next page with offset + limit."
        ),
    )
    # Tier 1: user-controlled ranking. "score" = the multi-factor
    # composite (value + momentum + quality, see screener.SCORE_WEIGHTS).
    sort_by: str = Field(
        default="market_cap",
        description=(
            "Sort key. One of: symbol, market_cap, current_price, "
            "price_change_pct, volume, avg_volume, pe_ratio, eps_ttm, "
            "dividend_yield, beta, rsi_14, sma_50, sma_200, high_52w, "
            "low_52w, ps_ratio, pb_ratio, ev_ebitda, roe_ttm, roa_ttm, "
            "gross_margin_ttm, net_margin_ttm, revenue_growth_ttm, "
            "eps_growth_ttm, atr_14, or 'score' for the composite."
        ),
    )
    sort_desc: bool = Field(
        default=True,
        description="True = descending (largest first), False = ascending.",
    )


class InterpretRequest(BaseModel):
    """Request body for the NL interpreter debug endpoint."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Natural language query to test interpretation on (up to 4000 characters)",
        examples=["oversold healthcare stocks"],
    )


class StockResult(BaseModel):
    """A single stock result from the screener."""

    symbol: str
    company_name: str
    sector: Optional[str] = None
    industry: Optional[str] = None
    current_price: Optional[float] = None
    price_change_pct: Optional[float] = None
    market_cap: Optional[int] = None
    pe_ratio: Optional[float] = None
    rsi_14: Optional[float] = None
    volume: Optional[int] = None
    dividend_yield: Optional[float] = None
    beta: Optional[float] = None
    sentiment: Optional[Dict[str, Any]] = None
    # Tier 1: stored-but-unused fields now surfaced
    avg_volume: Optional[int] = None
    eps_ttm: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    pct_off_52w_high: Optional[float] = None
    # Tier 2: valuation / profitability / growth / extra technicals
    ps_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    ev_ebitda: Optional[float] = None
    roe_ttm: Optional[float] = None
    roa_ttm: Optional[float] = None
    gross_margin_ttm: Optional[float] = None
    net_margin_ttm: Optional[float] = None
    revenue_growth_ttm: Optional[float] = None
    eps_growth_ttm: Optional[float] = None
    macd_12_26: Optional[float] = None
    atr_14: Optional[float] = None
    bollinger_upper: Optional[float] = None
    bollinger_lower: Optional[float] = None
    # Tier 3: composite score + explanation
    score: Optional[float] = None
    score_components: Dict[str, Any] = {}
    why_matched: List[str] = []
    # Tier 3: data freshness
    is_stale: bool = False
    data_age_minutes: Optional[float] = None


class ScreenResponse(BaseModel):
    """Response from a screening query (paginated)."""

    query: str
    interpreted_filters: Dict[str, Any]
    total_matches: int      # TRUE total matching rows (not just this page)
    results: List[StockResult]
    limit: int
    offset: int
    has_more: bool          # True if another page exists (offset + limit)
    next_offset: Optional[int] = None  # convenience: offset of the next page
    # Tier 1: echo the exact filters that were applied + how results ranked
    sort_by: str = "market_cap"
    sort_desc: bool = True
    applied_filters: List[str] = []
    # Tier 3: data freshness / coverage notes
    data_quality: Dict[str, Any] = {}
    # Tier 3 (0-match honesty): why a query returned 0 candidates — a data
    # gap (empty column) or a relaxed-from note — instead of a bare empty
    # list. Mirrors the /backtest message field contract.
    message: Optional[str] = None


class BacktestRequest(BaseModel):
    """Request body for the backtesting endpoint."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Natural language strategy description (up to 4000 characters)",
        examples=["buy oversold tech stocks with rising momentum"],
    )
    top_n: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Number of top stocks to backtest (1-50, default 5). The engine ranks the whole universe against the strategy and backtests the top matches; larger values take longer.",
    )
    # Tier 2 realism overrides — optional, explicit wins over whatever the
    # LLM inferred. Percentages arrive 0-100 and become decimal fractions
    # inside the engine. All default None = "not specified".
    stop_loss_pct: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Stop-loss below entry (e.g. 8 = exit at -8%)",
    )
    take_profit_pct: Optional[float] = Field(
        None,
        ge=0,
        le=1000,
        description="Take-profit above entry (e.g. 20 = exit at +20%)",
    )
    trailing_stop_pct: Optional[float] = Field(
        None,
        ge=0,
        le=100,
        description="Trailing stop from the running high/low (e.g. 15)",
    )
    position_sizing: Optional[str] = Field(
        None,
        description="Position sizing model: all_in | fixed_pct | risk_based",
    )
    position_pct: Optional[float] = Field(
        None,
        gt=0,
        le=1,
        description="Fraction of equity per position when position_sizing=fixed_pct",
    )
    risk_per_trade_pct: Optional[float] = Field(
        None,
        gt=0,
        le=100,
        description="% of equity risked per trade when position_sizing=risk_based",
    )
    allow_short: Optional[bool] = Field(
        None,
        description="Enable short selling (sell first, cover on the buy signal)",
    )
    max_leverage: Optional[float] = Field(
        None,
        ge=1,
        le=10,
        description="Maximum exposure multiple for shorts",
    )
    # Tier 4 product: date range on /backtest. The engine always accepted
    # these — the API never exposed them (10 years fixed was the #1 review
    # flag). Both optional; None defaults to 10y → today.
    start_date: Optional[str] = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Backtest start date (YYYY-MM-DD). Default: 10 years ago.",
    )
    end_date: Optional[str] = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Backtest end date (YYYY-MM-DD). Default: today.",
    )
    # Tier 5: portfolio-level simulation. The engine backtests each symbol
    # independently; these controls aggregate the per-symbol equity curves
    # into ONE shared-pool portfolio (combined curve, drawdown, Sharpe).
    portfolio_allocation: Optional[str] = Field(
        None,
        description=(
            "How the shared capital pool is split across the tested stocks: "
            "equal | risk_based (inverse-volatility weights). Default: equal."
        ),
    )
    rebalance_frequency: Optional[str] = Field(
        None,
        description=(
            "Rebalance schedule for the portfolio: none | monthly | "
            "quarterly | yearly. Default: none (weights drift)."
        ),
    )
    # Tier 5: cash accounting. Opt-in realism — off by default keeps the
    # validated adjusted-price total-return sim byte-identical.
    dividend_reinvest: Optional[bool] = Field(
        None,
        description=(
            "Enable cash-dividend accounting (raw prices, dividends booked "
            "as cash on ex-dates). True = reinvest dividends (DRIP), "
            "False = hold as cash income. None = off (adjusted prices)."
        ),
    )
    borrow_rate_pct: Optional[float] = Field(
        None,
        ge=0,
        le=20,
        description=(
            "Annualized short borrow fee (%%), charged daily on the mark "
            "value of open short positions. Only meaningful with "
            "allow_short=true."
        ),
    )
    max_sector_exposure: Optional[int] = Field(
        None,
        ge=1,
        le=50,
        description=(
            "Diversification knob: max stocks from the SAME sector the "
            "engine may pick (e.g. 2 = at most 2 tech names). The screener "
            "ranks by score, so top-10 can be 10 tech names without this."
        ),
    )
    # Tier 3 compare mode: pin the exact symbol set to backtest instead of
    # screening the universe. The compare UI runs strategy A, then re-runs
    # strategy B on A's symbol set so the two are genuinely side-by-side.
    symbols: Optional[List[str]] = Field(
        None,
        max_length=50,
        description=(
            "Exact symbols to backtest (skips the screener). Compare mode "
            "pins strategy B to strategy A's picks for a true A/B."
        ),
    )


class TradeInfo(BaseModel):
    """A single simulated trade from the backtest."""

    symbol: str
    buy_date: str
    buy_price: float
    sell_date: Optional[str] = None
    sell_price: Optional[float] = None
    shares: int
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    side: str = "long"  # "long" | "short"
    exit_reason: str = "signal"  # signal | stop_loss | take_profit | trailing_stop | end_of_period


class BacktestResultItem(BaseModel):
    """Backtest results for a single symbol."""

    symbol: str
    initial_capital: float
    final_value: float
    total_return_pct: float
    sharpe_ratio: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    win_rate_pct: Optional[float] = None
    total_trades: int
    trades: List[TradeInfo] = []
    # Tier 1 credibility: equity curve for charting, statistical-confidence
    # warnings, and benchmark comparison (SPY) with alpha/beta/excess.
    equity_curve: List[Dict[str, Any]] = []  # [{"date": ..., "value": ...}]
    # Tier 4 chart data: drawdown series (negative decimals, same dates as
    # the equity curve) so the frontend can render the second chart.
    drawdown_curve: List[Dict[str, Any]] = []  # [{"date": ..., "value": ...}]
    warnings: List[str] = []
    benchmark_total_return_pct: Optional[float] = None
    alpha_pct: Optional[float] = None
    beta: Optional[float] = None
    excess_return_pct: Optional[float] = None
    # Tier 2 realism: buy-and-hold breakdown + data-quality audit trail.
    buy_hold_price_return_pct: Optional[float] = None
    buy_hold_total_return_pct: Optional[float] = None
    dividend_contribution_pct: Optional[float] = None
    data_quality: List[str] = []
    # Tier 5 cash accounting (opt-in): cumulative $ received as dividends
    # (negative = paid by short positions) and $ paid as short borrow fees,
    # so the UI can show real income/expense instead of only the embedded
    # total-return number. Zero by default (off-by-default regression safe).
    dividends_received: float = 0.0
    borrow_fees_paid: float = 0.0
    # Tier 3 robustness: Monte Carlo outcome spread, bootstrap CIs for
    # Sharpe/max-DD, walk-forward IS/OOS, and the parameter grid.
    monte_carlo: Optional[Dict[str, Any]] = None
    confidence_intervals: Optional[Dict[str, Any]] = None
    walk_forward: Optional[Dict[str, Any]] = None
    parameter_sensitivity: Optional[Dict[str, Any]] = None
    # Per-symbol data freshness from the screener — mirrors StockResult's
    # DataAgeBadge so the backtest tab can show staleness per symbol.
    data_age_minutes: Optional[float] = None
    is_stale: bool = False


class BacktestResponse(BaseModel):
    """Response from a backtesting run."""

    query: str
    strategy: Dict[str, Any] = {}  # exact rules simulated (auditable)
    results: Dict[str, BacktestResultItem]
    total_symbols_tested: int
    # Human-readable note when the run could not produce a result set
    # (e.g. 0 candidates matched the interpreted filters). Lets the UI
    # explain WHY instead of showing a fake-successful empty run.
    message: Optional[str] = None
    # Tier 5: portfolio-level aggregation (shared pool, allocation,
    # rebalance schedule) — present when ≥2 symbols were backtested.
    # Combined equity/drawdown curves, weights, and portfolio-level
    # return/Sharpe/max-DD so the UI can render the WHOLE book, not N
    # parallel single-stock accounts.
    portfolio: Optional[Dict[str, Any]] = None


class EnrichRequest(BaseModel):
    """Request body for the enrichment endpoint."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Natural language query to screen and enrich (up to 4000 characters)",
    )
    max_stocks: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum stocks to enrich with news/sentiment",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum stocks to screen before enrichment",
    )


class NewsArticleItem(BaseModel):
    """A single news article."""

    headline: str
    source: str
    datetime: str
    sentiment: Optional[str] = None


class SentimentItem(BaseModel):
    """Sentiment and news data for a single stock."""

    symbol: str
    buzz: float
    company_news_score: float
    sector_news_score: float
    articles: List[NewsArticleItem] = []


class EnrichResponse(BaseModel):
    """Response from a screen + enrich operation."""

    query: str
    screening: ScreenResponse
    enrichment: Dict[str, SentimentItem]
    stocks_enriched: int


class ConfigStatus(BaseModel):
    """Configuration status response."""

    finnhub_key_count: int
    screener_llm_configured: bool
    backtest_llm_configured: bool
    database_configured: bool
    warnings: List[str]


class NewsArticleItemExtended(BaseModel):
    """A stored news article with per-article LLM sentiment."""

    symbol: str
    headline: str
    summary: str = ""
    source: str = ""
    url: str = ""
    published_at: Optional[datetime] = None  # DB returns timestamptz
    sentiment: Optional[str] = None
    sentiment_score: Optional[float] = None


class SentimentSummary(BaseModel):
    """Aggregate sentiment + recent articles for one stock."""

    symbol: str
    buzz: float = 0.0
    company_news_score: float = 0.0
    sector_news_score: float = 0.0
    article_count: int = 0
    updated_at: Optional[datetime] = None  # DB returns timestamptz
    articles: List[NewsArticleItemExtended] = []


class NewsFeedResponse(BaseModel):
    """Response from the news feed endpoint."""

    total_articles: int
    returned: int
    articles: List[NewsArticleItemExtended]


class RegisterRequest(BaseModel):
    """Request body for creating a new account."""

    email: str = Field(..., min_length=3, max_length=254, description="Account email")
    password: str = Field(
        ...,
        min_length=settings.password_min_length,
        max_length=128,
        description="Password (min 8 chars)",
    )


class LoginRequest(BaseModel):
    """Request body for logging in."""

    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


class UserOut(BaseModel):
    """Public user info (never includes the password hash)."""

    id: int
    email: str
    plan: str
    email_verified: bool = False
    is_admin: bool = False
    created_at: Optional[datetime] = None  # DB returns timestamptz


class AuthResponse(BaseModel):
    """Response from register/login — the JWT the client stores."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    # Signup-only: whether the verification email was actually queued with
    # SendGrid (False when unconfigured or the API call failed — the frontend
    # shows a "resend" option instead of silently assuming delivery).
    email_sent: bool = False


class PasswordResetRequest(BaseModel):
    """Request body for asking for a password-reset email."""

    email: str = Field(..., min_length=3, max_length=254, description="Account email")


class PasswordResetConfirm(BaseModel):
    """Request body for setting a new password from an emailed reset link."""

    token: str = Field(..., min_length=10, max_length=200, description="Token from the emailed link")
    new_password: str = Field(
        ...,
        min_length=settings.password_min_length,
        max_length=128,
        description="The new password",
    )


class ChangePasswordRequest(BaseModel):
    """Request body for changing the password while signed in."""

    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(
        ...,
        min_length=settings.password_min_length,
        max_length=128,
        description="The new password",
    )


class DeleteAccountRequest(BaseModel):
    """Request body for permanently deleting the signed-in account."""

    password: str = Field(..., min_length=1, max_length=128, description="Current password")


class ShareRequest(BaseModel):
    """Request body for publishing one finished backtest as a share link."""

    job_id: str = Field(..., min_length=3, max_length=64, description="The finished async backtest to share")


class VerifyEmailRequest(BaseModel):
    """Request body for the email verification endpoint.

    The token normally arrives as a query parameter (from the emailed link)
    AND as a JSON body (frontend pages that POST after rendering the link).
    """

    token: str = Field(..., min_length=10, max_length=200, description="One-time verification token from the emailed link")


class AdminUserOut(BaseModel):
    """A user as shown in the admin dashboard (never the password hash)."""

    id: int
    email: str
    plan: str
    email_verified: bool = False
    is_admin: bool = False
    created_at: Optional[datetime] = None


class AdminUsersResponse(BaseModel):
    """Paginated user list for the admin dashboard."""

    total: int
    limit: int
    offset: int
    users: List[AdminUserOut]


class AdminUserUpdate(BaseModel):
    """Mutable fields an admin can change on a user account."""

    plan: Optional[str] = Field(
        None, pattern="^(free|premium)$", description="free | premium"
    )
    is_admin: Optional[bool] = None
    email_verified: Optional[bool] = None


class PersonalDataPayload(BaseModel):
    """A user's saved watchlist / screeners / backtests (synced from the UI)."""

    watchlist: List[str] = []
    saved_screeners: List[Dict[str, Any]] = []
    recent_backtests: List[Dict[str, Any]] = []


class AdminOverviewResponse(BaseModel):
    """Aggregated system state for the admin dashboard landing page."""

    status: str = "ok"
    uptime_seconds: Optional[float] = None
    database: Dict[str, Any] = {}
    stocks: Dict[str, Any] = {}
    users: Dict[str, Any] = {}
    jobs: Dict[str, Any] = {}
    warnings: List[str] = []
    config: Dict[str, Any] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# Startup Event
# ═══════════════════════════════════════════════════════════════════════════════

_start_time: Optional[datetime] = None


@app.on_event("startup")
async def on_startup() -> None:
    """Log startup and validate configuration."""
    global _start_time
    _start_time = datetime.now()

    # Validate config on startup (logs warnings)
    from src.config import validate_config, settings

    warnings = validate_config()
    if warnings:
        for w in warnings:
            logger.warning("⚠️  %s", w)
    else:
        logger.info("✅ All configuration validated")

    # Self-heal the main-DB schema on every boot (idempotent IF NOT EXISTS).
    # The API is the only process that never explicitly ran init_schema(): the
    # CLI setup/backfill paths do, but a Fly/Vercel deploy boots the API
    # directly — so any table added to init_schema (user_data, job_runs, …)
    # silently stayed absent on live DBs and its endpoints 500'd with
    # "relation ... does not exist" (verified live on /me/personal).
    if settings.has_database_url:
        try:
            from src.database import PostgresDB

            PostgresDB().init_schema()
            logger.info("✅ Main-DB schema ensured (stock_master/users/jobs/user_data)")
        except Exception as e:
            logger.warning("Main-DB schema init failed (continuing): %s", e)

    # Mark orphaned async backtest jobs as interrupted (server restarted
    # while a backtest was in-flight — the user sees "Server restarted"
    # instead of "Job not found").
    try:
        orphaned = _fail_orphaned_backtest_jobs()
        if orphaned:
            logger.info("✅ Interrupted %d orphaned backtest job(s)", orphaned)
    except Exception as e:
        logger.debug("Backtest orphan detection skipped: %s", e)

    logger.info(
        "🚀 Stratoscan API v0.1.0 started with %d Finnhub keys, "
        "Screener LLM: %s, Database: %s",
        settings.finnhub_key_count,
        "✅" if settings.screener_llm_key else "❌",
        "✅" if settings.has_database_url else "❌",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _interpreted_to_dict(interpreted: Any) -> Dict[str, Any]:
    """Convert an InterpretedQuery object to a serializable dict.

    Args:
        interpreted: The InterpretedQuery from the interpreter module.

    Returns:
        A plain dict representation of the filters.
    """
    result: Dict[str, Any] = {}
    if interpreted.sector:
        result["sector"] = interpreted.sector
    if interpreted.industry:
        result["industry"] = interpreted.industry
    if interpreted.momentum:
        result["momentum"] = interpreted.momentum

    for f in interpreted.filters:
        result[f.field] = {
            "operator": f.operator.value,
            "value": f.value,
        }

    if interpreted.error:
        # LLM failure: make it visible in the response instead of silently
        # returning the whole stock universe as if nothing was wrong.
        result["llm_error"] = interpreted.error

    if not result and not interpreted.error:
        return {"note": "No specific filters extracted"}
    return result


def _result_to_stock(r: Any) -> StockResult:
    """Convert a ScreenerResult to a StockResult Pydantic model.

    Args:
        r: A ScreenerResult object from the screener module.

    Returns:
        A StockResult Pydantic model.
    """
    return StockResult(
        symbol=r.symbol,
        company_name=r.company_name,
        sector=r.sector,
        industry=r.industry,
        current_price=r.current_price,
        price_change_pct=r.price_change_pct,
        market_cap=r.market_cap,
        pe_ratio=r.pe_ratio,
        rsi_14=r.rsi_14,
        volume=r.volume,
        dividend_yield=r.dividend_yield,
        beta=r.beta,
        avg_volume=r.avg_volume,
        eps_ttm=r.eps_ttm,
        sma_50=r.sma_50,
        sma_200=r.sma_200,
        high_52w=r.high_52w,
        low_52w=r.low_52w,
        pct_off_52w_high=r.pct_off_52w_high,
        ps_ratio=r.ps_ratio,
        pb_ratio=r.pb_ratio,
        ev_ebitda=r.ev_ebitda,
        roe_ttm=r.roe_ttm,
        roa_ttm=r.roa_ttm,
        gross_margin_ttm=r.gross_margin_ttm,
        net_margin_ttm=r.net_margin_ttm,
        revenue_growth_ttm=r.revenue_growth_ttm,
        eps_growth_ttm=r.eps_growth_ttm,
        macd_12_26=r.macd_12_26,
        atr_14=r.atr_14,
        bollinger_upper=r.bollinger_upper,
        bollinger_lower=r.bollinger_lower,
        score=r.score,
        score_components=r.score_components or {},
        why_matched=r.why_matched or [],
        is_stale=r.is_stale,
        data_age_minutes=r.data_age_minutes,
    )


def _build_screen_response(
    query: str,
    interpreted: Any,
    report: Any,
    limit: int,
    results: List[StockResult],
) -> ScreenResponse:
    """Build the ScreenResponse for /screen and /enrich from ONE code path.

    Both endpoints embed a ScreenResponse, and the two must stay
    field-for-field identical forever. /enrich once silently dropped
    data_quality / applied_filters / sort_by / sort_desc — an LLM
    interpretation failure then looked like a bare "0 matches" and
    success responses lost the auditable filter echo. One construction
    = one contract: the only way these can diverge again is if the
    helper itself changes, which both callers then inherit.

    The pagination stride (offset + limit when more exist) lives HERE,
    not in the callers — /screen and /enrich used to each recompute
    next_offset, and a copy-pasted block diverging is exactly the bug
    class this helper exists to prevent. Same rule the codebase already
    follows for /backtest + /backtest/export (_run_backtest_pipeline).
    """
    # Offset pagination strides by `limit` at the DB level — even if a
    # post-query filter (momentum) shrank the visible page, the next DB
    # page starts at offset + limit, so next_offset mirrors that stride.
    next_offset = report.offset + limit if report.has_more else None
    return ScreenResponse(
        query=query,
        interpreted_filters=_interpreted_to_dict(interpreted),
        total_matches=report.total_matches,
        results=results,
        limit=limit,
        offset=report.offset,
        has_more=report.has_more,
        next_offset=next_offset,
        sort_by=report.sort_by,
        sort_desc=report.sort_desc,
        applied_filters=report.applied_filters,
        data_quality=report.data_quality,
        # 0-match honesty: surface WHY instead of a bare empty list.
        message=report.data_quality.get("message"),
    )


# ── Analytics wiring (src/analytics.py) ────────────────────────────────────
# One thin helper per shape of event, so every call site stays a single line and
# each prop name is written down exactly once. All of them are fire-and-forget:
# ``track()`` swallows its own failures by contract (analytics must never break
# a screen or a signup), so none of these can raise into a handler.

def _user_id_of(user: Optional[Dict[str, Any]]) -> Optional[int]:
    """The id from a ``get_current_user`` dict, or None when absent/malformed."""
    if not user:
        return None
    try:
        return int(user.get("id"))
    except (TypeError, ValueError):
        return None


def _track_interpret(interpreted: Any) -> None:
    """Record WHICH interpreter path served a query — the cost split.

    Nothing is recorded when ``source`` is empty: that means the interpreter
    never produced an interpretation (an empty query) or failed outright
    (``error`` is set), and the request is aborting rather than screening.

    No props: the deterministic path's confidence score lives only inside
    ``src/deterministic_interpreter`` and is deliberately not threaded through
    ``InterpretedQuery`` (the interpreter carries exactly one new field, its
    ``source``). ``interpret_deterministic`` therefore accepts a
    ``confidence`` key it does not currently receive.
    """
    source = getattr(interpreted, "source", "")
    if source not in ("deterministic", "llm"):
        return
    from src.analytics import track

    track("interpret_deterministic" if source == "deterministic" else "interpret_llm")


def _track_screen_returned(response: Any) -> None:
    """Record a screen the user was served — the two counters, nothing else.

    Fires for a cache hit as well as a fresh run: the funnel counts screens
    people SAW, and a repeat query inside the 60s cache TTL is still a screen.
    Deliberately not the query text — that is user content.
    """
    from src.analytics import track

    track(
        "screen_returned",
        props={
            "result_count": len(getattr(response, "results", None) or []),
            "total_matches": int(getattr(response, "total_matches", 0) or 0),
        },
    )


def _backtest_symbol_count(payload: Any) -> int:
    """How many symbols a run will test: explicit tickers, else ``top_n``."""
    symbols = getattr(payload, "symbols", None)
    if symbols:
        return len(symbols)
    return int(getattr(payload, "top_n", 0) or 0)


def _backtest_failure_reason(exc: Exception) -> str:
    """A short FIXED reason code for a failed backtest — never exception text.

    Exception messages can carry user content, credentials or provider
    payloads, so the funnel records one of a closed set of codes instead:
    ``http_<status>`` for a deliberate HTTP failure, ``engine`` for anything
    else. The real detail goes to the log (and to ``error_log``, scrubbed).
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return f"http_{status}"
    return "engine"


def _run_screen(query: str, limit: int, offset: int = 0,
                sort_by: str = "market_cap", sort_desc: bool = True) -> tuple:
    """Run the full screening pipeline synchronously.

    Returns (interpreted, report) tuple. Helper to avoid duplicating
    the logic across /screen and /enrich endpoints.

    Args:
        query: Natural language screening query.
        limit: Page size.
        offset: Page start (rows to skip).
        sort_by: Sort key (see screener.SORTABLE_COLUMNS).
        sort_desc: True = descending.
    """
    from src.interpreter import interpret_query
    from src.screener import StockScreener

    interpreted = interpret_query(query)
    _track_interpret(interpreted)
    screener = StockScreener()
    report = screener.screen(
        interpreted,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_desc=sort_desc,
    )
    return interpreted, report


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    tags=["System"],
)

async def health_check(request: Request, response: Response):
    """Check the API server status and configuration health.

    Actively probes the database with ``SELECT 1`` and reports latency.
    Returns ``database_alive`` and ``database_latency_ms`` for real
    insight into database health — not just whether it's configured.
    """
    from src.config import settings
    from src.database import PostgresDB

    uptime = None
    if _start_time:
        uptime = (datetime.now() - _start_time).total_seconds()

    # Probe the database
    db_alive: Optional[bool] = None
    db_latency: Optional[float] = None
    if settings.has_database_url:
        try:
            result = PostgresDB().health_check()
            db_alive = result["alive"]
            db_latency = result["latency_ms"]
        except Exception:
            db_alive = False
            db_latency = None

    return HealthResponse(
        status="ok" if (db_alive is None or db_alive) else "degraded",
        version="0.1.0",
        finnhub_keys_configured=settings.finnhub_key_count,
        screener_llm_configured=bool(settings.screener_llm_key),
        backtest_llm_configured=bool(settings.backtest_llm_key),
        database_configured=settings.has_database_url,
        database_alive=db_alive,
        database_latency_ms=db_latency,
        uptime_seconds=uptime,
    )


@app.get(
    "/config",
    response_model=ConfigStatus,
    summary="Configuration status",
    tags=["System"],
)

async def config_status(request: Request, response: Response):
    """Show which services are configured and any configuration warnings.

    Useful for debugging setup issues.
    """
    from src.config import settings, validate_config

    warnings = validate_config()
    return ConfigStatus(
        finnhub_key_count=settings.finnhub_key_count,
        screener_llm_configured=bool(settings.screener_llm_key),
        backtest_llm_configured=bool(settings.backtest_llm_key),
        database_configured=settings.has_database_url,
        warnings=warnings,
    )


@app.get(
    "/metrics",
    summary="Prometheus metrics (text format)",
    tags=["System"],
    include_in_schema=True,
)
async def prometheus_metrics(request: Request, response: Response):
    """Expose Prometheus-format metrics for scraping.

    Stdlib-only exporter (src/metrics.py): request counters + latency
    histogram by method/route/status, process uptime, and live
    background-job health gauges (staleness + scheduler liveness,
    recomputed at most every 5 min so a scrape never wakes a suspended
    Neon DB).

    Returns:
        ``text/plain; version=0.0.4`` Prometheus exposition.
    """
    return Response(
        content=render_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Frontend Discovery / Meta
# ═══════════════════════════════════════════════════════════════════════════════
#
# Endpoints that tell a frontend WHAT this API can do WITHOUT hardcoding:
#   - GET /               → service identity (name, version, docs links)
#   - GET /meta           → full capability manifest: endpoint catalog,
#                           sortable columns, filter fields + operators,
#                           example queries, rate-limit budgets, pagination
#                           contract and auth requirements. The frontend can
#                           render dynamic filter/sort UI straight from this.
#   - GET /meta/sectors   → distinct sectors + live stock counts (dropdowns)
#   - GET /meta/industries→ distinct industries + live stock counts
# The manifest is derived from the ACTUAL registered routes and the same
# constants the interpreter/screener use, so it can never drift from the
# real API surface.


class EndpointMeta(BaseModel):
    """One entry of the endpoint catalog."""

    path: str
    methods: List[str]
    summary: str = ""
    tags: List[str] = []
    auth: str = "none"  # "none" | "bearer" | "trial" | "admin"


class FilterFieldMeta(BaseModel):
    """A filter the NL interpreter understands (for filter-builder UIs)."""

    field: str                      # DB column (what /screen filters on)
    operators: List[str]            # lt | lte | gt | gte | eq | neq | in
    type: str = "number"            # number | string | boolean
    unit: str = ""                  # "$B", "%", "USD", ... (frontend display)
    label: str = ""                 # human label for filter UI
    query_key: str = ""             # the interpreter's raw key (e.g. max_pe_ratio)


class PaginationMeta(BaseModel):
    """Pagination contract shared by every list endpoint."""

    default_limit: int
    max_limit: int
    max_offset: int
    fields: Dict[str, str] = {}     # offset|limit|total|has_more|next_offset


class MetaResponse(BaseModel):
    """The capability manifest returned by GET /meta."""

    name: str
    version: str
    docs: Dict[str, str]
    endpoints: List[EndpointMeta]
    sortable_columns: List[str]
    filter_fields: List[FilterFieldMeta]
    example_queries: List[str]
    rate_limits: Dict[str, Dict[str, int]]
    pagination: PaginationMeta
    auth: Dict[str, Any]


# Filter-field metadata for the /meta manifest. SINGLE SOURCE OF TRUTH for
# the frontend's filter builder; kept in sync with the interpreter's
# field_mapping + system prompt (any field there that users can query shows
# up here with its operators). Field = DB column; query_key = the raw key
# the LLM emits; unit/label are display hints for the frontend.
FILTER_FIELDS_META: List[FilterFieldMeta] = [
    FilterFieldMeta(field="sector", operators=["eq", "in"], type="string", unit="", label="Sector", query_key="sector"),
    FilterFieldMeta(field="industry", operators=["eq", "in"], type="string", unit="", label="Industry", query_key="industry"),
    FilterFieldMeta(field="market_cap", operators=["gte", "lte"], type="number", unit="$B", label="Market cap", query_key="min_market_cap_billions/max_market_cap_billions"),
    FilterFieldMeta(field="pe_ratio", operators=["gte", "lte"], type="number", unit="x", label="P/E ratio", query_key="min_pe_ratio/max_pe_ratio"),
    FilterFieldMeta(field="rsi_14", operators=["gte", "lte"], type="number", unit="", label="RSI (14)", query_key="min_rsi/max_rsi"),
    FilterFieldMeta(field="current_price", operators=["gte", "lte"], type="number", unit="USD", label="Price", query_key="min_price/max_price"),
    FilterFieldMeta(field="volume", operators=["gte"], type="number", unit="shares", label="Volume", query_key="min_volume"),
    FilterFieldMeta(field="avg_volume", operators=["gte", "lte"], type="number", unit="shares", label="Avg volume", query_key="min_avg_volume/max_avg_volume"),
    FilterFieldMeta(field="eps_ttm", operators=["gte"], type="number", unit="USD", label="EPS (TTM)", query_key="min_eps_ttm"),
    FilterFieldMeta(field="dividend_yield", operators=["gte", "lte"], type="number", unit="%", label="Dividend yield", query_key="min_dividend_yield/max_dividend_yield"),
    FilterFieldMeta(field="beta", operators=["gte", "lte"], type="number", unit="", label="Beta", query_key="min_beta/max_beta"),
    FilterFieldMeta(field="price_change_pct", operators=["gte", "lte"], type="number", unit="%", label="Daily change", query_key="min_price_change_pct/max_price_change_pct"),
    FilterFieldMeta(field="ps_ratio", operators=["gte", "lte"], type="number", unit="x", label="P/S ratio", query_key="min_ps_ratio/max_ps_ratio"),
    FilterFieldMeta(field="pb_ratio", operators=["gte", "lte"], type="number", unit="x", label="P/B ratio", query_key="min_pb_ratio/max_pb_ratio"),
    FilterFieldMeta(field="ev_ebitda", operators=["lte"], type="number", unit="x", label="EV/EBITDA", query_key="max_ev_ebitda"),
    FilterFieldMeta(field="roe_ttm", operators=["gte"], type="number", unit="%", label="ROE (TTM)", query_key="min_roe_ttm"),
    FilterFieldMeta(field="roa_ttm", operators=["gte"], type="number", unit="%", label="ROA (TTM)", query_key="min_roa_ttm"),
    FilterFieldMeta(field="gross_margin_ttm", operators=["gte"], type="number", unit="%", label="Gross margin", query_key="min_gross_margin_ttm"),
    FilterFieldMeta(field="net_margin_ttm", operators=["gte"], type="number", unit="%", label="Net margin", query_key="min_net_margin_ttm"),
    FilterFieldMeta(field="revenue_growth_ttm", operators=["gte"], type="number", unit="%", label="Revenue growth", query_key="min_revenue_growth_ttm"),
    FilterFieldMeta(field="eps_growth_ttm", operators=["gte"], type="number", unit="%", label="EPS growth", query_key="min_eps_growth_ttm"),
    FilterFieldMeta(field="atr_14", operators=["lte"], type="number", unit="%", label="ATR (14)", query_key="max_atr_14"),
    # Momentum + derived boolean criteria (the interpreter's DERIVED_CRITERIA_KEYS
    # + momentum string). These map to column-to-column / regime expressions in
    # the screener — a frontend filter-builder must know they exist to offer
    # them, so the manifest mirrors what the interpreter can genuinely emit.
    FilterFieldMeta(field="momentum", operators=["eq"], type="string", unit="", label="Momentum", query_key="momentum"),
    FilterFieldMeta(field="above_sma_50", operators=["eq"], type="boolean", unit="", label="Above 50-day SMA", query_key="above_sma_50"),
    FilterFieldMeta(field="below_sma_50", operators=["eq"], type="boolean", unit="", label="Below 50-day SMA", query_key="below_sma_50"),
    FilterFieldMeta(field="above_sma_200", operators=["eq"], type="boolean", unit="", label="Above 200-day SMA", query_key="above_sma_200"),
    FilterFieldMeta(field="below_sma_200", operators=["eq"], type="boolean", unit="", label="Below 200-day SMA", query_key="below_sma_200"),
    FilterFieldMeta(field="golden_cross", operators=["eq"], type="boolean", unit="", label="Golden cross (50>200 SMA)", query_key="golden_cross"),
    FilterFieldMeta(field="death_cross", operators=["eq"], type="boolean", unit="", label="Death cross (50<200 SMA)", query_key="death_cross"),
    FilterFieldMeta(field="near_52w_high", operators=["eq"], type="boolean", unit="", label="Near 52-week high", query_key="near_52w_high"),
    FilterFieldMeta(field="near_52w_low", operators=["eq"], type="boolean", unit="", label="Near 52-week low", query_key="near_52w_low"),
    FilterFieldMeta(field="macd_bullish", operators=["eq"], type="boolean", unit="", label="MACD bullish", query_key="macd_bullish"),
    FilterFieldMeta(field="bollinger_breakout", operators=["eq"], type="boolean", unit="", label="Bollinger breakout", query_key="bollinger_breakout"),
    FilterFieldMeta(field="bollinger_oversold", operators=["eq"], type="boolean", unit="", label="Bollinger oversold", query_key="bollinger_oversold"),
]

# Natural-language example queries for the frontend's "try these" presets.
EXAMPLE_QUERIES = [
    "undervalued small-cap tech stocks with rising momentum",
    "large cap dividend stocks with low beta",
    "oversold healthcare stocks with high volume",
    "banks with high profit margins and stable prices",
    "stocks near their 52-week highs with strong momentum",
    "profitable energy stocks with low debt",
]


def _endpoint_auth(path: str, methods: List[str]) -> str:
    """Classify an endpoint's auth requirement for the /meta catalog.

    Uses the same conventions the routes themselves use:
      - /admin/* and /ops/* are admin-gated (require_admin)
      - /refresh is admin-gated
      - /backtest requires a bearer token (get_current_user)
      - /auth/me, /auth/resend-verification and /auth/logout-all require a token
      - /try/claim needs BOTH a bearer token and the trial token ("bearer")
      - other /try/* and /results/* take an X-Anon-Token trial token (or, for
        /results/*, a bearer token instead) — "trial"
      - everything else is public (rate-limited only)
    """
    if path.startswith(("/admin", "/ops", "/refresh")):
        return "admin"
    if path == "/try/claim":
        return "bearer"
    if path.startswith(("/try/backtest", "/results/")):
        return "trial"
    if path.startswith("/backtest"):
        return "bearer"
    if path in (
        "/auth/me", "/auth/resend-verification", "/auth/logout-all",
        "/auth/change-password", "/auth/delete-account", "/auth/refresh",
    ) or path.startswith("/me/"):
        return "bearer"
    return "none"


def _endpoint_catalog() -> List[EndpointMeta]:
    """Derive the endpoint catalog from the ACTUAL registered routes.

    Built from ``app.routes`` so it always matches reality: a route added
    without touching /meta shows up automatically, and a deleted route
    disappears. Skips the docs/redoc/openapi and static admin dashboard
    (served as HTML, not a JSON contract endpoint).
    """
    catalog: List[EndpointMeta] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None)
        if not path or not methods or path.startswith(("/docs", "/redoc", "/openapi")):
            continue
        # Only the exact /admin HTML dashboard is excluded from the JSON
        # contract catalog — the /admin/* JSON API routes stay visible so a
        # frontend can discover them (auth classified as "admin").
        if path == "/admin" or path == "/metrics":
            continue
        http_methods = sorted(m for m in methods if m in {"GET", "POST", "PUT", "PATCH", "DELETE"})
        catalog.append(EndpointMeta(
            path=path,
            methods=http_methods,
            summary=getattr(route, "summary", "") or "",
            tags=list(getattr(route, "tags", []) or []),
            auth=_endpoint_auth(path, http_methods),
        ))
    return catalog


@app.get("/", response_model=Dict[str, Any], summary="API identity", tags=["System"])
async def api_root(request: Request, response: Response):
    """Service identity: name, version, and where the docs live.

    A frontend can hit this once at boot to confirm the API is reachable
    and to link /docs and /meta from its own UI.
    """
    return {
        "name": app.title,
        "version": app.version,
        "description": app.description or "",
        # Docs links only when docs are actually enabled (dev). In
        # production docs_url/redoc_url are None, so advertise nothing.
        "docs": (
            {"/docs": "Swagger UI", "/redoc": "ReDoc"}
            if not settings.is_production
            else {}
        ),
        "meta": "/meta",
        "endpoint_count": len(_endpoint_catalog()),
    }


@app.get(
    "/meta",
    response_model=MetaResponse,
    summary="Capability manifest for frontends",
    tags=["System"],
)
async def api_meta(request: Request, response: Response):
    """Return the full capability manifest a frontend needs to render itself.

    Includes the endpoint catalog (path, methods, auth), the sortable
    columns, every NL filter field with its operators and display metadata,
    example queries, rate-limit budgets, the shared pagination contract,
    and auth feature flags (JWT TTL, password minimum, verification state).

    Everything is derived from the live app / shared constants, so the
    frontend never hardcodes a route list that can drift.
    """
    from src.screener import SORTABLE_COLUMNS

    return MetaResponse(
        name=app.title,
        version=app.version,
        docs=(
            {
                "swagger": "/docs",
                "redoc": "/redoc",
                "openapi": app.openapi_url or "/openapi.json",
            }
            if not settings.is_production
            else {}
        ),
        endpoints=_endpoint_catalog(),
        sortable_columns=sorted(SORTABLE_COLUMNS),
        filter_fields=FILTER_FIELDS_META,
        example_queries=EXAMPLE_QUERIES,
        rate_limits=RATE_LIMITS,
        pagination=PaginationMeta(
            default_limit=50,
            max_limit=500,
            max_offset=1_000_000,
            fields={
                "offset": "Number of rows to skip (page start)",
                "limit": "Page size (default 50, max 500)",
                "total_matches": "TRUE total matching rows (not just this page)",
                "has_more": "True if another page exists",
                "next_offset": "Offset of the next page, or null",
            },
        ),
        auth={
            "jwt_expire_hours": settings.jwt_expire_hours,
            "password_min_length": settings.password_min_length,
            "email_verification_required": settings.email_verification_required,
            "email_verification_enabled": settings.has_email_verification,
        },
    )


class SectorCountItem(BaseModel):
    """One sector/industry value with its live stock count."""

    name: str
    count: int


class SectorCountsResponse(BaseModel):
    """Distinct sectors/industries + counts (dropdown reference data)."""

    total: int
    values: List[SectorCountItem]


@app.get(
    "/meta/sectors",
    response_model=SectorCountsResponse,
    summary="Distinct sectors with live stock counts",
    tags=["Data"],
)
async def meta_sectors(request: Request, response: Response):
    """List every distinct sector in stock_master with its stock count.

    Frontend dropdown reference data — the ``values`` array is the exact
    set of sector values the screener can filter on (including the derived
    GICS-style sectors from the profile enrichment).
    """
    return _meta_distinct("sector")


@app.get(
    "/meta/industries",
    response_model=SectorCountsResponse,
    summary="Distinct industries with live stock counts",
    tags=["Data"],
)
async def meta_industries(request: Request, response: Response):
    """List every distinct industry in stock_master with its stock count.

    Frontend dropdown reference data for the industry filter.
    """
    return _meta_distinct("industry")


def _meta_distinct(column: str) -> SectorCountsResponse:
    """Shared distinct-values + counts query for /meta/sectors + /meta/industries.

    Args:
        column: ``sector`` or ``industry`` (both whitelisted DB columns).

    Returns:
        A SectorCountsResponse with values sorted by count descending.

    Raises:
        ValueError: If the column is not a known stock_master column — the
            name is interpolated into SQL, so it MUST resolve against the
            whitelist first (same invariant every other interpolator in this
            file follows: query_stocks sort_by, upsert_stock, aggregate_stats).
    """
    from src.database import STOCK_MASTER_COLUMNS

    if column not in STOCK_MASTER_COLUMNS:
        raise ValueError(f"unknown stock_master column: {column}")
    from src.database import PostgresDB

    if not settings.has_database_url:
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL is not configured. Set DATABASE_URL in your .env file.",
        )

    try:
        db = PostgresDB()
        with db._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {column} AS name, COUNT(*) AS count FROM stock_master "
                    f"WHERE {column} IS NOT NULL GROUP BY {column} "
                    "ORDER BY count DESC"
                )
                rows = cur.fetchall()
    except Exception as e:
        logger.error("Failed to list distinct %ss: %s", column, e)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to query {column}s. Please try again later.",
        )

    values = [SectorCountItem(name=str(r[0]), count=int(r[1])) for r in rows]
    return SectorCountsResponse(total=len(values), values=values)


def _bootstrap_admin_role(
    db, email: str, user_id: int, current_is_admin: bool, email_verified: bool
) -> bool:
    """Grant the admin role when ``email`` is listed in ADMIN_EMAILS AND verified.

    Idempotent and non-fatal: a DB hiccup never blocks register/login — the
    role is simply granted on the next sign-in or via PATCH /admin/users.

    **Verification is the whole security of this function.** ADMIN_EMAILS
    names an address, and an address is only an identity once someone has
    proved they can read its inbox. Without that check, anyone who registers
    the listed address first — before the operator does, e.g. on a fresh
    deploy — was handed an admin token in the register response, without
    ever seeing the inbox. The operator's path is unchanged in spirit:
    register, click the verification link, log in (or, with no mail
    transport configured, accounts start verified and the grant is
    immediate, as before). A completed password reset also verifies, so the
    real owner can always reclaim a squatted address.
    """
    if email not in settings.admin_email_list or current_is_admin:
        return current_is_admin
    if not email_verified:
        logger.warning(
            "ADMIN_EMAILS match for %r refused: address not verified", email
        )
        return current_is_admin
    try:
        db.set_user_admin(user_id, True)
        logger.info("Admin role granted to %s via ADMIN_EMAILS bootstrap", email)
        return True
    except Exception as e:
        # Non-fatal: a DB hiccup must not block a normal sign-in; the admin
        # can retry or be promoted manually.
        logger.warning("Admin bootstrap failed for %s: %s", email, e)
        return current_is_admin


@app.post(
    "/auth/register",
    response_model=AuthResponse,
    summary="Create an account (free tier)",
    tags=["Auth"],
)

async def register(request: Request, response: Response, payload: RegisterRequest):
    """Register a new account.

    Everything is free — registration exists for per-user rate limits
    and account features, not a paywall. Returns a JWT the client
    stores and sends as ``Authorization: Bearer <token>``.

    Email verification: every new account starts ``email_verified=False``
    and gets a verification email via SendGrid (``src/emails.py``). The
    response carries ``email_sent`` so the frontend can show "check your
    inbox" or a resend button. A SendGrid failure NEVER blocks the signup
    — the account exists and can log in; verification is a gradual-enable
    gate, not a hard precondition.
    """
    from src.auth import create_access_token, hash_password
    from src.database import PostgresDB
    from src.emails import generate_verification_token, send_verification_email

    if not settings.has_jwt_secret:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured. Set JWT_SECRET in .env.",
        )

    email = payload.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Please provide a valid email address.")

    # Email verification is optional (unconfigured SendGrid must not break
    # signup). Only generate + store a token when the pipeline is wired.
    verification_token = None
    verification_expires = None
    email_sent = False
    if settings.has_email_verification:
        verification_token = generate_verification_token()
        # UTC-aware expiry: the column is TIMESTAMPTZ and psycopg2
        # round-trips it as tz-aware. A naive local ``datetime.now()``
        # would drift by the app-server-vs-DB offset and expire links
        # early (or late) — always build the deadline in UTC.
        verification_expires = (
            datetime.now(timezone.utc)
            + timedelta(hours=settings.email_verification_token_ttl_hours)
        ).isoformat()

    try:
        db = PostgresDB()
        user_id = db.create_user(
            email,
            hash_password(payload.password),
            plan="free",
            email_verified=not settings.has_email_verification,
            verification_token=verification_token,
            verification_token_expires=verification_expires,
        )
    except Exception as e:
        if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists. Log in instead.",
            )
        logger.error("Registration failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Registration failed. Please try again later.",
        )

    if verification_token:
        email_sent = send_verification_email(email, verification_token)

    # Admin bootstrap (same as login): an email listed in ADMIN_EMAILS is
    # granted the role at signup too, so the first admin sees is_admin=True
    # in the very response that creates the session — no re-login needed.
    is_admin = _bootstrap_admin_role(
        db, email, user_id, False, not settings.has_email_verification
    )

    from src.analytics import track

    track("signup_completed", user_id=user_id, props={"method": "password"})

    # A freshly created row is token_version 0 (the column default).
    token = create_access_token(user_id, email, 0)
    # The user row was just created with plan='free'; the only extra write is
    # the (rare) ADMIN_EMAILS bootstrap UPDATE above when the email is listed.
    logger.info(
        "New account registered: %s (id=%d, plan=free, verified=%s, email_sent=%s, is_admin=%s)",
        email, user_id, not settings.has_email_verification, email_sent, is_admin,
    )
    return AuthResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expire_hours * 3600,
        email_sent=email_sent,
        user=UserOut(
            id=user_id,
            email=email,
            plan="free",
            email_verified=not settings.has_email_verification,
            is_admin=is_admin,
        ),
    )


@app.post(
    "/auth/login",
    response_model=AuthResponse,
    summary="Log in and get a JWT",
    tags=["Auth"],
)

async def login(request: Request, response: Response, payload: LoginRequest):
    """Log in with email + password and receive a JWT.

    Uses a single generic error message for unknown email vs. wrong
    password (standard practice — don't reveal which one failed).
    """
    from src.auth import create_access_token, verify_password, dummy_password_hash
    from src.database import PostgresDB

    if not settings.has_jwt_secret:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured. Set JWT_SECRET in .env.",
        )

    email = payload.email.strip().lower()
    db = PostgresDB()
    user = None
    try:
        user = db.get_user_by_email(email)
    except Exception as e:
        logger.error("Login lookup failed: %s", e)

    # VULN-001 fix (timing equalization): the old ``if not user or not
    # verify_password(...)`` short-circuited for unknown emails, skipping the
    # ~400ms PBKDF2 — so unknown-email responses returned in ~3ms vs ~400ms
    # for a known email, a timing oracle (measured 125x). Always burn the
    # hash cost against a fixed dummy hash, even when the user doesn't exist.
    if user is None:
        verify_password(payload.password, dummy_password_hash())
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
        )
    if not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password.",
        )

    # Admin bootstrap: any email listed in ADMIN_EMAILS is granted the admin
    # role on login (idempotent — no-op when already admin). This is the
    # documented way to create the first admin: add your email to .env, log
    # in, open /admin. Subsequent admins are managed via PATCH /admin/users.
    # The same shared helper /auth/register uses, so a listed email is granted
    # the role at signup too and the response reports the real is_admin.
    user["is_admin"] = _bootstrap_admin_role(
        db, user["email"], user["id"], bool(user.get("is_admin")),
        bool(user.get("email_verified")),
    )

    # Email verification gate (opt-in via EMAIL_VERIFICATION_REQUIRED): when
    # enabled, an unverified account can't log in — it gets a 403 pointing
    # at /auth/resend-verification instead. Off by default so existing
    # accounts and dev flows keep working.
    #
    # There is deliberately NO exemption for ADMIN_EMAILS addresses. One used
    # to exist to avoid a chicken-and-egg (the role was granted at login), but
    # it let anyone who registered the operator's address log in unverified —
    # and, with the grant not checking verification either, become admin. The
    # grant now requires a verified address, so the exemption bought nothing
    # but the hole.
    email_verified = bool(user.get("email_verified"))
    if settings.email_verification_required and not email_verified:
        raise HTTPException(
            status_code=403,
            detail=(
                "Please verify your email address before logging in. "
                "Check your inbox for the verification link, or request a "
                "new one via /auth/resend-verification."
            ),
        )

    # Stamp the row's CURRENT version, not 0: after a /auth/logout-all bump
    # the account's version is >0, and a token minted at 0 would be rejected
    # by get_current_user on its very first authenticated request.
    token = create_access_token(
        user["id"], user["email"], user.get("token_version", 0)
    )
    logger.info(
        "Login: %s (id=%d, plan=%s, verified=%s)",
        email, user["id"], user.get("plan"), email_verified,
    )
    return AuthResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.jwt_expire_hours * 3600,
        user=UserOut(
            id=user["id"],
            email=user["email"],
            plan=user.get("plan", "free"),
            email_verified=email_verified,
            is_admin=bool(user.get("is_admin")),
            created_at=user.get("created_at"),
        ),
    )


@app.get(
    "/auth/me",
    response_model=UserOut,
    summary="Get the current authenticated user",
    tags=["Auth"],
)

async def me(
    request: Request,
    response: Response,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Return the profile of the authenticated user (requires JWT)."""
    return UserOut(
        id=user["id"],
        email=user["email"],
        plan=user.get("plan", "free"),
        email_verified=bool(user.get("email_verified")),
        is_admin=bool(user.get("is_admin")),
        created_at=user.get("created_at"),
    )


@app.get(
    "/me/personal",
    response_model=PersonalDataPayload,
    summary="Get the current user's saved watchlist/screeners/backtests",
    tags=["Personal"],
)
async def get_personal_data(
    request: Request,
    response: Response,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Return the authenticated user's saved personal data (requires JWT).

    The frontend hydrates its watchlist / saved screeners / recent
    backtests from here on login, so a user's saved state follows them
    across devices instead of living only in one browser's localStorage.
    A user who has never saved anything gets empty arrays (no 404).
    """
    from src.database import PostgresDB

    db = PostgresDB()
    data = db.get_user_data(user["id"])
    if not data:
        return PersonalDataPayload()
    return PersonalDataPayload(
        watchlist=data.get("watchlist") or [],
        saved_screeners=data.get("saved_screeners") or [],
        recent_backtests=data.get("recent_backtests") or [],
    )


@app.put(
    "/me/personal",
    response_model=PersonalDataPayload,
    summary="Replace the current user's saved watchlist/screeners/backtests",
    tags=["Personal"],
)
async def put_personal_data(
    request: Request,
    response: Response,
    payload: PersonalDataPayload,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Replace the authenticated user's personal data (requires JWT).

    The frontend pushes its whole personal state whenever it changes
    (watchlist toggle, screener save, backtest run). Replace-not-merge
    keeps the API dumb and the client authoritative — exactly what a
    single-device-per-user SPA needs, with no conflict-resolution code.
    """
    from src.database import PostgresDB

    # Normalize symbols to uppercase BEFORE storing AND echo the normalized
    # payload back — the client should never see lowercase symbols it didn't
    # send (the test asserts the response round-trips normalized).
    normalized = PersonalDataPayload(
        watchlist=[s.strip().upper() for s in payload.watchlist if s.strip()],
        saved_screeners=payload.saved_screeners,
        recent_backtests=payload.recent_backtests,
    )
    db = PostgresDB()
    db.upsert_user_data(
        user["id"],
        watchlist=normalized.watchlist,
        saved_screeners=normalized.saved_screeners,
        recent_backtests=normalized.recent_backtests,
    )
    return normalized


@app.get(
    "/auth/verify-email",
    response_model=Dict[str, Any],
    summary="Verify an email address via the emailed link",
    tags=["Auth"],
)

async def verify_email(
    request: Request,
    response: Response,
    token: str = Query(..., min_length=10, max_length=200, description="One-time token from the emailed verification link"),
):
    """Mark a user's email verified (clicked the emailed link).

    Looks up the user by the one-time token, checks the token is NOT
    expired (expiry stored with the token), then flips ``email_verified``
    to True and consumes (nulls) the token so it can't be replayed.

    The link is ``{APP_BASE_URL}/verify-email?token=...`` — the frontend
    renders a success/expired page and calls this endpoint (GET or POST,
    see ``/auth/verify-email-body``).
    """
    from src.database import PostgresDB

    try:
        db = PostgresDB()
        row = db.get_user_by_verification_token(token)
    except Exception as e:
        logger.error("Verification lookup failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Verification failed. Please try again later.",
        )

    if not row:
        raise HTTPException(
            status_code=400,
            detail="Invalid or already-used verification link. Request a new one.",
        )

    # Idempotent: a link for an account that is already verified succeeds,
    # whatever its age. Checked BEFORE expiry, so neither a double click nor a
    # mail scanner's pre-fetch of the link turns the user's real click into an
    # error page.
    if row.get("email_verified"):
        return {
            "error": False,
            "verified": True,
            "email": row["email"],
            "message": "Your email is already verified. You can log in.",
        }

    expires = row.get("verification_token_expires")
    # Fail-closed: a token with no readable expiry is rejected. The expiry
    # is defense-in-depth against a leaked link living forever — when it
    # can't be determined, the safe posture is to reject, not accept.
    if not expires:
        logger.warning(
            "Verification token has no expiry (user id=%s) — rejecting", row["id"]
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid or already-used verification link. Request a new one.",
        )
    try:
        if isinstance(expires, datetime):
            # psycopg2 returns TIMESTAMPTZ as a tz-aware datetime already.
            exp_ts = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
        else:
            # Other drivers may return the timestamp as a string.
            parsed = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            exp_ts = parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
        if exp_ts.tzinfo is None:
            exp_ts = exp_ts.replace(tzinfo=timezone.utc)
    except Exception as e:
        logger.warning("Token expiry parse failed (%s) — rejecting", e)
        raise HTTPException(
            status_code=400,
            detail="Invalid or already-used verification link. Request a new one.",
        )
    if exp_ts < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail="This verification link has expired. Request a new one.",
        )

    try:
        db.mark_email_verified(row["id"])
    except Exception as e:
        logger.error("Mark-verified failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Verification failed. Please try again later.",
        )

    logger.info("Email verified: %s (id=%d)", row["email"], row["id"])
    return {
        "error": False,
        "verified": True,
        "email": row["email"],
        "message": "Email verified successfully. You can now log in.",
    }


@app.post(
    "/auth/verify-email",
    response_model=Dict[str, Any],
    summary="Verify an email address (JSON body form)",
    tags=["Auth"],
)

async def verify_email_body(request: Request, response: Response, payload: VerifyEmailRequest):
    """Verify an email address with the token in a JSON body.

    Same logic as ``GET /auth/verify-email`` (delegates to it) — exists for
    frontends that POST after rendering the emailed link.
    """
    return await verify_email(request, response, token=payload.token)


@app.post(
    "/auth/resend-verification",
    response_model=Dict[str, Any],
    summary="Resend the email verification link",
    tags=["Auth"],
)

async def resend_verification(
    request: Request,
    response: Response,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Send a fresh verification email to the authenticated user.

    Generates a NEW token (the old one is invalidated) and emails it.
    Rate-limited by the auth bucket like every /auth endpoint. Returns
    ``email_sent`` so the frontend can react to a SendGrid failure.
    """
    from src.database import PostgresDB
    from src.emails import generate_verification_token, send_verification_email

    if not settings.has_email_verification:
        raise HTTPException(
            status_code=503,
            detail=(
                "Email verification is not configured. Set SENDGRID_API_KEY "
                "and EMAIL_FROM_ADDRESS in .env."
            ),
        )

    if user.get("email_verified"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Your email is already verified — no verification link needed."
            ),
        )

    user_id = int(user["id"])
    email = user["email"]
    token = generate_verification_token()
    # UTC-aware expiry (same reasoning as /auth/register — TIMESTAMPTZ
    # round-trips tz-aware; a naive timestamp would drift by the offset).
    expires = (
        datetime.now(timezone.utc)
        + timedelta(hours=settings.email_verification_token_ttl_hours)
    ).isoformat()
    try:
        db = PostgresDB()
        db.set_user_verification_token(user_id, token, expires)
    except Exception as e:
        logger.error("Verification token store failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Could not send the verification email right now. Try again later.",
        )

    email_sent = send_verification_email(email, token)
    logger.info(
        "Verification email re-sent: %s (id=%d, sent=%s)", email, user_id, email_sent
    )
    return {
        "error": False,
        "email_sent": email_sent,
        "message": (
            "Verification email sent. Check your inbox."
            if email_sent
            else "Could not send the verification email right now. Try again later."
        ),
    }


@app.post(
    "/auth/logout-all",
    response_model=Dict[str, Any],
    summary="Revoke every token this account has issued",
    tags=["Auth"],
)

async def logout_all(
    request: Request,
    response: Response,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Log out everywhere: invalidate every JWT this account holds.

    Bumps ``users.token_version``, which ``get_current_user`` compares
    against the ``ver`` claim of every token on every authenticated
    request. The caller's own token is therefore dead as soon as this
    returns — that is the point (a stolen laptop, a leaked token), and the
    frontend's 401 handler turns it into a normal logout. Log in again to
    mint a token carrying the new version.
    """
    from src.database import PostgresDB

    try:
        new_version = PostgresDB().bump_token_version(int(user["id"]))
    except Exception as e:
        logger.error("Token revocation failed for user %s: %s", user["id"], e)
        raise HTTPException(
            status_code=502,
            detail="Could not log out all sessions right now. Try again later.",
        )
    logger.info(
        "User %s revoked all sessions (token_version=%d)", user["id"], new_version
    )
    return {
        "error": False,
        "token_version": new_version,
        "message": "All sessions are logged out. Log in again for a new token.",
    }


# ── Google OAuth ("Continue with Google") ───────────────────────────────
# Two thin routes; everything Google-specific — the state cookie, the token
# exchange, ID-token validation and the account merge rule — lives in
# src/google_oauth.py. Both paths fall into the `auth` rate-limit scope by
# prefix, so RATE_LIMITS and _scope_for_path are deliberately untouched.


def _google_error_redirect(code: str, reason: str = "") -> RedirectResponse:
    """302 to the frontend login page with an ``oauth_error`` code.

    The real reason is logged; only the code reaches the browser (golden rule
    #12 — no raw exception text in responses). The state cookie is cleared on
    every outcome, success or failure: a survivor must never be reused by a
    later attempt.
    """
    from src import google_oauth

    logger.warning(
        "Google sign-in failed (%s)%s", code, f": {reason}" if reason else ""
    )
    redirect = RedirectResponse(google_oauth.login_error_url(code), status_code=302)
    redirect.delete_cookie(google_oauth.COOKIE_NAME, path=google_oauth.COOKIE_PATH)
    return redirect


@app.get(
    "/auth/google/start",
    summary="Begin Google sign-in (302 to Google)",
    tags=["Auth"],
)
async def google_oauth_start(request: Request, response: Response):
    """Send the browser to Google's consent screen with a signed state cookie.

    A plain 302 for an ``<a href>``: no JavaScript, no popup, and no token in
    the URL until the callback arrives. When the credential pair — or the JWT
    secret that signs the state — is missing, this degrades to the login page
    instead of to a consent screen that could never be completed.
    """
    from src import google_oauth

    if not settings.has_google_oauth or not settings.has_jwt_secret:
        return _google_error_redirect("unavailable", "google oauth not configured")

    url, cookie_value = google_oauth.begin_authorization()
    redirect = RedirectResponse(url, status_code=302)
    redirect.set_cookie(**google_oauth.cookie_settings(cookie_value))
    return redirect


@app.get(
    "/auth/google/callback",
    summary="Finish Google sign-in (server-side code exchange)",
    tags=["Auth"],
)
async def google_oauth_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
):
    """Exchange the code, validate the ID token, apply the merge rule, sign in.

    The parameters are plain optional strings on purpose: Google sends
    ``?error=access_denied`` when the user declines, and a missing ``code``
    has to become a friendly redirect rather than a 422 from FastAPI.
    """
    from src import google_oauth
    from src.auth import create_access_token
    from src.database import PostgresDB

    if not settings.has_google_oauth or not settings.has_jwt_secret:
        return _google_error_redirect("unavailable", "google oauth not configured")

    # CSRF defence, and it is not optional. The cookie must verify (HMAC keyed
    # by jwt_secret) AND its `state` must equal the one Google echoed back.
    # Without this, an attacker can hand a victim's browser a callback carrying
    # the ATTACKER's code — the victim is then silently signed into the
    # attacker's account, and everything they do lands there.
    claims = google_oauth.verify_state_cookie(
        request.cookies.get(google_oauth.COOKIE_NAME)
    )
    if not claims:
        return _google_error_redirect("state", "missing or unverifiable state cookie")
    if not google_oauth.state_matches(claims, state):
        return _google_error_redirect("state", "state did not match the cookie")

    if error or not code:
        return _google_error_redirect("failed", f"no code (error={error!r})")

    try:
        tokens = google_oauth.exchange_code(code)
    except Exception as e:
        return _google_error_redirect("exchange", str(e))

    id_token = tokens.get("id_token")
    if not id_token:
        return _google_error_redirect("token", "token response carried no id_token")

    try:
        payload = google_oauth.validate_id_token(id_token, claims["nonce"])
    except google_oauth.IdTokenError as e:
        return _google_error_redirect(e.code, str(e))
    except Exception as e:
        return _google_error_redirect("token", str(e))

    sub = str(payload.get("sub") or "")
    email = str(payload.get("email") or "").strip().lower()
    if not sub or not email:
        return _google_error_redirect("token", "id_token lacked sub/email")

    try:
        db = PostgresDB()
        identity = google_oauth.resolve_google_identity(db, sub, email)
        user_id = int(identity["id"])
        # Google proved the address, so ADMIN_EMAILS applies immediately — the
        # same rule as a clicked verification link (see _bootstrap_admin_role).
        _bootstrap_admin_role(db, email, user_id, bool(identity["is_admin"]), True)
        token = create_access_token(user_id, email, int(identity["token_version"]))
    except Exception as e:
        return _google_error_redirect("failed", str(e))

    # Only a freshly CREATED account is a signup. Signing in to an existing
    # account (via sub, a link, or a takeover) is not, and counting it would
    # inflate the exact number this event exists to report.
    if identity.get("via") == "created":
        from src.analytics import track

        track("signup_completed", user_id=user_id, props={"method": "google"})

    expires_in = settings.jwt_expire_hours * 3600
    redirect = RedirectResponse(
        google_oauth.complete_redirect_url(token, expires_in), status_code=302
    )
    redirect.delete_cookie(google_oauth.COOKIE_NAME, path=google_oauth.COOKIE_PATH)
    logger.info(
        "Google sign-in: user %s signed in (via %s)", user_id, identity.get("via")
    )
    return redirect


# ── Password reset & change ─────────────────────────────────────────────
# Reset must not reveal whether an address has an account — by content,
# status code, OR timing. Content and status are easy (one response for
# both). Timing is not: the real path does an UPDATE and the unknown path
# does nothing. Two measures close it: the email is sent AFTER the response
# (a BackgroundTask), so the slow part never sits on the request path; and
# every response is padded to a fixed floor, so the one remaining UPDATE
# (single-digit milliseconds) disappears under it. This is the same
# property login gets from its dummy-hash comparison.
RESET_RESPONSE_FLOOR_SECONDS = 0.35

# One reset email per account per window, whoever asks and from however many
# IPs. The per-IP "auth_mail" scope stops one client hammering; this stops a
# botnet mail-bombing one victim. Suppressed requests get the identical
# response.
RESET_PER_ACCOUNT_COOLDOWN_SECONDS = 60

_RESET_REQUEST_MESSAGE = (
    "If an account exists for that address, we've sent a link to reset its "
    "password. It expires soon, so check your inbox now."
)


async def _pad_to_floor(started: float, floor: float) -> None:
    """Sleep until ``floor`` seconds have passed since ``started``."""
    remaining = floor - (time.monotonic() - started)
    if remaining > 0:
        await asyncio.sleep(remaining)


def _as_utc(value: Any) -> Optional[datetime]:
    """Parse a TIMESTAMPTZ (datetime or ISO string) to aware UTC, or None."""
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


@app.post(
    "/auth/password-reset/request",
    response_model=Dict[str, Any],
    summary="Email a password-reset link (same response whether or not the account exists)",
    tags=["Auth"],
)
async def request_password_reset(
    request: Request,
    response: Response,
    payload: PasswordResetRequest,
    background_tasks: BackgroundTasks,
):
    """Start a password reset.

    Always returns the same 200 body after the same minimum time, whether
    the address has an account, has no account, is throttled, or the mail
    transport is down. The only observable difference is the email, which
    reaches only the address's owner.
    """
    from src.auth import generate_reset_token, hash_reset_token
    from src.database import PostgresDB
    from src.emails import send_password_reset_email

    started = time.monotonic()
    email = payload.email.strip().lower()

    try:
        db = PostgresDB()
        user = db.get_user_by_email(email)
    except Exception as e:
        logger.error("Password-reset lookup failed: %s", e)
        user = None

    if user is not None:
        ttl = timedelta(minutes=settings.password_reset_token_ttl_minutes)
        now = datetime.now(timezone.utc)
        prior_expiry = _as_utc(user.get("reset_token_expires"))
        issued_recently = (
            prior_expiry is not None
            and now - (prior_expiry - ttl)
            < timedelta(seconds=RESET_PER_ACCOUNT_COOLDOWN_SECONDS)
        )
        if not settings.has_email_verification:
            logger.warning(
                "Password reset requested but no mail transport is configured"
            )
        elif issued_recently:
            logger.info("Password reset for user %s suppressed (cooldown)", user["id"])
        else:
            token = generate_reset_token()
            try:
                db.set_password_reset_token(
                    user["id"], hash_reset_token(token), (now + ttl).isoformat()
                )
                # After the response: the send is the slow part, and it must
                # not be what distinguishes a real address from a fake one.
                background_tasks.add_task(send_password_reset_email, email, token)
                logger.info("Password reset issued for user %s", user["id"])
            except Exception as e:
                logger.error("Password-reset token store failed: %s", e)

    await _pad_to_floor(started, RESET_RESPONSE_FLOOR_SECONDS)
    return {"error": False, "message": _RESET_REQUEST_MESSAGE}


@app.post(
    "/auth/password-reset/confirm",
    response_model=Dict[str, Any],
    summary="Set a new password from an emailed reset link",
    tags=["Auth"],
)
async def confirm_password_reset(
    request: Request,
    response: Response,
    payload: PasswordResetConfirm,
    background_tasks: BackgroundTasks,
):
    """Finish a password reset.

    On success the password changes, the link stops working, the email is
    marked verified (the link proved control of it), and **every session the
    account holds is revoked** via ``token_version`` — including any session
    an attacker had. The owner is emailed that it happened.
    """
    from src.auth import hash_password, hash_reset_token
    from src.database import PostgresDB
    from src.emails import send_password_changed_email

    invalid = HTTPException(
        status_code=400,
        detail="This reset link is invalid or has already been used. Request a new one.",
    )
    token_hash = hash_reset_token(payload.token)
    try:
        db = PostgresDB()
        row = db.get_user_by_reset_token_hash(token_hash)
    except Exception as e:
        logger.error("Password-reset lookup failed: %s", e)
        raise HTTPException(
            status_code=502, detail="Could not reset your password right now. Try again later."
        )
    if not row:
        raise invalid

    # Fail closed on a missing or unparseable expiry, as verify-email does.
    expires = _as_utc(row.get("reset_token_expires"))
    if expires is None:
        raise invalid
    if expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=400,
            detail="This reset link has expired. Request a new one.",
        )

    try:
        new_version = db.complete_password_reset(
            row["id"], token_hash, hash_password(payload.new_password)
        )
    except Exception as e:
        logger.error("Password reset failed for user %s: %s", row["id"], e)
        raise HTTPException(
            status_code=502, detail="Could not reset your password right now. Try again later."
        )
    if new_version is None:
        # Another request consumed this token between our read and write.
        raise invalid

    background_tasks.add_task(send_password_changed_email, row["email"])
    logger.info(
        "Password reset completed for user %s (sessions revoked, token_version=%d)",
        row["id"], new_version,
    )
    return {
        "error": False,
        "message": "Your password has been reset and every device has been signed out. Log in with your new password.",
    }


@app.post(
    "/auth/change-password",
    response_model=Dict[str, Any],
    summary="Change the password while signed in (signs out every other device)",
    tags=["Auth"],
)
async def change_password(
    request: Request,
    response: Response,
    payload: ChangePasswordRequest,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Change the password and revoke every other session.

    Returns a fresh token carrying the new ``token_version`` so the device
    that made the change stays signed in while every other one is signed out.

    A wrong current password is a **400**, not a 401: the frontend treats any
    401 outside its auth allowlist as "your session died" and logs out, so a
    typo here would otherwise sign the user out of the page they are on.
    """
    from src.auth import create_access_token, hash_password, verify_password
    from src.database import PostgresDB
    from src.emails import send_password_changed_email

    if not verify_password(payload.current_password, user.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Your current password is incorrect.")
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=400, detail="Choose a password different from your current one."
        )

    try:
        new_version = PostgresDB().change_password(
            int(user["id"]), hash_password(payload.new_password)
        )
    except Exception as e:
        logger.error("Password change failed for user %s: %s", user["id"], e)
        raise HTTPException(
            status_code=502, detail="Could not change your password right now. Try again later."
        )

    if settings.has_email_verification:
        background_tasks.add_task(send_password_changed_email, user["email"])
    logger.info(
        "Password changed for user %s (other sessions revoked, token_version=%d)",
        user["id"], new_version,
    )
    return {
        "error": False,
        "access_token": create_access_token(int(user["id"]), user["email"], new_version),
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_hours * 3600,
        "message": "Password changed. Every other device has been signed out.",
    }


@app.post(
    "/auth/refresh",
    response_model=Dict[str, Any],
    summary="Re-issue the current token with a fresh expiry (sliding session)",
    tags=["Auth"],
)
async def refresh_token(
    request: Request,
    response: Response,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Extend an active session without asking for the password again.

    Ends the hard logout that dumped users mid-backtest when a 7-day token
    lapsed. Three properties keep it from weakening anything:

    - It requires a token that is valid **right now** (``get_current_user``),
      including the ``token_version`` check — so a refresh can never outlive
      a revocation, a reset or a password change.
    - The new token carries the same ``ver``: refreshing is not a way to
      escape "log out everywhere".
    - ``auth_time`` is carried over, not reset, and a session older than
      ``SESSION_MAX_AGE_DAYS`` since the last password entry is refused. A
      stolen token can be ridden for at most that long, not forever.
    """
    from src.auth import _extract_bearer_token, create_access_token, verify_access_token

    payload = verify_access_token(_extract_bearer_token(request) or "") or {}
    # Tokens minted before auth_time existed fall back to iat.
    auth_time = int(payload.get("auth_time") or payload.get("iat") or 0)
    if time.time() - auth_time > settings.session_max_age_days * 86400:
        raise HTTPException(
            status_code=401,
            detail="Your session is too old to extend. Log in again.",
        )
    token = create_access_token(
        int(user["id"]), user["email"], int(user.get("token_version", 0)), auth_time
    )
    return {
        "error": False,
        "access_token": token,
        "token_type": "bearer",
        "expires_in": settings.jwt_expire_hours * 3600,
    }


@app.post(
    "/auth/delete-account",
    response_model=Dict[str, Any],
    summary="Permanently delete the signed-in account and its data",
    tags=["Auth"],
)
async def delete_account(
    request: Request,
    response: Response,
    payload: DeleteAccountRequest,
    background_tasks: BackgroundTasks,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Delete the account, its saved data and its backtest history.

    Requires the password again: a stolen or left-open session must not be
    enough to destroy an account irreversibly. As with change-password, a
    wrong password is a 400, not a 401, so a typo does not log the UI out.

    Afterwards every token the account held fails on the next request, via
    the missing-user branch of ``get_current_user`` — there is no row left
    to compare a ``ver`` claim against.
    """
    from src.auth import verify_password
    from src.database import PostgresDB
    from src.emails import send_account_deleted_email

    if not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="Your password is incorrect.")

    email = user["email"]
    try:
        counts = PostgresDB().delete_user(int(user["id"]), email)
    except Exception as e:
        logger.error("Account deletion failed for user %s: %s", user["id"], e)
        raise HTTPException(
            status_code=502, detail="Could not delete your account right now. Try again later."
        )

    # In-flight async backtests live in memory too; drop this account's.
    with _BACKTEST_JOBS_LOCK:
        for job_id in [j for j, job in _BACKTEST_JOBS.items() if job.get("user_email") == email]:
            _BACKTEST_JOBS.pop(job_id, None)

    if settings.has_email_verification:
        background_tasks.add_task(send_account_deleted_email, email)
    logger.info(
        "Account deleted: user %s (%d backtest jobs removed)",
        user["id"], counts.get("backtest_jobs", 0),
    )
    return {
        "error": False,
        "message": "Your account and all of its data have been permanently deleted.",
    }


@app.post(
    "/screen",
    response_model=ScreenResponse,
    summary="Screen stocks using natural language",
    tags=["Screener"],
)
async def screen_stocks(request: Request, response: Response, payload: ScreenRequest):
    """Screen the US stock universe using a plain-English query.

    The natural language interpreter translates your query into structured
    filter criteria, then queries the local database — zero API calls per
    screening. When the news database is configured, each result also
    carries its aggregate sentiment score (stored, zero API calls).

    **Pagination:** ``limit`` + ``offset`` page through results. The
    response includes ``total_matches`` (true total), ``has_more`` and
    ``next_offset`` so clients can render "load more" without guessing.

    **Examples:**
    - ``undervalued small-cap tech stocks with rising momentum``
    - ``large cap dividend stocks with low beta``
    - ``oversold healthcare stocks with high volume``
    """
    from src.interpreter import format_filters_for_display

    # Key on the RAW query (not normalized): the response echoes `query`
    # verbatim, so a different casing must map to a different cached entry.
    # The expensive LLM step is still deduped by the interpreter's own
    # normalized cache, so this costs nothing. Sort params are part of the
    # key — a different ranking must not serve a stale page.
    cache_key = (payload.query, payload.limit, payload.offset,
                 payload.sort_by, payload.sort_desc)
    if settings.cache_enabled:
        cached = _screen_cache.get(cache_key)
        if cached is not None:
            logger.info(
                "Screen cache HIT for '%s' (limit=%d, offset=%d, sort=%s)",
                payload.query,
                payload.limit,
                payload.offset,
                payload.sort_by,
            )
            # A cache hit is still a screen the user saw, so the funnel counts
            # it. Only the interpret_* event is skipped — the interpreter did
            # not run, and claiming it did would overstate the LLM cost split.
            _track_screen_returned(cached)
            return cached

    logger.info(
        "Screen request: '%s' (limit=%d, offset=%d, sort_by=%s, desc=%s)",
        payload.query,
        payload.limit,
        payload.offset,
        payload.sort_by,
        payload.sort_desc,
    )

    try:
        interpreted, report = _run_screen(
            payload.query,
            payload.limit,
            payload.offset,
            sort_by=payload.sort_by,
            sort_desc=payload.sort_desc,
        )
    except Exception as e:
        logger.error("Screening failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Screening query failed. Please try again later.",
        )

    logger.info("Interpreted filters: %s", format_filters_for_display(interpreted))

    results = [_result_to_stock(r) for r in report.results]

    # Attach stored sentiment to each result in one batch DB read (0 API calls)
    if results and settings.has_news_database:
        from src.news import get_sentiment_for_screen

        symbols = [r.symbol for r in results]
        sentiments = get_sentiment_for_screen(symbols)
        for r in results:
            if r.symbol in sentiments:
                s = sentiments[r.symbol]
                r.sentiment = {
                    "buzz": s.get("buzz", 0.0),
                    "company_news_score": s.get("company_news_score", 0.0),
                    "sector_news_score": s.get("sector_news_score", 0.0),
                    "article_count": s.get("article_count", 0),
                }

    # Offset pagination strides by `limit` at the DB level — even if a
    # post-query filter (momentum) shrank the visible page, the next DB
    # page starts at offset + limit, so next_offset mirrors that stride
    # (computed inside _build_screen_response — one owner, both endpoints).
    response = _build_screen_response(
        payload.query, interpreted, report, payload.limit, results,
    )
    # Never cache an ERROR response: the screener sets data_quality.error on
    # both an LLM interpretation failure and an internal query failure. A
    # transient LLM outage would otherwise poison the cache with "could not
    # interpret" for the whole TTL (up to 60s) after the provider recovered
    # — every user would see the stale error. Same rule the interpreter's own
    # cache follows: only successes are worth serving again.
    if settings.cache_enabled and not report.data_quality.get("error"):
        _screen_cache.set(cache_key, response)
    _track_screen_returned(response)
    return response


@app.post(
    "/interpret",
    response_model=Dict[str, Any],
    summary="Test the NL interpreter without running a screen",
    tags=["Screener"],
)
async def interpret_only(request: Request, response: Response, payload: InterpretRequest):
    """Test how the system interprets a natural language query.

    Returns the extracted filter criteria without actually running the
    database query — useful for debugging what the LLM extracts from
    different phrasings.
    """
    from src.interpreter import interpret_query

    try:
        interpreted = interpret_query(payload.query)
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Interpretation failed. Please try again later.",
        )

    return {
        "raw_input": payload.query,
        "filters": _interpreted_to_dict(interpreted),
        "filter_count": len(interpreted.filters),
        "llm_error": interpreted.error,
        "display": (
            f"Sector: {interpreted.sector or 'Any'} | "
            f"Momentum: {interpreted.momentum or 'neutral'} | "
            f"{len(interpreted.filters)} numeric filters"
        ),
    }


class EventRequest(BaseModel):
    """A browser-reported analytics event (POST /events).

    Only ``name`` is validated here; the allowlist that actually matters is
    ``src.analytics.CLIENT_EVENTS``, applied in the handler so the event
    vocabulary stays in exactly one place.
    """

    name: str
    props: Optional[Dict[str, Any]] = None


@app.post(
    "/events",
    status_code=204,
    summary="Record a browser-side analytics event",
    tags=["System"],
)
async def submit_event(request: Request, payload: EventRequest):
    """Record one browser event from the app's fire-and-forget ``track()``.

    Public by design: the events that matter most happen BEFORE an account
    exists (a landing view, a first screen, a signup form mount). The
    ``X-Anon-Id`` header carries the browser's local UUID so the anonymous and
    signed-in halves of one person's funnel join up; the Bearer token, when
    present, attributes the event to the account.

    A server-only name (``backtest_completed`` and friends) is rejected with
    400 rather than ignored: those events are derived from real work, and
    accepting them from a browser would let a script forge the funnel.

    Rate limiting is deliberately left in the default ``read`` scope — this is
    a cheap insert, and a dedicated scope would add a bucket to every
    submitting surface the browser calls. See ``_scope_for_path``.
    """
    from src.analytics import CLIENT_EVENTS, track

    if payload.name not in CLIENT_EVENTS:
        raise HTTPException(
            status_code=400,
            detail=f"'{payload.name}' is not a client-reportable event.",
        )

    # Optional attribution: never 401. A missing or expired token just means
    # the event lands anonymously — the browser retries nothing, and the
    # funnel stays honest instead of losing the row.
    user_id: Optional[int] = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        from src.auth import verify_access_token

        claims = verify_access_token(auth_header[7:].strip())
        if claims:
            try:
                user_id = int(claims.get("sub"))
            except (TypeError, ValueError):
                user_id = None

    track(
        payload.name,
        user_id=user_id,
        anon_id=request.headers.get("x-anon-id"),
        props=payload.props,
    )
    # 204 unconditionally, even if the write failed: track() swallows its own
    # failures, and a telemetry endpoint that reports errors the client cannot
    # act on would only add noise to the browser console.
    return Response(status_code=204)


def _run_backtest_pipeline(
    payload: BacktestRequest,
    user: Dict[str, Any],
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Shared /backtest + /backtest/export pipeline (never diverge).

    One code path for both endpoints: interpret → validate → translate →
    merge risk overrides → run the engine. Both validations that must not
    be skipped live here (position_sizing whitelist, start <= end), so a
    future caller CANNOT silently drop them the way a copy-pasted block
    did. Returns (strategy_rules, bt_results) where bt_results maps symbol
    → BacktestResult.
    """
    from src.interpreter import interpret_query
    from src.backtester import (
        BacktestEngine,
        translate_strategy,
        parse_backtest_query,
        compute_portfolio,
        DEFAULT_INITIAL_CAPITAL,
    )

    logger.info(
        "Backtest request: '%s' (top_n=%d, user=%s plan=%s)",
        payload.query,
        payload.top_n,
        user.get("email"),
        user.get("plan"),
    )

    # Step 1: Interpret the query
    try:
        interpreted = interpret_query(payload.query)
    except Exception as e:
        logger.error("Interpretation failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Failed to interpret strategy. Please try again later.",
        )
    _track_interpret(interpreted)

    # interpret_query never raises for LLM failures — it returns an
    # InterpretedQuery with .error set and empty filters. Silently running
    # the default RSI strategy on a failed interpretation would backtest
    # the WRONG strategy and present it as the user's idea. Surface it
    # instead (same contract /screen honors via llm_error).
    if interpreted.error:
        # VULN-002: interpreted.error can carry raw exception text
        # ("LLM call failed: {e}" from interpreter.py). Log it server-side;
        # give the client a generic message. The designed `llm_error`
        # response field (/screen) still surfaces the failure to the UI.
        logger.error("Backtest interpretation failed: %s", interpreted.error)
        raise HTTPException(
            status_code=502,
            detail="Could not interpret strategy. Please try again later.",
        )

    # Echo the exact rules the engine will simulate (buy/sell conditions,
    # fee, slippage, capital, risk parameters) so the response is auditable
    # — not a black box.
    strategy_rules = translate_strategy(interpreted)

    # Tier 2: merge explicit risk/execution overrides from the request on
    # top of the LLM-derived rules. None = "not specified" → LLM default.
    # Percentages arrive as 0-100 and become decimal fractions internally.
    # position_sizing is validated here — the ONLY place it may be — so
    # /backtest/export can't silently accept a bogus value.
    overrides: Dict[str, Any] = {}
    if payload.stop_loss_pct is not None:
        overrides["stop_loss"] = payload.stop_loss_pct / 100.0
    if payload.take_profit_pct is not None:
        overrides["take_profit"] = payload.take_profit_pct / 100.0
    if payload.trailing_stop_pct is not None:
        overrides["trailing_stop"] = payload.trailing_stop_pct / 100.0
    if payload.position_sizing is not None:
        if payload.position_sizing not in {"all_in", "fixed_pct", "risk_based"}:
            raise HTTPException(
                status_code=422,
                detail="position_sizing must be one of: all_in, fixed_pct, risk_based",
            )
        overrides["position_sizing"] = payload.position_sizing
    if payload.position_pct is not None:
        overrides["position_pct"] = payload.position_pct
    if payload.risk_per_trade_pct is not None:
        overrides["risk_per_trade"] = payload.risk_per_trade_pct / 100.0
    if payload.allow_short is not None:
        overrides["allow_short"] = payload.allow_short
    if payload.max_leverage is not None:
        overrides["max_leverage"] = payload.max_leverage
    # Tier 5: cash accounting (dividends + short borrow). Off by default
    # keeps the validated adjusted-price sim byte-identical; enabling any
    # of these switches the sim to the cash-flow model.
    if payload.dividend_reinvest is not None:
        overrides["dividend_accrual"] = True
        overrides["dividend_reinvest"] = payload.dividend_reinvest
    if payload.borrow_rate_pct is not None:
        overrides["borrow_rate"] = payload.borrow_rate_pct / 100.0
    if payload.portfolio_allocation not in (None, "equal", "risk_based"):
        raise HTTPException(
            status_code=422,
            detail="portfolio_allocation must be one of: equal, risk_based",
        )
    if payload.rebalance_frequency not in (
        None, "none", "monthly", "quarterly", "yearly"
    ):
        raise HTTPException(
            status_code=422,
            detail="rebalance_frequency must be one of: none, monthly, quarterly, yearly",
        )
    strategy_rules = {**strategy_rules, **overrides}

    # Tier 4: validate the optional date range (pattern already constrains
    # format; reject start > end up front with a clear 422).
    if payload.start_date and payload.end_date and payload.start_date > payload.end_date:
        raise HTTPException(
            status_code=422,
            detail="start_date must not be after end_date.",
        )

    # ── Strategy directives the SCREENER interpreter cannot see ──────────
    # "buy and hold AAPL for the last 6 months" extracts ZERO screener
    # criteria (interpret_query is a screener interpreter), so the engine
    # used to silently run the default RSI mean-reversion strategy over 10
    # years on the top-5 by market cap — the ticker, the hold behavior and
    # the window were all discarded. Recover them from the raw text here.
    parsed = parse_backtest_query(payload.query)

    # Buy-and-hold: buy on the first bar (price > 0 is always true) and
    # never sell (price < 0 is never true) — the position rides to the end
    # and is force-liquidated at the final close: pure buy-and-hold, echoed
    # audibly in the strategy payload.
    if parsed["buy_and_hold"]:
        strategy_rules = {
            **strategy_rules,
            "buy_rules": ["price > 0"],
            "sell_rules": ["price < 0"],
        }

    # Natural-language date windows apply only when the request didn't pin
    # an explicit range (explicit wins).
    start_date = payload.start_date or parsed["start_date"]
    end_date = payload.end_date or parsed["end_date"]

    # Step 2: Run the backtest
    screen_message: Optional[str] = None
    try:
        engine = BacktestEngine()
        if payload.symbols:
            # Tier 3 compare mode: an explicit symbol set from the request
            # wins over everything — strategy B re-runs on strategy A's
            # picks. The screener is skipped entirely so the A/B is genuine.
            bt_results = engine.backtest_top_results(
                interpreted,
                top_n=len(payload.symbols),
                strategy_rules=strategy_rules,
                start_date=start_date,
                end_date=end_date,
                max_sector_exposure=payload.max_sector_exposure,
                symbols=payload.symbols,
                progress_cb=progress_cb,
            )
        elif parsed["symbols"]:
            # Explicit tickers in the query → backtest exactly those
            # symbols instead of screening the whole universe.
            bt_results = engine.backtest(
                interpreted,
                parsed["symbols"],
                strategy_rules=strategy_rules,
                start_date=start_date,
                end_date=end_date,
                progress_cb=progress_cb,
            )
        else:
            bt_results = engine.backtest_top_results(
                interpreted,
                top_n=payload.top_n,
                strategy_rules=strategy_rules,
                start_date=start_date,
                end_date=end_date,
                max_sector_exposure=payload.max_sector_exposure,
                progress_cb=progress_cb,
            )
            # Forward the screener's honesty notes: a relaxed screen (0
            # matches → nearest candidates) or candidate fill-in (top-N
            # symbols lacked data) must be visible to the user, not silent.
            # getattr with a None default: test fakes (and any engine
            # subclass) may define backtest_top_results without the note
            # attributes — a direct attribute read would AttributeError
            # inside the try and surface as a misleading 502.
            screen_message = " ".join(
                n for n in (
                    getattr(engine, "_last_screen_message", None),
                    getattr(engine, "_last_fill_note", None),
                ) if n
            ) or None
    except Exception as e:
        logger.error("Backtest failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Backtest failed. Please try again later.",
        )

    # Per-symbol data age: when the explicit-symbols path bypassed the
    # screener, the engine's _screen_data_age dict is empty. Look up each
    # symbol's DB freshness directly so the UI can still render a
    # DataAgeBadge.
    # Collect symbols that need a DB age lookup (skip those already set
    # by the screener path).
    missing_age = [
        sym for sym, bt in bt_results.items()
        if bt.data_age_minutes is None
    ]
    if missing_age:
        try:
            from datetime import datetime, timezone
            from src.database import PostgresDB
            from src.screener import STALE_AFTER_MINUTES
            _db = PostgresDB()
            with _db._get_conn() as _conn:
                with _conn.cursor() as _cur:
                    _cur.execute(
                        "SELECT symbol, updated_at FROM stock_master "
                        "WHERE symbol = ANY(%s)",
                        (missing_age,),
                    )
                    for row in _cur.fetchall():
                        sym, ts = row[0], row[1]
                        if sym in bt_results and ts:
                            if isinstance(ts, str):
                                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            age = (datetime.now(timezone.utc) - ts).total_seconds() / 60
                            bt_results[sym].data_age_minutes = round(age, 1)
                            bt_results[sym].is_stale = age > STALE_AFTER_MINUTES
        except Exception:
            pass  # best-effort: don't fail the backtest for age lookup

    # Tier 5: aggregate the per-symbol results into ONE shared-pool
    # portfolio (combined equity/drawdown curves, portfolio Sharpe/DD) when
    # ≥2 symbols were backtested. Off-by-default features must not change
    # the default payload — a single-symbol or empty run yields None, and
    # the caller decides whether to surface it.
    portfolio = compute_portfolio(
        bt_results,
        initial_capital=strategy_rules.get(
            "initial_capital", DEFAULT_INITIAL_CAPITAL
        ),
        allocation=payload.portfolio_allocation or "equal",
        rebalance=payload.rebalance_frequency or "none",
    )

    return strategy_rules, bt_results, screen_message, portfolio


@app.post(
    "/backtest",
    response_model=BacktestResponse,
    summary="Backtest a strategy (free for all authenticated users)",
    tags=["Backtesting"],
)
async def backtest_strategy(
    request: Request,
    response: Response,
    payload: BacktestRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Backtest a natural language strategy on historical data.

    Free for all authenticated users (the old premium paywall was
    removed — Stratoscan is fully free). Auth still applies for
    per-user rate limiting.

    The engine:
    1. Interprets your strategy description into buy/sell rules
    2. Screens for top matching stocks
    3. Downloads 10 years of OHLCV data (via yfinance)
    4. Simulates trading with fees and slippage
    5. Calculates Sharpe ratio, max drawdown, win rate
    """
    from src.analytics import track

    mode = "sync"
    symbols = _backtest_symbol_count(payload)
    user_id = _user_id_of(user)
    track("backtest_started", user_id=user_id,
          props={"symbols": symbols, "mode": mode})
    started = time.monotonic()
    try:
        strategy_rules, bt_results, screen_message, portfolio = _run_backtest_pipeline(
            payload, user
        )
        response_payload = _build_backtest_response(
            payload, strategy_rules, bt_results, screen_message, portfolio
        )
    except Exception as e:  # noqa: BLE001 — recorded, then re-raised unchanged
        # The client's response is untouched: this only adds a funnel row for
        # the failure. HTTPException keeps its status; anything else still
        # reaches generic_exception_handler.
        track("backtest_failed", user_id=user_id,
              props={"mode": mode, "reason": _backtest_failure_reason(e)})
        raise
    track("backtest_completed", user_id=user_id,
          props={"symbols": symbols, "mode": mode})
    track("backtest_duration", user_id=user_id,
          props={"duration_ms": int((time.monotonic() - started) * 1000),
                 "mode": mode})
    return response_payload


def _build_backtest_response(
    payload: BacktestRequest,
    strategy_rules: Dict[str, Any],
    bt_results: Dict[str, Any],
    screen_message: Optional[str],
    portfolio: Any,
) -> BacktestResponse:
    """Convert engine results into the API response (shared sync + async).

    One code path for the synchronous ``/backtest`` endpoint AND the async
    job's finalize step — a compare/shared-link consumer must see the exact
    same payload shape whichever path produced it.
    """
    # Step 3: Convert results
    result_items: Dict[str, BacktestResultItem] = {}
    for symbol, bt_result in bt_results.items():
        trades = [
            TradeInfo(
                symbol=t.symbol,
                buy_date=t.buy_date,
                buy_price=t.buy_price,
                sell_date=t.sell_date,
                sell_price=t.sell_price,
                shares=t.shares,
                pnl=t.pnl,
                pnl_pct=t.pnl_pct,
                side=t.side,
                exit_reason=t.exit_reason,
            )
            for t in bt_result.trades
        ]

        result_items[symbol] = BacktestResultItem(
            symbol=bt_result.symbol,
            initial_capital=bt_result.initial_capital,
            final_value=bt_result.final_value,
            total_return_pct=round(bt_result.total_return * 100, 2),
            sharpe_ratio=(
                round(bt_result.sharpe_ratio, 3)
                if bt_result.sharpe_ratio is not None
                else None
            ),
            max_drawdown_pct=(
                round(bt_result.max_drawdown * 100, 2)
                if bt_result.max_drawdown is not None
                else None
            ),
            win_rate_pct=(
                round(bt_result.win_rate * 100, 1)
                if bt_result.win_rate is not None
                else None
            ),
            total_trades=bt_result.total_trades,
            trades=trades,
            equity_curve=bt_result.equity_curve,
            drawdown_curve=bt_result.drawdown_curve,
            warnings=bt_result.warnings,
            benchmark_total_return_pct=(
                round(bt_result.benchmark_total_return * 100, 2)
                if bt_result.benchmark_total_return is not None
                else None
            ),
            alpha_pct=(
                round(bt_result.alpha * 100, 2)
                if bt_result.alpha is not None
                else None
            ),
            beta=round(bt_result.beta, 3) if bt_result.beta is not None else None,
            excess_return_pct=(
                round(bt_result.excess_return * 100, 2)
                if bt_result.excess_return is not None
                else None
            ),
            buy_hold_price_return_pct=(
                round(bt_result.buy_hold_price_return * 100, 2)
                if bt_result.buy_hold_price_return is not None
                else None
            ),
            buy_hold_total_return_pct=(
                round(bt_result.buy_hold_total_return * 100, 2)
                if bt_result.buy_hold_total_return is not None
                else None
            ),
            dividend_contribution_pct=(
                round(bt_result.dividend_contribution * 100, 2)
                if bt_result.dividend_contribution is not None
                else None
            ),
            data_quality=bt_result.data_quality,
            # Tier 5 cash accounting: wire the actual $ received/paid through
            # (they live on the engine's BacktestResult; the response model's
            # 0.0 defaults would otherwise silently erase real income/expense).
            dividends_received=bt_result.dividends_received,
            borrow_fees_paid=bt_result.borrow_fees_paid,
            monte_carlo=bt_result.monte_carlo,
            confidence_intervals=bt_result.confidence_intervals,
            walk_forward=bt_result.walk_forward,
            parameter_sensitivity=bt_result.parameter_sensitivity,
            data_age_minutes=bt_result.data_age_minutes,
            is_stale=bt_result.is_stale,
        )

    message = None
    if not result_items:
        # Neutral wording: this fires BOTH when the screener matched 0
        # candidates (contradictory filters) and when explicit tickers in
        # the query had no usable price data — one honest message for both.
        generic = (
            "No stocks could be backtested — either 0 candidates matched "
            "your strategy's filters or historical price data was "
            "unavailable for the requested symbols. Try a broader query or "
            "loosen the filters."
        )
        logger.warning("Backtest produced 0 results for '%s'", payload.query)
        if screen_message:
            # The screener's specific note (e.g. "Relaxed N filter(s) but
            # still found 0 stocks") is more actionable than the generic
            # copy — surface it alongside rather than discarding it.
            message = f"{screen_message} {generic}"
        else:
            message = generic
    elif screen_message:
        # The screen found candidates but had to relax filters / replace
        # data-less symbols — surface that honesty note (the relax note
        # from the screener's 0-match rescue, or the fill-in note from
        # backtest_top_results). Never silently present a relaxed backtest
        # as an exact one.
        message = screen_message

    return BacktestResponse(
        query=payload.query,
        strategy=strategy_rules,
        results=result_items,
        total_symbols_tested=len(result_items),
        message=message,
        portfolio=portfolio,
    )


# ── Tier 3: async backtest jobs (live per-symbol progress) ─────────────────
# A backtest can take minutes on a cold cache (10 years of OHLCV per symbol).
# The async path returns a job_id immediately and streams symbol-by-symbol
# completion via the engine's progress_cb, so the UI can show live progress
# instead of a 20-minute synchronous wait. Jobs live in-process (single
# uvicorn worker — run.py), keyed by a UUID; the job-monitor tables exist for
# SCHEDULED jobs, while these are per-user ad-hoc runs that belong in memory.
_BACKTEST_JOBS: Dict[str, Dict[str, Any]] = {}
_BACKTEST_JOBS_LOCK = threading.Lock()
# Jobs are in-memory, so bound them: a job older than this is dropped (the
# poller has already got its result, or it errored) and the registry is
# capped so a busy session can't grow memory unbounded. Each entry holds a
# full BacktestResponse (N equity curves + trades), so eviction matters.
_BACKTEST_JOB_TTL_SECONDS = 30 * 60
_BACKTEST_JOB_MAX_JOBS = 100


def _backtest_job_snapshot(job_id: str) -> Optional[Dict[str, Any]]:
    """Return a consistent copy of a job's state for serialization.

    Copies under the lock so the event loop never serializes a dict the
    worker thread is mid-mutation on; ``done_symbols`` is also copied so
    the response list is not a live reference the worker keeps appending to.
    """
    with _BACKTEST_JOBS_LOCK:
        job = _BACKTEST_JOBS.get(job_id)
        if not job:
            return None
        return {
            k: (list(v) if isinstance(v, list) else v)
            for k, v in job.items()
        }


def _evict_stale_backtest_jobs() -> None:
    """Drop finished/expired jobs so the in-memory registry stays bounded.

    Caller must hold ``_BACKTEST_JOBS_LOCK``. Evicts jobs older than the
    TTL first (their result was already delivered or lost on restart —
    either way the poller is gone), then trims to the newest ``MAX_JOBS``.
    """
    now = time.time()
    expired = [
        jid for jid, j in _BACKTEST_JOBS.items()
        if now - j.get("created_at", 0) > _BACKTEST_JOB_TTL_SECONDS
    ]
    for jid in expired:
        _BACKTEST_JOBS.pop(jid, None)
    # Cap: drop the OLDEST jobs beyond the cap (by creation time).
    if len(_BACKTEST_JOBS) > _BACKTEST_JOB_MAX_JOBS:
        oldest = sorted(
            _BACKTEST_JOBS,
            key=lambda jid: _BACKTEST_JOBS[jid].get("created_at", 0),
        )[: len(_BACKTEST_JOBS) - _BACKTEST_JOB_MAX_JOBS]
        for jid in oldest:
            _BACKTEST_JOBS.pop(jid, None)


# ── backtest_jobs persistence (durability layer) ────────────────────────────
# The in-memory dict is the fast path for polling; the DB table is the
# durability layer. On restart, any job with status='running' is marked
# 'interrupted' so the UI shows "Server restarted" instead of "Job not found".

def _db_backtest_job_upsert(
    job_id: str, user_email: str, query: str, status: str,
    phase: str = None, done_symbols: list = None, total_symbols: int = 0,
    result: Any = None, error: str = None,
) -> None:
    """Insert or update a backtest job row. Best-effort — DB failures must
    never crash the in-memory job flow."""
    if not settings.has_database_url:
        return
    try:
        from src.database import PostgresDB
        import json as _json
        db = PostgresDB()
        with db._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO backtest_jobs
                        (job_id, user_email, query, status, phase,
                         done_symbols, total_symbols, result, error, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, NOW())
                    ON CONFLICT (job_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        phase = EXCLUDED.phase,
                        done_symbols = EXCLUDED.done_symbols,
                        result = EXCLUDED.result,
                        error = EXCLUDED.error,
                        updated_at = NOW()
                    """,
                    (
                        job_id, user_email, query, status, phase,
                        _json.dumps(done_symbols or []), total_symbols,
                        _json.dumps(result) if result else None,
                        error,
                    ),
                )
            conn.commit()
    except Exception:
        logger.debug("backtest_jobs upsert failed for %s (best-effort)", job_id, exc_info=True)


def _fail_orphaned_backtest_jobs() -> int:
    """Mark any jobs stuck in 'running'/'queued' as 'interrupted' (server
    restarted mid-flight). Returns the count of jobs interrupted."""
    if not settings.has_database_url:
        return 0
    try:
        from src.database import PostgresDB
        db = PostgresDB()
        with db._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE backtest_jobs
                    SET status = 'interrupted',
                        error = 'Server restarted while backtest was running',
                        updated_at = NOW()
                    WHERE status IN ('running', 'queued')
                    RETURNING job_id
                    """,
                )
                orphaned = [row[0] for row in cur.fetchall()]
            conn.commit()
        if orphaned:
            logger.info("Interrupted %d orphaned backtest job(s): %s", len(orphaned), orphaned)
        return len(orphaned)
    except Exception:
        logger.debug("backtest_jobs orphan detection failed (best-effort)", exc_info=True)
        return 0


def _load_backtest_job_from_db(job_id: str) -> Optional[Dict[str, Any]]:
    """Load a job from DB when the in-memory entry was evicted or lost."""
    if not settings.has_database_url:
        return None
    try:
        from src.database import PostgresDB
        db = PostgresDB()
        with db._get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT job_id, user_email, status, phase, done_symbols, "
                    "total_symbols, result, error FROM backtest_jobs "
                    "WHERE job_id = %s",
                    (job_id,),
                )
                row = cur.fetchone()
        if not row:
            return None
        return {
            "job_id": row[0],
            "user_email": row[1],
            "status": row[2],
            "phase": row[3],
            "done_symbols": row[4] if isinstance(row[4], list) else [],
            "total_symbols": row[5] or 0,
            "result": row[6],
            "error": row[7],
        }
    except Exception:
        logger.debug("backtest_jobs DB load failed for %s", job_id, exc_info=True)
        return None


# A failed backtest must not hand the client raw exception text (golden rule
# #12): an HTTPException's detail was written FOR the user; anything else is
# logged in full and replaced with a generic sentence. Before Day 6 the async
# path stored ``str(e)`` on the job and returned it from the status endpoint.
_GENERIC_BACKTEST_ERROR = (
    "The backtest failed unexpectedly. Please try again — if it keeps happening, "
    "try a simpler strategy."
)


def _public_backtest_error(exc: Exception) -> str:
    """The message a client may see for a failed backtest."""
    if isinstance(exc, HTTPException) and exc.status_code < 500 and exc.detail:
        return str(exc.detail)[:300]
    return _GENERIC_BACKTEST_ERROR


def _start_async_backtest(
    payload: BacktestRequest,
    owner: str,
    pipeline_user: Dict[str, Any],
    user_id: Optional[int],
    anon_id: Optional[str],
) -> Dict[str, Any]:
    """Register a job owned by ``owner`` and run the pipeline in a thread.

    ``owner`` is the account email, or ``anon:<id>`` for a trial session —
    the value every later ownership check compares against. Shared by
    ``POST /backtest/async`` and ``POST /try/backtest`` so the two can never
    drift apart.
    """
    from src.analytics import track

    job_id = f"bt_{uuid.uuid4().hex[:12]}"
    job: Dict[str, Any] = {
        "job_id": job_id,
        "status": "queued",
        "phase": "interpreting",
        "done_symbols": [],
        "total_symbols": payload.top_n if not payload.symbols else len(payload.symbols),
        "result": None,
        "error": None,
        "user_email": owner,
        "created_at": time.time(),
    }
    with _BACKTEST_JOBS_LOCK:
        _evict_stale_backtest_jobs()
        _BACKTEST_JOBS[job_id] = job
    _db_backtest_job_upsert(
        job_id, owner, payload.query, "queued",
        phase="interpreting", total_symbols=job["total_symbols"],
    )
    symbols = int(job["total_symbols"])

    def _progress(symbol: str) -> None:
        """Engine progress callback: stream each completed symbol."""
        with _BACKTEST_JOBS_LOCK:
            cur = _BACKTEST_JOBS.get(job_id)
            if cur is None:
                return
            if symbol not in cur["done_symbols"]:
                cur["done_symbols"].append(symbol)
            cur["phase"] = "backtesting"
            cur["status"] = "running"

    def _run() -> None:
        started = time.monotonic()
        try:
            with _BACKTEST_JOBS_LOCK:
                _BACKTEST_JOBS[job_id]["status"] = "running"
                _BACKTEST_JOBS[job_id]["phase"] = "interpreting"
            strategy_rules, bt_results, screen_message, portfolio = _run_backtest_pipeline(
                payload, pipeline_user, progress_cb=_progress
            )
            # A plain JSON dict, not the Pydantic model: json.dumps() cannot
            # serialize the model, so the DB upsert below used to fail
            # silently and a finished result never reached backtest_jobs
            # (lost on restart, and unshareable). The dict is also exactly
            # what a poll serves, in memory or from the DB.
            result = _build_backtest_response(
                payload, strategy_rules, bt_results, screen_message, portfolio
            ).model_dump(mode="json")
            with _BACKTEST_JOBS_LOCK:
                _BACKTEST_JOBS[job_id]["status"] = "done"
                _BACKTEST_JOBS[job_id]["phase"] = "done"
                _BACKTEST_JOBS[job_id]["result"] = result
            _db_backtest_job_upsert(
                job_id, owner, payload.query, "done", phase="done", result=result,
            )
            track("backtest_completed", user_id=user_id, anon_id=anon_id,
                  props={"symbols": symbols, "mode": "async"})
            track("backtest_duration", user_id=user_id, anon_id=anon_id,
                  props={"duration_ms": int((time.monotonic() - started) * 1000),
                         "mode": "async"})
        except Exception as e:  # noqa: BLE001 — job must always finalize
            logger.error("Async backtest %s failed: %s", job_id, e, exc_info=True)
            public = _public_backtest_error(e)
            with _BACKTEST_JOBS_LOCK:
                _BACKTEST_JOBS[job_id]["status"] = "error"
                _BACKTEST_JOBS[job_id]["error"] = public
            _db_backtest_job_upsert(job_id, owner, payload.query, "error", error=public)
            track("backtest_failed", user_id=user_id, anon_id=anon_id,
                  props={"mode": "async", "reason": _backtest_failure_reason(e)})

    track("backtest_started", user_id=user_id, anon_id=anon_id,
          props={"symbols": symbols, "mode": "async"})
    threading.Thread(target=_run, daemon=True).start()
    return {
        "job_id": job_id,
        "status": "queued",
        "phase": "interpreting",
        "total_symbols": job["total_symbols"],
    }


def _job_status_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """The poll response for one job — identical for account and trial owners."""
    return {
        "job_id": snapshot["job_id"],
        "status": snapshot["status"],
        "phase": snapshot.get("phase"),
        "done_symbols": snapshot.get("done_symbols", []),
        "total_symbols": snapshot.get("total_symbols", 0),
        "error": snapshot.get("error"),
        "result": snapshot.get("result"),
    }


def _owned_job_snapshot(job_id: str, owner: str) -> Dict[str, Any]:
    """A job's state if ``owner`` owns it; 404 otherwise (never reveal others')."""
    snapshot = _backtest_job_snapshot(job_id) or _load_backtest_job_from_db(job_id)
    if not snapshot or snapshot.get("user_email") != owner:
        raise HTTPException(status_code=404, detail="Backtest job not found")
    return snapshot


@app.post(
    "/backtest/async",
    summary="Start an async backtest (live per-symbol progress)",
    tags=["Backtesting"],
)
async def backtest_async(
    request: Request,
    response: Response,
    payload: BacktestRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Start a backtest and return a job_id to poll for progress.

    The synchronous ``/backtest`` blocks for the whole run (cold-cache
    downloads can take minutes). This endpoint validates the request, kicks
    off the identical pipeline in a background thread, and returns
    immediately with ``job_id``. Poll ``GET /backtest/async/{job_id}`` for
    per-symbol status; the payload shape on completion is identical to the
    synchronous endpoint.
    """
    return _start_async_backtest(
        payload,
        owner=user.get("email") or "",
        pipeline_user=user,
        user_id=_user_id_of(user),
        anon_id=request.headers.get("x-anon-id"),
    )


@app.get(
    "/backtest/async/{job_id}",
    summary="Poll an async backtest's live progress",
    tags=["Backtesting"],
)
async def backtest_async_status(
    request: Request,
    response: Response,
    job_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Poll an async backtest job for its current progress and, when done,
    the full result (identical payload shape to ``/backtest``).
    """
    return _job_status_payload(_owned_job_snapshot(job_id, user.get("email") or ""))


# ═══════════════════════════════════════════════════════════════════════════
# Day 6 — no signup wall, and shareable proof
# ═══════════════════════════════════════════════════════════════════════════
# A stranger runs real backtests before creating an account, then shares a
# result as a link. Every wall placed before the moment of value costs roughly
# half the funnel; queries here are nearly free, so the usual reason to gate
# (cost) does not apply — but anonymous traffic is the first abuse surface a
# bot finds, so it is held to tighter limits than signed-in traffic.
#
# Three layers keep it honest:
#   1. ANON_BACKTEST_ALLOWANCE per trial session (the friendly limit — after
#      it the UI asks for an account, "after value, never before");
#   2. the "anon_backtest" per-IP scope, which a visitor cannot reset by
#      throwing the session token away;
#   3. the planet-wide backtest_llm bucket, shared with signed-in users.
ANON_BACKTEST_ALLOWANCE = 3
ANON_MAX_SYMBOLS = 5
_SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _anon_owner(aid: str) -> str:
    """The ``backtest_jobs.user_email`` value that marks a trial session's jobs."""
    from src.database import ANON_OWNER_PREFIX

    return ANON_OWNER_PREFIX + aid


def _anon_id_from(request: Request) -> Optional[str]:
    """The trial-session id from a valid ``X-Anon-Token`` header, else None."""
    from src.auth import verify_anon_token

    return verify_anon_token(request.headers.get("x-anon-token"))


def _require_anon_id(request: Request) -> str:
    aid = _anon_id_from(request)
    if not aid:
        raise HTTPException(
            status_code=401,
            detail="Start a free trial session first (POST /try/session).",
        )
    return aid


def _count_owner_backtests(owner: str) -> int:
    """Non-errored backtests ``owner`` has started — memory and DB, whichever
    is higher (memory alone forgets on restart; the DB alone lags a new job)."""
    with _BACKTEST_JOBS_LOCK:
        in_memory = sum(
            1 for j in _BACKTEST_JOBS.values()
            if j.get("user_email") == owner and j.get("status") != "error"
        )
    stored = 0
    if settings.has_database_url:
        try:
            from src.database import PostgresDB

            stored = PostgresDB().count_owner_backtests(owner)
        except Exception as e:  # noqa: BLE001 — fall back to the memory count
            logger.warning("Trial allowance lookup failed: %s", e)
    return max(in_memory, stored)


@app.post(
    "/try/session",
    summary="Start an anonymous trial session (no account needed)",
    tags=["Trial"],
)
async def try_session(request: Request, response: Response):
    """Mint a signed trial token. Send it as ``X-Anon-Token`` on ``/try/*``
    and ``/results/*`` calls. The id inside is generated here, never chosen
    by the client, so nobody can claim another visitor's results."""
    from src.auth import create_anon_token

    if not settings.has_jwt_secret:
        raise HTTPException(status_code=503, detail="Trials are not configured right now.")
    minted = create_anon_token()
    return {
        "anon_token": minted["anon_token"],
        "expires_in": minted["expires_in"],
        "allowance": ANON_BACKTEST_ALLOWANCE,
    }


@app.post(
    "/try/backtest",
    summary="Start a backtest without an account (trial allowance applies)",
    tags=["Trial"],
)
async def try_backtest(request: Request, response: Response, payload: BacktestRequest):
    """Same engine, same job flow as ``POST /backtest/async``, owned by the
    trial session. Capped at ``ANON_MAX_SYMBOLS`` symbols; after
    ``ANON_BACKTEST_ALLOWANCE`` runs it answers 403 asking for an account."""
    aid = _require_anon_id(request)
    owner = _anon_owner(aid)
    used = _count_owner_backtests(owner)
    if used >= ANON_BACKTEST_ALLOWANCE:
        raise HTTPException(
            status_code=403,
            detail=(
                f"You've used your {ANON_BACKTEST_ALLOWANCE} free backtests. Create a "
                "free account to keep going — your results come with you."
            ),
        )
    updates: Dict[str, Any] = {"top_n": min(payload.top_n, ANON_MAX_SYMBOLS)}
    if payload.symbols:
        updates["symbols"] = list(payload.symbols)[:ANON_MAX_SYMBOLS]
    payload = payload.model_copy(update=updates)
    started = _start_async_backtest(
        payload,
        owner=owner,
        pipeline_user={"email": None, "plan": "trial"},
        user_id=None,
        anon_id=request.headers.get("x-anon-id"),
    )
    started["anon_remaining"] = max(0, ANON_BACKTEST_ALLOWANCE - used - 1)
    return started


@app.get(
    "/try/backtest/{job_id}",
    summary="Poll a trial backtest",
    tags=["Trial"],
)
async def try_backtest_status(request: Request, response: Response, job_id: str):
    """Poll a job this trial session owns (404 for anyone else's)."""
    aid = _require_anon_id(request)
    return _job_status_payload(_owned_job_snapshot(job_id, _anon_owner(aid)))


@app.post(
    "/try/claim",
    summary="Move a trial session's backtests and share links into this account",
    tags=["Trial"],
)
async def try_claim(
    request: Request,
    response: Response,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Called right after signup or login with both the account token and
    the trial token, so nothing made before signing up is lost."""
    from src.database import PostgresDB

    aid = _require_anon_id(request)
    owner = _anon_owner(aid)
    email = user.get("email") or ""
    with _BACKTEST_JOBS_LOCK:
        moved = 0
        for job in _BACKTEST_JOBS.values():
            if job.get("user_email") == owner:
                job["user_email"] = email
                moved += 1
    try:
        counts = PostgresDB().claim_anonymous_work(aid, int(user["id"]), email)
    except Exception as e:  # noqa: BLE001
        logger.error("Trial claim failed for user %s: %s", user.get("id"), e)
        raise HTTPException(
            status_code=502,
            detail="Could not move your trial results right now. Try again later.",
        )
    return {
        "error": False,
        "claimed_backtests": max(moved, int(counts.get("jobs", 0))),
        "claimed_shares": int(counts.get("shares", 0)),
    }


def _share_owner(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """``(user, None)`` for an account, ``(None, anon_id)`` for a trial session.

    An account token wins when both are present. A Bearer token that fails
    validation is a 401 — never silently downgraded to the trial identity.
    """
    from src.auth import _extract_bearer_token

    if _extract_bearer_token(request):
        return get_current_user(request), None
    aid = _anon_id_from(request)
    if aid:
        return None, aid
    raise HTTPException(
        status_code=401, detail="Sign in or start a free trial to share results."
    )


def _share_url(request: Request, share_id: str) -> str:
    base = (settings.share_base_url or str(request.base_url)).rstrip("/")
    return f"{base}/r/{share_id}"


@app.post(
    "/results/share",
    summary="Publish one finished backtest as a public share link",
    tags=["Sharing"],
)
async def share_result(request: Request, response: Response, payload: ShareRequest):
    """Sharing is explicit opt-in: nothing is public until this is called.

    The public page shows a whitelisted summary built HERE from the engine's
    own stored result — the browser never supplies the numbers. Sharing the
    same job twice returns the existing link.
    """
    from src.database import PostgresDB
    from src.share_page import build_share_summary

    user, aid = _share_owner(request)
    owner = (user.get("email") or "") if user else _anon_owner(aid)
    snapshot = _owned_job_snapshot(payload.job_id, owner)
    result = snapshot.get("result")
    if snapshot.get("status") != "done" or not isinstance(result, dict):
        raise HTTPException(status_code=409, detail="This backtest hasn't finished yet.")
    if not result.get("results"):
        raise HTTPException(
            status_code=422,
            detail="There's nothing to share — this strategy matched no stocks.",
        )
    owner_user_id = int(user["id"]) if user else None
    owner_anon = None if user else aid
    try:
        db = PostgresDB()
        share_id = db.find_share_for_job(payload.job_id, owner_user_id, owner_anon)
        if not share_id:
            share_id = secrets.token_urlsafe(16)
            summary = build_share_summary(result)
            db.create_share(
                share_id, payload.job_id, owner_user_id, owner_anon,
                summary["query"], summary,
            )
    except Exception as e:  # noqa: BLE001
        logger.error("Share creation failed: %s", e)
        raise HTTPException(
            status_code=502, detail="Could not create a share link right now. Try again later."
        )
    return {"error": False, "share_id": share_id, "url": _share_url(request, share_id)}


@app.delete(
    "/results/share/{share_id}",
    summary="Revoke a share link (the link stops working for everyone)",
    tags=["Sharing"],
)
async def revoke_share(request: Request, response: Response, share_id: str):
    """Owner-only. Anyone else's request matches nothing and gets 404."""
    from src.database import PostgresDB

    user, aid = _share_owner(request)
    if not _SHARE_ID_RE.match(share_id):
        raise HTTPException(status_code=404, detail="Share link not found")
    try:
        deleted = PostgresDB().delete_share(
            share_id, int(user["id"]) if user else None, None if user else aid
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Share revoke failed: %s", e)
        raise HTTPException(
            status_code=502, detail="Could not revoke that link right now. Try again later."
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="Share link not found")
    return {"error": False, "message": "That link no longer works."}


@app.get(
    "/results/shares",
    summary="List my live share links",
    tags=["Sharing"],
)
async def list_my_shares(request: Request, response: Response):
    from src.database import PostgresDB

    user, aid = _share_owner(request)
    try:
        rows = PostgresDB().list_shares(
            int(user["id"]) if user else None, None if user else aid
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Share listing failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not load your links right now.")
    return {
        "shares": [
            {
                "share_id": r["share_id"],
                "url": _share_url(request, r["share_id"]),
                "query": r.get("query"),
                "created_at": str(r.get("created_at") or ""),
                "headline": (r.get("summary") or {}).get("headline"),
            }
            for r in rows
        ]
    }


# The page carries text a stranger wrote (the query). It is escaped, and this
# CSP is the second wall: no script of any kind may run, nothing may be
# loaded from elsewhere, and the page cannot be framed.
_SHARE_PAGE_HEADERS = {
    "Cache-Control": "public, max-age=300",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


def _load_public_share(share_id: str) -> Dict[str, Any]:
    """The stored public summary for ``share_id``, or 404."""
    from src.database import PostgresDB

    if not _SHARE_ID_RE.match(share_id):
        raise HTTPException(status_code=404, detail="Share link not found")
    try:
        row = PostgresDB().get_share(share_id)
    except Exception as e:  # noqa: BLE001
        logger.error("Share lookup failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not load this result right now.")
    if not row or not isinstance(row.get("summary"), dict):
        raise HTTPException(status_code=404, detail="Share link not found")
    return row


# Preview images are deterministic per summary, so a rendered PNG is cached
# under its content hash: repeat fetches of a popular link cost a dict lookup,
# and an attacker cannot force renders without first creating real shares
# (the "share" scope caps that). Unknown ids 404 before any rendering.
_SHARE_CARD_CACHE = TTLCache(ttl_seconds=24 * 3600, max_entries=256, name="share_card")


@app.get(
    "/r/{share_id}.png",
    summary="Public share preview image (1200×630 PNG for link cards)",
    tags=["Sharing"],
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def public_share_image(request: Request, response: Response, share_id: str):
    """The Open Graph / Twitter Card image for a share link."""
    from starlette.concurrency import run_in_threadpool

    from src.share_card import card_key, render_share_card

    row = _load_public_share(share_id)
    key = card_key(row["summary"])
    etag = f'"{key[:32]}"'
    headers = {
        "Cache-Control": "public, max-age=3600",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    png = _SHARE_CARD_CACHE.get(key)
    if png is None:
        # ~50–200 ms of CPU: off the event loop so it cannot stall other requests.
        png = await run_in_threadpool(render_share_card, row["summary"])
        _SHARE_CARD_CACHE.set(key, png)
    return Response(content=png, media_type="image/png", headers=headers)


@app.get(
    "/r/{share_id}.json",
    summary="Public share payload (JSON)",
    tags=["Sharing"],
)
async def public_share_json(request: Request, response: Response, share_id: str):
    """Exactly the whitelisted summary — no email, user id, job id or trial id."""
    row = _load_public_share(share_id)
    response.headers["Cache-Control"] = "public, max-age=300"
    return {"share_id": row["share_id"], **row["summary"]}


@app.get(
    "/r/{share_id}",
    summary="Public share page (server-rendered HTML)",
    tags=["Sharing"],
    response_class=HTMLResponse,
)
async def public_share_page(request: Request, response: Response, share_id: str):
    """Rendered on the server so link-preview crawlers see real content."""
    from src.share_page import render_share_page

    row = _load_public_share(share_id)
    page_url = _share_url(request, share_id)
    page = render_share_page(
        row["summary"],
        page_url=page_url,
        app_url=settings.app_base_url,
        image_url=f"{page_url}.png",
    )
    return HTMLResponse(page, headers=_SHARE_PAGE_HEADERS)


@app.post(
    "/backtest/export",
    summary="Backtest and download simulated trades as CSV (Tier 4 product feature)",
    tags=["Backtesting"],
    responses={
        200: {"content": {"text/csv": {}}, "description": "CSV of simulated trades"},
    },
)
async def backtest_csv(
    request: Request,
    response: Response,
    payload: BacktestRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Backtest a strategy and return the simulated trades as a CSV.

    Same pipeline as ``/backtest`` (one LLM call, warm data, identical
    overrides + validation — both live in ``_run_backtest_pipeline`` so
    this endpoint can never diverge) but the response is ``text/csv``
    with one row per CLOSED trade — symbol, side, entry/exit dates +
    prices, shares, P&L, and the exit reason (signal / stop_loss /
    take_profit / trailing_stop). This is what a paying user expects
    after reviewing a strategy: the actual fills, importable into any
    spreadsheet.
    """
    import csv
    import io

    strategy_rules, bt_results, _, _ = _run_backtest_pipeline(payload, user)

    columns = [
        "symbol", "side", "buy_date", "buy_price", "sell_date",
        "sell_price", "shares", "pnl", "pnl_pct", "exit_reason",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for bt_result in bt_results.values():
        for t in bt_result.trades:
            if t.is_open:
                continue  # only closed trades are audit-able fills
            writer.writerow({
                "symbol": t.symbol,
                "side": t.side,
                "buy_date": t.buy_date,
                "buy_price": round(t.buy_price, 2),
                "sell_date": t.sell_date or "",
                "sell_price": round(t.sell_price, 2) if t.sell_price is not None else "",
                "shares": t.shares,
                "pnl": round(t.pnl, 2) if t.pnl is not None else "",
                "pnl_pct": round(t.pnl_pct, 4) if t.pnl_pct is not None else "",
                "exit_reason": t.exit_reason,
            })

    filename = "stratoscan_backtest_trades.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post(
    "/screen/export",
    summary="Screen and download results as CSV (Tier 4 product feature)",
    tags=["Screener"],
    responses={
        200: {"content": {"text/csv": {}}, "description": "CSV of screening results"},
    },
)
async def screen_csv(request: Request, response: Response, payload: ScreenRequest):
    """Screen stocks and return the results as a downloadable CSV.

    Same pipeline as ``/screen`` (one LLM call, local DB query) but the
    response is ``text/csv`` with one row per stock — what paying users
    expect from any serious screener. Includes every numeric column the
    screener knows about, plus the composite score.
    """
    import csv
    import io

    try:
        _, report = _run_screen(
            payload.query,
            payload.limit,
            payload.offset,
            sort_by=payload.sort_by,
            sort_desc=payload.sort_desc,
        )
    except Exception as e:
        logger.error("CSV export failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="CSV export failed. Please try again later.",
        )

    columns = [
        "symbol", "company_name", "sector", "industry", "current_price",
        "price_change_pct", "market_cap", "pe_ratio", "rsi_14", "volume",
        "avg_volume", "eps_ttm", "dividend_yield", "beta", "sma_50",
        "sma_200", "high_52w", "low_52w", "pct_off_52w_high", "ps_ratio",
        "pb_ratio", "ev_ebitda", "roe_ttm", "roa_ttm", "gross_margin_ttm",
        "net_margin_ttm", "revenue_growth_ttm", "eps_growth_ttm",
        "atr_14", "score", "is_stale",
    ]

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for r in report.results:
        row = _result_to_stock(r).model_dump()
        writer.writerow(row)

    filename = "stratoscan_screen.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post(
    "/enrich",
    response_model=EnrichResponse,
    summary="Screen stocks and enrich with news/sentiment",
    tags=["Enrichment"],
)
async def enrich_stocks(request: Request, response: Response, payload: EnrichRequest):
    """Screen stocks using a natural language query, then enrich the top
    results with real-time news articles and sentiment analysis.

    Costs a fixed number of Finnhub API calls: screens enrich × 2 calls.
    """
    from src.enrichment import EnrichmentEngine

    logger.info(
        "Enrich request: '%s' (max_stocks=%d, limit=%d)",
        payload.query,
        payload.max_stocks,
        payload.limit,
    )

    # Step 1: Screen
    try:
        interpreted, report = _run_screen(payload.query, payload.limit)
    except Exception as e:
        logger.error("Screening failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Screening failed. Please try again later.",
        )

    # Step 2: Enrich
    try:
        engine = EnrichmentEngine()
        enrichment = engine.enrich_results(
            payload.query,
            report.results,
            max_stocks=payload.max_stocks,
        )
    except Exception as e:
        logger.error("Enrichment failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Enrichment failed. Please try again later.",
        )

    # Step 3: Convert enrichment data
    enrichment_dict: Dict[str, SentimentItem] = {}
    for symbol, data in enrichment.sentiments.items():
        articles = [
            NewsArticleItem(
                headline=a.headline,
                source=a.source,
                datetime=a.datetime,
                sentiment=a.sentiment,
            )
            for a in data.articles
        ]
        enrichment_dict[symbol] = SentimentItem(
            symbol=data.symbol,
            buzz=data.buzz,
            company_news_score=data.company_news_score,
            sector_news_score=data.sector_news_score,
            articles=articles,
        )

    # The embedded screening MUST mirror /screen's contract field-for-field
    # (built by the SAME helper — one construction, one contract):
    # data_quality carries the designed error note on an LLM interpretation
    # failure ("Could not interpret your query..."), applied_filters echoes
    # the enforced criteria, and sort_by/sort_desc describe the ranking.
    # next_offset comes from the same helper too, so pagination can't
    # diverge. An embed that drops these silently turns an interpretation
    # failure into a bare "0 matches" and loses the auditable filter echo
    # on success.
    return EnrichResponse(
        query=payload.query,
        screening=_build_screen_response(
            payload.query,
            interpreted,
            report,
            payload.limit,
            [_result_to_stock(r) for r in report.results],
        ),
        enrichment=enrichment_dict,
        stocks_enriched=len(enrichment_dict),
    )


@app.get(
    "/news",
    response_model=NewsFeedResponse,
    summary="Browse the latest news articles with sentiment",
    tags=["News"],
)

async def news_feed(
    request: Request,
    response: Response,
    symbol: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    """Browse recent news articles across all stocks (or one symbol).

    Reads from the dedicated news database — zero API calls. Requires
    ``NEWS_DATABASE_URL`` to be configured and the news refresh job to
    have run at least once.
    """
    from src.config import settings

    if not settings.has_news_database:
        raise HTTPException(
            status_code=503,
            detail=(
                "News database is not configured. Set NEWS_DATABASE_URL "
                "in your .env file, then run the news refresh."
            ),
        )

    from src.database import NewsDB

    db = NewsDB()
    try:
        if symbol:
            rows = db.get_articles_for_symbol(symbol, limit=limit)
            total = len(rows)
        else:
            rows = db.get_recent_articles(limit=limit)
            total = db.article_count()
    except Exception as e:
        logger.error("News feed query failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Failed to query news. Please try again later.",
        )

    articles = [
        NewsArticleItemExtended(
            symbol=r["symbol"],
            headline=r["headline"],
            summary=r.get("summary") or "",
            source=r.get("source") or "",
            url=r.get("url") or "",
            published_at=r.get("published_at"),
            sentiment=r.get("sentiment"),
            sentiment_score=r.get("sentiment_score"),
        )
        for r in rows
    ]

    return NewsFeedResponse(
        total_articles=total,
        returned=len(articles),
        articles=articles,
    )


@app.get(
    "/news/{symbol}",
    response_model=SentimentSummary,
    summary="Get one stock's sentiment + recent news",
    tags=["News"],
)

async def stock_news(request: Request, response: Response, symbol: str):
    """Fetch aggregate sentiment and recent articles for one stock.

    Reads from the dedicated news database — zero API calls.
    """
    from src.config import settings

    if not settings.has_news_database:
        raise HTTPException(
            status_code=503,
            detail=(
                "News database is not configured. Set NEWS_DATABASE_URL "
                "in your .env file."
            ),
        )

    from src.database import NewsDB

    db = NewsDB()
    try:
        sentiment = db.get_sentiment(symbol)
        articles = db.get_articles_for_symbol(symbol, limit=20)
    except Exception as e:
        logger.error("News query for %s failed: %s", symbol, e)
        raise HTTPException(
            status_code=502,
            detail="Failed to query news. Please try again later.",
        )

    if not sentiment and not articles:
        raise HTTPException(
            status_code=404,
            detail=f"No news data for '{symbol}'. Run the news refresh first.",
        )

    return SentimentSummary(
        symbol=symbol,
        buzz=(sentiment or {}).get("buzz", 0.0),
        company_news_score=(sentiment or {}).get("company_news_score", 0.0),
        sector_news_score=(sentiment or {}).get("sector_news_score", 0.0),
        article_count=(sentiment or {}).get("article_count", 0),
        updated_at=(sentiment or {}).get("updated_at"),
        articles=[
            NewsArticleItemExtended(
                symbol=r["symbol"],
                headline=r["headline"],
                summary=r.get("summary") or "",
                source=r.get("source") or "",
                url=r.get("url") or "",
                published_at=r.get("published_at"),
                sentiment=r.get("sentiment"),
                sentiment_score=r.get("sentiment_score"),
            )
            for r in articles
        ],
    )


@app.get(
    "/stocks",
    summary="List stocks in the database",
    tags=["Data"],
)

async def list_stocks(
    request: Request,
    response: Response,
    sector: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List stocks available in the database with optional sector filter.

    Returns an unfiltered list of stocks from the local database.
    Use the ``/screen`` endpoint for natural language filtering.
    """
    from src.config import settings

    if not settings.has_database_url:
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL is not configured. Set DATABASE_URL in your .env file.",
        )

    from src.database import PostgresDB
    from src.screener import ScreenerResult

    db = PostgresDB()
    filters: Dict[str, Any] = {}
    if sector:
        filters["sector"] = sector

    try:
        records = db.query_stocks(filters=filters, limit=limit, offset=offset)
        total = db.stock_count()
    except Exception as e:
        logger.error("Failed to list stocks: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Failed to query stocks. Please try again later.",
        )

    results = [ScreenerResult.from_db_record(r) for r in records]

    return {
        "total_in_database": total,
        "returned": len(results),
        "limit": limit,
        "offset": offset,
        "results": [_result_to_stock(r) for r in results],
    }


@app.get(
    "/stocks/{symbol}",
    response_model=Dict[str, Any],
    summary="Get a single stock's details",
    tags=["Data"],
)

async def get_stock(request: Request, response: Response, symbol: str):
    """Fetch full details for a single stock symbol, with sentiment.

    Includes aggregate sentiment from the news database when available.
    """
    from src.config import settings

    if not settings.has_database_url:
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL is not configured. Set DATABASE_URL in your .env file.",
        )

    from src.database import PostgresDB
    from src.screener import ScreenerResult

    db = PostgresDB()

    try:
        records = db.query_stocks(filters={"symbol": symbol}, limit=1)
    except Exception as e:
        logger.error("Failed to fetch stock %s: %s", symbol, e)
        raise HTTPException(
            status_code=502,
            detail="Failed to fetch stock. Please try again later.",
        )

    if not records:
        raise HTTPException(
            status_code=404,
            detail=f"Stock '{symbol}' not found in database",
        )

    result = _result_to_stock(ScreenerResult.from_db_record(records[0]))
    payload: Dict[str, Any] = result.model_dump()

    # Attach aggregate sentiment from the news DB (0 API calls, optional)
    if settings.has_news_database:
        try:
            from src.database import NewsDB

            news_db = NewsDB()
            sentiment = news_db.get_sentiment(symbol)
            if sentiment:
                payload["sentiment"] = {
                    "buzz": sentiment.get("buzz", 0.0),
                    "company_news_score": sentiment.get("company_news_score", 0.0),
                    "sector_news_score": sentiment.get("sector_news_score", 0.0),
                    "article_count": sentiment.get("article_count", 0),
                }
        except Exception as e:
            logger.debug("Sentiment attach failed for %s: %s", symbol, e)

    return payload


@app.post(
    "/refresh",
    summary="Trigger a one-time data refresh",
    tags=["Data"],
)

async def trigger_refresh(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """Trigger an asynchronous refresh of stock price data.

    This runs in a thread pool executor so it doesn't block the event loop.
    Fetches updated quotes and technical indicators for all US stocks via
    Finnhub, then updates the PostgreSQL database.

    **Security:** ADMIN-GATED (VULN-001 fix). Each call fires thousands of
    Finnhub requests, so an unauthenticated endpoint was a free way to
    drain the free-tier API budget. Only an authenticated admin may
    trigger a manual refresh; the global ``refresh_ops`` daily cap
    (defense-in-depth) limits the whole planet to 3/day.

    **Note:** This is a long-running operation (~5k API calls).
    Check the server logs (or ``GET /ops/jobs``) for progress.
    """
    loop = asyncio.get_event_loop()

    def _do_refresh_sync() -> None:
        """Run the full refresh synchronously (offloaded to executor)."""
        from src.data_fetcher import (
            initialize_client_pool,
            fetch_us_stock_symbols,
            refresh_price_data,
        )
        from src.database import PostgresDB
        from src.job_monitor import JobMonitor, get_job_monitor_store

        # Record this on-demand run in the job monitor (same visibility as
        # the scheduled jobs) — monitoring must never break the refresh, so
        # a monitor failure is swallowed by the run context.
        monitor = JobMonitor(get_job_monitor_store())
        try:
            with monitor.run("manual_refresh") as run:
                pool = initialize_client_pool()
                symbols = fetch_us_stock_symbols(pool)
                logger.info("Refresh: found %d symbols", len(symbols))

                records = refresh_price_data(pool, symbols)

                db = PostgresDB()
                db.bulk_upsert_stocks(records)
                run.records = len(records)
                logger.info("Refresh complete: updated %d records", len(records))
        finally:
            monitor.close()

    background_tasks.add_task(loop.run_in_executor, None, _do_refresh_sync)

    return {
        "message": "Data refresh started in the background (thread pool)",
        "status": "running",
    }


@app.get(
    "/ops/jobs",
    summary="Live status of every background job (job monitor)",
    tags=["System"],
)
async def ops_jobs(
    request: Request,
    response: Response,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """Live status of every tracked background job.

    For each job in the scheduler: latest run (status, timestamps, duration,
    record count, error), consecutive failures, and a staleness flag — a job
    whose last success is older than ``interval x stale_multiplier`` is
    flagged so a broken refresh is visible immediately. Also reports
    scheduler liveness via the throttled heartbeat beacon.

    **Security:** ADMIN-GATED (VULN-003 fix) — this exposes internal job
    telemetry incl. error strings; it is operator data, not public data.
    The admin dashboard reads it via the role-gated ``/admin/jobs`` route.

    Reads from the job monitor (Neon ``job_runs`` table when configured,
    in-memory fallback otherwise). Zero API calls.
    """
    from src.job_monitor import JobMonitor, get_job_monitor_store

    monitor = JobMonitor(get_job_monitor_store())
    try:
        return monitor.summary()
    finally:
        monitor.close()


@app.get(
    "/ops/jobs/{job_name}",
    summary="Recent run history for one background job",
    tags=["System"],
)
async def ops_job_history(
    request: Request,
    response: Response,
    job_name: str,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """Recent run history (newest first) for a single background job.

    Every row is one execution: status, start/finish timestamps, duration,
    records processed, and the error message on failure.

    **Security:** ADMIN-GATED (VULN-003 fix) — per-job error strings are
    internal telemetry, not public data.

    Args:
        job_name: One of the known job names, e.g. ``price_refresh``,
            ``technical_refresh``, ``fundamental_refresh``,
            ``news_refresh``, ``manual_refresh``.
    """
    from src.job_monitor import JOB_DEFINITIONS, JobMonitor, get_job_monitor_store

    if job_name not in JOB_DEFINITIONS:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown job '{job_name}'. Known jobs: "
                f"{', '.join(sorted(JOB_DEFINITIONS))}"
            ),
        )

    monitor = JobMonitor(get_job_monitor_store())
    try:
        runs = monitor.history(job_name, limit=50)
    finally:
        monitor.close()

    return {
        "job_name": job_name,
        "label": JOB_DEFINITIONS[job_name]["label"],
        "schedule": JOB_DEFINITIONS[job_name]["schedule"],
        "runs": runs,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Admin Dashboard (role-gated by require_admin)
# ═══════════════════════════════════════════════════════════════════════════════


@app.get(
    "/admin/overview",
    response_model=AdminOverviewResponse,
    summary="Aggregated system state for the admin dashboard",
    tags=["Admin"],
)
async def admin_overview(
    request: Request,
    response: Response,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """Landing-page payload for the admin dashboard.

    Aggregates everything an operator needs on one screen: server uptime,
    database health + latency, stock-universe size + freshness, user
    counts (total/admins/verified/plan mix), live job-monitor status, and
    any configuration warnings. Every value is a cheap local read or a
    single query — zero external API calls, so refreshing the dashboard
    costs nothing.
    """
    from src.config import validate_config
    from src.database import PostgresDB
    from src.job_monitor import JobMonitor, get_job_monitor_store

    uptime = None
    if _start_time:
        uptime = round((datetime.now() - _start_time).total_seconds(), 1)

    # Database health + universe stats (best-effort: degrade gracefully).
    db_state: Dict[str, Any] = {"configured": settings.has_database_url}
    stocks: Dict[str, Any] = {}
    users: Dict[str, Any] = {}
    if settings.has_database_url:
        try:
            db = PostgresDB()
            db_state["alive"] = db.health_check()["alive"]
            db_state["latency_ms"] = db.health_check()["latency_ms"]
            stocks["count"] = db.stock_count()
            stocks["freshness"] = db.data_freshness()
            users = db.user_stats()
        except Exception as e:
            logger.warning("Admin overview DB gather failed: %s", e)
            db_state["alive"] = False
            db_state["error"] = str(e)[:120]

    # Live job monitor summary (staleness flags + scheduler liveness).
    monitor = JobMonitor(get_job_monitor_store())
    try:
        jobs = monitor.summary()
    finally:
        monitor.close()

    return AdminOverviewResponse(
        status="ok",
        uptime_seconds=uptime,
        database=db_state,
        stocks=stocks,
        users=users,
        jobs=jobs,
        warnings=validate_config(),
        config={
            "finnhub_keys": settings.finnhub_key_count,
            "screener_llm": bool(settings.screener_llm_key),
            "backtest_llm": bool(settings.backtest_llm_key),
            "database": settings.has_database_url,
            "news_database": settings.has_news_database,
            "backtest_database": settings.has_backtest_database,
            "redis_rate_limit": settings.has_upstash_redis,
            "email_verification": settings.has_email_verification,
            "admin_emails": settings.admin_email_list,
        },
    )


@app.get(
    "/admin/users",
    response_model=AdminUsersResponse,
    summary="List users (paginated, searchable) — admin only",
    tags=["Admin"],
)
async def admin_users(
    request: Request,
    response: Response,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    search: Optional[str] = Query(default=None, max_length=254),
    admin: Dict[str, Any] = Depends(require_admin),
):
    """List accounts for the admin dashboard's user-management table.

    Newest first, with optional case-insensitive email search and the same
    limit/offset pagination contract as /screen. Never returns password
    hashes.
    """
    from src.database import PostgresDB

    try:
        db = PostgresDB()
        rows = db.list_users(limit=limit, offset=offset, search=search)
        total = db.count_users(search=search)
    except Exception as e:
        logger.error("Admin user list failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Failed to list users. Please try again later.",
        )

    return AdminUsersResponse(
        total=total,
        limit=limit,
        offset=offset,
        users=[
            AdminUserOut(
                id=r["id"],
                email=r["email"],
                plan=r.get("plan", "free"),
                email_verified=bool(r.get("email_verified")),
                is_admin=bool(r.get("is_admin")),
                created_at=r.get("created_at"),
            )
            for r in rows
        ],
    )


@app.patch(
    "/admin/users/{user_id}",
    response_model=AdminUserOut,
    summary="Update a user (plan / admin role / verified) — admin only",
    tags=["Admin"],
)
async def admin_update_user(
    request: Request,
    response: Response,
    user_id: int,
    payload: AdminUserUpdate,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """Change a user's plan, admin role, or email-verification state.

    Guards:
      - Only the fields present in the body are touched (partial PATCH).
      - An admin can never revoke their OWN admin role — that would lock
        the last operator out of the dashboard.
      - The account must exist (404 otherwise).
    """
    from src.database import PostgresDB

    if payload.plan is None and payload.is_admin is None and payload.email_verified is None:
        raise HTTPException(status_code=422, detail="Nothing to update. Send plan, is_admin, or email_verified.")

    # Self-demotion guard: the acting admin must not strip their own role.
    if payload.is_admin is False and int(admin["id"]) == user_id:
        raise HTTPException(
            status_code=400,
            detail="You cannot revoke your own admin role.",
        )

    try:
        db = PostgresDB()
        current = db.get_user_by_id(user_id)
        if not current:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

        if payload.plan is not None:
            db.set_user_plan(user_id, payload.plan)
        if payload.is_admin is not None:
            db.set_user_admin(user_id, payload.is_admin)
        if payload.email_verified is True:
            # Only the promote direction is supported via the dashboard
            # (marking verified); un-verifying is intentionally not exposed.
            db.mark_email_verified(user_id)

        updated = db.get_user_by_id(user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Admin user update failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail="Failed to update user. Please try again later.",
        )

    logger.info(
        "Admin %s updated user %d (plan=%s, is_admin=%s, verified=%s)",
        admin.get("email"), user_id, payload.plan, payload.is_admin, payload.email_verified,
    )
    return AdminUserOut(
        id=updated["id"],
        email=updated["email"],
        plan=updated.get("plan", "free"),
        email_verified=bool(updated.get("email_verified")),
        is_admin=bool(updated.get("is_admin")),
        created_at=updated.get("created_at"),
    )


@app.get(
    "/admin/jobs",
    response_model=Dict[str, Any],
    summary="Job monitor summary — admin only",
    tags=["Admin"],
)
async def admin_jobs(
    request: Request,
    response: Response,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """Live job-monitor summary for the admin dashboard's jobs panel.

    Same payload as ``GET /ops/jobs`` — which is itself admin-gated, not
    public — under an ``/admin``-prefixed path so the dashboard can fetch all
    of its panels from one namespace. Staleness flags, consecutive failures
    and the scheduler heartbeat are never exposed to anonymous users.
    """
    from src.job_monitor import JobMonitor, get_job_monitor_store

    monitor = JobMonitor(get_job_monitor_store())
    try:
        return monitor.summary()
    finally:
        monitor.close()


@app.get(
    "/admin/events",
    response_model=Dict[str, Any],
    summary="Recent product events, newest first — admin only",
    tags=["Admin"],
)
async def admin_events(
    request: Request,
    response: Response,
    limit: int = 100,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """The raw event stream — how the owner walks the funnel.

    No aggregation on purpose: the point of this read-out is to see the events
    land in the order they actually happened (landing → signup → screen →
    backtest → share → backtest again), which an aggregate would hide. Each
    row is ``{name, occurred_at, anon_id, user_id, props}``.

    ``limit`` is clamped here rather than validated into a 422 — an operator
    pasting a silly number should get data, not an error page. Returns
    ``{count, events}``; the wrapper matches every other ``/admin`` payload,
    and ``count`` answers "did all fifteen land" without a second call.
    """
    from src.analytics import recent_events

    try:
        events = recent_events(limit=max(1, min(int(limit), 500)))
    except Exception as e:
        # Degrade to an empty list rather than 500: the dashboard is the one
        # place an operator goes when things are broken, so it must not be the
        # one place that breaks too.
        logger.warning("Admin events read failed: %s", e)
        events = []
    return {"count": len(events), "events": events}


@app.get(
    "/admin/errors",
    response_model=Dict[str, Any],
    summary="Recent unhandled errors, newest first — admin only",
    tags=["Admin"],
)
async def admin_errors(
    request: Request,
    response: Response,
    limit: int = 50,
    admin: Dict[str, Any] = Depends(require_admin),
):
    """What has been breaking, without shelling into the machine.

    One row per unhandled exception: when, which method, the route TEMPLATE
    (never the raw URL — a query string can carry a token), the exception type
    and the scrubbed trace. Returns ``{count, errors}``; ``limit`` is clamped
    rather than validated into a 422, same as ``/admin/events``.
    """
    from src.database import PostgresDB

    try:
        errors = PostgresDB().list_errors(limit=max(1, min(int(limit), 200)))
    except Exception as e:
        logger.warning("Admin errors read failed: %s", e)
        errors = []
    return {"count": len(errors), "errors": errors}


@app.get(
    "/admin",
    response_class=Response,
    summary="Admin dashboard (HTML)",
    tags=["Admin"],
    include_in_schema=False,
)
async def admin_dashboard(request: Request, response: Response):
    """Serve the self-contained admin dashboard (single HTML file).

    The page itself is public so the login form can render; every data
    endpoint it calls (/admin/*) is admin-gated. The dashboard stores the
    JWT in localStorage and sends it as a Bearer token.
    """
    from src.admin_dashboard import DASHBOARD_HTML

    return Response(
        content=DASHBOARD_HTML,
        media_type="text/html",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Error Handlers
# ═══════════════════════════════════════════════════════════════════════════════


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc: HTTPException):
    """Standard JSON error response for HTTP errors."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": True,
            "detail": exc.detail,
            "status_code": exc.status_code,
        },
    )


def _error_route_template(request) -> str:
    """The route TEMPLATE that raised — never the raw URL.

    ``/backtest/async/{job_id}``, not ``/backtest/async/bt_9f3a?token=…``. Two
    reasons, and the second is the important one: a concrete path would make
    every job id its own row in the error table, and a query string can carry a
    credential. What an operator needs is "which endpoint keeps breaking".

    Falls back to the bare path (query string stripped) when no route matched —
    a 404 path, or an error raised before routing happened.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if template:
        return template
    return request.url.path


def _capture_error(request, exc: Exception) -> None:
    """Persist one unhandled exception to ``error_log``, scrubbed.

    Never raises and never touches the response: the client still receives the
    same generic 500 envelope it always did (golden rule #12 — no raw exception
    text leaves the process), and a failure to WRITE the row must not turn one
    500 into a different one. The trace is scrubbed by ``analytics.scrub``
    (emails, JWTs, Bearer headers) and length-capped before it reaches the
    table; request bodies and headers are never stored at all.
    """
    try:
        import traceback

        from src.analytics import scrub
        from src.database import PostgresDB

        trace = scrub(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        )
        PostgresDB().insert_error(
            method=request.method,
            route=_error_route_template(request),
            error_type=type(exc).__name__,
            trace=trace,
        )
    except Exception as write_failure:  # noqa: BLE001 — must never propagate
        logger.warning("Could not persist error_log row: %s", write_failure)


@app.exception_handler(Exception)
async def generic_exception_handler(request, exc: Exception):
    """Catch-all error handler — logs, records, and returns a generic 500.

    The client-facing body is unchanged from before Day 4 (a fixed detail
    string, no exception text); what is new is the ``error_log`` row, which is
    how "find out when something breaks" stops meaning "read the logs on the
    machine".
    """
    logger.exception("Unhandled exception: %s", exc)
    _capture_error(request, exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "detail": "Internal server error. Please try again later.",
            "status_code": 500,
        },
    )
