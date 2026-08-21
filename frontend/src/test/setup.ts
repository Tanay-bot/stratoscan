import '@testing-library/jest-dom/vitest'

// jsdom does not implement matchMedia; some UI helpers (motion, toasts)
// probe it. Provide a stable no-op so component tests never crash on it.
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList
}

// jsdom does not implement IntersectionObserver either; framer-motion's
// whileInView animations depend on it. Provide a no-op stub so pages that
// use scroll-reveal animations render (and test) without crashing.
if (typeof window !== 'undefined' && typeof window.IntersectionObserver === 'undefined') {
  class IntersectionObserverStub implements IntersectionObserver {
    readonly root = null
    readonly rootMargin = ''
    readonly thresholds = []
    observe = () => {}
    unobserve = () => {}
    disconnect = () => {}
    takeRecords = () => []
  }
  window.IntersectionObserver = IntersectionObserverStub as unknown as typeof IntersectionObserver
}
