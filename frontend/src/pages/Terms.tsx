import { LegalPage } from '@/components/legal/LegalPage'

const SECTIONS = [
  {
    id: 'acceptance',
    title: 'Acceptance of Terms',
    body: (
      <>
        <p>
          By accessing or using Stratoscan (“the Service”), you agree to be bound by these Terms of
          Service and our Privacy Policy. If you do not agree to any part of these terms, please
          discontinue use of the Service immediately.
        </p>
        <p>
          You must be at least 18 years old, or the age of majority in your jurisdiction, to use the
          Service. By using the Service you represent that you meet this requirement.
        </p>
      </>
    ),
  },
  {
    id: 'obligations',
    title: 'User Obligations',
    body: (
      <>
        <p>When you use the Service, you agree to:</p>
        <ul>
          <li>
            Provide accurate, current, and complete information when creating an account and keep
            your credentials confidential.
          </li>
          <li>Use the Service only for lawful purposes and in accordance with these Terms.</li>
          <li>
            Not attempt to access, scrape, or probe the Service’s systems, APIs, or data beyond your
            authorized usage, and not circumvent rate limits or access controls.
          </li>
          <li>
            Not upload, transmit, or solicit content that is unlawful, harmful, or infringing.
          </li>
          <li>
            Not reverse-engineer, resell, or redistribute the Service or its data in ways that
            compete with or abuse the platform.
          </li>
        </ul>
        <p>
          You are responsible for all activity that occurs under your account. If you believe your
          account has been compromised, contact us immediately.
        </p>
      </>
    ),
  },
  {
    id: 'intellectual-property',
    title: 'Intellectual Property',
    body: (
      <>
        <p>
          The Service — including its software, design, branding, algorithms, and original content —
          is owned by Stratoscan and protected by intellectual property laws. You receive a limited,
          non-exclusive, non-transferable license to use the Service for personal, non-commercial
          research, subject to these Terms.
        </p>
        <p>
          Market data, news, and third-party content displayed through the Service remain the
          property of their respective owners and are provided for informational purposes only.
          Nothing in these Terms grants you ownership of any content or data you did not create.
        </p>
      </>
    ),
  },
  {
    id: 'not-financial-advice',
    title: 'Not Financial Advice',
    body: (
      <>
        <p>
          Stratoscan is a research tool, not a stock-picking service, broker, or investment advisor.
          Screen results, backtests, metrics, and news sentiment are generated for informational and
          educational purposes only.
        </p>
        <p>
          Nothing on the Service constitutes financial, investment, legal, or tax advice, or a
          recommendation to buy or sell any security. Past performance, including simulated
          backtests, does not guarantee future results. You alone are responsible for your
          investment decisions.
        </p>
      </>
    ),
  },
  {
    id: 'disclaimers',
    title: 'Disclaimers',
    body: (
      <>
        <p>
          The Service is provided on an “as is” and “as available” basis, without warranties of any
          kind, whether express or implied, including but not limited to implied warranties of
          merchantability, fitness for a particular purpose, and non-infringement.
        </p>
        <p>
          We do not warrant that the Service will be uninterrupted, error-free, or that data —
          including prices, fundamentals, news, or backtest results — will be accurate, complete, or
          current. Data may be delayed or subject to third-party errors.
        </p>
      </>
    ),
  },
  {
    id: 'limitation-of-liability',
    title: 'Limitation of Liability',
    body: (
      <>
        <p>
          To the maximum extent permitted by law, Stratoscan and its operators shall not be liable
          for any indirect, incidental, special, consequential, or punitive damages, or for any loss
          of profits, data, or goodwill, arising out of or related to your use of, or inability to
          use, the Service.
        </p>
        <p>
          Our total aggregate liability for all claims arising from the Service shall not exceed the
          amount you paid us (if any) in the twelve months preceding the claim. Because the Service
          is provided free of charge, this liability cap may be zero.
        </p>
      </>
    ),
  },
  {
    id: 'termination',
    title: 'Termination',
    body: (
      <>
        <p>
          You may stop using the Service at any time and delete your account through the Settings
          page or by contacting us. We may suspend or terminate your access if you violate these
          Terms, misuse the Service, or if we are required to do so by law.
        </p>
        <p>
          Upon termination, your right to use the Service ceases immediately. Sections that by their
          nature should survive — including intellectual property, disclaimers, limitation of
          liability, and governing law — will survive termination.
        </p>
      </>
    ),
  },
  {
    id: 'changes',
    title: 'Changes to These Terms',
    body: (
      <>
        <p>
          We may update these Terms from time to time to reflect changes in the Service or legal
          requirements. Material changes will be communicated through the Service (for example, a
          notice or updated “last updated” date).
        </p>
        <p>
          Your continued use of the Service after changes take effect constitutes acceptance of the
          revised Terms. If you do not agree with the revised Terms, you should stop using the
          Service.
        </p>
      </>
    ),
  },
  {
    id: 'governing-law',
    title: 'Governing Law',
    body: (
      <>
        <p>
          These Terms are governed by and construed in accordance with the laws of the jurisdiction
          in which Stratoscan operates, without regard to its conflict-of-law principles.
        </p>
        <p>
          Any disputes arising under these Terms will be resolved in the competent courts of that
          jurisdiction. If any provision of these Terms is found to be unenforceable, the remaining
          provisions remain in full force and effect.
        </p>
      </>
    ),
  },
  {
    id: 'contact',
    title: 'Contact',
    body: (
      <>
        <p>
          Questions about these Terms? Contact us through the Contact page or reach the operator
          directly. We aim to respond within a few business days.
        </p>
      </>
    ),
  },
]

export function Terms() {
  return (
    <LegalPage
      eyebrow="Legal"
      title="Terms of Service"
      intro="The agreement that governs your use of Stratoscan. Read it once — it protects both of us."
      lastUpdated="August 6, 2026"
      sections={SECTIONS}
    />
  )
}
