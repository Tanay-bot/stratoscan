// ESLint 9 flat config — Stratoscan frontend
//
// Gates: type errors via @typescript-eslint, React hooks rules (exhaustive
// deps, rules-of-hooks), and jsx-a11y (accessible markup). Prettier
// formatting is enforced separately via `npm run format:check`; this config
// disables stylistic rules that clash with prettier (eslint-config-prettier).

import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import jsxA11y from 'eslint-plugin-jsx-a11y'
import prettier from 'eslint-config-prettier'

export default tseslint.config(
  {
    ignores: ['dist', 'node_modules', '*.tsbuildinfo', 'verify_*.mjs'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    plugins: {
      'react-hooks': reactHooks,
      'jsx-a11y': jsxA11y,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      ...jsxA11y.configs.recommended.rules,
      // TS is the type-checker here — JS's no-undef would false-positive on
      // DOM/import.meta globals that TS already validates.
      'no-undef': 'off',
      // Allow unused function args prefixed with _ (common in error handlers).
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // ── React Compiler-era rules: OFF ────────────────────────────────
      // eslint-plugin-react-hooks v6+ ships experimental rules designed for
      // apps adopting the React Compiler (set-state-in-effect, refs, purity,
      // etc.). Stratoscan is NOT compiler-enabled, and these rules flag the
      // codebase's idiomatic patterns (fetch-on-mount with a loading flag,
      // animation refs read in render, Date.now() for chart ticks) as
      // errors. Enabling them would force a compiler migration as the only
      // clean fix — out of scope. The classic, stable contract we DO enforce
      // is rules-of-hooks + exhaustive-deps (above). Revisit if the app
      // adopts the React Compiler.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/refs': 'off',
      'react-hooks/purity': 'off',
    },
  },
  prettier,
)
