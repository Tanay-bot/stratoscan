#!/usr/bin/env node
/* global Buffer, console */
/**
 * Generates public/og-image.png — a branded 1200×630 share image
 * (dark gradient, subtle grid, neon candlesticks) with no external deps.
 * Run: node scripts/generate-og-image.mjs
 */
import { deflateSync } from 'node:zlib'
import { writeFileSync, mkdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const W = 1200
const H = 630
const px = new Uint8Array(W * H * 4)

// ── helpers ────────────────────────────────────────────────────────────────
const setPx = (x, y, r, g, b, a = 255) => {
  if (x < 0 || y < 0 || x >= W || y >= H) return
  const i = (y * W + x) * 4
  px[i] = r; px[i + 1] = g; px[i + 2] = b; px[i + 3] = a
}

const lerp = (a, b, t) => Math.round(a + (b - a) * t)

// ── background: vertical gradient #0a0a0f → #141433 with a purple bloom ───
for (let y = 0; y < H; y++) {
  const t = y / H
  const r = lerp(10, 28, t)
  const g = lerp(10, 22, t)
  const b = lerp(15, 58, t)
  for (let x = 0; x < W; x++) setPx(x, y, r, g, b)
}

// subtle horizontal grid lines
for (let y = 0; y < H; y += 36) {
  for (let x = 0; x < W; x++) {
    const [r, g, b] = [px[(y * W + x) * 4], px[(y * W + x) * 4 + 1], px[(y * W + x) * 4 + 2]]
    setPx(x, y, Math.min(255, r + 8), Math.min(255, g + 8), Math.min(255, b + 8))
  }
}

// ── candlesticks (brand: cyan up, violet down) ─────────────────────────────
const sticks = [
  { x: 220, o: 430, c: 360, hi: 330, lo: 460 },
  { x: 320, o: 380, c: 420, hi: 340, lo: 470 },
  { x: 420, o: 430, c: 350, hi: 320, lo: 460 },
  { x: 520, o: 360, c: 310, hi: 280, lo: 400 },
  { x: 620, o: 330, c: 380, hi: 300, lo: 420 },
  { x: 720, o: 400, c: 340, hi: 310, lo: 440 },
  { x: 820, o: 350, c: 290, hi: 260, lo: 390 },
  { x: 920, o: 300, c: 330, hi: 270, lo: 370 },
  { x: 1020, o: 320, c: 250, hi: 230, lo: 360 },
]
const BODY_W = 54

sticks.forEach((s) => {
  const up = s.c < s.o
  const [r, g, b] = up ? [0, 212, 255] : [124, 58, 237]
  // wick
  for (let y = s.hi; y <= s.lo; y++) setPx(s.x, y, r, g, b)
  // body
  const top = Math.min(s.o, s.c)
  const bot = Math.max(s.o, s.c)
  for (let y = top; y <= bot; y++) {
    for (let x = s.x - BODY_W / 2; x <= s.x + BODY_W / 2; x++) setPx(x, y, r, g, b)
  }
  // soft glow on the last (freshest) candle
  if (s === sticks[sticks.length - 1]) {
    for (let dy = -6; dy <= 6; dy++) {
      for (let dx = -6; dx <= 6; dx++) {
        if (dx * dx + dy * dy > 36) continue
        const x = s.x + dx, y = (s.c + s.o) / 2 + dy
        setPx(x, y, 0, 212, 255, 90) // additive-ish cyan glow
      }
    }
  }
})

// ── PNG encoding (zlib + hand-rolled CRC32, zero deps) ─────────────────────
const CRC_TABLE = (() => {
  const t = new Int32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c
  }
  return t
})()

function crc32(buf) {
  let c = 0xffffffff
  for (let i = 0; i < buf.length; i++) c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

function chunk(type, data) {
  const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
  const typeBuf = Buffer.from(type, 'ascii')
  const crcBuf = Buffer.alloc(4)
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])))
  return Buffer.concat([len, typeBuf, data, crcBuf])
}

const ihdr = Buffer.alloc(13)
ihdr.writeUInt32BE(W, 0)
ihdr.writeUInt32BE(H, 4)
ihdr[8] = 8   // bit depth
ihdr[9] = 6   // color type RGBA
// 10-12: compression/filter/interlace = 0

// scanlines: filter byte 0 + RGBA rows
const raw = Buffer.alloc(H * (1 + W * 4))
for (let y = 0; y < H; y++) {
  const rowStart = y * (1 + W * 4)
  raw[rowStart] = 0
  Buffer.from(px.buffer, y * W * 4, W * 4).copy(raw, rowStart + 1)
}

const png = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  chunk('IHDR', ihdr),
  chunk('IDAT', deflateSync(raw, { level: 9 })),
  chunk('IEND', Buffer.alloc(0)),
])

const out = join(dirname(fileURLToPath(import.meta.url)), '..', 'public', 'og-image.png')
mkdirSync(dirname(out), { recursive: true })
writeFileSync(out, png)
console.log(`Wrote ${out} (${png.length} bytes, ${W}x${H})`)
