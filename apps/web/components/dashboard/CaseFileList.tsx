import Link from "next/link";
import type { JSX } from "react";

import { Chip, EmptyState, formatTimestamp, humanize } from "@/components/product/ui";
import type { CaseFileRecord, Delivery } from "@/lib/api/schemas";
import { caseFilePath } from "@/lib/routes";

/**
 * Published Case Files and, where one exists, the draft pull request delivered
 * with them. uTrace opens a pull request only for validated implementation, so
 * a Case File without one says that rather than leaving a gap.
 *
 * The sealed core carries the Case File's own title, and reading it is a
 * separate authenticated request per Case File; the list names each one by the
 * concern of the case it closed, which the case list already carries.
 */
export function CaseFileList(
  props: Readonly<{
    caseFiles: readonly CaseFileRecord[];
    deliveries: ReadonlyMap<string, Delivery>;
    concerns: ReadonlyMap<string, string>;
  }>,
): JSX.Element {
  if (props.caseFiles.length === 0) {
    return <EmptyState testId="case-files-empty">No Case File has been published yet.</EmptyState>;
  }

  return (
    <ul className="pr-list" data-testid="case-file-list">
      {props.caseFiles.map((caseFile) => {
        const delivery = props.deliveries.get(caseFile.id);
        return (
          <li key={caseFile.id}>
            <div className="pr-card" data-testid={`case-file-card-${caseFile.id}`}>
              <p className="pr-card-title">
                <Link href={caseFilePath(caseFile.id)} data-testid={`case-file-link-${caseFile.id}`}>
                  {props.concerns.get(caseFile.product_signal_id) ??
                    `Product Signal ${caseFile.product_signal_id}`}
                </Link>
              </p>
              <div className="pr-card-meta">
                <Chip
                  tone={caseFile.variant === "validated_implementation" ? "active" : "neutral"}
                  testId={`case-file-variant-${caseFile.id}`}
                >
                  {humanize(caseFile.variant)}
                </Chip>
                {caseFile.sealed_at === undefined ? (
                  <span>Not sealed</span>
                ) : (
                  <span>Sealed {formatTimestamp(caseFile.sealed_at)}</span>
                )}
                {delivery?.pull_request_ref === undefined ? (
                  <span data-testid={`case-file-no-pr-${caseFile.id}`}>No pull request</span>
                ) : (
                  <a
                    href={delivery.pull_request_ref}
                    rel="noreferrer noopener"
                    target="_blank"
                    data-testid={`case-file-pr-${caseFile.id}`}
                  >
                    Draft pull request
                  </a>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
