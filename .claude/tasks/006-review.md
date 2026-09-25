# Review — `task/006-smtp-sender` @ `a86e1e8`

**Verdict: one fix before merge.** The transport is well built, the E2E proof is stronger
than the spec asked for, and the self-audit was honest — including flagging its own
flawed first regression probe rather than burying it. But the `never raises` contract the
spec called load-bearing is violated on a reachable input, and the new suite cannot catch
it by construction.

## What I verified independently

| Check | Result |
|---|---|
| Files changed | 8, exactly the spec's list; `src/api.py`, `src/alerts.py` untouched |
| Backend suites | **41/41** |
| `validate_env_template()` | complete — all five settings documented |
| `requirements*.txt` | 0 lines changed |
| The MagicMock guard | present at `test_email_verification.py:152`, one line, no restructure |
| Dispatch shape | select-then-dispatch, not try-SendGrid-then-fall-back, as specified |

The `mail_transport` diagnosis in the audit is correct and I am acting on it (below). The
`has_email_verification` widening was necessary, not scope creep: with `SENDGRID_API_KEY`
empty locally, the old `bool(sendgrid_api_key and email_from_address)` gate made the
spec's own `email_sent: true` unreachable. Flagging it against yourself anyway was right.

## The defect — `send_email` raises, and register 500s with the account already created

`_send_via_smtp` builds the message **before** the `try`:

```python
    message = EmailMessage()
    message["To"] = to_email          # ← outside the try
    ...
    try:
        with smtplib.SMTP(...)
```

Python's `email` library refuses to serialise a header containing a linefeed — correctly,
that is the header-injection defence — but it raises at **assignment**, not at send:

```
RAISED: ValueError - Header values may not contain linefeed or carriage return characters
contract VIOLATED: send_email must never raise
SMTP constructed: False
```

`/auth/register` does not wrap the call (`api.py`, `if verification_token:
email_sent = send_verification_email(...)`), so this reaches `generic_exception_handler`
and returns **500** — *after* the user row has been committed. The account exists and the
caller is told registration failed.

It is reachable: `RegisterRequest.email` is `email: str` with a length bound and **no
format validation at all** — no `EmailStr`, no regex. I checked.

To be clear about what this is and is not: it is **not** a header-injection vulnerability.
Python blocks the forgery. It is a crash where the contract promised a `False`.

### Why 41/41 did not catch it

The new suite pins four failure modes — `send_message` raising, the constructor raising,
AUTH rejected, STARTTLS refused. Every one of them happens *inside* the try. So the suite
proves the `except` clause works; it never tests the claim on the tin, which is that the
function does not raise. A green suite and a broken contract, which is the same species of
"looks healthy" this project keeps producing.

### The fix

Move the construction inside the existing `try`. It is an indent change plus one line:

```python
    try:
        message = EmailMessage()
        message["To"] = to_email
        message["From"] = settings.email_from_address
        message["Subject"] = subject
        message.set_content(text_body)
        if html_body:
            message.add_alternative(html_body, subtype="html")
        with smtplib.SMTP(...) as smtp:
            ...
```

Then add one assertion to `tests/test_emails_smtp.py`, which is the part that matters more
than the fix:

```python
# A malformed recipient must degrade, not raise: /auth/register does not wrap
# this call, and the account row is already committed by the time it runs.
with mock.patch.multiple(settings, smtp_host="mailpit", smtp_port=1025,
                         email_from_address="noreply@x.test"):
    with mock.patch("src.emails.smtplib.SMTP") as s:
        assert send_email("a@b.test\nBcc: x@y.test", "s", "b") is False
        assert not s.called          # never got as far as a connection
print("✅ header-injection attempt → False, no connection, no exception")
```

## Also fix now, since it is the same two files

The audit's own top finding, and it is right: **`alerts_enabled` still encodes the
SendGrid-only rule**, so an SMTP-only deployment delivers verification mail and cannot
page anyone. Do it here rather than as its own task — it is one predicate in a file
already open, and leaving a known functional falsehood in the tree to be fixed "next
pass" is how it becomes permanent.

Take the `mail_transport` design from the audit, as the single owner:

```python
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
```

Then both predicates consume it:

```python
return bool(self.mail_transport and self.email_from_address)          # has_email_verification
return bool(self.alert_email and self.mail_transport and self.email_from_address)  # alerts_enabled
```

`src/emails.py` keeps reading `settings.smtp_host` directly — that is deliberate and the
spec's reason stands (a bare attribute is what an existing test can neutralise in one
line; a property on a `MagicMock` is truthy and cannot be).

Pin it: `alerts_enabled` is True with `smtp_host` + `alert_email` + `email_from_address`
and no SendGrid key; `mail_transport` returns `"smtp"` when both are configured.
`tests/test_metrics_alerts.py` is the suite that covers alerts — check it still passes.

Also rename `SENDGRID_TIMEOUT` → `MAIL_TIMEOUT` (1 def, 3 refs). It times SMTP now, and
the comment `# shared outbound-mail timeout` is currently apologising for the name.

## Not doing

- **The `Snippet` fragility** you flagged on the mail check — correct call to leave it.
  It fails loudly with a message naming what is missing, and the fix is one request if it
  ever bites. Noted, not scheduled.
- **The unexercised SendGrid branch against a live API.** Agreed it is the remaining
  correctness gap, but the probe belongs in `run_probes.py` against staging, not in this
  branch. Separate task.
- **Prose volume.** Settled in the 005 review; it stays, and you were right not to
  re-litigate it.

## Merge

Apply the contract fix + its assertion, the `mail_transport` owner, and the timeout
rename. Re-run the full Verify block including `make verify`. Then this merges.
