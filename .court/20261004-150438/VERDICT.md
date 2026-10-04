# The verdict: HUNG JURY, 3 to 3

**The case:** Launch Stratoscan — a free, natural-language stock screener and 10-year backtester for US stocks — as a product people will come back to.

**The charge:** The case fails as stated: it should not go ahead the way it is described.

## How the jury voted

| Juror | Vote | Why |
|---|---|---|
| The First-Time Customer | GUILTY | As someone who has never heard of this, I'd only try it if I could run a quick backtest without signing up, and right now I'd have to make an account, maybe dig a verification email out of spam, and then wait minutes for a result, so I'd close the tab and stay with what I already use. |
| The Statistician | GUILTY | A kill criterion only works if it gets a big enough sample and has a line that counts as failure, and this plan has neither: there is no distribution channel built to bring users in and no threshold, so whatever the second-backtest share turns out to be on a handful of users will be noise, not a verdict on whether people come back. |
| The Small-Business Owner | NOT GUILTY | I've opened plenty of things without knowing who'd walk in, and when the rent is a couple of dollars and one person can keep the lights on, a slow month costs next to nothing; the gaps the prosecution lists are things you only learn by opening the doors, provided the founder finishes the remaining plan days first as described. |
| The Parent | NOT GUILTY | For the people involved, little is at stake: users pay nothing and get numbers that have been checked against first principles, with a disclaimer next to every result, and the founder's outlay is a few dollars a month, so the risk is small and launching is the only way to learn whether anyone comes back. |
| The Retired Judge | NOT GUILTY | The prosecution carries the burden, and what it proves is that the product is unfinished and unproven, which the case openly admits; it does not prove that the plan fails, and on the data licence it claimed more than the record supports. |
| The Teenager | GUILTY | Nobody I know is waiting minutes for a backtest that nobody sent them a link to, and the stuff meant to make me come back next week isn't even built yet, so as it's described right now this is a cool engine nobody shows up for twice. |

## Charges that stuck

1. **The way in isn't built.** The no-signup trial and shareable result links are still "ahead", so a stranger can't try the product without signing up. All three guilty jurors leaned on this.
2. **The first result is too slow.** A first backtest "can take minutes on cold data", and people won't wait that long to see what a product does. Two jurors cited it.
3. **Signing up is hard and capped.** Signups stop at 50 a day, and verification emails may land in spam. Two jurors cited it.

## What the defense got right

1. **Failing costs almost nothing.** A server at about $2–3 a month and zero outside calls per screen make a launch a cheap experiment.
2. **The test is set in advance.** The founder chose the second-backtest share as the kill criterion before launch, and the missing pieces are already days in a written plan.

## Conditions for acquittal

1. **Ship the no-signup trial and share links before launch, and say so.** Make "launch only after these work" a written rule.
2. **Get a first backtest under 60 seconds.** For example, pre-load the data for the likely first queries so a stranger never hits a cold cache.
3. **Put numbers on the kill criterion.** Write down the second-backtest share and day-2/day-7 return that would count as failure, the minimum number of users to judge on, and where those users will come from.

## The sentence

Do not launch on the current date. Launch when the gate is met. Finish the trial, the share links and the fast first run, write down the failure line before anyone arrives, and then open. Test the yfinance fallback while you wait, so a cutoff doesn't take the product down with it.

_The full trial is in /home/user/stratoscan/.court/20261004-150438: the opening statements, the rebuttals and every juror's vote._

_Made with [/court](https://github.com/alexyc9381/court-skill), a free Claude Code skill by Alex Chen ([@nocodealex](https://instagram.com/nocodealex))._
