"""Email service for Stratoscan (stdlib-only: SendGrid Web API or SMTP).

Two transports, one contract. SendGrid's REST endpoint (``POST
/v3/mail/send``) is called directly via ``urllib``; SMTP goes through
``smtplib`` — neither needs an SDK or a mail library, matching the
project's zero-dependency philosophy (JWT via hmac, Redis via urllib,
etc.). ``SMTP_HOST`` selects between them: set it to route mail over SMTP
(a local mail catcher in the dev stack), leave it blank to use SendGrid.

Signup verification: every new account gets an emailed verification link.

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
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage
from typing import Optional

from src.config import settings

logger = logging.getLogger(__name__)

# SendGrid Web API v3 — transactional mail endpoint.
SENDGRID_MAIL_URL = "https://api.sendgrid.com/v3/mail/send"

# Timeout for both transports: mail is accepted in well under a second; a
# generous cap keeps a slow network from hanging a register request.
MAIL_TIMEOUT: float = 10.0


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
    """Send a transactional email over the configured transport.

    SMTP when ``SMTP_HOST`` is set, otherwise the SendGrid Web API. Both
    paths honour the same contract, so no caller branches on the transport.

    SendGrid path, pure stdlib: builds the JSON payload, POSTs it to
    ``/v3/mail/send`` with ``Authorization: Bearer <SENDGRID_API_KEY>``.

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
    if settings.smtp_host:
        return _send_via_smtp(to_email, subject, text_body, html_body)

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
        with urllib.request.urlopen(request, timeout=MAIL_TIMEOUT) as resp:
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


def build_password_reset_url(token: str) -> str:
    """Build the emailed password-reset link (frontend origin + token).

    The frontend's ``/reset-password`` route renders the new-password form
    and POSTs the token to ``/auth/password-reset/confirm``.
    """
    base = settings.app_base_url.rstrip("/")
    return f"{base}/reset-password?token={token}"


def send_password_reset_email(to_email: str, token: str) -> bool:
    """Email a single-use password-reset link.

    Only ever called for an address that has an account; the API returns the
    same response either way, so this email's existence is the only signal,
    and it goes to the address's owner.

    Returns:
        True if the transport accepted the mail, False otherwise.
    """
    url = build_password_reset_url(token)
    minutes = settings.password_reset_token_ttl_minutes
    subject = "Reset your Stratoscan password"
    text = (
        "Someone asked to reset the password for this Stratoscan account.\n\n"
        "To choose a new password, open this link:\n"
        f"{url}\n\n"
        f"This link expires in {minutes} minutes and works once. Requesting "
        "another link cancels this one.\n\n"
        "If this wasn't you, ignore this email — your password has not changed.\n"
    )
    html = (
        "<p>Someone asked to reset the password for this Stratoscan account.</p>"
        f'<p><a href="{url}" style="background:#4f46e5;color:#fff;'
        'padding:10px 18px;border-radius:6px;text-decoration:none;">'
        "Choose a new password</a></p>"
        f"<p>Or open this link: <a href=\"{url}\">{url}</a></p>"
        f"<p>This link expires in {minutes} minutes and works once. Requesting "
        "another link cancels this one.</p>"
        "<p>If this wasn't you, ignore this email — your password has not changed.</p>"
    )
    return send_email(to_email, subject, text, html_body=html)


def send_password_changed_email(to_email: str) -> bool:
    """Tell the account owner their password changed and sessions were ended.

    Sent after a reset or an in-app change. It is the owner's only warning
    if someone else did it, so it says what happened and what to do.

    Returns:
        True if the transport accepted the mail, False otherwise.
    """
    base = settings.app_base_url.rstrip("/")
    subject = "Your Stratoscan password was changed"
    text = (
        "The password for this Stratoscan account was just changed, and every "
        "device that was signed in has been signed out.\n\n"
        "If this was you, there is nothing to do.\n\n"
        "If it wasn't, reset your password now:\n"
        f"{base}/forgot-password\n"
    )
    html = (
        "<p>The password for this Stratoscan account was just changed, and "
        "every device that was signed in has been signed out.</p>"
        "<p>If this was you, there is nothing to do.</p>"
        f'<p>If it wasn\'t, <a href="{base}/forgot-password">reset your password now</a>.</p>'
    )
    return send_email(to_email, subject, text, html_body=html)


def _send_via_smtp(
    to_email: str,
    subject: str,
    text_body: str,
    html_body: Optional[str] = None,
) -> bool:
    """Send a transactional email over SMTP (``smtplib``, stdlib).

    Selected by ``SMTP_HOST`` — see ``send_email`` for the dispatch.

    Args:
        to_email: Recipient address.
        subject: Email subject line.
        text_body: Plain-text body.
        html_body: Optional HTML body. Attached as an ``alternative``
            part, which yields ``multipart/alternative`` with the
            plain-text part FIRST — the ordering clients use to pick a
            rendering.

    Returns:
        True if the relay accepted the message; False on any failure
        (unreachable host, TLS/AUTH failure, timeout). Never raises: the
        same contract as the SendGrid path, because ``/auth/register``
        treats a failed send as non-fatal and reports ``email_sent``.
    """
    try:
        # Construction stays INSIDE the try on purpose: email.message raises
        # at header ASSIGNMENT for a linefeed in the recipient (its
        # injection defence), which is before any socket exists. Hoist it
        # back out and a malformed address escapes as a 500 from
        # /auth/register — after the user row is already committed.
        message = EmailMessage()
        message["To"] = to_email
        message["From"] = settings.email_from_address
        message["Subject"] = subject
        message.set_content(text_body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

        with smtplib.SMTP(
            settings.smtp_host,
            settings.smtp_port,
            timeout=MAIL_TIMEOUT,
        ) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except Exception as e:
        # %r, not %s: to_email is attacker-controlled, and under LOG_FORMAT=text
        # its newlines would forge log lines.
        logger.warning("SMTP send failed for %r: %s", to_email, e)
        return False
    logger.info("Email sent via SMTP to %s", to_email)
    return True
