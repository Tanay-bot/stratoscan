"""Email service for Stratoscan (SendGrid Web API, stdlib-only).

Signup verification: every new account gets an emailed verification link.
SendGrid's REST endpoint (``POST /v3/mail/send``) is called directly via
``urllib`` — no SDK dependency, matching the project's zero-dependency
philosophy (JWT via hmac, Redis via urllib, etc.).

Flow:
  1. ``/auth/register`` creates the account with ``email_verified=False``,
     generates a random one-time token and stores it + its expiry.
  2. ``send_verification_email(email, token)`` mails a link built from
     ``settings.app_base_url`` (your frontend origin) that calls
     ``GET /auth/verify-email?token=...``.
  3. The API flips ``email_verified`` on a valid, unexpired token.

Failure is never fatal to registration: if SendGrid is unconfigured or the
API call fails, the account is still created (the email is a UX nicety, not
a precondition). The caller decides how to surface the send status — the
register endpoint returns it as ``email_sent`` so the frontend can show a
\"check your inbox\" banner or a \"resend\" button.

Cost discipline: emails are sent ONLY at signup and on explicit resend —
never per login. Free-tier SendGrid (100 emails/day) covers the signup
volume.
"""

from __future__ import annotations

import json
import logging
import secrets
import urllib.error
import urllib.request
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)

# SendGrid Web API v3 — transactional mail endpoint.
SENDGRID_MAIL_URL = "https://api.sendgrid.com/v3/mail/send"

# Request timeout: SendGrid accepts a message in well under a second; a
# generous cap keeps a slow network from hanging a register request.
SENDGRID_TIMEOUT: float = 10.0


def generate_verification_token() -> str:
    """Return a cryptographically random one-time verification token.

    Uses ``secrets.token_urlsafe`` (32 bytes → 43 URL-safe chars), the
    same source of randomness as password salts. Tokens are stored in the
    users table and consumed (nulled) on successful verification.

    Returns:
        A URL-safe random string safe to embed in a query parameter.
    """
    return secrets.token_urlsafe(32)


def build_verification_url(token: str) -> str:
    """Build the emailed verification link (frontend origin + token).

    The link points at ``settings.app_base_url`` (e.g.
    ``https://app.stratoscan.com/verify-email?token=...``). The frontend
    route renders a success/expired page and calls the backend's
    ``GET /auth/verify-email?token=...`` to actually verify.

    Args:
        token: The one-time verification token.

    Returns:
        The absolute verification URL.
    """
    base = settings.app_base_url.rstrip("/")
    return f"{base}/verify-email?token={token}"


def send_email(
    to_email: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
) -> bool:
    """Send a transactional email through the SendGrid Web API.

    Pure stdlib: builds the JSON payload, POSTs it to ``/v3/mail/send``
    with ``Authorization: Bearer <SENDGRID_API_KEY>``.

    Args:
        to_email: Recipient address.
        subject: Email subject line.
        text_body: Plain-text body.
        html_body: Optional HTML body (plain text is always included as a
            fallback for mail clients that block HTML).

    Returns:
        True if SendGrid accepted the message (HTTP 202); False on any
        failure (unconfigured key, network error, API error). Never raises
        — callers use the boolean to decide how to degrade.
    """
    if not settings.sendgrid_api_key:
        logger.warning(
            "SendGrid not configured (SENDGRID_API_KEY missing) — email to %s skipped",
            to_email,
        )
        return False

    content: list = [{"type": "text/plain", "value": text_body}]
    if html_body:
        content.append({"type": "text/html", "value": html_body})

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": settings.email_from_address},
        "subject": subject,
        "content": content,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        SENDGRID_MAIL_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.sendgrid_api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=SENDGRID_TIMEOUT) as resp:
            # SendGrid returns 202 Accepted on success (mail is queued).
            if resp.status != 202:
                logger.warning(
                    "SendGrid unexpected status %s for %s",
                    resp.status,
                    to_email,
                )
                return False
            logger.info("Verification email queued for %s", to_email)
            return True
    except urllib.error.HTTPError as e:
        # 403 = sender not verified in the SendGrid dashboard; 401 = bad key.
        logger.warning(
            "SendGrid HTTP %s sending to %s: %s",
            e.code,
            to_email,
            e.read().decode("utf-8", errors="replace")[:300],
        )
        return False
    except Exception as e:
        logger.warning("SendGrid send failed for %s: %s", to_email, e)
        return False


def send_verification_email(
    to_email: str,
    token: str,
    frontend_base: Optional[str] = None,
) -> bool:
    """Email a verification link for a newly-registered account.

    Args:
        to_email: The new account's email address.
        token: The one-time verification token (stored on the user row).
        frontend_base: Override for ``settings.app_base_url`` (used by
            hermetic tests; defaults to the configured value).

    Returns:
        True if SendGrid accepted the mail, False otherwise (caller logs
        or surfaces ``email_sent`` in the API response).
    """
    if frontend_base is not None:
        base = frontend_base.rstrip("/")
        url = f"{base}/verify-email?token={token}"
    else:
        url = build_verification_url(token)

    subject = "Verify your Stratoscan account"
    text = (
        "Welcome to Stratoscan!\n\n"
        "Please verify your email address by opening this link:\n"
        f"{url}\n\n"
        "This link expires in "
        f"{settings.email_verification_token_ttl_hours} hours.\n\n"
        "If you didn't create a Stratoscan account, you can ignore this email.\n"
    )
    html = (
        "<p>Welcome to Stratoscan!</p>"
        "<p>Please verify your email address by clicking the button below:</p>"
        f'<p><a href="{url}" style="background:#4f46e5;color:#fff;'
        'padding:10px 18px;border-radius:6px;text-decoration:none;">'
        "Verify my email</a></p>"
        f"<p>Or open this link: <a href=\"{url}\">{url}</a></p>"
        f"<p>This link expires in {settings.email_verification_token_ttl_hours} hours.</p>"
        "<p>If you didn't create a Stratoscan account, you can ignore this email.</p>"
    )
    return send_email(to_email, subject, text, html_body=html)
