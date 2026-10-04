import type { JSX, ReactNode } from "react";

import "../product.css";

/**
 * The product ground. `data-product` scopes the tokens in `product.css`, which
 * keeps the authenticated surfaces on white paper while the application shell
 * in `globals.css` stays as it is for the landing.
 */
export default function ProductLayout(props: Readonly<{ children: ReactNode }>): JSX.Element {
  return (
    <div data-product="" data-testid="product-root">
      {props.children}
    </div>
  );
}
