import { useEffect, useRef, useState } from 'react'

/**
 * Measure a container's width so charts can defer mounting until they have
 * real space.
 *
 * recharts' `<ResponsiveContainer>` warns "width(0) … should be greater than
 * 0" when it mounts inside a `display: none` (or zero-width) container. Some
 * pages render the same chart twice for responsive layouts (e.g. Backtest's
 * `lg:hidden` mobile copy + `hidden lg:block` desktop copy) — the CSS-hidden
 * instance stays mounted and spams that warning. This hook returns the
 * container's current width, so callers can render `<ResponsiveContainer>`
 * only when `width > 0` and unmount it when the container is hidden again.
 *
 * Sources of truth, in order:
 * - ResizeObserver (layout flips between CSS breakpoints fire it)
 * - `clientWidth` re-measured on window resize (ResizeObserver-less envs)
 * - an initial synchronous measure after mount
 */
export function useContainerWidth<T extends HTMLElement = HTMLDivElement>() {
  const ref = useRef<T | null>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    const measure = () => {
      // clientWidth of a display:none element is 0; visible ones report real px.
      setWidth(el.clientWidth)
    }

    measure()

    let observer: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(measure)
      observer.observe(el)
    }
    window.addEventListener('resize', measure)

    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [])

  return { ref, width }
}
