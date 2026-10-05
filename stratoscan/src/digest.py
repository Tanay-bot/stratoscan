"""The weekly strategy digest (Day 10: a reason to come back).

People test ideas in bursts and then go quiet for weeks. This job gives the
product a reason to reach out that the user did not have to start: once a
week, every saved strategy is re-run over the period since it was saved, and
its owner gets one email saying what it would have done — good news or bad.

Three parts, each injectable so the whole job runs hermetically in tests:

- :func:`evaluate_strategy` — the light re-run. The SAVED rules (the engine's
  own strategy echo, captured at save time) are traded on each saved symbol
  from the save date to today, with pre-save bars used only as indicator
  warm-up. No LLM call: the rules are stored, not re-interpreted. The
  current candidate list comes from the screener (0 API calls) to say which
  saved stocks no longer match.
- :func:`compose_email` — plain words, the bad news included: a strategy that
  trailed SPY says so in the subject line.
- :func:`run_digest` — the batch, with the failure modes designed out:
    * a session advisory lock, so two runs never overlap (a crashed holder's
      session ends and the lock goes with it);
    * a per-(user, ISO week) claim taken BEFORE sending, so a re-run, a
      restart or a crash can never send twice;
    * the opt-out re-read immediately before each send, so an unsubscribe is
      honoured mid-batch;
    * a hard cap on sends per run (SendGrid's free tier is ~100/day and
      verification mail shares it);
    * a circuit breaker: after N consecutive provider failures the run
      stops — no send storm into an outage; the failed claims are retried
      on a later run that week, at most DIGEST_MAX_ATTEMPTS times;
    * per-user isolation: one malformed strategy produces an honest line in
      that user's email, and one user's crash cannot kill the batch.
"""

from __future__ import annotations

import html
import logging
import statistics
import urllib.parse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

# Saved stocks re-run per strategy, and strategies per email.
MAX_SYMBOLS_PER_STRATEGY = 5
MAX_STRATEGIES_PER_EMAIL = 3
# Under this many calendar days since saving, there is nothing to report.
MIN_DAYS_TO_REPORT = 5
# Indicator warm-up before the save date (the 200-day average needs it).
WARMUP_DAYS = 420
# Consecutive provider failures that stop a run.
CIRCUIT_BREAKER_FAILURES = 3

DISCLAIMER = (
    "Simulated on historical prices, including fees and slippage. Past performance "
    "does not predict future results. This is not investment advice."
)


def iso_week_key(now: datetime) -> str:
    """'2026-W41' — the unit of "at most one digest per user"."""
    year, week, _ = now.astimezone(timezone.utc).isocalendar()
    return f"{year}-W{week:02d}"


# ═══════════════════════════════════════════════════════════════════════════
# Evaluate one saved strategy
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class StrategyUpdate:
    strategy_id: Any
    query: str
    saved_on: Optional[str]          # YYYY-MM-DD
    days: int = 0                    # calendar days since saving
    status: str = "ok"               # ok | too_early | no_data | error
    return_pct: Optional[float] = None   # equal-weight average across priced symbols
    spy_pct: Optional[float] = None
    trades: int = 0
    priced: List[str] = field(default_factory=list)
    best: Optional[tuple] = None     # (symbol, return_pct)
    worst: Optional[tuple] = None
    still_match: List[str] = field(default_factory=list)
    no_longer_match: List[str] = field(default_factory=list)
    new_matches: List[str] = field(default_factory=list)
    screen_checked: bool = False


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _period_return(df: Any, since: str) -> Optional[float]:
    """Close-to-close return from the first bar on/after ``since`` to the last."""
    try:
        window = df.loc[since:]
        if len(window) < 2:
            return None
        first, last = float(window["Close"].iloc[0]), float(window["Close"].iloc[-1])
        return None if first <= 0 else (last / first - 1) * 100
    except Exception:
        return None


