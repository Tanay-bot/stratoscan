# 006 — Give `send_email` an SMTP transport so mailpit stops being a prop

## Goal

The local stack provisions a mailbox the application cannot reach.

`src/emails.py` POSTs to `https://api.sendgrid.com/v3/mail/send` over HTTPS and has no
SMTP path anywhere (`grep -rn "smtp" src/` returns nothing). So mailpit runs, its UI
answers on :8025, and nothing will ever arrive in it. Task 005 documented that honestly
rather than papering over it, but it means the stack's central claim — *arranged the way
production is arranged* — is false for the mail path, and registration is the one flow
you cannot exercise end to end locally.

Add a second transport to `send_email`, selected by config. When `SMTP_HOST` is set, send
via `smtplib`; otherwise the SendGrid path behaves exactly as it does today. Then point
the local stack at mailpit and prove a real message lands in it.

**This is the first task allowed to edit `stratoscan/src/`.** Task 005's non-goals
forbade it, which is why the gap is still open; that restriction does not apply here.

## Files

- `stratoscan/src/emails.py` *(the transport branch)*
- `stratoscan/src/config.py` *(five new settings)*
- `stratoscan/.env.example` *(golden rule #4 — see Pattern)*
- `stratoscan/tests/test_emails_smtp.py` *(new)*
- `stratoscan/tests/test_email_verification.py` *(one guard line — see the trap below)*
- `.env.local.example` *(point it at mailpit; drop the "nothing arrives" warning)*
- `docker-compose.yml` *(drop the KNOWN GAP comment on the mailpit service)*
- `Makefile` *(one new assertion in `verify`)*

Nothing else. Do not touch `src/alerts.py` — it calls `send_email` and therefore gains
SMTP for free, which is correct and needs no change there.

## Pattern

### The settings

Add to `Settings` in `config.py`, beside the existing SendGrid block (~line 228), in the
house comment style:

```python
smtp_host: str = ""          # non-empty selects SMTP over the SendGrid API
smtp_port: int = 1025
smtp_username: str = ""      # blank = no AUTH (mailpit, and most local relays)
smtp_password: str = ""
smtp_starttls: bool = False
```

**`validate_env_template()` will fail the build if any of these five is missing from
`stratoscan/.env.example`** (golden rule #4). Add all five there, commented in the style
of the surrounding block, and say plainly that `SMTP_HOST` is the switch.

### The transport branch

`send_email` keeps its exact signature and its exact contract: **returns `bool`, never
raises.** That contract is load-bearing — `/auth/register` treats a failed send as
non-fatal and reports it as `email_sent`, so an exception escaping here turns a working
registration into a 500.

Structure it as a select-then-dispatch, not as a try-SendGrid-fall-back-to-SMTP:

```python
if settings.smtp_host:
    return _send_via_smtp(to_email, subject, text_body, html_body)
# ... the existing SendGrid body, unchanged ...
```

`_send_via_smtp` builds the message with `email.message.EmailMessage` (stdlib — set
`set_content(text_body)` and, when `html_body` is given, `add_alternative(html_body,
subtype="html")`, which produces the correct `multipart/alternative` ordering with plain
text first). Then `smtplib.SMTP(host, port, timeout=SENDGRID_TIMEOUT)`, `starttls()` only
when `smtp_starttls`, `login()` only when `smtp_username` is non-empty, `send_message`.
Wrap the whole thing so that **any** exception logs a warning and returns `False`, mirroring
how the SendGrid path already degrades.

Keep it stdlib-only. `smtplib` and `email.message` are both in the standard library, so
this adds no dependency and `requirements-lock.txt` must not change.

### ⚠️ The trap — read this before you run the suite

`tests/test_email_verification.py:150` does:

```python
with mock.patch("src.emails.settings") as ms:
    ms.sendgrid_api_key = ""
```

That replaces `settings` with a `MagicMock`, and **`ms.smtp_host` on a MagicMock is a
truthy Mock object**. So the moment you add `if settings.smtp_host:`, that existing test
stops testing the unconfigured-SendGrid path and silently takes the SMTP branch instead —
where the assertion it guards with (`urlopen` raising `AssertionError("no network!")`)
no longer applies, because SMTP does not call `urlopen`.

Fix it with one line in that `with` block:

```python
    ms.smtp_host = ""          # keep this case on the SendGrid path
```

That is the only change permitted to that file. Do not restructure the test.

This is also the reason the branch must read `settings.smtp_host` and not something like
`settings.has_smtp` — a property on a MagicMock is truthy too, but a bare attribute is the
one an existing test can neutralise in a single line.

### The local stack

In `.env.local.example`, replace the ⚠️ KNOWN GAP block with working values:

```
SMTP_HOST="mailpit"
SMTP_PORT=1025
```

`mailpit` resolves on the compose network; 1025 is the SMTP port the service already
publishes. `SENDGRID_API_KEY` stays empty — it is now irrelevant locally. Remove the
matching KNOWN GAP comment from the `mailpit` service in `docker-compose.yml` and say
what it now does instead.

Note `EMAIL_VERIFICATION_REQUIRED=false` stays as it is. Turning it on is a separate
decision about local flow, not part of making the transport work.

## Non-goals

- Do not touch the SendGrid code path's behaviour. Same function, same return values,
  same log lines. It is what production uses.
- Do not add a mail library. `smtplib` is stdlib; `requirements.txt` and
  `requirements-lock.txt` must be byte-identical when you finish.
- Do not change `src/alerts.py`, `src/api.py`, or the register/verify endpoints. The
  transport is the whole change.
- Do not turn on `EMAIL_VERIFICATION_REQUIRED` anywhere.
- Do not restructure `test_email_verification.py` beyond the single guard line.
- No reformatting, renaming or drive-by cleanup.

## Baseline

Everything is green before you start — confirm it, and stop if it is not:

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
```

Prints nothing = **40/40**. You are adding one suite, so the target when you finish is
**41/41**.

## Verify

### Hermetic — must pass with no Docker and no network

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret

python -m tests.test_emails_smtp            # the new suite, exit 0
python -m tests.test_email_verification     # the guard line works, exit 0

ruff check .                                # stays clean

# Golden rule #4 — all five new settings documented:
python -c "from src.config import validate_env_template as v; print(v() or 'complete')"

# 41/41 — prints nothing:
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done

# Dependencies untouched:
git diff --exit-code requirements.txt requirements-lock.txt && echo "deps unchanged"
```

`tests/test_emails_smtp.py` follows this repo's script style (module-level asserts,
`print("✅ …")` per check, a `Run: python -m tests.test_emails_smtp` line in the
docstring — see `tests/test_lse_source.py`). Patch `smtplib.SMTP`; **no real socket.**
Pin at minimum:

- `SMTP_HOST` set → `smtplib.SMTP` is constructed with that host and port, and
  `send_message` is called once. `urlopen` is patched to raise, proving SendGrid was
  never reached.
- `SMTP_HOST` empty → the SendGrid path runs and `smtplib.SMTP` is never constructed.
- `smtp_username` empty → `login` is **not** called. Set → it is, with both credentials.
- `smtp_starttls` False → `starttls` is not called. True → it is.
- An exception raised by `send_message` returns `False` and does not propagate. Assert
  this explicitly — it is the contract `/auth/register` depends on.
- The message that reaches `send_message` has the right `To`, `Subject`, and `From`
  (`settings.email_from_address`), and with an `html_body` it is `multipart/alternative`
  with the plain-text part first.

### End to end — the assertion this task exists for

```bash
make rebuild
```

Then add this to the `verify` target and run `make verify`. Mailpit's REST API lists
what it actually received, so this proves delivery rather than inferring it:

```bash
# Send one real verification email through the running API, then read it back
# out of mailpit. Anything less than this is the same "looks healthy" trap
# task 005 was written to avoid.
curl -fsS -X POST http://localhost:8000/auth/register \
  -H 'Content-Type: application/json' \
  --max-time 20 \
  -d '{"email":"smtp-check@example.test","password":"local-dev-password"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get('email_sent') else 1)"
# then:
curl -fsS --max-time 10 http://localhost:8025/api/v1/messages \
  | python3 -c "import json,sys; m=json.load(sys.stdin); sys.exit(0 if any('smtp-check@example.test' in str(x) for x in m.get('messages',[])) else 1)"
```

Both must exit 0, and `email_sent` must be `true` — which it cannot be unless SMTP
actually accepted the message. Confirm by eye at http://localhost:8025 too.

If `register` 429s, you have hit the `register` scope (3/min, 10/day — it is a cost
shield, see CLAUDE.md §10). Use a fresh address and wait, or run it right after
`make rebuild`. **Do not raise that cap to make the check pass.**

## Done

Commit and push branch `task/006-smtp-sender`. **Do not merge.** Claude reviews the diff
first — particularly the transport branch's failure handling and the `MagicMock` guard.
