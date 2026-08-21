import { defineConfig, loadEnv, type Plugin, type PluginOption } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import { sentryVitePlugin } from '@sentry/vite-plugin'

/**
 * P0 guard: demo mode must NEVER ship to production.
 *
 * VITE_DEMO_MODE=true makes every API call return synthetic data through
 * the typed interface — great for a backend-free preview, catastrophic in
 * a production build where users would silently get fake numbers. Failing
 * the BUILD (not just a runtime check) means the pipeline catches it
 * before anything deploys.
 */
function demoModeGuard(): Plugin {
  return {
    name: 'demo-mode-guard',
    configResolved(config) {
      const mode = config.mode // 'development' | 'production' (vite build)
      const env = loadEnv(mode, path.resolve(__dirname), '')
      // Mirror Vite's own env resolution order: process.env (an explicit
      // operator override, e.g. CI setting VITE_DEMO_MODE=false) wins over
      // .env files (which include .env.local — where dev machines commonly
      // enable demo mode). The guard and the bundle can never disagree.
      const demo = (process.env.VITE_DEMO_MODE ?? env.VITE_DEMO_MODE ?? 'false') === 'true'
      if (mode === 'production' && demo) {
        throw new Error(
          '[demo-mode-guard] VITE_DEMO_MODE=true is set for a production build. ' +
            'Refusing to ship fake data. Set VITE_DEMO_MODE=false (or remove it ' +
            'from .env.production) and rebuild.',
        )
      }
    },
  }
}

// `command` is intentionally destructured so the Sentry plugin can be
// gated to real builds (`vite build`) — it is never active during `vite dev`.
export default defineConfig(({ command }) => {
  const plugins: PluginOption[] = [react(), demoModeGuard()]

  // Mirrors demoModeGuard: process.env (explicit operator override, e.g. CI)
  // wins over .env* files, exactly as Vite itself resolves VITE_* vars.
  const env = loadEnv(command === 'build' ? 'production' : 'development', path.resolve(__dirname), '')

  // Sentry sourcemap upload — only for production builds with the FULL
  // credential set (token + org + project). Partial configs (e.g. a CI job
  // with only a token) skip the plugin cleanly instead of failing the build
  // with a confusing Sentry error. Sourcemaps are uploaded to the release and
  // then deleted from dist/, so the shipped bundle never exposes readable
  // source to browsers. The plugin injects `__sentry_release__` into the
  // bundle, which @sentry/react picks up automatically — no main.tsx change
  // needed for release association.
  if (
    command === 'build' &&
    process.env.SENTRY_AUTH_TOKEN &&
    process.env.SENTRY_ORG &&
    process.env.SENTRY_PROJECT
  ) {
    // sentryVitePlugin returns an array of plugins — spread, not push.
    plugins.push(
      ...sentryVitePlugin({
        org: process.env.SENTRY_ORG,
        project: process.env.SENTRY_PROJECT,
        authToken: process.env.SENTRY_AUTH_TOKEN,
        sourcemaps: {
          filesToDeleteAfterUpload: ['dist/**/*.map'],
        },
        release: {
          name: process.env.VITE_RELEASE_VERSION || env.VITE_RELEASE_VERSION || 'dev',
        },
        telemetry: false,
        debug: process.env.SENTRY_DEBUG === 'true',
      }),
    )
  }

  return {
    plugins,
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: 5173,
      host: true,
      // Dev proxy: /api/* → http://localhost:8000/* so the browser never sees
      // cross-origin traffic. The backend routes have no /api prefix, so the
      // proxy strips it before forwarding. Set VITE_API_URL to an absolute URL
      // (prod) to bypass the proxy entirely.
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          rewrite: (p) => p.replace(/^\/api/, ''),
        },
      },
    },
    build: {
      // Sourcemaps are required for the Sentry plugin to upload readable
      // stack traces. With SENTRY_AUTH_TOKEN set they're removed from dist/
      // after upload; without it they remain (harmless, dist is not deployed
      // from token-less machines).
      sourcemap: true,
      rollupOptions: {
        output: {
          manualChunks: {
            react: ['react', 'react-dom', 'react-router-dom'],
            charts: ['recharts'],
            motion: ['framer-motion'],
            forms: ['react-hook-form', 'zod', '@hookform/resolvers'],
            radix: [
              '@radix-ui/react-dialog',
              '@radix-ui/react-select',
              '@radix-ui/react-slider',
              '@radix-ui/react-switch',
              '@radix-ui/react-tabs',
              '@radix-ui/react-tooltip',
            ],
          },
        },
      },
    },
  }
})
