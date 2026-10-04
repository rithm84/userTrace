import Link from "next/link";
import type { JSX } from "react";

import { Chip, EmptyState, formatTimestamp, humanize } from "@/components/product/ui";
import type { CaseOverview, ProductSignal } from "@/lib/api/schemas";
import { labelFor, type ReferenceLabels } from "@/lib/dashboard/references";
import { casePath } from "@/lib/routes";

import { flowTypeLabel, lifecycleTone } from "./lifecycle";

/**
 * Cases with the concern the interview established and the customer organization
 * behind them. A case whose concern has not
 * been recorded yet shows its identifier rather than a made-up title.
 */
export function CaseList(
  props: Readonly<{
    cases: readonly ProductSignal[];
    overviews: ReadonlyMap<string, CaseOverview>;
    labels: ReferenceLabels;
  }>,
): JSX.Element {
  if (props.cases.length === 0) {
    return <EmptyState testId="cases-empty">No case is in progress right now.</EmptyState>;
  }

  return (
    <ul className="pr-list" data-testid="case-list">
      {props.cases.map((item) => {
        const overview = props.overviews.get(item.id);
        return (
          <li key={item.id}>
            <Link
              href={casePath(item.id)}
              className="pr-card"
              data-testid={`case-card-${item.id}`}
            >
              <p className="pr-card-title">
                {overview?.concern?.pain_point ?? `Product Signal ${item.id}`}
              </p>
              <div className="pr-card-meta">
                <Chip tone={lifecycleTone(item.state)} testId={`case-state-${item.id}`}>
                  {humanize(item.state)}
                </Chip>
                <span>{flowTypeLabel(item.established_flow_type ?? item.initial_flow_type)}</span>
                {overview === undefined ? null : (
                  <>
                    <span data-testid={`case-account-${item.id}`}>
                      {labelFor(props.labels, overview.customer_account_ref)}
                    </span>
                    <span data-testid={`case-user-${item.id}`}>
                      {labelFor(props.labels, overview.originating_user_ref)}
                    </span>
                  </>
                )}
                <span>Updated {formatTimestamp(item.updated_at)}</span>
              </div>
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
