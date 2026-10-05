# Build log

> The 15-day plan's closing ritual: two lines a day — what shipped, what broke. On day 15
> this is the launch narrative. Newest first. Detail lives in `.claude/tasks/`.

## Day 13 — Testing blitz

**Shipped:** the product is now tested the way people use it and the way people attack it. Four
journeys run in a real browser against a real stack on every push — a stranger who never signs
up, a new account from signup to saved strategy, a forgotten password, and a plan going Pro and
back — with a throwaway database of synthetic prices and a mail catcher standing in for the
inbox. An adversarial suite throws 577 checks at it: every signed-in route with forged, expired
and stolen tokens, other people's ids, SQL and markup through every input including the
language model's own answer, and every known way around the rate limits. Three new failure
drills (mail down, error capture down, the database vanishing mid-request) and an accessibility
sweep that measures rather than estimates. The load ceiling is a written number (LOAD.md).

**Broke / found:** a lot, which is the point. Behind Fly's proxy every visitor shared one
rate-limit bucket. A database blip answered "account not found", which logged everyone out. The
connection pool failed instead of waiting, so eight people backtesting at once got errors. The
SQL whitelist was closed only because every caller happened to map fields first. The
explanation called the portfolio's return "the strategy's" next to a single stock's number, and
said a rising account had been underwater for 90 months. A heatmap drew solid colour behind
same-colour text. And the accuracy loop's own counter was wrong — it double-counted, reported
16% accuracy beside "all checks passed", and ignored queries that never ran — so its headline
figure is now recomputed and dated rather than trusted.

## Day 12 — Entitlements without a payment provider

**Shipped:** two plans, one decision. `plan_for(user)` is the only place that says what an account
may do; every gated route asks it through one dependency, and a test finds those routes from the
app itself, so a new gate without a direct-API test fails the build. Exactly four things are gated
— monthly backtests (Free 50), saved strategies (3 vs 20), CSV export, weekly strategy emails —
and accuracy never is: a Free backtest is the same result as a Pro one. Upgrades apply at once,
downgrades at period end, revocations after a 7-day grace; an admin's change applies on the next
request. The first 100 accounts are Pro for life, issued by a counter that never runs backwards.
No price appears anywhere until a flag is flipped, and `BILLING.md` lists what a payment adapter
must add — plus a conformance suite it must pass.

**Broke / found:** the site promised "no tiers, every feature for everyone, no upgrade path" in a
dozen places — all corrected to what is now true: the free plan is forever, and the numbers are
never paywalled. The Pricing page itself printed "$0", a price. Screen CSV export was public; it
now needs an account. The shared database pool raises instead of waiting when more than five
requests want a connection at once — found while racing 50 concurrent charges; left for its own
change. Free's backtests went from "10 a day" (a rate limit) to 50 a month (an allowance) — the
per-IP rate limits still apply on top.

## Day 11 — Explain the result, don't just return it

**Shipped:** every backtest now gets a "What happened" reading. Plain-English findings come
straight from the numbers and are always there: why a loser lost (bigger losers than winners, a
low hit rate, stop-outs, a couple of disasters), and the uncomfortable conclusions said plainly —
two trades made the profit (luck), the last 30% of history disagreed with the first 70%
(probably fitted), fewer than 30 trades (not meaningful). A model writes a short summary over the
same numbers, shown only if it passes two automatic checks: no advice or prediction language (35
patterns), and no number it was not given. If every provider is down, the findings still show.
The same result always gets the same reading, even after a restart.

**Broke / found:** the first number guard read the date 2021-06-30 as the numbers 6 and 30, which
would have let a model invent "lost 6%"; dates now count as their year only, and any percentage
must be a real figure however small. The compliance list passed a mutation check only after a
sentence caught by nothing but "will" was added — every earlier example tripped two patterns.

## Day 10 — A saved strategy writes back

**Shipped:** "Follow weekly" on any finished backtest saves it — the rules the engine actually
ran, not the words typed — and once a week its owner gets one email on how it has done since
that day, next to SPY over the same days: losses called losses in the subject, stocks that no
longer screen in named, "too early" before there is a week to show. A Saved strategies panel on
the dashboard, a real on/off switch under Settings → Email, and an unsubscribe link that works
without signing in (one click from the mail app, a confirm button from a browser). Provider-down
drill: three failures and the run stops; tomorrow's pass sends each person exactly once.

**Broke / found:** the Settings page had three notification switches that were never sent
anywhere — removed. With `SHARE_BASE_URL` unset, unsubscribe links would have pointed at the
frontend and 404'd, so the digest now refuses to send without it (and it was never staged by
the deploy workflow — now it is). The session check named the trial token's type explicitly, so
a new kind (the unsubscribe token) would have been kept out only by a missing claim; any typed
token is now refused outright. Capacity note: SendGrid's
free tier is ~100 emails/day across verification, resets and the digest — the per-run cap is 60.

## Day 9 — Simple on the surface, deep underneath

**Shipped:** five layers on the backtest — the box, the result, then three collapsed panels
that remember each person's choices: "Show the rules" (the rules in plain English, defaults
labelled as defaults, and editable in place — re-runs without retyping), "Adjust & compare"
(an overfitting warning that gets louder after three re-runs, compare mode, the setup link),
and "Show the code" (the script plus the raw configuration the engine ran) — one click from
any result. Charts describe themselves to screen readers; a re-run from the panel is proven
to give the same numbers as the rule typed from scratch.

**Broke / found:** the muted text colour measured 4.24:1 on the stronger glass panels — under
WCAG AA for small text — so it was lightened to 4.83:1 and a test now measures every text
colour on every panel over every glow. The "Simulated strategy" badge read a field the API
never sends.

**Earlier the same day (approved signal change):** bullish momentum now enters on price above the 50-day
inside an uptrend and exits when price breaks the 200-day — rules that cannot both be true,
so the sim can no longer buy and sell on consecutive bars. Median holds went from 1 day to
18–128 days; pinned by a new golden scenario, and the three momentum templates are back. The
Tune panel's RSI fields are real request fields now.

**Broke / found:** every UI backtest carried an 8% stop and 20% target nobody chose, and its
RSI/SMA settings were appended to the query as text the parser ignored (and that then showed
on share pages). The capital and SMA inputs were never read by the engine. A shared link's
settings never applied to the run it triggered — hydrated one render too late.

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
