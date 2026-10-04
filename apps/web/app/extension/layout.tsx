import type { JSX, ReactNode } from "react";

import "../product.css";

/**
 * The extension surfaces share the product ground. They are outside the
 * `(product)` group because the browser extension opens them in its own
 * `chrome.identity` window, where the product navigation would be misleading.
 */
export default function ExtensionLayout(props: Readonly<{ children: ReactNode }>): JSX.Element {
  return (
    <div data-product="" data-testid="product-root">
      {props.children}
    </div>
  );
}
