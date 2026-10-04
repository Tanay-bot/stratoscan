Launch Stratoscan — a free, natural-language stock screener and 10-year backtester for US stocks — as a product people will come back to.

## Part 2 — The case file

### The case, in one sentence

**Launch Stratoscan — a free, natural-language stock screener and 10-year backtester for US
stocks — as a product people will come back to.**

### What it is

- **The founder's tagline:** *"Bridge from idea to candidate list. A natural-language market
  terminal with 10 years of backtested history — free, forever."*
- **The loop:** a user types a strategy in plain English (e.g. *"find undervalued small-cap tech
  stocks with rising momentum"*). The app turns it into filters, screens about **5,000 US
  exchange-listed stocks**, then simulates the strategy on **10 years of daily prices** —
  including trading fees, slippage, stop-losses, and short selling — and compares it with simply
  holding the S&P 500 (SPY).
- **Robustness checks shown with each result:** Monte Carlo simulation, bootstrap confidence
  intervals, walk-forward testing, and parameter sensitivity — i.e. whether the result survives
  when the data or settings are shaken.
- **Scope:** US stocks and ETFs only. The founder has ruled out crypto, futures, forex and
  commodities on purpose: *"every one of them dilutes the verification claim that is your whole
  position."*

### The founder's philosophy (stated, not judged)

1. **Free, with no paywall.** There is no paid tier. Accounts exist only so usage can be
   rate-limited per person. A `plan` field is kept in the database for possible future use; a
   payment system was built and then removed.
2. **Verification is the position.** The founder treats "every number is correct" as the core
   claim. An automated accuracy loop re-derives every reported number from first principles
   (recorded at 14,294 of 14,294 checks passing), and a frozen set of reference results fails the
   build if the simulation math ever changes without deliberate approval.
3. **Near-zero cost per user.** Most queries are interpreted by a rule-based parser at no cost;
   an AI model is called only for queries the rules cannot read (about 20%), and answers are
   cached for 24 hours. The screener makes zero outside calls per query. Everything runs on free
   tiers: Neon (database), Upstash (rate limits), Finnhub (10 free keys), yfinance (price
   history), free AI models, SendGrid (email), with one small always-on server planned at about
   **$2–3 a month**.
4. **Value before signup.** The plan is to let a stranger run a real backtest *without* an
   account, and share the result as a link — "after value, never before."
5. **Honest about limits.** Every result carries a disclaimer next to the number: simulated
   results on historical data, past performance does not predict future results, not investment
   advice.

### Who built it, and how

- A solo founder. The founder describes their own coding ability as limited and built the
  software largely with AI coding agents (Freebuff / DeepSeek, and Claude). The core engine was
  built in about five days.
- It is mature for a pre-launch project: around 49 automated test suites on the server side and
  240 on the frontend, security review of sign-in and sessions, Google sign-in, password reset,
  account deletion, usage tracking and error capture.

### Where it stands

- **Not launched. No users yet.** Nothing is deployed; it runs only on the founder's laptop.
- The founder is following a written **15-day plan** to get from working prototype to
  "deployment-ready." Days 1–4 are done (local production setup, background data jobs, full
  account system, usage tracking and legal pages). Still ahead: making a first backtest return in
  under 60 seconds (today it can take minutes on cold data); no-signup trials and shareable result
  links; templates; retention features; "explain the result"; a testing push; a deployment dress
  rehearsal.
- **The founder's own kill criterion:** the share of users who run a **second backtest**. The plan
  calls it *"the single most important number in the product."* It also tracks whether users
  return on day 2 and day 7.

### Known constraints and risks the founder has already written down

- **Email budget:** the free email tier allows 100 emails a day, and signup is capped at 50 per
  day site-wide to stay under it. That also caps how many people can sign up per day at launch.
- **Data sources:** price history comes from **yfinance**, an unofficial library that reads
  Yahoo Finance. An alternative licensed source (London Strategic Edge) has been prepared but not
  tested against the live service; its licence forbids showing its prices to end users.
- **Free-tier dependence:** every external service is on a free plan with its own limits; the
  app is built to stay inside them using caching and hard rate limits.
- **Regulatory context:** the product shows simulated historical performance to people who may
  act on it. Terms, Privacy and Risk Disclaimer pages exist; the disclaimer is shown on every
  result.
- **Email deliverability:** without a domain set up for email authentication, verification
  emails may land in spam. Not yet resolved.

---

## Part 3 — What the record does not contain

The founder has **not** stated any of the following. Jurors should treat them as gaps in the
evidence, not fill them in:

- How the product will ever make money, or whether it needs to.
- Who the target user is, specifically (beginner investors? active traders? students?), and how
  many such people there are.
- How users will find it — any marketing or distribution plan beyond shareable result links.
- Named competitors, or how Stratoscan compares with them.
- Any user research, interviews, waitlist, or early interest.
- The founder's goal for the project (a business, a portfolio piece, a learning project, a
  side income).
- What happens to running costs if usage grows beyond the free tiers.
