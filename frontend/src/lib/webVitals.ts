import * as Sentry from '@sentry/react'
import { onCLS, onFCP, onINP, onLCP, onTTFB } from 'web-vitals'

/**
 * Core Web Vitals → Sentry.
 *
 * Every metric is reported through `Sentry.metrics.distribution` so we get
 * 100% coverage in the Metrics feed (the tracing-based Web Vitals module is
 * sampled at `tracesSampleRate`, so it only sees 10% of page loads).
 *
 * Timed metrics carry `unit: 'millisecond'`; CLS is unitless.
 *
 * No-op unless a Sentry DSN is present (same gate as `Sentry.init` in
 * main.tsx), so local dev without a DSN never touches the network.
 */

export interface WebVitalsReporter {
  (name: string, value: number, unit?: string): void
}

let initialized = false

function defaultReporter(name: string, value: number, unit?: string): void {
  try {
    Sentry.metrics.distribution(name, value, {
      unit,
      attributes: { metric: name },
    })
  } catch {
    // Observability must never take the app down.
  }
}

/**
 * Register web-vitals callbacks that forward each metric to Sentry.
 *
 * @param enabled Explicitly enable reporting. Defaults to "a DSN is set",
 *   mirroring the Sentry.init gate. Pass false to force no-op (tests).
 * @param reporter Injectable sink for tests; defaults to Sentry metrics.
 */
export function initWebVitals(
  enabled: boolean = Boolean(import.meta.env.VITE_SENTRY_DSN),
  reporter: WebVitalsReporter = defaultReporter,
): void {
  if (initialized) return
  initialized = true
  if (!enabled) return

  onLCP((m) => reporter('web-vitals.lcp', m.value, 'millisecond'))
  onCLS((m) => reporter('web-vitals.cls', m.value))
  onINP((m) => reporter('web-vitals.inp', m.value, 'millisecond'))
  onFCP((m) => reporter('web-vitals.fcp', m.value, 'millisecond'))
  onTTFB((m) => reporter('web-vitals.ttfb', m.value, 'millisecond'))
}

/** Re-arm for tests. Not part of the public contract. */
export function __resetWebVitals(): void {
  initialized = false
}
