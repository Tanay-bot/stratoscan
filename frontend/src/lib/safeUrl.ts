/**
 * Allowlist an external URL before it's rendered into an `href`.
 *
 * Article URLs come from the backend news pipeline (Finnhub → DB → API), so
 * a poisoned `javascript:` / `data:` / `vbscript:` scheme would otherwise be
 * rendered into a clickable link — React auto-escapes attribute VALUES but
 * does NOT block dangerous schemes, and clicking such a link executes
 * attacker-controlled code in the app's origin.
 *
 * Returns the URL unchanged when it is a safe http(s) absolute link, or null
 * when it is missing, malformed, relative, or carries any other scheme.
 * Callers render plain text instead of an anchor when null.
 */
export function safeExternalUrl(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? url : null
  } catch {
    return null
  }
}
