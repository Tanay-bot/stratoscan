Stratoscan: a free, natural-language stock screener and 10-year backtester for US stocks, launched as a product people will come back to.

## What it is

- Tagline: "Bridge from idea to candidate list. A natural-language market terminal with 10 years of backtested history — free, forever."
- A user types a strategy in plain English (for example, "find undervalued small-cap tech stocks with rising momentum"). The app turns it into filters, screens about 5,000 US exchange-listed stocks, then simulates the strategy on 10 years of daily prices, including trading fees, slippage, stop-losses and short selling, and compares it with holding the S&P 500 (SPY).
- Each result shows robustness checks: Monte Carlo simulation, bootstrap confidence intervals, walk-forward testing and parameter sensitivity.
- Scope: US stocks and ETFs only. Crypto, futures, forex and commodities are ruled out on purpose: "every one of them dilutes the verification claim that is your whole position."

## Price and revenue

- Free, with no paywall and no paid tier. Accounts exist only so usage can be rate-limited per person.
- A `plan` field is kept in the database for possible future use. A payment system was built and then removed.

## Costs

- Near-zero cost per user. A rule-based parser interprets most queries at no cost; an AI model is called only for the roughly 20% it cannot read, and answers are cached for 24 hours. The screener makes zero outside calls per query.
- Everything runs on free tiers: Neon (database), Upstash (rate limits), Finnhub (10 free keys), yfinance (price history), free AI models and SendGrid (email). One small always-on server is planned at about $2–3 a month.

## Customers so far

- None. Not launched. Nothing is deployed; it runs only on the founder's laptop.

## How users will find it

- The plan is to let a stranger run a real backtest without an account and share the result as a link: "after value, never before." No-signup trials and shareable links are not built yet.

## What makes it different, in the founder's words

- Verification is the position: "every number is correct" is the core claim. An automated accuracy loop re-derives every reported number from first principles (14,294 of 14,294 checks passing), and a frozen set of reference results fails the build if the simulation math changes without approval.
- Every result carries a disclaimer: simulated results on historical data, past performance does not predict future results, not investment advice.

## Team and build

- A solo founder who describes their own coding ability as limited. The software was built largely with AI coding agents (Freebuff / DeepSeek, and Claude). The core engine was built in about five days.
- About 49 server-side test suites and 240 on the frontend; security review of sign-in and sessions; Google sign-in, password reset, account deletion, usage tracking and error capture.

## Timeline

- A written 15-day plan from working prototype to "deployment-ready." Days 1–4 are done: local production setup, background data jobs, full account system, usage tracking and legal pages.
- Still ahead: a first backtest in under 60 seconds (today it can take minutes on cold data); no-signup trials and shareable result links; templates; retention features; "explain the result"; a testing push; a deployment dress rehearsal.

## How success is measured

- Kill criterion: the share of users who run a second backtest, called "the single most important number in the product." It also tracks whether users return on day 2 and day 7.

## Known constraints and risks

- Email: the free tier allows 100 emails a day, and signup is capped at 50 a day site-wide to stay under it.
- Email deliverability: without a domain set up for email authentication, verification emails may land in spam. Not yet resolved.
- Data: price history comes from yfinance, an unofficial library that reads Yahoo Finance. An alternative licensed source (London Strategic Edge) is prepared but untested against the live service; its licence forbids showing its prices to end users.
- Every external service is on a free plan with its own limits; caching and hard rate limits keep the app inside them.
- Regulatory: the product shows simulated historical performance to people who may act on it. Terms, Privacy and Risk Disclaimer pages exist.

## What the pitch does not contain

The founder has not stated any of the following. Treat them as gaps, not as facts to fill in:

- How the product will ever make money, or whether it needs to.
- Who the target user is, specifically (beginner investors? active traders? students?), and how many such people there are.
- How users will find it beyond shareable result links: any marketing or distribution plan.
- Named competitors, or how Stratoscan compares with them.
- Any user research, interviews, waitlist or early interest.
- The founder's goal for the project (a business, a portfolio piece, a learning project, a side income).
- What an investment would be used for, and how much is being asked for.
- What happens to running costs if usage grows beyond the free tiers.

Filling any of these in before the session gives the sharks more to judge; leaving them blank puts them on the homework list.
