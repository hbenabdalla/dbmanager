import "@testing-library/jest-dom/vitest";

class RO {
  observe() {}
  unobserve() {}
  disconnect() {}
}
// jsdom lacks these browser APIs used by charts and Radix.
(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO;
if (!window.matchMedia) {
  window.matchMedia = (q: string) => ({ matches: false, media: q, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false }) as MediaQueryList;
}
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});
