"use client";

import { useEffect, type JSX } from "react";

/**
 * The product error boundary. uTrace surfaces do not degrade quietly: when a
 * page cannot get authoritative state, it says so rather than rendering an
 * empty dashboard or a session that looks idle.
 */
export default function ProductError(
  props: Readonly<{ error: Error & { digest?: string }; reset: () => void }>,
): JSX.Element {
  useEffect(() => {
    console.error("product_route_error", { message: props.error.message, digest: props.error.digest });
  }, [props.error]);

  return (
    <main className="pr-main">
      <div className="pr-stack" style={{ maxWidth: 520 }} data-testid="product-error">
        <h1 className="pr-h1">This page could not load</h1>
        <p className="pr-lede">
          uTrace could not read the state behind this page, so it is showing nothing rather than
          something stale. Nothing you were doing has been lost.
        </p>
        {props.error.digest === undefined ? null : (
          <p className="pr-note" data-testid="product-error-digest">
            Reference {props.error.digest}
          </p>
        )}
        <div>
          <button type="button" className="pr-btn" onClick={props.reset} data-testid="product-error-retry">
            Try again
          </button>
        </div>
      </div>
    </main>
  );
}
