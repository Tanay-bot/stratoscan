# 007 — Prove an alert actually lands, and stop logging raw recipients

## Goal

Task 006 made `alerts_enabled` transport-aware, so an SMTP-only deployment can now page
someone. Nothing has proved that it does.

`_dispatch_email` reuses `send_email` (`alerts.py:86`), so the SMTP transport should work
for alerts with no further code. "Should" is the word this project keeps getting caught by:
the predicate is exercised, the delivery is not. Close it the same way task 006 closed
verification mail — trigger a real alert against the running stack and read it back out of
mailpit.

Second, smaller item in the same area: `_send_via_smtp` logs the recipient with `%s`, so a
linefeed in the address forges log lines. The transport now degrades correctly on that
input (task 006), but it writes the attacker's newline into the log on the way out.

## Files

- `.env.local.example` *(configure the email alert channel locally)*
- `Makefile` *(one new assertion in `verify`)*
- `stratoscan/src/emails.py` *(one format specifier)*
- `stratoscan/src/alerts.py` *(one stale docstring line)*
- `stratoscan/tests/test_emails_smtp.py` *(pin the log fix)*

Nothing else. In particular **do not change `send_alert`, `_dispatch`, `_dispatch_email`,
the throttle, or `config.py`.** The wiring is already correct — this task proves it and
fixes one log string.

## Pattern

### 1. The log-injection fix (`src/emails.py`)

In `_send_via_smtp`'s `except` block:

```python
    except Exception as e:
        logger.warning("SMTP send failed for %r: %s", to_email, e)
```

`%s` → `%r`. `RegisterRequest.email` is a bare `str` with no format validation, so
`to_email` is attacker-controlled; with `LOG_FORMAT=text` a newline in it writes forged
lines into the log. `%r` escapes it to `\n` inside quotes. One character.

Pin it in `tests/test_emails_smtp.py` beside the assertion added last pass — assert the
emitted record contains no raw newline. Use `unittest.mock.patch.object(emails.logger,
"warning")` and inspect the call args, or `assertLogs`-style capture in this file's script
idiom. The point is the *record*, not the return value, which is already pinned.

### 2. The stale docstring (`src/alerts.py:81`)

It says `_dispatch_email` "Reuses ``src.emails.send_email`` — the same stdlib-only
SendGrid client…". Since 006 that client may be SMTP. Say "the same stdlib-only mail
sender (SendGrid API or SMTP, per `SMTP_HOST`)". One line; do not rewrite the function.

### 3. Configure the channel locally (`.env.local.example`)

Uncomment/set `ALERT_EMAIL` to a local address, and leave `ALERT_WEBHOOK_URL` blank —
`_dispatch` prefers the webhook when both are set (`alerts.py:100-105`), and the point
here is the mail path. Explain in a comment that this is what makes the alert land in
mailpit.

### 4. The assertion (`Makefile`, `verify` target)

Trigger a real alert inside the running API container and read it back. **Two traps, and
they are why this is a spec rather than a one-liner:**

**Trap A — the alert is asynchronous, and the thread is a daemon.** `send_alert` hands off
to `threading.Thread(..., daemon=True)` and returns `True` *before* delivery
(`alerts.py:148`). Its own docstring says the return value "does NOT mean the webhook or
email service accepted it." A `python -c` that calls `send_alert` and exits kills the
daemon thread mid-flight and delivers nothing — while returning `True`. Join it by name:

```python
import threading
for t in threading.enumerate():
    if t.name == "alert-dispatch":
        t.join(timeout=15)
```

**Trap B — a fixed subject is satisfiable by a stale message**, exactly as the fixed
address was for the mail check. Use a per-run token in the title, the same way the mail
step uses a timestamped address, and match on that token.

Shape it like the existing mail check:

```bash
@echo "── an alert is actually delivered over SMTP ─────────────"
@tok=alert-$$(date +%s); \
  $(COMPOSE) exec -T api python -c "import sys,threading; \
    from src.alerts import send_alert; \
    sys.exit(0 if send_alert('stratoscan drill $$tok', 'local stack verify', \
      level='error', throttle_key='$$tok') else 1); " ... ; \
  # join the dispatch thread before the process exits, then poll mailpit for $$tok
```

Write it properly rather than copying that sketch — it is showing you the two traps, not
the final command. Poll mailpit with a bounded retry (a few seconds, `--max-time` on the
curl) rather than a single immediate read; the send is asynchronous and a one-shot check
races it. Fail with a message naming what was missing, like the mail check does.

Note the per-process throttle is not an obstacle: `_last_sent` lives in the process
(`alerts.py`), and each `exec` is a fresh one, so repeated `make verify` runs are not
throttled. Do not lower `ALERT_WEBHOOK_MIN_INTERVAL_SECONDS` to work around a problem you
do not have.

## Non-goals

- Do not modify `send_alert`, `_dispatch`, `_dispatch_webhook`, `_dispatch_email` bodies,
  the throttle, or anything in `config.py`. 006 settled the predicate.
- Do not add an endpoint, a CLI command, or a test hook to trigger alerts. `docker compose
  exec` into the running container is the trigger, and it needs no new surface.
- Do not touch `src/api.py`, `job_monitor.py` or `scheduler.py` — the three
  `alerts_enabled` consumers are already correct.
- Do not add email-format validation to `RegisterRequest`. It is worth doing and it is not
  this task; the transport degrades correctly without it.
- Do not add a dependency. `threading` and `smtplib` are stdlib.
- No reformatting, renaming or drive-by cleanup.

## Baseline

```bash
cd stratoscan
export JWT_SECRET=ci-dummy-jwt-secret
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done
```

Prints nothing = **41/41**. You add no suite, so it must still be 41/41 when you finish.

## Verify

```bash
cd stratoscan && export JWT_SECRET=ci-dummy-jwt-secret
python -m tests.test_emails_smtp        # exit 0, incl. the new log assertion
python -m tests.test_metrics_alerts      # exit 0 — the alerts suite still passes
ruff check .                             # clean
python -c "from src.config import validate_env_template as v; print(v() or 'complete')"
for t in tests/test_*.py; do
  python -m "tests.$(basename "$t" .py)" >/dev/null 2>&1 || echo "FAIL $t"
done                                     # prints nothing
git diff --exit-code requirements.txt requirements-lock.txt && echo "deps unchanged"
```

Then, from the repo root:

```bash
make rebuild        # exit 0
make verify         # exit 0, including the new alert assertion
make verify         # exit 0 AGAIN — a second run must still pass
```

Confirm by eye at http://localhost:8025 that the alert message is there alongside the
verification mail, and that its body carries the drill token.

**Prove the new assertion discriminates**, the way the link clause was proved last pass:
show it failing when it should. Two cases, run by hand:

- the thread is not joined (delete the join, re-run) → must **fail**, not pass;
- the token is one that was never sent → must **fail**.

Report both. An assertion that has never been observed failing is not yet evidence.

## Done

Commit and push branch `task/007-prove-alert-delivery`. **Do not merge.** Claude reviews
the diff first — particularly the join and the per-run token, which are the two places
this check can silently become decorative.
