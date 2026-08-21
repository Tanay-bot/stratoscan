/** Deterministic pseudo-random float for stable illustrative charts */
export function seeded(seed: number) {
  let s = seed
  return () => {
    s = (s * 9301 + 49297) % 233280
    return s / 233280
  }
}

export interface Point {
  x: number
  y: number
}

/**
 * Generate a mean-reverting-ish random walk, normalized into a viewBox band.
 * Used for illustrative equity curves — deterministic per seed, no live data.
 */
export function makeSeries(
  rng: () => number,
  n: number,
  drift: number,
  vol: number,
  yTop = 8,
  yBottom = 68,
): Point[] {
  let v = 100
  const raw: number[] = []
  for (let i = 0; i < n; i++) {
    v = v * (1 + drift + (rng() - 0.5) * vol)
    raw.push(v)
  }
  const min = Math.min(...raw)
  const max = Math.max(...raw)
  const span = max - min || 1
  return raw.map((val, i) => ({
    x: (i / (n - 1)) * 100,
    y: yBottom - ((val - min) / span) * (yBottom - yTop),
  }))
}

/** Build an SVG polyline path string from points */
export function toPath(pts: Point[]): string {
  return pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
}
