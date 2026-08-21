import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { NotFound } from './NotFound'
import { AppShell } from '@/components/layout/AppShell'

// P1-11: every page's main container must expose the #main-content target so
// the SkipToContent link (App.tsx) has somewhere to jump. We spot-check the
// two structurally distinct families: a standalone public page (NotFound)
// and the shared protected app shell (AppShell) that wraps Dashboard,
// Screener, Backtest, News, Settings and StockDetail.
describe('skip-to-content targets', () => {
  it('NotFound exposes a focusable #main-content container', () => {
    render(
      <MemoryRouter>
        <NotFound />
      </MemoryRouter>,
    )
    const target = document.getElementById('main-content')
    expect(target).not.toBeNull()
    // tabIndex -1: programmatically focusable, but NOT in the tab order.
    expect(target?.getAttribute('tabindex')).toBe('-1')
  })

  it('AppShell exposes a focusable #main-content container around app pages', () => {
    render(
      <MemoryRouter initialEntries={['/app']}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/app" element={<div>dashboard content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    )
    const target = document.getElementById('main-content')
    expect(target).not.toBeNull()
    expect(target?.tagName).toBe('MAIN')
    expect(target?.getAttribute('tabindex')).toBe('-1')
  })
})