def evaluate_strategy(
    saved: Dict[str, Any],
    *,
    load_prices: Callable[[str, str, str], Any],
    screen_now: Optional[Callable[[str], Optional[List[str]]]],
    today: date,
) -> StrategyUpdate:
    """What ``saved`` would have done since it was saved. Never raises."""
    from src.backtester import run_backtest

    query = str(saved.get("query") or "")[:300]
    saved_on = _as_date(saved.get("saved_at"))
    upd = StrategyUpdate(
        strategy_id=saved.get("id"),
        query=query,
        saved_on=saved_on.isoformat() if saved_on else None,
    )
    try:
        rules = saved.get("rules")
        symbols = saved.get("symbols")
        if (
            saved_on is None
            or not isinstance(rules, dict)
            or not isinstance(rules.get("buy_rules"), list)
            or not isinstance(rules.get("sell_rules"), list)
            or not isinstance(symbols, list)
        ):
            upd.status = "error"
            return upd
        symbols = [s for s in symbols if isinstance(s, str) and s][:MAX_SYMBOLS_PER_STRATEGY]
        upd.days = max(0, (today - saved_on).days)

        # Which saved stocks still match today (0 API calls: the screener).
        if screen_now is not None:
            try:
                current = screen_now(query)
                if current is not None:
                    upd.screen_checked = True
                    now_set = set(current)
                    upd.still_match = [s for s in symbols if s in now_set]
                    upd.no_longer_match = [s for s in symbols if s not in now_set]
                    upd.new_matches = [s for s in current if s not in set(symbols)][:5]
            except Exception as e:  # noqa: BLE001 — a screen failure is a missing line, not a missing email
                logger.warning("Digest screen failed for strategy %s: %s", upd.strategy_id, e)

        if upd.days < MIN_DAYS_TO_REPORT:
            upd.status = "too_early"
            return upd

        since = saved_on.isoformat()
        warmup = (saved_on - timedelta(days=WARMUP_DAYS)).isoformat()
        end = today.isoformat()
        returns: Dict[str, float] = {}
        for sym in symbols:
            df = load_prices(sym, warmup, end)
            if df is None or len(df) == 0:
                continue
            res = run_backtest(
                sym, rules, warmup, end, df=df.loc[warmup:end],
                signal_start_date=since, compute_robustness=False,
            )
            if res.final_value <= 0 and res.total_trades == 0:
                continue  # the engine's "no usable data" result
            returns[sym] = res.total_return * 100
            upd.trades += int(res.total_trades)
        spy = load_prices("SPY", warmup, end)
        upd.spy_pct = _period_return(spy, since) if spy is not None else None

        if not returns:
            upd.status = "no_data"
            return upd
        upd.priced = sorted(returns)
        upd.return_pct = statistics.fmean(returns.values())
        upd.best = max(returns.items(), key=lambda kv: kv[1])
        upd.worst = min(returns.items(), key=lambda kv: kv[1])
        return upd
    except Exception as e:  # noqa: BLE001 — one strategy must never sink the email
        logger.warning("Digest evaluation failed for strategy %s: %s", upd.strategy_id, e)
        upd.status = "error"
        return upd


# ═══════════════════════════════════════════════════════════════════════════
# Compose the email
# ═══════════════════════════════════════════════════════════════════════════

def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _short(q: str, n: int = 48) -> str:
    q = " ".join(q.split())
    return q if len(q) <= n else q[: n - 1].rstrip() + "…"


def _fmt_day(iso: Optional[str]) -> str:
    try:
        d = date.fromisoformat(iso or "")
        return f"{d.strftime('%b')} {d.day}"
    except ValueError:
        return "when you saved it"


