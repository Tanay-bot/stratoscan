import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class'],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: '#0a0a0f',
          deep: '#07070b',
          panel: '#0e0e16',
          raise: '#14141f',
          line: 'rgba(255,255,255,0.08)',
        },
        neon: {
          DEFAULT: '#00d4ff',
          soft: '#00b7e0',
          dim: '#0a7a94',
        },
        grape: {
          DEFAULT: '#7c3aed',
          soft: '#a78bfa',
        },
        gain: {
          DEFAULT: '#16c784',
          soft: '#34d399',
        },
        loss: {
          DEFAULT: '#ea3943',
          soft: '#fb7185',
        },
        text: {
          hi: '#f2f4fb',
          mid: '#9aa3b8',
          low: '#7c86a0',
        },
      },
      fontFamily: {
        sans: ['"Inter Variable"', 'Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '0.9rem' }],
      },
      boxShadow: {
        glow: '0 0 24px rgba(0, 212, 255, 0.35)',
        'glow-sm': '0 0 12px rgba(0, 212, 255, 0.28)',
        'glow-grape': '0 0 24px rgba(124, 58, 237, 0.35)',
        card: '0 20px 50px -20px rgba(0, 0, 0, 0.65)',
      },
      backdropBlur: {
        xs: '2px',
      },
      keyframes: {
        'ticker': {
          '0%': { transform: 'translateX(0)' },
          '100%': { transform: 'translateX(-50%)' },
        },
        'pulse-ring': {
          '0%': { transform: 'scale(0.8)', opacity: '0.7' },
          '100%': { transform: 'scale(1.6)', opacity: '0' },
        },
        'shimmer': {
          '0%': { backgroundPosition: '-600px 0' },
          '100%': { backgroundPosition: '600px 0' },
        },
        'scanline': {
          '0%': { top: '0%' },
          '100%': { top: '100%' },
        },
      },
      animation: {
        ticker: 'ticker 32s linear infinite',
        'pulse-ring': 'pulse-ring 2.2s ease-out infinite',
        shimmer: 'shimmer 1.6s linear infinite',
        scanline: 'scanline 7s linear infinite',
      },
    },
  },
  plugins: [],
} satisfies Config
