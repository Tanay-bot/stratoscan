# Day 3 — Authentication, completely: the split

> Plan goal: *"Every realistic account scenario is handled, tested, and cannot leak
> information about who has an account."* Appendix A is the acceptance matrix.

## Honest sizing

Day 3 is not a day. Days 1 and 2 produced 13 files between them; Day 3 asks for password
reset, refresh tokens, session revocation, account deletion, email change, Google OAuth,
and a hermetic test for all 29 rows of Appendix A. That is three to four times the
combined size of everything built so far, and it touches the three files the whole
application funnels through.

Splitting it into five specs is not padding. It is the only way any of it gets reviewed
rather than rubber-stamped.

## The chain — sequential, not parallel

| # | Spec | Delivers | Depends on |
|---|---|---|---|
| 008 | `token_version` + log out everywhere | The revocation primitive every later task needs | — |
| 009 | Password reset | The plan's headline gap; "all sessions invalidated" needs 008 | 008 |
| 010 | Email change + account deletion | The rest of the lifecycle | 008 |
| 011 | Refresh tokens / sliding session | Ends the mid-backtest hard logout | 008 |
| 012 | Google OAuth + the merge rule | Highest-conversion item; the merge path is the classic bug | 009, 010 |

**Run these one at a time.** Every one of them edits `src/auth.py`, `src/api.py` and
`src/database.py`. `.claude/tasks/README.md` says never parallelise two specs that touch
the same file, and these overlap on all three — `scripts/worktree.sh` would just buy you
a three-way merge conflict between agents that cannot talk to each other.

## Why `token_version` is first

Appendix A has four rows that are impossible without it:

- *Password successfully reset → every existing session invalidated*
- *Logged in on two devices, password changed on one → the other device's token stops working*
- *"Log out everywhere" → token version bumped, all sessions invalidated*
- *Reset token reused after success → rejected*

Build reset first and you ship a reset flow that leaves the attacker's stolen session
live — which is worse than no reset flow, because it looks like security. The primitive
comes first.

It is also cheap here, and that is worth knowing before anyone proposes a token blocklist
or a Redis session store: `get_current_user` already calls `_fetch_user` on **every**
authenticated request (`auth.py:239`). The user row is in hand already. Comparing one
integer adds no query, no cache, no new dependency.

## Deferred deliberately

- **Domain authentication (SPF/DKIM/DMARC).** The plan says it cannot be done without a
  domain. Note it in the deploy runbook; it is not a task.
- **"Reset your own password end to end on production" and "a verification email lands in
  a Gmail inbox".** Both Day 3 done-criteria require a deployment that does not exist
  yet. They belong to Day 15's dress rehearsal. Everything else in Appendix A is provable
  locally against mailpit, which is exactly what Days 1–2 bought.
- **Share links dying with the account** (Appendix A, last row). Share links are Day 6.
  The rule gets decided and tested when the thing it applies to exists.

## The standing bar

Unchanged from 005–007, and it is what makes this reviewable at all: an assertion nobody
has watched fail is not evidence. Every spec below asks for the failure direction to be
observed, not just the pass.