def describe_update(u: StrategyUpdate) -> List[str]:
    """Plain-English lines for one strategy — honest whichever way it went."""
    lines: List[str] = []
    since = f"Since you saved it on {_fmt_day(u.saved_on)} ({u.days} days)"
    if u.status == "error":
        lines.append(
            "We couldn't re-run this one — its saved settings are unreadable. "
            "Open it and save it again to keep getting updates."
        )
    elif u.status == "too_early":
        lines.append(f"Saved {u.days} day{'s' if u.days != 1 else ''} ago — too soon to say anything useful. Next week.")
    elif u.status == "no_data":
        lines.append(f"{since}: we couldn't get prices for its stocks this week. We'll try again next week.")
    else:
        n = len(u.priced)
        lines.append(
            f"{since}: {_pct(u.return_pct)} on average across its {n} stock{'s' if n != 1 else ''}."
        )
        if u.spy_pct is not None and u.return_pct is not None:
            gap = u.return_pct - u.spy_pct
            verdict = (
                f"It beat the market by {abs(gap):.1f} points." if gap > 0.05
                else f"It trailed the market by {abs(gap):.1f} points." if gap < -0.05
                else "It matched the market."
            )
            lines.append(f"SPY did {_pct(u.spy_pct)} over the same days. {verdict}")
        else:
            lines.append("SPY's return for the same days wasn't available, so there's no comparison this week.")
        if u.trades == 0:
            lines.append("No trades fired — the entry rules never triggered, so it sat in cash.")
        elif u.best and u.worst and u.best[0] != u.worst[0]:
            lines.append(
                f"Best: {u.best[0]} {_pct(u.best[1])}. Worst: {u.worst[0]} {_pct(u.worst[1])}."
            )
    if u.screen_checked and u.status != "error":
        if u.no_longer_match and not u.still_match:
            lines.append(
                "None of the stocks you saved match this strategy any more"
                + (f" — today it picks {', '.join(u.new_matches)}." if u.new_matches else ".")
            )
        elif u.no_longer_match:
            lines.append(
                f"No longer a match: {', '.join(u.no_longer_match)}."
                + (f" New matches: {', '.join(u.new_matches)}." if u.new_matches else "")
            )
        elif u.still_match:
            lines.append("Every stock you saved still matches.")
    return lines


def compose_email(
    updates: Sequence[StrategyUpdate],
    *,
    app_url: str,
    unsubscribe_url: str,
    preferences_url: str,
) -> Dict[str, str]:
    """``{"subject", "text", "html"}`` for one user's weekly digest."""
    scored = [u for u in updates if u.status == "ok" and u.return_pct is not None]
    if len(updates) == 1:
        u = updates[0]
        if u in scored and u.spy_pct is not None:
            word = "ahead of" if u.return_pct > u.spy_pct else "behind"
            subject = f"“{_short(u.query)}”: {_pct(u.return_pct)} since you saved it, {word} SPY"
        elif u in scored:
            subject = f"“{_short(u.query)}”: {_pct(u.return_pct)} since you saved it"
        else:
            subject = f"Your weekly update on “{_short(u.query)}”"
    else:
        up = sum(1 for u in scored if u.return_pct > 0)
        down = sum(1 for u in scored if u.return_pct <= 0)
        subject = (
            f"Your strategies this week: {up} up, {down} down"
            if scored else "Your weekly strategy update"
        )

    base = app_url.rstrip("/")
    blocks_text, blocks_html = [], []
    for u in updates:
        link = f"{base}/app/backtest?q={urllib.parse.quote(u.query)}"
        lines = describe_update(u)
        blocks_text.append("\n".join([f"“{u.query}”", *lines, f"Full result: {link}"]))
        blocks_html.append(
            '<div style="margin:0 0 20px;padding:14px 16px;border:1px solid #e3e5ea;border-radius:10px">'
            f'<p style="margin:0 0 8px;font-weight:600">“{html.escape(u.query)}”</p>'
            + "".join(f'<p style="margin:0 0 6px">{html.escape(line)}</p>' for line in lines)
            + f'<p style="margin:10px 0 0"><a href="{html.escape(link, quote=True)}">Open the full result</a></p>'
            "</div>"
        )
    footer = (
        f"{DISCLAIMER}\n\nYou get this because you saved strategies on Stratoscan. "
        f"Email preferences: {preferences_url}\nUnsubscribe from weekly updates: {unsubscribe_url}"
    )
    text = "Here's what your saved strategies did this week.\n\n" + "\n\n".join(blocks_text) + "\n\n—\n" + footer
    body_html = (
        '<div style="font-family:Inter,Arial,sans-serif;font-size:15px;line-height:1.5;color:#14141f;max-width:600px">'
        "<p>Here's what your saved strategies did this week.</p>"
        + "".join(blocks_html)
        + f'<p style="font-size:12px;color:#5b6275">{html.escape(DISCLAIMER)}</p>'
        '<p style="font-size:12px;color:#5b6275">You get this because you saved strategies on Stratoscan. '
        f'<a href="{html.escape(preferences_url, quote=True)}">Email preferences</a> · '
        f'<a href="{html.escape(unsubscribe_url, quote=True)}">Unsubscribe</a></p></div>'
    )
    return {"subject": subject, "text": text, "html": body_html}


