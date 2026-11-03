import "@testing-library/jest-dom/vitest";
import { createElement } from "react";
import { vi } from "vitest";

class MockIntersectionObserver implements IntersectionObserver {
  readonly root = null;
  readonly rootMargin = "";
  readonly thresholds = [0];

  disconnect() {}

  observe() {}

  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }

  unobserve() {}
}

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

globalThis.IntersectionObserver = MockIntersectionObserver;

// jsdom has no CSS Font Loading API; the landing waits on it before measuring type
Object.defineProperty(document, "fonts", {
  configurable: true,
  value: { ready: Promise.resolve(), load: () => Promise.resolve([{}]) },
});

class MockResizeObserver implements ResizeObserver {
  disconnect() {}

  observe() {}

  unobserve() {}
}

globalThis.ResizeObserver = MockResizeObserver;

Object.defineProperty(HTMLMediaElement.prototype, "play", {
  configurable: true,
  writable: true,
  value: vi.fn().mockResolvedValue(undefined),
});

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: Record<string, unknown>) =>
    createElement("a", { href, ...props }, children),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    refresh: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
  redirect: vi.fn(),
  notFound: vi.fn(),
}));

// jsdom has no WebGL: every landing rig renders an empty canvas and never loads.
vi.mock("@rive-app/react-webgl2", () => ({
  useRive: () => ({ rive: null, RiveComponent: () => createElement("canvas") }),
  useViewModelInstanceNumber: () => ({ value: 0, setValue: () => undefined }),
  useViewModelInstanceEnum: () => ({ value: "idle", values: [], setValue: () => undefined }),
}));
