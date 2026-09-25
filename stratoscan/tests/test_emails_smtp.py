"""Hermetic tests for the SMTP transport in src/emails.py — no socket, no network.

``SMTP_HOST`` selects the transport, and the two branches have genuinely
different failure modes, so both are pinned here. ``smtplib.SMTP`` is replaced
wholesale: no relay is ever contacted (there is none), because the thing under
test is the branching and the message, not the wire.

Why the real ``settings`` object is patched attribute-by-attribute instead of
replaced with a ``MagicMock``: a Mock's attributes are TRUTHY, so
``if settings.smtp_host:`` would take the SMTP branch no matter what the test
intended. Patching the real object leaves every unpatched setting at its real
value, which is what makes the "SMTP_HOST empty" case below meaningful.

The contract that matters most is the return value. ``/auth/register`` treats a
failed send as NON-FATAL and reports it as ``email_sent``, so an exception
escaping this module turns a working signup into a 500. Every failure mode is
therefore asserted to degrade to ``False`` rather than raise.

Run: python -m tests.test_emails_smtp
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ.setdefault("JWT_SECRET", "smtp-sender-dummy-secret")
os.environ["SENDGRID_API_KEY"] = "SG.testkey123"
os.environ["EMAIL_FROM_ADDRESS"] = "noreply@stratoscan.app"

from src.config import settings  # noqa: E402
from src import emails as emails_mod  # noqa: E402
from src.emails import MAIL_TIMEOUT, SENDGRID_MAIL_URL, send_email  # noqa: E402

SENDGRID_KEY = "SG.testkey123"

# The configuration every case starts from — mailpit's real values, since that
# is the setup this transport exists for. The SendGrid key is EMPTY because the
# local stack runs without one, which is what proves the branch is chosen on
# SMTP_HOST alone rather than by "no key configured".
BASE = {
    "smtp_host": "mailpit",
    "smtp_port": 1025,
    "smtp_username": "",
    "smtp_password": "",
    "smtp_starttls": False,
    "sendgrid_api_key": "",
}


class _FakeResponse:
    """Stand-in for ``urllib``'s response — ``send_email`` uses ``with
    urlopen(...)``, so the fake must support the context-manager protocol."""

    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture_sendgrid(request, timeout=10):
    """Record what the SendGrid path would have posted, then succeed."""
    _captured["url"] = request.full_url
    _captured["body"] = json.loads(request.data.decode("utf-8"))
    return _FakeResponse()


@contextmanager
def _smtp(urlopen=None, **overrides):
    """Patch the SMTP settings, the socket, and ``urlopen`` for one case.

    ``urlopen`` raises by default: any case that reaches the SendGrid path by
    accident fails loudly here instead of silently making a network call.

    Yields:
        ``(SMTP mock, urlopen mock)`` so each case can assert on the transport
        it did NOT take.
    """
    if urlopen is None:
        urlopen = AssertionError("SendGrid must not be reached")
    with mock.patch.multiple(settings, **dict(BASE, **overrides)), mock.patch(
        "src.emails.smtplib.SMTP"
    ) as smtp_mock, mock.patch("urllib.request.urlopen", side_effect=urlopen) as urlopen_mock:
        yield smtp_mock, urlopen_mock


# ═══════════════════════════════════════════════════════════════════════════
# 1. SMTP_HOST set → SMTP, and the SendGrid path is never reached
# ═══════════════════════════════════════════════════════════════════════════
print("1. SMTP_HOST set → the SMTP branch wins...")
with _smtp() as (smtp_mock, urlopen_mock):
    ok = send_email("user@example.com", "Subject line", "plain body")
assert ok is True, "SMTP send must report success"
smtp_mock.assert_called_once_with("mailpit", 1025, timeout=MAIL_TIMEOUT)
smtp_mock.return_value.__enter__().send_message.assert_called_once()
urlopen_mock.assert_not_called()
print("✅ SMTP_HOST set → smtplib.SMTP('mailpit', 1025), one send_message, zero SendGrid calls")
print("✅ works with an EMPTY SendGrid key (the local stack's configuration)")


# ═══════════════════════════════════════════════════════════════════════════
# 2. SMTP_HOST empty → SendGrid, and no socket is ever opened
# ═══════════════════════════════════════════════════════════════════════════
print("2. SMTP_HOST empty → the SendGrid branch is untouched...")
_captured: dict = {}
with _smtp(smtp_host="", sendgrid_api_key=SENDGRID_KEY, urlopen=_capture_sendgrid) as (
    smtp_mock,
    _,
):
    ok = send_email("user@example.com", "Subject line", "plain body")
assert ok is True, "the SendGrid path must still succeed unchanged"
smtp_mock.assert_not_called()
assert _captured["url"] == SENDGRID_MAIL_URL, _captured["url"]
assert _captured["body"]["content"][0]["value"] == "plain body"
print("✅ SMTP_HOST empty → SendGrid POST /v3/mail/send, smtplib.SMTP never constructed")


# ═══════════════════════════════════════════════════════════════════════════
# 3. AUTH is opt-in, and credentials are never sent in the clear
# ═══════════════════════════════════════════════════════════════════════════
print("3. AUTH only when SMTP_USERNAME is set...")
with _smtp() as (smtp_mock, _):
    assert send_email("user@example.com", "s", "b") is True
smtp_mock.return_value.__enter__().login.assert_not_called()
print("✅ blank SMTP_USERNAME → login() not called (mailpit needs no AUTH)")

with _smtp(smtp_username="apikey", smtp_password="s3cret", smtp_starttls=True) as (smtp_mock, _):
    assert send_email("user@example.com", "s", "b") is True
authed = smtp_mock.return_value.__enter__()
authed.login.assert_called_once_with("apikey", "s3cret")
print("✅ SMTP_USERNAME set → login() called with both credentials")

# STARTTLS must precede AUTH: authenticating over a cleartext socket leaks the
# password to anyone on the path — the one ordering bug that matters here.
order = [c[0] for c in authed.mock_calls if c[0] in ("starttls", "login", "send_message")]
assert order == ["starttls", "login", "send_message"], order
print("✅ order is starttls → login → send_message (no credentials before the upgrade)")


# ═══════════════════════════════════════════════════════════════════════════
# 4. STARTTLS is opt-in
# ═══════════════════════════════════════════════════════════════════════════
print("4. STARTTLS only when SMTP_STARTTLS is true...")
with _smtp() as (smtp_mock, _):
    assert send_email("user@example.com", "s", "b") is True
smtp_mock.return_value.__enter__().starttls.assert_not_called()
print("✅ SMTP_STARTTLS=false → starttls() not called (correct for a local relay on 1025)")

with _smtp(smtp_starttls=True) as (smtp_mock, _):
    assert send_email("user@example.com", "s", "b") is True
smtp_mock.return_value.__enter__().starttls.assert_called_once()
print("✅ SMTP_STARTTLS=true → starttls() called once")


# ═══════════════════════════════════════════════════════════════════════════
# 5. The message that reaches the relay
# ═══════════════════════════════════════════════════════════════════════════
print("5. Message shape...")
with _smtp() as (smtp_mock, _):
    assert send_email(
        "user@example.com", "Verify your Stratoscan account", "plain body",
        html_body="<p>plain body</p>",
    ) is True
msg = smtp_mock.return_value.__enter__().send_message.call_args.args[0]
assert msg["To"] == "user@example.com", msg["To"]
assert msg["Subject"] == "Verify your Stratoscan account", msg["Subject"]
assert msg["From"] == "noreply@stratoscan.app", msg["From"]
assert msg.get_content_type() == "multipart/alternative", msg.get_content_type()
parts = msg.get_payload()
assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"], [
    p.get_content_type() for p in parts
]
bodies = [p.get_payload(decode=True).decode() for p in parts]
assert "plain body" in bodies[0] and "plain body" in bodies[1], bodies
print("✅ To / Subject / From set; text body first, then the HTML alternative")

with _smtp() as (smtp_mock, _):
    assert send_email("user@example.com", "s", "text only") is True
plain = smtp_mock.return_value.__enter__().send_message.call_args.args[0]
assert plain.get_content_type() == "text/plain", plain.get_content_type()
print("✅ no html_body → a plain text/plain message, not an empty alternative")


# ═══════════════════════════════════════════════════════════════════════════
# 6. Every failure degrades to False — the contract /auth/register relies on
# ═══════════════════════════════════════════════════════════════════════════
print("6. Failures return False and never propagate...")
with _smtp() as (smtp_mock, _):
    smtp_mock.return_value.__enter__().send_message.side_effect = OSError(
        "relay went away mid-message"
    )
    assert send_email("user@example.com", "s", "b") is False
print("✅ send_message raises → False (no exception escapes to the caller)")

with mock.patch.multiple(settings, **BASE), mock.patch(
    "src.emails.smtplib.SMTP", side_effect=OSError("Name or service not known")
):
    assert send_email("user@example.com", "s", "b") is False
print("✅ unreachable relay (constructor raises) → False")

with _smtp(smtp_username="u", smtp_password="p") as (smtp_mock, _):
    smtp_mock.return_value.__enter__().login.side_effect = OSError("535 auth failed")
    assert send_email("user@example.com", "s", "b") is False
print("✅ AUTH rejected → False")

with _smtp(smtp_starttls=True) as (smtp_mock, _):
    smtp_mock.return_value.__enter__().starttls.side_effect = OSError("no STARTTLS offered")
    assert send_email("user@example.com", "s", "b") is False
print("✅ STARTTLS refused → False")

# A malformed recipient must degrade, not raise. `email.message` raises at
# header ASSIGNMENT (its linefeed/injection defence), which is before any
# socket exists — so this only returns False because construction happens
# inside the try. /auth/register does not wrap this call and the user row is
# already committed, so an escaping exception is a 500 on a created account.
with _smtp() as (smtp_mock, _):
    assert send_email("a@b.test\nBcc: x@y.test", "s", "b") is False
    smtp_mock.assert_not_called()  # never got as far as a connection
print("✅ header-injection attempt → False, no connection, no exception")

# The return value is not the whole contract: the recipient also reaches the LOG,
# and `to_email` is attacker-controlled (RegisterRequest.email is a bare str with
# no format validation). Formatted with %s a raw newline forges log lines under
# LOG_FORMAT=text; %r escapes it inside quotes. Assert on the RENDERED record —
# the call args still hold the original unescaped value, which is why inspecting
# them alone would prove nothing.
with mock.patch.object(emails_mod.logger, "warning") as log_warning:
    with _smtp():
        assert send_email("a@b.test\nBcc: x@y.test", "s", "b") is False
log_warning.assert_called_once()
_fmt, *_fmt_args = log_warning.call_args.args
_rendered = _fmt % tuple(_fmt_args)
assert "\n" not in _rendered, f"raw newline written to the log record: {_rendered!r}"
assert "\\n" in _rendered, f"expected an escaped newline: {_rendered!r}"
print("✅ a linefeed in the recipient is escaped in the log record, never written raw")


# ═══════════════════════════════════════════════════════════════════════════
# 7. mail_transport — the single owner both gates read
# ═══════════════════════════════════════════════════════════════════════════
print("7. mail_transport names the selected transport...")
with mock.patch.multiple(
    settings, smtp_host="mailpit", sendgrid_api_key="SG.key", email_from_address="a@b.test"
):
    assert settings.mail_transport == "smtp", settings.mail_transport
    # SMTP_HOST wins over a configured key: it is what src/emails.py dispatches on.
print("✅ SMTP_HOST set (key also present) → 'smtp' — agrees with the dispatch")

with mock.patch.multiple(
    settings, smtp_host="", sendgrid_api_key="SG.key", email_from_address="a@b.test"
):
    assert settings.mail_transport == "sendgrid", settings.mail_transport
print("✅ SMTP_HOST empty + key set → 'sendgrid'")

with mock.patch.multiple(
    settings, smtp_host="", sendgrid_api_key="", alert_email="ops@x.test"
):
    assert settings.mail_transport == "", settings.mail_transport
    assert settings.has_email_verification is False
    assert settings.alerts_enabled is False
print("✅ neither transport → '' → verification and alerts both off")

# The alerts bug this pins: alerts_enabled used to require SENDGRID_API_KEY,
# so an SMTP-only deployment delivered verification mail and paged nobody.
with mock.patch.multiple(
    settings,
    smtp_host="mailpit",
    sendgrid_api_key="",
    email_from_address="noreply@stratoscan.test",
    alert_email="ops@x.test",
    alert_webhook_url="",
):
    assert settings.alerts_enabled is True
print("✅ SMTP-only + ALERT_EMAIL (no SendGrid key) → alerts_enabled True")

print()
print("🎯 All SMTP transport tests pass!")
