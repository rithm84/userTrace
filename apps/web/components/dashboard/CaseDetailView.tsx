import Link from "next/link";
import type { JSX } from "react";

import {
  Chip,
  EmptyState,
  Panel,
  Row,
  Rows,
  formatMinorUnits,
  formatTimestamp,
  humanize,
} from "@/components/product/ui";
import type { CaseDetail, ClientMember } from "@/lib/api/schemas";
import { labelFor, type ReferenceLabels } from "@/lib/dashboard/references";
import { caseFilePath } from "@/lib/routes";

import { flowTypeLabel, lifecycleTone } from "./lifecycle";
import { StopCaseControl } from "./StopCaseControl";

/**
 * One case, end to end: the concern the interview established, the Interview
 * Sessions it ran, every Implementation Gate decision and Implementation
 * Attempt, the candidate Preview Versions, the validation attempts, delivery,
 * the offer disposition, and the deny-only stop control while the control plane
 * still holds authority over it.
 */
export function CaseDetailView(
  props: Readonly<{
    detail: CaseDetail;
    labels: ReferenceLabels;
    members: ReadonlyMap<string, ClientMember>;
  }>,
): JSX.Element {
  const { detail, labels } = props;
  const signal = detail.case;
  const concern = detail.overview.concern;

  return (
    <div className="pr-stack" data-testid="case-detail" data-state={signal.state}>
      <div className="pr-head">
        <div>
          <h1 className="pr-h1">
            {concern?.pain_point ?? `Product Signal ${signal.id}`}
          </h1>
          {concern === undefined ? (
            <p className="pr-lede">
              No concern has been recorded for this case yet, so uTrace has nothing to summarize.
            </p>
          ) : (
            <p className="pr-lede">{concern.validation_criterion}</p>
          )}
        </div>
        <div className="pr-head-end">
          <Chip tone={lifecycleTone(signal.state)} testId="case-lifecycle-state">
            {humanize(signal.state)}
          </Chip>
        </div>
      </div>

      <Panel title="Case" variant="raised" testId="case-overview" aside={null}>
        <Rows testId="case-overview-rows">
          <Row term="Flow type" testId="case-flow-type">
            {flowTypeLabel(signal.established_flow_type ?? signal.initial_flow_type)}
            {signal.established_flow_type === undefined ? " (initial classification)" : ""}
          </Row>
          <Row term="Originating user" testId="case-originating-user">
            {labelFor(labels, detail.overview.originating_user_ref)} at{" "}
            {labelFor(labels, detail.overview.customer_account_ref)}
          </Row>
          {concern === undefined ? null : (
            <Row term="Concern revision" testId="case-concern-revision">
              {concern.concern_revision} · {humanize(concern.origin)}
            </Row>
          )}
          <Row term="Opened" testId="case-opened">
            {formatTimestamp(signal.created_at)}
          </Row>
          {signal.closure_reason === undefined ? null : (
            <Row term="Closure reason" testId="case-closure-reason">
              {humanize(signal.closure_reason)}
            </Row>
          )}
        </Rows>
      </Panel>

      <Panel title="Interview Sessions" variant="raised" testId="case-sessions" aside={null}>
        {detail.interview_sessions.length === 0 ? (
          <EmptyState testId="case-sessions-empty">No Interview Session has run.</EmptyState>
        ) : (
          <Rows testId="case-session-rows">
            {detail.interview_sessions.map((session) => (
              <Row
                key={session.id}
                term={humanize(session.purpose)}
                testId={`case-session-${session.id}`}
              >
                {humanize(session.state)} · {humanize(session.phase)} ·{" "}
                {formatTimestamp(session.created_at)}
                {session.closed_at === undefined
                  ? " · still open"
                  : ` to ${formatTimestamp(session.closed_at)}`}
                {session.closure_reason === undefined
                  ? null
                  : ` · ${humanize(session.closure_reason)}`}
              </Row>
            ))}
          </Rows>
        )}
      </Panel>

      <Panel title="Implementation Gate" variant="raised" testId="case-gate" aside={null}>
        {detail.gate_decisions.length === 0 ? (
          <EmptyState testId="case-gate-empty">
            The Implementation Gate has not decided on this case yet.
          </EmptyState>
        ) : (
          <Rows testId="case-gate-rows">
            {detail.gate_decisions.map((decision) => (
              <Row
                key={decision.id}
                term={humanize(decision.decision)}
                testId={`case-gate-${decision.id}`}
              >
                {formatTimestamp(decision.decided_at)}
                {decision.blocker_reason === undefined ? null : ` · ${decision.blocker_reason}`}
                {` · acceptance criteria revision ${String(decision.acceptance_criteria_revision)}`}
              </Row>
            ))}
          </Rows>
        )}
        <p className="pr-note" style={{ marginTop: 12 }}>
          The approved and excluded scope is sealed into the Case File; the gate record itself
          carries the decision, its revisions, and the policy release it was taken under.
        </p>
      </Panel>

      <Panel title="Implementation Attempts" variant="raised" testId="case-attempts" aside={null}>
        {detail.implementation_attempts.length === 0 ? (
          <EmptyState testId="case-attempts-empty">
            No Implementation Attempt has been dispatched.
          </EmptyState>
        ) : (
          <ul className="pr-list" data-testid="case-attempt-list">
            {detail.implementation_attempts.map((attempt) => (
              <li key={attempt.id} className="pr-card" data-testid={`case-attempt-${attempt.id}`}>
                <p className="pr-card-title">
                  Live attempt · {humanize(attempt.state)}
                </p>
                <div className="pr-card-meta">
                  <span>Started {formatTimestamp(attempt.created_at)}</span>
                  <span>Updated {formatTimestamp(attempt.updated_at)}</span>
                  <span className="pr-mono">{attempt.repository_ref}</span>
                  <span className="pr-mono">{attempt.base_revision}</span>
                  {attempt.outcome_reason === undefined ? null : (
                    <span>{attempt.outcome_reason}</span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Preview Versions" variant="raised" testId="case-previews" aside={null}>
        {detail.preview_versions.length === 0 ? (
          <EmptyState testId="case-previews-empty">
            No candidate Preview Version was built.
          </EmptyState>
        ) : (
          <Rows testId="case-preview-rows">
            {detail.preview_versions.map((version) => (
              <Row key={version.id} term={version.id} testId={`case-preview-${version.id}`}>
                <span className="pr-mono">{version.source_digest}</span> ·{" "}
                {humanize(version.state)}
                {version.ready_at === undefined
                  ? null
                  : ` · ready ${formatTimestamp(version.ready_at)}`}
              </Row>
            ))}
          </Rows>
        )}
      </Panel>

      <Panel title="Validation" variant="raised" testId="case-validation" aside={null}>
        {detail.validation_attempts.length === 0 ? (
          <EmptyState testId="case-validation-empty">
            The originating user has not validated a candidate.
          </EmptyState>
        ) : (
          <Rows testId="case-validation-rows">
            {detail.validation_attempts.map((attempt) => (
              <Row
                key={attempt.id}
                term={
                  attempt.outcome === undefined ? humanize(attempt.state) : humanize(attempt.outcome)
                }
                testId={`case-validation-${attempt.id}`}
              >
                Preview Version {attempt.preview_version_id}
                {attempt.completed_at === undefined
                  ? " · not completed"
                  : ` · ${formatTimestamp(attempt.completed_at)}`}
              </Row>
            ))}
          </Rows>
        )}
      </Panel>

      <Panel title="Delivery" variant="raised" testId="case-delivery" aside={null}>
        <Rows testId="case-delivery-rows">
          <Row term="Case File" testId="case-delivery-case-file">
            {detail.case_file === undefined ? (
              "No Case File"
            ) : detail.case_file.stage === "published" ? (
              <Link
                href={caseFilePath(detail.case_file.id)}
                data-testid="case-delivery-case-file-link"
              >
                Open the Case File
              </Link>
            ) : (
              `Case File ${humanize(detail.case_file.stage)}`
            )}
          </Row>
          <Row term="Pull request" testId="case-delivery-pr">
            {detail.delivery === undefined ? (
              "No delivery; uTrace opens a pull request only for validated implementation."
            ) : detail.delivery.pull_request_ref === undefined ? (
              `Delivery ${humanize(detail.delivery.state)}`
            ) : (
              <a
                href={detail.delivery.pull_request_ref}
                target="_blank"
                rel="noreferrer noopener"
                data-testid="case-delivery-pr-link"
              >
                {detail.delivery.pull_request_ref}
              </a>
            )}
          </Row>
          {detail.delivery?.blocker_reason === undefined ? null : (
            <Row term="Blocker" testId="case-delivery-blocker">
              {detail.delivery.blocker_reason}
            </Row>
          )}
        </Rows>
      </Panel>

      <Panel title="Outreach Offer" variant="raised" testId="case-offer" aside={null}>
        {detail.offer === undefined ? (
          <EmptyState testId="case-offer-empty">No offer was made for this case.</EmptyState>
        ) : (
          <Rows testId="case-offer-rows">
            <Row term="Amount" testId="case-offer-amount">
              {formatMinorUnits(detail.offer.amount_minor_units, detail.offer.currency)} to{" "}
              {labelFor(labels, detail.overview.customer_account_ref)}
            </Row>
            <Row term="Disposition" testId="case-offer-disposition">
              {humanize(detail.offer.state)}
            </Row>
            <Row term="Issuance" testId="case-offer-issuance">
              {detail.offer.issuance_state === undefined
                ? "Not due"
                : humanize(detail.offer.issuance_state)}
              {detail.offer.issuance_blocked_reason === undefined
                ? null
                : ` · ${detail.offer.issuance_blocked_reason}`}
            </Row>
          </Rows>
        )}
      </Panel>

      <Panel title="Client stop" variant="raised" testId="case-stop" aside={null}>
        {detail.stop === undefined ? null : (
          <Rows testId="case-stop-rows">
            <Row term="Stopped" testId="case-stop-at">
              {formatTimestamp(detail.stop.recorded_at)}
            </Row>
            <Row term="By" testId="case-stop-by">
              {humanize(detail.stop.staff_role)} ·{" "}
              {props.members.get(detail.stop.client_member_id)?.email ??
                detail.stop.client_member_id}
            </Row>
            <Row term="Reason" testId="case-stop-reason">
              {detail.stop.reason}
            </Row>
          </Rows>
        )}
        {detail.stoppable ? (
          <div style={{ marginTop: detail.stop === undefined ? 0 : 14 }}>
            <StopCaseControl productSignalId={signal.id} />
          </div>
        ) : (
          <EmptyState testId="case-stop-unavailable">
            This case has reached a terminal state, so there is no further authority to withdraw.
          </EmptyState>
        )}
      </Panel>
    </div>
  );
}
