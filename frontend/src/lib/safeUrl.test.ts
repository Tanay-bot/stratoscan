import { describe, expect, it } from 'vitest'
import { safeExternalUrl } from './safeUrl'

describe('safeExternalUrl', () => {
  it('passes https and http absolute URLs through unchanged', () => {
    expect(safeExternalUrl('https://finance.example.com/news/1')).toBe(
      'https://finance.example.com/news/1',
    )
    expect(safeExternalUrl('http://example.com/a')).toBe('http://example.com/a')
  })

  it('rejects javascript: URLs (the XSS vector)', () => {
    expect(safeExternalUrl('javascript:alert(document.cookie)')).toBeNull()
    expect(safeExternalUrl('JAVASCRIPT:alert(1)')).toBeNull() // schemes are case-insensitive
    expect(safeExternalUrl(' javascript:alert(1) ')).toBeNull() // whitespace-padded
  })

  it('rejects other non-http schemes', () => {
    expect(safeExternalUrl('data:text/html,<script>alert(1)</script>')).toBeNull()
    expect(safeExternalUrl('vbscript:msgbox(1)')).toBeNull()
    expect(safeExternalUrl('file:///etc/passwd')).toBeNull()
    expect(safeExternalUrl('ftp://example.com/file')).toBeNull()
  })

  it('returns null for missing, malformed, and relative URLs', () => {
    expect(safeExternalUrl(null)).toBeNull()
    expect(safeExternalUrl(undefined)).toBeNull()
    expect(safeExternalUrl('')).toBeNull()
    expect(safeExternalUrl('not a url')).toBeNull()
    expect(safeExternalUrl('/relative/path')).toBeNull()
  })
})
