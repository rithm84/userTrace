/**
 * The slice of the Chrome extension API the journey drives from inside the
 * browser, through `page.evaluate` and `worker.evaluate`.
 *
 * The real surface is `@types/chrome`, which belongs to the extension package.
 * Declaring only what this suite calls keeps the journey from depending on the
 * whole extension type surface, and a call it has not declared does not
 * compile.
 */

declare const chrome: {
  readonly runtime: {
    readonly sendMessage: (
      extensionId: string,
      message: unknown,
      callback: (response: unknown) => void,
    ) => void;
  };
  readonly storage: {
    readonly local: {
      readonly set: (items: Record<string, unknown>) => Promise<void>;
    };
  };
};
