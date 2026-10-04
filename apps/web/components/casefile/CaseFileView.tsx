import type { JSX } from "react";

import { Chip, EmptyState, Panel, Row, Rows, formatTimestamp, humanize } from "@/components/product/ui";
import type { CaseFileDetail, Citation, CitedStatement } from "@/lib/api/schemas";
import { labelFor, type ReferenceLabels } from "@/lib/dashboard/references";

/**
 * The published Case File.
 *
 * The sealed core is drawn as one heavy-bordered stack carrying the digest.
 * The core names people and organizations by opaque client-scoped reference;
 * the labels beside them come from the authenticated staff read model, and a
 * reference uTrace holds no name for is shown as itself.
 */
export function CaseFileView(
  props: Readonly<{
    caseFile: CaseFileDetail;
    labels: ReferenceLabels;
  }>,
): JSX.Element {
  const { caseFile, labels } = props;
  const record = caseFile.case_file;
  const core = caseFile.core;

  if (core === undefined) {
    return (
      <div className="pr-stack" data-testid="case-file" data-variant={record.variant}>
        <EmptyState testId="case-file-unsealed">
          This Case File is {humanize(record.stage).toLowerCase()} and carries no sealed core yet.
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="pr-stack" data-testid="case-file" data-variant={record.variant}>
      <div className="pr-head">
        <div>
          <h1 className="pr-h1">{core.summary.title}</h1>
          <p className="pr-lede">
            Case File for {labelFor(labels, core.summary.customer_account_ref)}
            {record.sealed_at === undefined
              ? "."
              : `, sealed ${formatTimestamp(record.sealed_at)}.`}
          </p>
        </div>
        <div className="pr-head-end">
          <Chip
            tone={core.summary.validation_outcome === "validated" ? "active" : "neutral"}
            testId="case-file-outcome"
          >
            {core.summary.validation_outcome === undefined
              ? humanize(record.variant)
              : humanize(core.summary.validation_outcome)}
          </Chip>
        </div>
      </div>

      <section className="pr-panel pr-panel--core" data-testid="case-file-core" data-variant="core">
        <div className="pr-panel-head">
          <h2 className="pr-h2">Published record</h2>
          <p className="pr-note" data-testid="case-file-digest">
            Immutable core · {record.core_digest ?? "not yet sealed"}
          </p>
        </div>

        <Panel title="Summary" variant="plain" testId="case-file-summary" aside={null}>
          <Rows testId="case-file-summary-rows">
            <Row term="Product Signal" testId="case-file-summary-signal">
              <Statement claim={core.summary.concern} />
            </Row>
            <Row term="Flow type" testId="case-file-summary-flow">
              {humanize(core.summary.flow_type)}
            </Row>
            <Row term="Originating user" testId="case-file-summary-user">
              {labelFor(labels, core.summary.originating_user_ref)}
            </Row>
            <Row term="Customer account" testId="case-file-summary-account">
              {labelFor(labels, core.summary.customer_account_ref)}
            </Row>
            <Row term="Validation result" testId="case-file-summary-validation">
              {core.summary.validation_outcome === undefined
                ? "No validation; this Case File records why no implementation happened."
                : humanize(core.summary.validation_outcome)}
            </Row>
          </Rows>
        </Panel>

        <Panel title="Customer evidence" variant="plain" testId="case-file-evidence" aside={null}>
          <blockquote className="pr-message pr-message--quiet" data-testid="case-file-source-message">
            “{core.customer_evidence.source_message.statement}”
            <span className="pr-note" style={{ display: "block", marginTop: 6 }}>
              {describeCitations(core.customer_evidence.source_message.citations)}
            </span>
          </blockquote>
          <ul className="pr-list" style={{ marginTop: 12 }} data-testid="case-file-excerpts">
            {core.customer_evidence.interview_excerpts.map((excerpt) => (
              <li key={excerpt.citations[0].evidence_record_id} className="pr-card">
                <p className="pr-card-title">“{excerpt.statement}”</p>
                <div className="pr-card-meta">
                  <span>{describeCitations(excerpt.citations)}</span>
                </div>
              </li>
            ))}
          </ul>
          <Rows testId="case-file-evidence-rows">
            <Row term="Expected behaviour" testId="case-file-expected">
              <Statement claim={core.customer_evidence.expected_behavior} />
            </Row>
            <Row term="Actual behaviour" testId="case-file-actual">
              <Statement claim={core.customer_evidence.actual_behavior} />
            </Row>
            <Row term="Impact" testId="case-file-impact">
              <Statement claim={core.customer_evidence.impact} />
            </Row>
            {core.customer_evidence.workaround === undefined ? null : (
              <Row term="Workaround" testId="case-file-workaround">
                <Statement claim={core.customer_evidence.workaround} />
              </Row>
            )}
          </Rows>
        </Panel>

        <Panel title="Product trace" variant="plain" testId="case-file-trace" aside={null}>
          {core.product_trace === undefined ? (
            <p className="pr-lede" data-testid="case-file-no-trace">
              This Case File records no product trace: no criterion-level observation was
              admitted for it.
            </p>
          ) : (
            <>
              <Rows testId="case-file-trace-rows">
                <Row term="Workflow" testId="case-file-workflow">
                  {core.product_trace.workflow_ref}
                </Row>
                <Row term="Validated candidate" testId="case-file-after-candidate">
                  {core.product_trace.after.preview_version_id ?? "None"}
                </Row>
                <Row term="Coverage limits" testId="case-file-coverage">
                  {core.product_trace.coverage_limits.length === 0
                    ? "None recorded"
                    : core.product_trace.coverage_limits.join(" · ")}
                </Row>
              </Rows>
              <ul className="pr-list" style={{ marginTop: 12 }} data-testid="case-file-criteria">
                {core.product_trace.criteria.map((criterion) => (
                  <li key={criterion.criterion_id} className="pr-card">
                    <p className="pr-card-title">{criterion.statement}</p>
                    <div className="pr-card-meta">
                      <Chip
                        tone={criterion.result === "met" ? "active" : "waiting"}
                        testId={`case-file-criterion-${criterion.criterion_id}`}
                      >
                        {humanize(criterion.result)}
                      </Chip>
                      <span>{humanize(criterion.observability)}</span>
                      <span>
                        {criterion.user_confirmed ? "User confirmed" : "Not user confirmed"}
                      </span>
                    </div>
                    <p className="pr-lede" style={{ fontSize: 14 }}>
                      {criterion.reason}
                    </p>
                    <ul className="pr-bullets">
                      {criterion.observed_evidence.map((evidence) => (
                        <li key={evidence.evidence_record_id}>{describeCitations([evidence])}</li>
                      ))}
                    </ul>
                    {criterion.contradicting_evidence.length === 0 ? null : (
                      <p className="pr-note" style={{ marginTop: 6 }}>
                        Contradicted by {describeCitations(criterion.contradicting_evidence)}
                      </p>
                    )}
                    {criterion.coverage_limits.length === 0 ? null : (
                      <p className="pr-note" style={{ marginTop: 6 }}>
                        Coverage limit: {criterion.coverage_limits.join("; ")}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </Panel>

        <Panel
          title="Implementation Gate decision"
          variant="plain"
          testId="case-file-gate"
          aside={null}
        >
          {core.gate_decision === undefined ? (
            <p className="pr-lede" data-testid="case-file-no-gate">
              This Case File records no Implementation Gate decision.
            </p>
          ) : (
            <Rows testId="case-file-gate-rows">
              <Row term="Decision" testId="case-file-gate-decision">
                {humanize(core.gate_decision.decision)}
              </Row>
              <Row term="Reason" testId="case-file-gate-reason">
                {core.gate_decision.reason}
              </Row>
              <Row term="Approved scope" testId="case-file-gate-approved">
                {core.gate_decision.approved_scope.join("; ")}
              </Row>
              <Row term="Excluded" testId="case-file-gate-excluded">
                {core.gate_decision.excluded_scope.join("; ")}
              </Row>
              <Row term="Required checks" testId="case-file-gate-checks">
                {core.gate_decision.required_checks.join(" · ")}
              </Row>
            </Rows>
          )}
        </Panel>

        <Panel title="Implementation" variant="plain" testId="case-file-implementation" aside={null}>
          {core.implementation === undefined ? (
            <p className="pr-lede" data-testid="case-file-no-implementation">
              No implementation was delivered, so this Case File carries no candidate and no pull
              request.
            </p>
          ) : (
            <Rows testId="case-file-implementation-rows">
              <Row term="Repository" testId="case-file-repository">
                {core.implementation.repository_ref}
              </Row>
              <Row term="Branch" testId="case-file-branch">
                {core.implementation.branch_ref ?? "None"}
              </Row>
              <Row term="Validated Preview Version" testId="case-file-preview-version">
                {core.implementation.preview_version_id} ·{" "}
                <span className="pr-mono">{core.implementation.source_digest}</span>
              </Row>
              <Row term="Validated commit" testId="case-file-source-commit">
                <span className="pr-mono">{core.implementation.source_commit}</span> on base{" "}
                <span className="pr-mono">{core.implementation.base_revision}</span>
              </Row>
              <Row term="Files touched" testId="case-file-files">
                {core.implementation.files_touched.join(", ")}
              </Row>
              <Row term="Checks" testId="case-file-checks">
                {core.implementation.checks
                  .map((check) => `${check.name}: ${check.outcome}`)
                  .join(" · ")}
              </Row>
              <Row term="Cleanup" testId="case-file-cleanup">
                {core.implementation.cleanup_state}
              </Row>
              <Row term="Pull request" testId="case-file-pull-request">
                {core.implementation.pull_request_ref === undefined ? (
                  "None"
                ) : (
                  <a
                    href={core.implementation.pull_request_ref}
                    target="_blank"
                    rel="noreferrer noopener"
                    data-testid="case-file-pull-request-link"
                  >
                    {core.implementation.pull_request_ref}
                  </a>
                )}
              </Row>
            </Rows>
          )}
        </Panel>

        <Panel title="Risk" variant="plain" testId="case-file-risk" aside={null}>
          <Rows testId="case-file-risk-rows">
            <Row term="Blast radius" testId="case-file-blast-radius">
              <Statement claim={core.risk.blast_radius} />
            </Row>
            <Row term="Assumptions" testId="case-file-assumptions">
              {core.risk.assumptions.join("; ")}
            </Row>
            <Row term="Follow-up checks" testId="case-file-follow-up">
              {core.risk.follow_up_checks.join("; ")}
            </Row>
          </Rows>
        </Panel>

        {core.blocker_reason === undefined ? null : (
          <Panel title="Blocker" variant="plain" testId="case-file-blocker" aside={null}>
            <p className="pr-lede" data-testid="case-file-blocker-reason">
              {core.blocker_reason}
            </p>
          </Panel>
        )}

        <Panel title="Next action" variant="plain" testId="case-file-next-action" aside={null}>
          <p className="pr-lede" data-testid="case-file-next-action-detail">
            <strong>{humanize(core.next_action.action)}.</strong> {core.next_action.detail}
          </p>
        </Panel>
      </section>

    </div>
  );
}

function Statement(props: Readonly<{ claim: CitedStatement }>): JSX.Element {
  return (
    <span>
      {props.claim.statement}{" "}
      <span className="pr-note">({describeCitations(props.claim.citations)})</span>
    </span>
  );
}

/** Every claim cites an authorized evidence slice; this names those slices. */
function describeCitations(citations: readonly Citation[]): string {
  return citations
    .map((citation) => `${humanize(citation.evidence_class)}: ${citation.source_ref}`)
    .join("; ");
}
