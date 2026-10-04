# Build log

> The 15-day plan's closing ritual: two lines a day — what shipped, what broke. On day 15
> this is the launch narrative. Newest first. Detail lives in `.claude/tasks/`.

## Day 8 — Nobody faces an empty box

**Shipped:** twelve strategy templates that each parse for free, trade exactly what their
title says, and hold for weeks — enforced by a suite that also pins every rule they produce;
a public `/strategies` page; one-click templates in every empty box (Backtest empty and
zero-match states, the landing hero); a share-links panel with an off switch; and a Screener
notice whenever filters are relaxed or dropped. Landing → template → result is two clicks.

**Broke / found:** "stocks above *their* 200-day average" silently lost the trend condition —
the parser only knew "its". A momentum entry with the default RSI-70 exit buys and sells on
consecutive bars in an uptrend (median hold: 1 day); golden cross and Bollinger are screened
but traded on RSI. Left for the founder (backtest math is frozen); those templates wait. The
landing chip "momentum breakout stocks" was walking trial users straight into that churn. The
Tune panel's RSI/SMA sliders never reached the deterministic parser.

## Day 7 — Make a shared link worth clicking

**Shipped:** every share link now unfurls into a 1200×630 card — the query, the return, where
SPY ended, the curve, and the full disclaimer on the image itself — rendered in Python
(Pillow, hash-locked; Inter shipped as OFL fonts), cached by content hash, ETag/304, and
og:image/twitter:image on the page. Sitemap and robots now agree with the SEO registry (a
test keeps them that way). 11 hostile summaries render; 11/11 mutations caught.

**Broke / found:** Pillow's bundled font is ASCII-only — an em dash or "é" in a query drew a
box. Measuring text costs ~1 ms per call, so the first ellipsis loop took 900 ms on a long
query; binary search brought the worst case to 150 ms. The sitemap was missing /risk,
/terms, /privacy and /try, and advertised the token-only /verify-email.

## Day 6 — No signup wall, and shareable proof

**Shipped:** `/try` — the real backtester for strangers: three free runs on a signed trial
token (never accepted as a session, and vice versa), a countdown banner, and the account ask
only after the third result. Share links: opt-in, a server-built whitelist summary (no email,
ids or trade log), a server-rendered page with the disclaimer beside the numbers and a
script-free CSP, revocable, killed with the account. Signing up claims the trial's runs and
links. 50/50 backend (13/13 mutations caught), 254/254 frontend; live E2E across a restart.

**Broke / found:** every finished async backtest failed to persist — the job stored a Pydantic
model that `json.dumps` cannot serialize, the upsert swallowed the error, so a restart lost the
result (and nothing could have been shared). Async failures also returned raw exception text.

## Day 4 — Instrumentation and the legal floor

**Shipped (Freebuff, reviewed):** the plan's fifteen funnel events through one `track()`, with
a per-event whitelist that makes storing query text or emails impossible; browser can only
send the five client events; scrubbed error capture; risk disclaimer inside the result block,
a `/risk` page, and a privacy section on what's stored and how to delete it. 49/49, 240/240.

**Broke / found:** polling a running backtest counted against the backtest cost shield — with
rate limiting on (production; staging has it off, so no probe ever saw it) the third poll of
every job was a 429, and polls drained the user's 10/day and the planet-wide backtest bucket.
Polls now have their own free scope.

## Day 3 — Authentication, completely

**Shipped:** revocable sessions (`token_version`, log out everywhere); password reset with
identical response *and timing* for unknown addresses (351 vs 352 ms, measured over HTTP);
in-app password change; account deletion that purges `backtest_jobs` too (it has no foreign
key); sliding sessions with a 30-day ceiling; idempotent verification links. Frontend for all
of it. 45/45 backend, 219/219 frontend.

**Broke / found:** `ADMIN_EMAILS` handed an admin token to whoever registered the listed
address first, verification never checked — fixed, and the test that asserted the bypass
rewritten. Resend-verification was sending email outside the SendGrid budget. Verification
links failed on the second click, i.e. on the first real click behind a mail scanner.
Then Google sign-in (Freebuff, reviewed): server-side code exchange, and a Google sign-in that
matches an unverified signup takes the row over — squatter's password, sessions and saved data
all gone. Still open: email change, register's 409 enumeration (settle with Day 6).

## Day 2 — The scheduler runs, unattended

**Shipped:** SMTP transport so mail actually delivers locally; `mail_transport` as the single
owner of "which transport"; alerts prove delivery end to end in `make verify`, and the
scheduler's own watchdog paged over SMTP unprompted.

**Broke / found:** `send_email` could raise on a malformed recipient (500 after the account
row was committed); raw recipients were written into logs (forgeable lines).

## Day 1 — A production-parity environment

**Shipped:** `make up` brings up API + scheduler as separate services, three logical Postgres
databases, Redis behind an Upstash-REST proxy, and a mail catcher; `make verify` asserts the
scheduler is a scheduler and Redis is really in use.

**Broke / found:** the entrypoint ignored its arguments, so a "scheduler" service would have
been a second API; the API's own healthcheck exhausted its rate-limit budget in 83 minutes.
