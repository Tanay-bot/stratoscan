"""Alerting for Stratoscan (stdlib-only, fire-and-forget).

Delivers one-line alerts when a background job fails or the scheduler goes
stale — so an operator gets paged at 3am instead of discovering a dead
refresh on Monday. Two delivery channels (at least one must be configured
in .env, or every function is a no-op):

  - **Webhook** (``ALERT_WEBHOOK_URL``): a Slack/Discord/ntfy-compatible
    incoming webhook. Preferred when set.
  - **Email** (``ALERT_EMAIL`` + a mail transport): delivered through the
    existing stdlib-only sender in ``src/emails.py`` (the same one that
    sends verification emails), over whichever transport
    ``settings.mail_transport`` resolves to — SendGrid's API, or SMTP when
    ``SMTP_HOST`` is set. Used when no webhook is configured.

Design (the Stratoscan way — near-zero cost, never breaks the job):
  - **Fire-and-forget**: the POST/send runs in a daemon thread; the caller
    (scheduler / job monitor) returns instantly. Alerting can never block
    a refresh job or the API.
  - **Throttled per key**: at most one alert per ``throttle_key`` per
    ``ALERT_WEBHOOK_MIN_INTERVAL_SECONDS`` (default 15 min). A job that
    fails every 15 minutes produces one alert, not 1,440, and a broken
    webhook can't cause an alert storm.
  - **Silent degradation**: an unreachable webhook or failed email send is
    logged at WARNING and never raises into the caller.
  - **Opt-in**: with neither channel configured, every function is a no-op
    returning False — zero overhead in dev.

The webhook payload uses the Slack "incoming webhook" JSON shape, which
Discord and ntfy both accept (ntfy maps ``text``/``title``; Discord maps
``content``). A generic ``POST {text, title, level}`` is also appended
for custom receivers.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_sent: dict = {}  # throttle_key -> epoch seconds of the last dispatch


def _dispatch_webhook(title: str, message: str, level: str) -> None:
    """POST the alert payload to the configured webhook (blocking)."""
    payload = {
        "text": f"[{level.upper()}] {title}\n{message}",
        "title": title,
        "level": level,
        "ts": int(time.time()),
        # Slack-style attachment so Discord/ntfy render it nicely too.
        "attachments": [
            {
                "color": "danger" if level == "error" else "warning",
                "title": title,
                "text": message,
                "ts": int(time.time()),
            }
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        settings.alert_webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def _dispatch_email(title: str, message: str, level: str) -> None:
    """Send the alert through the configured mail transport (blocking).

    Reuses ``src.emails.send_email`` — the same stdlib-only sender that
    handles verification mail, over SendGrid's API or SMTP per
    ``SMTP_HOST`` — so alerts need no new dependency, and nothing beyond a
    recipient (``ALERT_EMAIL``) on top of the transport and
    ``EMAIL_FROM_ADDRESS`` that ``settings.alerts_enabled`` already gates on.
    """
    from src.emails import send_email

    subject = f"[STRATOSCAN {level.upper()}] {title}"
    text = f"{message}\n\nts={int(time.time())}"
    html = (
        "<p><strong>Stratoscan alert</strong></p>"
        f"<p>{message}</p>"
        f"<p><small>level: {level} · ts: {int(time.time())}</small></p>"
    )
    sent = send_email(settings.alert_email, subject, text, html_body=html)
    if not sent:
        raise RuntimeError(f"send_email returned False for {settings.alert_email}")


def _dispatch(title: str, message: str, level: str) -> None:
    """Deliver through the configured channel (webhook preferred)."""
    if settings.alert_webhook_url:
        _dispatch_webhook(title, message, level)
        return
    _dispatch_email(title, message, level)


def send_alert(
    title: str,
    message: str = "",
    level: str = "warning",
    throttle_key: Optional[str] = None,
) -> bool:
    """Send a fire-and-forget alert (throttled per key).

    Args:
        title: Short alert subject (e.g. ``"price_refresh failed"``).
        message: Optional detail (error text, counts).
        level: ``"warning"`` or ``"error"`` (colors the payload).
        throttle_key: Cooldown key. Defaults to the title. Use a stable
            key (e.g. ``"job:price_refresh"``) so a repeating failure
            alerts at most once per interval instead of every cycle.

    Returns:
        True if the alert was dispatched (throttle passed + a channel is
        configured), False otherwise. Note: returns True after handing
        off to the background thread — it does NOT mean the webhook or
        email service accepted it.
    """
    if not settings.alerts_enabled:
        return False

    key = throttle_key or title
    interval = max(1, settings.alert_webhook_min_interval_seconds)
    now = time.time()

    with _lock:
        last = _last_sent.get(key, 0.0)
        if now - last < interval:
            return False
        _last_sent[key] = now

    def _worker() -> None:
        try:
            _dispatch(title, message, level)
            logger.info("Alert dispatched (%s): %s",
                        "webhook" if settings.alert_webhook_url else "email",
                        title)
        except Exception as e:
            # Silent degradation — never let alerting break the caller.
            logger.warning("Alert dispatch failed (%s): %s", title, e)

    threading.Thread(target=_worker, name="alert-dispatch", daemon=True).start()
    return True
