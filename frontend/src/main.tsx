import React from 'react'
import ReactDOM from 'react-dom/client'
import * as Sentry from '@sentry/react'
import '@fontsource-variable/inter'
import '@fontsource/jetbrains-mono'
import './index.css'
import App from './App'
import { useAuth } from '@/store/auth'
import { initWebVitals } from '@/lib/webVitals'

// Sentry error monitoring — disabled unless VITE_SENTRY_DSN is set.
// Enable in production: set VITE_SENTRY_DSN in Cloudflare Pages env vars.
const SENTRY_DSN = import.meta.env.VITE_SENTRY_DSN as string | undefined
if (SENTRY_DSN) {
  Sentry.init({
    dsn: SENTRY_DSN,
    environment: import.meta.env.MODE,
    tracesSampleRate: 0.1, // sample 10% of transactions (free-tier friendly)
    // BrowserTracing auto-captures Web Vitals (LCP/CLS/INP/FCP/TTFB) into
    // pageload transactions — the source of Sentry's Web Vitals module.
    integrations: [Sentry.browserTracingIntegration()],
    beforeSend(event) {
      // Never upload tokens or passwords if they ever leak into an error
      delete event.extra?.auth
      return event
    },
  })
}

// Forward Core Web Vitals to Sentry metrics (100% coverage, unsampled).
// No-op unless VITE_SENTRY_DSN is set — same gate as Sentry.init above.
initWebVitals()

// Kick off auth bootstrap (token revalidation) before first paint of /app
void useAuth.getState().bootstrap()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Sentry.ErrorBoundary
      fallback={
        <div
          style={{
            minHeight: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: '#0a0a0f',
            color: '#e8e8f0',
            fontFamily: 'Inter, system-ui, sans-serif',
            padding: 24,
          }}
        >
          <div style={{ maxWidth: 420, textAlign: 'center' }}>
            <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 8 }}>Something went wrong</h1>
            <p style={{ color: '#9a9ab0', marginBottom: 24 }}>
              A unexpected error crashed this view. Reload to continue — we've been notified
              automatically.
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              style={{
                background: '#00d4ff',
                color: '#0a0a0f',
                border: 'none',
                borderRadius: 10,
                padding: '10px 22px',
                fontSize: 15,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Reload app
            </button>
          </div>
        </div>
      }
    >
      <App />
    </Sentry.ErrorBoundary>
  </React.StrictMode>,
)
