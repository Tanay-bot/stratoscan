import { LegalPage } from '@/components/legal/LegalPage'

const SECTIONS = [
  {
    id: 'what-a-backtest-is',
    title: 'What a Backtest Actually Is',
    body: (
      <>
        <p>
          A backtest replays a rule set over historical prices. We take your plain-English idea,
          translate it into explicit buy and sell conditions, walk them forward over up to ten years
          of daily bars, and apply fees and slippage on every fill. The equity curve, Sharpe ratio,
          drawdown and win rate you see are the arithmetic result of those rules on that data.
        </p>
        <p>
          That makes a backtest a way to test whether an idea is <em>internally coherent</em> — not
          a forecast. It answers &ldquo;would this rule have traded this way?&rdquo;, never
          &ldquo;will this make money?&rdquo;.
        </p>
      </>
    ),
  },
  {
    id: 'survivorship',
    title: 'Survivorship Bias',
    body: (
      <>
        <p>
          Our universe is the list of stocks that exist <strong>today</strong>. Companies that were
          acquired, went bankrupt, or were delisted during the tested window are mostly absent from
          it. Any strategy is therefore measured on a slightly luckier population than the one you
          would actually have picked from, and the bias is upward: the losers that vanished are not
          in the sample.
        </p>
        <p>
          We state this rather than silently adjust for it because a private adjustment would be its
          own kind of dishonesty. Read small edges as noise.
        </p>
      </>
    ),
  },
  {
    id: 'look-ahead',
    title: 'Look-Ahead and Data Limits',
    body: (
      <>
        <p>
          Signals are computed from a day&rsquo;s closing price and filled at the next day&rsquo;s
          open, plus fees and slippage &mdash; a stop that gaps through its level fills at the open,
          not at the level. That is a reasonable approximation, not a tick-accurate fill model: real
          orders face spreads, partial fills, halts and liquidity you cannot demand from a daily
          bar.
        </p>
        <p>
          Fundamentals (P/E, market cap, margins) enter at the screening step, where they are the
          most recently stored values — not the values that were public on each historical date.
          Where the engine cannot represent a condition honestly it says so in the data-quality
          notes rather than inventing a number.
        </p>
      </>
    ),
  },
  {
    id: 'data-can-be-wrong',
    title: 'The Data Can Be Wrong or Stale',
    body: (
      <>
        <p>
          Prices come from third-party providers. Adjusted closes, splits and dividends are applied
          by those providers, and their history occasionally contains gaps, bad ticks or later
          restatements. Fundamental fields are refreshed on a schedule, so they can lag the market
          by hours or days, and rows older than a week are excluded from screening entirely.
        </p>
        <p>
          Results carry a data-age flag for exactly this reason. If a figure looks impossible, it
          probably is.
        </p>
      </>
    ),
  },
  {
    id: 'not-advice',
    title: 'Not Investment Advice',
    body: (
      <>
        <p>
          Stratoscan is a research and screening tool. Nothing on this site is a recommendation, a
          solicitation, or personalised advice, and we are not a broker, dealer, or investment
          adviser. We do not know your objectives, your tax position, or your tolerance for loss,
          and a backtest cannot learn them.
        </p>
        <p>
          No content here is a guarantee of any outcome, and no result shown — including any
          &ldquo;outperformance&rdquo; of a benchmark — is a promise about the future. Investing
          involves the risk of losing money, including more than you intended to invest.
        </p>
      </>
    ),
  },
  {
    id: 'your-decision',
    title: 'Your Decisions Are Yours',
    body: (
      <>
        <p>
          Any trade you place is your own decision, made on your own analysis and at your own risk.
          Do not act on a simulated result you do not understand, and do not treat a favourable
          number on this site as evidence that a strategy will work with real money at real scale.
        </p>
        <p>
          If you are unsure whether something is suitable for you, speak to a licensed financial
          professional in your jurisdiction before acting.
        </p>
      </>
    ),
  },
  {
    id: 'contact',
    title: 'Questions',
    body: (
      <>
        <p>
          If any result looks wrong, tell us — a reported discrepancy is worth more to us than a
          clean-looking equity curve. Use the Contact page.
        </p>
      </>
    ),
  },
]

export function Risk() {
  return (
    <LegalPage
      eyebrow="Risk disclaimer"
      title="Risk Disclaimer"
      intro="What a backtest can and cannot tell you — in plain English, before you act on a number."
      lastUpdated="October 5, 2026"
      sections={SECTIONS}
    />
  )
}
