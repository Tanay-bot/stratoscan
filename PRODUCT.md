# Product

<!-- impeccable:product-schema 1 -->

> ℹ️ **Historical brief.** This file records the product intent captured
> *before* the frontend was built, and is kept as-is (it is tool-managed).
> Several statements are no longer true of the shipped system — notably,
> watchlist / saved screeners / recent backtests are **not** client-only any
> more: they sync through `GET/PUT /me/personal`. For the current state of
> the system see `CLAUDE.md`; for what the frontend actually looks like see
> `DESIGN.md`.

## Platform

web

## Stack

delegated: user specified React + Vite, Tailwind CSS, Framer Motion, Recharts, React Hook Form, Axios, React Router v6. Added TypeScript (strict) and Zustand for auth/session state. Frontend lives in `frontend/`, sibling of the `stratoscan/` FastAPI backend, connected via `VITE_API_URL`.

## Users

- Primary: retail investors / semi-professional traders who have an investing idea in plain English but no way to quickly turn it into a ranked, backtested candidate list. They are technical enough to understand RSI, SMA, drawdown, Sharpe — but they want the terminal power without writing code.
- Situation: at a desk (desktop-first), scanning markets, testing strategy ideas. "Bloomberg Terminal met Instagram" — they want serious fintech data density and speed with a polished, modern, delightful surface.

## Product Purpose

Stratoscan turns a natural-language investing idea ("find undervalued small-cap tech stocks with rising momentum") into a ranked candidate list and a backtested strategy report. Success = a visitor can go from idea → candidate list → validated strategy in under a minute and trust the numbers.

## Positioning

The meaningfully different mechanism: a natural-language interpreter (LLM) translates plain-English investing ideas into structured filter criteria, runs them against a pre-fetched local stock universe (zero API calls per screen), and backtests the strategy on 10 years of OHLCV with Monte Carlo, confidence intervals, and benchmark (SPY) comparison. The core loop is free forever; Pro only adds volume and convenience, never accuracy.

## Operating Context

- The backend is a complete, working FastAPI service (`stratoscan/`). Do not modify it.
- JWT auth: `POST /auth/register`, `POST /auth/login` return `{access_token, expires_in, user{id,email,plan,email_verified}}`. Frontend stores the token and sends `Authorization: Bearer <token>`.
- Core calls: `POST /screen` (query, limit, offset, sort_by, sort_desc), `POST /backtest` (query + risk overrides, requires auth), `GET /news`, `GET /news/{symbol}`, `GET /stocks`, `GET /stocks/{symbol}`.
- Rate limits are real and tight: screen burst 5/min, backtest 2/min, 10/day. The UI must surface 429s with a graceful countdown (`retry_after` in the response).
- CORS already allows `http://localhost:5173` (Vite default) and `http://localhost:3000`.
- Backend cannot run on this dev machine (needs `.env` secrets + Neon DB + Python 3.14 venv). Therefore the frontend ships a `VITE_DEMO_MODE` that serves realistic mock data through the exact same typed API interface so the UI is fully demoable/screenshot-able without the server. Production builds strip it; demo mode never ships fake data to production unless the env flag is set.
- There are no backend endpoints for watchlist / saved screeners / recent backtests. These dashboard widgets persist in localStorage (client-side only), clearly labeled.
- Email verification: signup returns `email_sent`; there is a `/verify-email` route the emailed link points to.

## Capabilities and Constraints

- Pages: Landing, Login/Signup, Screener Results, Backtest Workspace, Stock Detail, Dashboard (sidebar shell), News Feed, Settings, Verify Email.
- Backend-provided data: screen results with 30+ metrics per stock (price, change, market cap, PE, RSI, SMA 50/200, 52w high/low, valuation/profitability/growth, composite score + why_matched); backtest with equity_curve, drawdown_curve, benchmark total return, alpha/beta/excess, Sharpe, max DD, win rate, trades, Monte Carlo, CIs, walk-forward, parameter sensitivity; news articles with per-article sentiment + sentiment_score.
- Client-side only (no backend support): watchlist, saved screeners, recent backtests history, user settings persistence.
- Constraints: dark theme with glass-morphism, palette `#0a0a0f` bg / `#00d4ff` cyan / `#7c3aed` purple, green gains / red losses, Inter typography, mobile-first responsive, accessibility (contrast, keyboard nav, ARIA), skeleton loaders, toast notifications, graceful loading/error states.

## Brand Commitments

- Name: Stratoscan. Tagline (pinned): "Bridge from idea to candidate list."
- Visual world (pinned by user): Bloomberg Terminal meets Instagram — glass-morphism / frosted glass throughout, deep indigo/blue `#0a0a0f` background, neon cyan `#00d4ff` primary, purple `#7c3aed` secondary, green for gains, red for losses, Inter or SF Pro Display, bold numbers with light labels.
- Example query chips (pinned): "find undervalued small-cap tech stocks", "high-dividend energy companies", "momentum breakout stocks".
- Voice: serious fintech, precise, confident; not hype. README states "not a stock-picking service."
- Plans (Day 12): Free (forever) and Pro. Only volume/convenience is gated (backtests per month, saved strategies, CSV export, weekly emails); accuracy never is. No price is shown until the price flag is on — the frontend must not invent prices or present gates the API does not enforce.

## Evidence on Hand

- The complete backend contract in `stratoscan/src/api.py` (models + endpoints, read directly).
- README, config, and security docs in `stratoscan/`.
- No existing frontend, no real screenshots, no testimonials, no real user data. All demo/mock numbers in `VITE_DEMO_MODE` are synthetic and must be labeled as such in the UI when shown in demo mode.

## Product Principles

1. Idea → candidate list → validated strategy is one continuous flow, never a wall between surfaces.
2. Numbers are the product: bold, tabular, trustworthy, with light labels.
3. Speed and confidence over decoration — but craft is expected at every state (loading, empty, error).
4. Free and honest: no fake paywalls, no fabricated claims; demo data is labeled.
5. Serious tool, polished surface: the density of a terminal, the finish of a modern consumer app.

## Accessibility & Inclusion

- WCAG-AA contrast on body text; focus-visible rings; keyboard navigation; ARIA labels on icon buttons; `prefers-reduced-motion` respected; semantic HTML; tabular numerals for data columns.
