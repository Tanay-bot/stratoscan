import { Link } from 'react-router-dom'
import { LegalPage, type LegalSection } from '@/components/legal/LegalPage'

// Day 14: the evidence behind the numbers. Every figure on this page comes from
// a test or a dated run, and tests/test_public_claims.py checks the ones that
// can drift (the split example, the accuracy-loop line, the drill count).
// Rule for editing: if a check has not run, say so — a verification page that
// oversells refutes itself.

const cell = 'border-b border-white/[0.06] px-3 py-2 text-left align-top'
const head = `${cell} text-2xs font-semibold uppercase tracking-wider text-text-low`

const DRILLS: { n: number; what: string; proves: string; how: string }[] = [
  {
    n: 1,
    what: 'All ten market-data keys dead',
    proves:
      'Retries back off, a hard failure stops fast, a dead refresh writes nothing and never corrupts stored prices.',
    how: 'release gate',
  },
  {
    n: 2,
    what: 'Every language-model provider down',
    proves:
      'A screen says it could not read the query instead of returning the whole market; a backtest fails with a plain message, never internals.',
    how: 'release gate',
  },
  {
    n: 3,
    what: 'Price history download times out',
    proves:
      'The missing symbol is dropped cleanly; no half-empty series reaches the simulator to poison the averages.',
    how: 'release gate',
  },
  {
    n: 4,
    what: 'Rate-limit store (Redis) down',
    proves:
      'The site stays up and limits keep working per server; the fallback is logged so we can see it happened.',
    how: 'release gate',
  },
  {
    n: 5,
    what: 'Email provider down',
    proves:
      'Sign-up still works and says the email was not sent; a password reset answers exactly as it always does, so it cannot reveal whether an address has an account.',
    how: 'every deploy',
  },
  {
    n: 6,
    what: 'Error logging itself failing',
    proves: 'Normal requests are unaffected, and a failing request gets the same plain answer.',
    how: 'every deploy',
  },
  {
    n: 7,
    what: 'Database unreachable mid-request',
    proves:
      'You get “try again shortly” with a retry time — never a crash, and never a sign-out because the database was briefly asleep.',
    how: 'every deploy',
  },
]

