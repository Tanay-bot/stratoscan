import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * Contrast measured, not eyeballed (Day 9). Text sits on frosted glass over
 * a near-black ground with neon/grape glows behind it, so every text token
 * is checked against the composited colour of each panel type over each
 * backdrop — at both ends of the panel's own gradient.
 */

type RGB = [number, number, number]
const hex = (h: string): RGB => {
  const s = h.replace('#', '')
  return [0, 2, 4].map((i) => parseInt(s.slice(i, i + 2), 16)) as RGB
}
const over = (fg: RGB, a: number, bg: RGB): RGB => fg.map((c, i) => c * a + bg[i] * (1 - a)) as RGB
const lum = ([r, g, b]: RGB) => {
  const f = (v: number) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
}
const ratio = (a: RGB, b: RGB) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

// tailwind.config.js holds TypeScript syntax, so read the tokens from its text.
const tw = readFileSync(resolve(__dirname, '../../tailwind.config.js'), 'utf8')
const token = (group: string, key: string): string => {
  const block = tw.match(new RegExp(`${group}:\\s*\\{([^}]*)\\}`))
  const m = block?.[1].match(new RegExp(`${key}:\\s*'(#[0-9a-fA-F]{6})'`))
  if (!m) throw new Error(`token ${group}.${key} not found in tailwind.config.js`)
  return m[1]
}
const colors = {
  ink: { DEFAULT: token('ink', 'DEFAULT') },
  neon: { DEFAULT: token('neon', 'DEFAULT') },
  grape: { DEFAULT: token('grape', 'DEFAULT') },
  text: { hi: token('text', 'hi'), mid: token('text', 'mid'), low: token('text', 'low') },
}
const ground = hex(colors.ink.DEFAULT)
const backdrops: Record<string, RGB> = {
  ground,
  'neon glow 8%': over(hex(colors.neon.DEFAULT), 0.08, ground),
  'grape glow 12%': over(hex(colors.grape.DEFAULT), 0.12, ground),
}
// From src/index.css: base tint + the lightest point of the white sheen.
const panels: Record<string, { tint: RGB; alpha: number; sheen: number }> = {
  glass: { tint: [14, 14, 22], alpha: 0.55, sheen: 0.055 },
  'glass-strong': { tint: [12, 12, 20], alpha: 0.78, sheen: 0.09 },
}

describe('text contrast on glass panels (WCAG AA, small text ≥ 4.5:1)', () => {
  const css = readFileSync(resolve(__dirname, '../index.css'), 'utf8')

  it('index.css panel definitions are the ones measured here', () => {
    expect(css).toContain('rgba(14, 14, 22, 0.55)')
    expect(css).toContain('rgba(12, 12, 20, 0.78)')
    expect(css).toContain(`--text-low: ${colors.text.low}`)
  })

  for (const token of ['hi', 'mid', 'low'] as const) {
    it(`text-${token} passes on every panel over every backdrop`, () => {
      const fg = hex(colors.text[token])
      for (const [bName, bd] of Object.entries(backdrops)) {
        for (const [pName, p] of Object.entries(panels)) {
          const base = over(p.tint, p.alpha, bd)
          const lightest = over([255, 255, 255], p.sheen, base)
          const worst = Math.min(ratio(fg, base), ratio(fg, lightest))
          expect(worst, `text-${token} on ${pName} over ${bName}`).toBeGreaterThanOrEqual(4.5)
        }
      }
    })
  }
})
