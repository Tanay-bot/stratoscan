# Stratoscan — put it on trial

This document has three parts. **Part 1** tells a Claude chat what to do with it. **Part 2** is
the case file: the facts, stated neutrally, with no opinion about whether the idea is good.
**Part 3** lists what the record does not contain, so nobody fills the gaps with guesses.

---

## Part 1 — Instructions for the Claude chat

### If you are Claude Code with the `/court` skill installed (this repo)

Run:

```
/court --quick Read COURT_CASE.md at the repo root. Part 2 is the case file — use it word for word as .court/case.md. Part 3 belongs in the case file too.
```

`--quick` is 6 jurors (11 sub-agents). Use `--jury 12` for the full trial (17), or `--jury 3`
for the cheapest (8).

### If you are a normal Claude chat (claude.ai, no skill)

The person pasting this wants **honest pushback, not agreement.** Run the trial yourself, in
this order, and keep each role separate — do not let one role's conclusion leak into the next.

1. **Prosecution — opening.** Argue that the case in Part 2 fails as stated. Use only facts in
   Part 2 and Part 3. Make the strongest case, not a fair one.
2. **Defense — opening.** Argue that it works as stated. Same rule.
3. **Prosecution rebuttal**, then **defense rebuttal.** Answer the other side's best point
   directly.
4. **The jury — 12 jurors, each voting independently.** For each juror, write as if you had
   not read the other jurors' votes. Each gives: a vote (**GUILTY** = it fails as stated /
   **NOT GUILTY** = it works as stated), a confidence from 1–10, and the one reason that
   decided it. The jurors and their lenses:

   | # | Juror | Lens |
   |---|---|---|
   | 1 | The Accountant | Follows the money. Distrusts any number with no source; wants to know who pays, how much, and when. |
   | 2 | The First-Time Customer | Never heard of it before today. Would they actually use it, this week, over what they already do? |
   | 3 | The Competitor | Runs the business this would compete with. How would they beat it, copy it, or ignore it? |
   | 4 | The Retired Judge | Burden of proof. Is the evidence in the record, or is it hope dressed up as fact? |
   | 5 | The Teenager | Blunt and easily bored. Would anyone care, talk about it, or come back next week? |
   | 6 | The Engineer | Can it actually be built and run, and what breaks first when it grows? |
   | 7 | The Small-Business Owner | Who does the work on Monday, where does the cash come from until it pays, what happens in a slow month? |
   | 8 | The Statistician | Base rates, tiny samples, survivorship bias. How often does a plan like this actually work? |
   | 9 | The Parent | Protects family time, money and safety. Is it trustworthy and worth the risk? |
   | 10 | The Investor | Upside against risk. Would they put their own money in exactly as described? |
   | 11 | The Designer | Would a stranger understand what this is and why it matters in five seconds? |
   | 12 | The Lawyer | Legal, regulatory and liability traps the case does not mention. |

5. **The judge.** Count the votes and state the verdict. Then write: the strongest point on each
   side; what the record is missing; and **"Conditions for acquittal"** — the specific, testable
   things that would flip a GUILTY verdict (or the risks that could flip NOT GUILTY).

**Rules for you:** never soften the verdict, never add praise the jury did not give, and never
add facts that are not in Part 2. If the founder disagrees, offer a retrial with the conditions
for acquittal written into the case — do not argue for either side yourself.

*(Juror list from the MIT-licensed `/court` skill by Alex Chen, github.com/alexyc9381/court-skill.
A single chat playing all twelve jurors is less independent than twelve separate agents — treat a
close vote with that in mind.)*

---

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
