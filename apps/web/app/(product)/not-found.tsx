import Link from "next/link";
import type { JSX } from "react";

import { DASHBOARD_PATH } from "@/lib/routes";

/** A product record that does not exist, or that this viewer may not see. */
export default function ProductNotFound(): JSX.Element {
  return (
    <main className="pr-main">
      <div className="pr-stack" style={{ maxWidth: 520 }} data-testid="product-not-found">
        <h1 className="pr-h1">Not found</h1>
        <p className="pr-lede">
          This record does not exist, or it belongs to a client you are not a member of.
        </p>
        <Link href={DASHBOARD_PATH} className="pr-btn" data-testid="product-not-found-home">
          Back to the dashboard
        </Link>
      </div>
    </main>
  );
}