const SECTIONS: LegalSection[] = [
  {
    id: 'why',
    title: 'Why This Page Exists',
    body: (
      <>
        <p>
          Every backtesting product shows you numbers. Almost none show you evidence that the
          arithmetic behind them is right. This page is ours: what we check, how, when we last ran
          it — and, just as plainly, what we have not checked.
        </p>
        <p>
          Stratoscan does not tell you what to buy. It shows you whether what you already believe
          has ever been true. That is only worth anything if the replay is honest, so the replay is
          what we test hardest.
        </p>
      </>
    ),
  },
  {
    id: 'golden-set',
    title: 'The Golden Set: Frozen Numbers',
    body: (
      <>
        <p>
          The simulator&rsquo;s rules are fixed: $100,000 starting capital, a 0.1% fee and 0.05%
          slippage on every buy and every sell, signals read at a day&rsquo;s close and filled at
          the next day&rsquo;s open (so a rule can never act on a price it could not yet have seen).
        </p>
        <p>
          Four reference strategies run on fixed synthetic price series &mdash; an RSI pullback in
          an uptrend, buy-and-hold in a flat market, a losing mean-reversion rule, and the momentum
          rule the product actually uses. Their results were captured once and frozen to the last
          decimal: return, Sharpe ratio, drawdown, win rate, trade count, the Monte Carlo
          percentiles, the confidence intervals and the walk-forward verdicts.
        </p>
        <p>
          Any change to fees, slippage, signals or position sizing moves one of those numbers and
          fails the build. A second check recomputes fills, profit and loss, Sharpe, drawdown and
          the benchmark comparison by hand, from first principles, and a third reconciles every
          trade&rsquo;s profit against the actual change in cash. All three run on every change to
          the backend.
        </p>
      </>
    ),
  },
  {
    id: 'accuracy-loop',
    title: 'The Accuracy Loop',
    body: (
      <>
        <p>
          Thirty deliberately varied questions go to the running service. For every result that
          comes back, every number is recomputed independently from the response itself:
        </p>
        <ul>
          <li>
            each trade&rsquo;s profit against the actual cash movement, including fees on both legs;
          </li>
          <li>
            cash conservation &mdash; the final value equals the start plus every trade&rsquo;s
            profit;
          </li>
          <li>the win rate against the count of profitable trades;</li>
          <li>the equity curve&rsquo;s first and last points against the reported return;</li>
          <li>the benchmark and excess return, and the rules echoed back;</li>
          <li>a nonsense question must fail honestly, never return a confident result.</li>
        </ul>
        <p className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-4 text-text-hi">
          Last run: 5 October 2026 &mdash; 3,503 of 3,503 checks passed on 49 results from 19 of the
          30 questions.
        </p>
        <p>
          Read that line carefully. It ran on our offline test stack: synthetic prices and no
          language model, so the 10 questions that need a model to be understood were not run, and
          the one nonsense question failed, as it must. It has not yet been re-run against real
          prices with a model; that run is on the checklist before launch, and this line will carry
          its date.
        </p>
        <p>
          An earlier figure of 14,294 of 14,294 came from a counter that double-counted failed
          questions and ignored ones that returned nothing. We fixed the counter and withdrew that
          figure.
        </p>
      </>
    ),
  },
  {
    id: 'split',
    title: 'Worked Example: A 2-for-1 Split',
    body: (
      <>
        <p>
          A stock trades flat at $100. Halfway through, it splits 2-for-1: from that day on there
          are twice as many shares, each worth $50. Nobody gained or lost a cent. Buy it with
          $100,000 at the start and hold it to the end:
        </p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[420px] border-collapse text-sm">
            <thead>
              <tr>
                <th className={head}>Backtester</th>
                <th className={head}>Final value</th>
                <th className={head}>Reported return</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td className={cell}>Naive (raw prices)</td>
                <td className={`${cell} font-mono tabular`}>$49,875</td>
                <td className={`${cell} font-mono tabular text-loss-soft`}>&minus;50.1%</td>
              </tr>
              <tr>
                <td className={cell}>Stratoscan (adjusted prices)</td>
                <td className={`${cell} font-mono tabular`}>$99,700</td>
                <td className={`${cell} font-mono tabular text-text-hi`}>&minus;0.3%</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p>
          The naive reading sees $100 become $50 and reports a crash that never happened. We
          download raw and split- and dividend-adjusted closes in the same request and trade the
          adjusted series, in which every price before the split is halved &mdash; so the holding is
          flat. The 0.3% that is left is real: a 0.1% fee and 0.05% slippage on the way in and again
          on the way out. The raw close is kept alongside for reference.
        </p>
        <p>
          These exact figures come from a test that runs both readings through the real simulator.
          If the handling ever changes, that test fails before this page can go stale.
        </p>
      </>
    ),
  },
  {
    id: 'drills',
    title: 'Failure Drills',
    body: (
      <>
        <p>
          Seven ways the outside world breaks, each forced on purpose. The rule for all seven: get
          worse gracefully, say so, and never show a crash page or a made-up number.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] border-collapse text-sm">
            <thead>
              <tr>
                <th className={head}>#</th>
                <th className={head}>What breaks</th>
                <th className={head}>What it proves</th>
                <th className={head}>Runs</th>
              </tr>
            </thead>
            <tbody>
              {DRILLS.map((d) => (
                <tr key={d.n}>
                  <td className={`${cell} font-mono tabular text-text-low`}>{d.n}</td>
                  <td className={`${cell} text-text-hi`}>{d.what}</td>
                  <td className={cell}>{d.proves}</td>
                  <td className={`${cell} whitespace-nowrap text-text-low`}>{d.how}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          None of the seven needs a real outage: each failure is injected at the exact point where
          the outside service is called, and the real code path runs. &ldquo;Every deploy&rdquo;
          drills gate every release; &ldquo;release gate&rdquo; drills run in the fuller check
          before a release. All seven last passed on 5 October 2026.
        </p>
      </>
    ),
  },
  {
    id: 'attacks',
    title: 'Attacks, Journeys and Load',
    body: (
      <>
        <ul>
          <li>
            <strong className="text-text-hi">Someone trying to break in.</strong> Every signed-in
            route is called with no token, a tampered one, a forged one, an expired one and one for
            a deleted account; every person&rsquo;s results, saved strategies and share links are
            requested by someone else; hostile text is fed to the filter builder directly and
            through the language model; markup in a query is checked to come out inert on every page
            that shows it, the public share page above all; and the rate limits are attacked with
            forged headers and token cycling.
          </li>
          <li>
            <strong className="text-text-hi">Real people, in a real browser.</strong> Five journeys
            run end to end against a local copy of the whole service: a first visit with no account,
            a new account through to a saved strategy, password recovery, the plan limits, and an
            automated accessibility scan of the main public and signed-in pages.
          </li>
          <li>
            <strong className="text-text-hi">Load.</strong> On our test machine, screens held about
            100 a second with 95% answered in under 0.75 seconds at 64 people at once. Backtests are
            heavier: about 8 to 9 a minute per server, so at 8 at once each waits about a minute.
            Past that, requests queue and then get &ldquo;try again shortly&rdquo; with a retry time
            &mdash; slower, never wrong.
          </li>
        </ul>
      </>
    ),
  },
  {
    id: 'not-covered',
    title: 'What Is Not Covered',
    body: (
      <>
        <p>This is the part most pages leave out. Read it before trusting any result.</p>
        <ul>
          <li>
            <strong className="text-text-hi">Survivorship.</strong> The universe is today&rsquo;s
            listed stocks. Companies that went bankrupt or were bought out during the test window
            are missing, which flatters every strategy.
          </li>
          <li>
            <strong className="text-text-hi">Selection uses today&rsquo;s data.</strong> A screen
            picks stocks on their current fundamentals and indicators, then replays ten years. The
            stocks were chosen with knowledge the past did not have. P/E and other fundamentals are
            never historical, and are never used as a trading rule.
          </li>
          <li>
            <strong className="text-text-hi">The price source.</strong> History comes from Yahoo
            Finance via an unofficial library. A release check compares the screener&rsquo;s stored
            prices and indicators with it for 25 stocks; nobody has independently audited the
            ten-year history itself.
          </li>
          <li>
            <strong className="text-text-hi">Daily bars only.</strong> No intraday prices, spreads,
            partial fills, halts or market impact beyond a volume cap. A stop that gaps through its
            level fills at the open.
          </li>
          <li>
            <strong className="text-text-hi">Costs we do not model.</strong> Taxes, and real-world
            costs beyond the fixed fee and slippage.
          </li>
          <li>
            <strong className="text-text-hi">Partial coverage.</strong> Technical indicators are
            refreshed for the 500 largest stocks and the 45 ETFs; volume is known for the largest
            names only, and a volume filter says so.
          </li>
          <li>
            <strong className="text-text-hi">The written explanation.</strong> The plain-English
            reading of a result is written by a language model. It is shown only if every number in
            it matches the result and it contains no advice; we check those two things, not its
            judgement.
          </li>
          <li>
            <strong className="text-text-hi">Asset classes.</strong> US-listed stocks and a curated
            set of 45 plain ETFs. No leveraged or inverse funds, options, futures, crypto or non-US
            markets.
          </li>
          <li>
            <strong className="text-text-hi">The future.</strong> Nothing here, verified or not,
            says what will happen next. See the{' '}
            <Link to="/risk" className="text-neon hover:underline">
              risk disclaimer
            </Link>
            .
          </li>
        </ul>
      </>
    ),
  },
]

export function Verification() {
  return (
    <LegalPage
      eyebrow="Verification"
      title="How We Check the Numbers"
      intro="What we test, how, when we last ran it — and what we have not checked. If a result looks wrong, tell us."
      lastUpdated="October 5, 2026"
      sections={SECTIONS}
    />
  )
}