# ═══════════════════════════════════════════════════════════════════════════
# The batch
# ═══════════════════════════════════════════════════════════════════════════

class DigestStore(Protocol):
    def digest_lock(self) -> Any: ...
    def digest_candidates(self, week_key: str, limit: int) -> List[Dict[str, Any]]: ...
    def get_weekly_digest(self, user_id: int) -> Optional[bool]: ...
    def claim_digest(self, user_id: int, week_key: str) -> bool: ...
    def mark_digest(self, user_id: int, week_key: str, status: str) -> None: ...
    def list_strategies(self, user_id: int) -> List[Dict[str, Any]]: ...


SendFn = Callable[..., bool]
EvaluateFn = Callable[[Dict[str, Any]], StrategyUpdate]


@dataclass
class DigestReport:
    week: str = ""
    locked_out: bool = False
    candidates: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    aborted: bool = False
    misconfigured: bool = False
    previews: List[Dict[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        if self.misconfigured:
            return "SHARE_BASE_URL is unset — nothing sent (no working unsubscribe link)"
        if self.locked_out:
            return "another digest run holds the lock — skipped"
        return (
            f"{self.week}: {self.sent} sent, {self.failed} failed, {self.skipped} skipped, "
            f"{self.errors} errors of {self.candidates} due"
            + (" — STOPPED: email provider failing" if self.aborted else "")
        )


def run_digest(
    store: DigestStore,
    *,
    send: SendFn,
    evaluate: EvaluateFn,
    now: datetime,
    max_sends: int,
    app_url: str,
    links: Callable[[int], Dict[str, str]],
    dry_run: bool = False,
    only_email: Optional[str] = None,
    entitled: Optional[Callable[[int], bool]] = None,
) -> DigestReport:
    """Run one digest pass. See the module docstring for the guarantees.

    ``entitled(user_id)`` — Day 12: monitoring emails are a gated capability,
    so each user's plan is checked (fresh, right before sending) like the
    opt-out is. None = everyone is entitled (tests of the send mechanics).
    """
    report = DigestReport(week=iso_week_key(now))
    with store.digest_lock() as held:
        if not held:
            report.locked_out = True
            return report
        # A single-user run (--user) scans every due user for the one address,
        # so a full queue cannot hide it behind the per-run cap.
        limit = 100_000 if only_email else max(1, max_sends)
        candidates = store.digest_candidates(report.week, limit)
        if only_email:
            candidates = [c for c in candidates if str(c.get("email", "")).lower() == only_email.lower()]
        report.candidates = len(candidates)
        consecutive_failures = 0

        for user in candidates:
            if report.sent + report.failed >= max_sends:
                break
            uid = int(user["id"])
            email = str(user["email"])
            try:
                # Re-read right before sending: an unsubscribe that landed
                # after the candidate list was built is still honoured.
                if not store.get_weekly_digest(uid):
                    report.skipped += 1
                    continue
                if entitled is not None and not entitled(uid):
                    report.skipped += 1
                    continue
                strategies = store.list_strategies(uid)[:MAX_STRATEGIES_PER_EMAIL]
                if not strategies:
                    report.skipped += 1
                    continue
                updates = [evaluate(s) for s in strategies]
                urls = links(uid)
                message = compose_email(
                    updates, app_url=app_url,
                    unsubscribe_url=urls["unsubscribe"], preferences_url=urls["preferences"],
                )
            except Exception as e:  # noqa: BLE001 — isolate the user, keep the batch
                logger.error("Digest build failed for user %s: %s", uid, e)
                report.errors += 1
                continue

            if dry_run:
                report.previews.append({"to": email, **message})
                continue
            if not store.claim_digest(uid, report.week):
                report.skipped += 1  # already sent / in flight this week
                continue

            headers = {
                "List-Unsubscribe": f"<{urls['unsubscribe_one_click']}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
            try:
                ok = bool(send(email, message["subject"], message["text"],
                               html_body=message["html"], headers=headers))
            except Exception as e:  # noqa: BLE001 — a raising transport is a failed send
                logger.error("Digest send raised for user %s: %s", uid, e)
                ok = False
            store.mark_digest(uid, report.week, "sent" if ok else "failed")
            if ok:
                report.sent += 1
                consecutive_failures = 0
            else:
                report.failed += 1
                consecutive_failures += 1
                if consecutive_failures >= CIRCUIT_BREAKER_FAILURES:
                    report.aborted = True
                    logger.error(
                        "Digest stopped after %d consecutive send failures — provider down?",
                        consecutive_failures,
                    )
                    break
    return report


# ═══════════════════════════════════════════════════════════════════════════
# Production wiring
# ═══════════════════════════════════════════════════════════════════════════

def run_weekly_digest(*, dry_run: bool = False, only_email: Optional[str] = None) -> DigestReport:
    """The scheduler/CLI entry point, wired to Postgres, the engine and mail."""
    from src.auth import create_unsubscribe_token
    from src.backtester import BacktestEngine
    from src.config import settings
    from src.database import PostgresDB
    from src.emails import send_email
    from src.interpreter import interpret_query
    from src.screener import StockScreener

    # The unsubscribe link is an API route, and the scheduler has no request
    # to take an origin from. Without SHARE_BASE_URL the link would point at
    # the frontend and 404 — an email whose unsubscribe link is dead must
    # never go out. A dry run still previews, with a visible placeholder.
    api = settings.share_base_url.rstrip("/")
    if not api:
        if not dry_run:
            logger.error("Weekly digest not sent: SHARE_BASE_URL (the API's public origin) is unset")
            return DigestReport(week=iso_week_key(datetime.now(timezone.utc)), misconfigured=True)
        api = "https://SHARE_BASE_URL-unset"

    db = PostgresDB()
    engine = BacktestEngine()
    today = datetime.now(timezone.utc).date()

    def screen_now(query: str) -> Optional[List[str]]:
        iq = interpret_query(query)
        if iq.error:
            return None
        return [r.symbol for r in StockScreener().screen(iq, limit=50).results]

    def evaluate(saved: Dict[str, Any]) -> StrategyUpdate:
        return evaluate_strategy(saved, load_prices=engine.load_prices,
                                 screen_now=screen_now, today=today)

    def links(uid: int) -> Dict[str, str]:
        token = urllib.parse.quote(create_unsubscribe_token(uid))
        return {
            "unsubscribe": f"{api}/email/unsubscribe?token={token}",
            "unsubscribe_one_click": f"{api}/email/unsubscribe?token={token}",
            "preferences": f"{settings.app_base_url.rstrip('/')}/app/settings#email",
        }

    try:
        def entitled(uid: int) -> bool:
            from src.entitlements import MONITORING_EMAILS, plan_for

            user = db.get_user_by_id(uid)
            return bool(user) and plan_for(user).allows(MONITORING_EMAILS)

        return run_digest(
            db, send=send_email, evaluate=evaluate, now=datetime.now(timezone.utc),
            max_sends=settings.digest_max_sends_per_run, app_url=settings.app_base_url,
            links=links, dry_run=dry_run, only_email=only_email, entitled=entitled,
        )
    finally:
        # Let Neon suspend: nothing stays open after the run.
        for closer in (db, getattr(engine, "_db", None)):
            try:
                if closer is not None and hasattr(closer, "close"):
                    closer.close()
            except Exception:
                pass
