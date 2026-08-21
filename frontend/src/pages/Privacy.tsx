import { LegalPage } from '@/components/legal/LegalPage'

const SECTIONS = [
  {
    id: 'collection',
    title: 'Data We Collect',
    body: (
      <>
        <p>We collect only the information needed to run the Service:</p>
        <ul>
          {' '}
          <li>
            <strong className="text-text-hi">Account information</strong> — your email address and a
            securely hashed password when you register.
          </li>
          <li>
            <strong className="text-text-hi">Saved content</strong> — screeners and backtests you
            explicitly save to your account.
          </li>
          <li>
            <strong className="text-text-hi">Local preferences</strong> — drafts, watchlists, and UI
            settings stored in your browser on your device.
          </li>
          <li>
            <strong className="text-text-hi">Usage data</strong> — anonymized logs of page views,
            feature usage, and API calls, used to operate and improve the Service.
          </li>
          <li>
            <strong className="text-text-hi">Device &amp; technical data</strong> — browser type,
            operating system, and error reports (including Sentry diagnostics), used to keep the
            Service stable.
          </li>
        </ul>
        <p>
          We do <em>not</em> collect your brokerage credentials, trades, or portfolio, and we never
          require financial information to use the Service.
        </p>
      </>
    ),
  },
  {
    id: 'usage',
    title: 'How We Use Your Data',
    body: (
      <>
        <p>We use your data only for legitimate, documented purposes:</p>{' '}
        <ul>
          <li>Creating and authenticating your account.</li>
          <li>Storing and restoring the screeners and backtests you save to your account.</li>
          <li>Operating, securing, and improving the Service and its performance.</li>
          <li>
            Sending essential service communications (e.g., account verification or security
            alerts). We do not send promotional email.
          </li>
          <li>Diagnosing and fixing errors via crash reporting.</li>
        </ul>
      </>
    ),
  },
  {
    id: 'storage',
    title: 'Data Storage & Security',
    body: (
      <>
        <p>
          Your data is stored on encrypted infrastructure with access limited to authorized
          personnel. Passwords are hashed using strong, salted algorithms (PBKDF2) and are never
          stored or transmitted in plain text. All traffic between your browser and the Service is
          encrypted in transit (HTTPS/TLS).
        </p>
        <p>
          While we implement industry-standard safeguards, no method of transmission or storage is
          completely secure. We encourage you to use a strong, unique password and enable any
          additional security features we offer.
        </p>
      </>
    ),
  },
  {
    id: 'sharing',
    title: 'Data Sharing',
    body: (
      <>
        <p>
          We do <strong>not</strong> sell your personal data. Data stored in your browser (drafts,
          watchlists, preferences) never leaves your device. We share data only in these limited
          circumstances:
        </p>
        <ul>
          <li>
            <strong className="text-text-hi">Service providers</strong> — trusted vendors (e.g.,
            hosting, email delivery, error monitoring) that process data on our behalf under
            contractual obligations to protect it.
          </li>
          <li>
            <strong className="text-text-hi">Legal requirements</strong> — if required by law,
            regulation, or valid legal process, or to protect the rights, safety, and property of
            Stratoscan, our users, or the public.
          </li>
          <li>
            <strong className="text-text-hi">Business transfers</strong> — in connection with a
            merger, acquisition, or sale of assets, with notice to you where required.
          </li>
        </ul>
        <p>
          Your saved screens and backtests are private to your account and are never shared publicly
          or with other users.
        </p>
      </>
    ),
  },
  {
    id: 'rights',
    title: 'Your Rights',
    body: (
      <>
        <p>Depending on your jurisdiction, you may have the right to:</p>
        <ul>
          <li>
            <strong className="text-text-hi">Access</strong> — request a copy of the personal data
            we hold about you.
          </li>
          <li>
            <strong className="text-text-hi">Correction</strong> — update inaccurate information.
          </li>
          <li>
            <strong className="text-text-hi">Deletion</strong> — request that we delete your account
            and associated data (also available directly in Settings).
          </li>
          <li>
            <strong className="text-text-hi">Portability</strong> — export your data in a common,
            machine-readable format.
          </li>
          <li>
            <strong className="text-text-hi">Objection / restriction</strong> — object to or
            restrict certain processing where applicable.
          </li>
        </ul>
        <p>
          To exercise any of these rights, contact us using the details below. We will respond
          within the timeframe required by applicable law, and never discriminate against you for
          exercising your rights.
        </p>
      </>
    ),
  },
  {
    id: 'cookies',
    title: 'Cookies & Local Storage',
    body: (
      <>
        <p>
          We use cookies and browser local storage to keep you signed in, remember your preferences,
          and understand how the Service is used. Specifically:
        </p>
        <ul>
          <li>
            <strong className="text-text-hi">Authentication</strong> — a token that keeps you logged
            in across sessions.
          </li>
          <li>
            <strong className="text-text-hi">Preferences</strong> — local settings such as your
            saved screener drafts and UI choices.
          </li>
          <li>
            <strong className="text-text-hi">Analytics &amp; error monitoring</strong> — anonymized
            usage and crash data (e.g., Sentry).
          </li>
        </ul>
        <p>
          You can clear or block cookies through your browser settings; doing so may sign you out or
          disable certain features.
        </p>
      </>
    ),
  },
  {
    id: 'retention',
    title: 'Data Retention',
    body: (
      <>
        <p>
          We retain your personal data only as long as necessary to provide the Service, comply with
          legal obligations, and resolve disputes. When you delete your account, we delete or
          anonymize your personal data in line with our retention schedules, except where we are
          legally required to keep limited records.
        </p>
      </>
    ),
  },
  {
    id: 'children',
    title: 'Children’s Privacy',
    body: (
      <>
        <p>
          The Service is not directed to individuals under the age of 18 (or the applicable age of
          majority). We do not knowingly collect personal data from children. If you believe a child
          has provided us personal data, contact us and we will delete it.
        </p>
      </>
    ),
  },
  {
    id: 'changes',
    title: 'Changes to This Policy',
    body: (
      <>
        <p>
          We may update this Privacy Policy as the Service evolves. Material changes will be
          communicated through the Service, and the “last updated” date at the top of this page will
          be revised accordingly. Continued use of the Service after changes take effect constitutes
          acceptance of the updated policy.
        </p>
      </>
    ),
  },
  {
    id: 'contact',
    title: 'Contact Information',
    body: (
      <>
        <p>
          For privacy questions, data requests, or to exercise your rights, contact us through the
          Contact page or email the operator directly. We aim to respond to privacy inquiries within
          a few business days.
        </p>
      </>
    ),
  },
]

export function Privacy() {
  return (
    <LegalPage
      eyebrow="Legal"
      title="Privacy Policy"
      intro="How Stratoscan collects, uses, and protects your data. Short version: we collect as little as possible, and we never sell it."
      lastUpdated="August 6, 2026"
      sections={SECTIONS}
    />
  )
}
