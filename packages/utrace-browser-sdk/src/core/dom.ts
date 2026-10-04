/**
 * The browser view the runtime observes.
 *
 * `Window & typeof globalThis` is the type of a real document's global object:
 * it carries the constructors (`Request`, `WebSocket`, `MutationObserver`,
 * `Element`, ...) that the bare `Window` interface omits. Every module takes the
 * view explicitly instead of reaching for an ambient global, so a runtime is
 * always bound to exactly one document.
 */
export type BrowserView = Window & typeof globalThis;
