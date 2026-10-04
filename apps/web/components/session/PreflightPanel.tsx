"use client";

import { Check, X } from "lucide-react";
import { useState, useTransition, type JSX } from "react";

import { PREFLIGHT_CHECK_IDS, type PreflightCheckId, type PreflightResult } from "@/lib/api/schemas";
import { submitSessionPreflight } from "@/lib/session/actions";
import { ExtensionError } from "@/lib/session/extension";
import { collectPreflightReport } from "@/lib/session/preflight";

/**
 * The participant compatibility preflight.
 *
 * The checks run on an explicit press, because one of them asks for the
 * microphone. The browser only gathers evidence; the control plane decides
 * whether the session may be entered, and its verdict is what is shown.
 */
export function PreflightPanel(
  props: Readonly<{
    sessionId: string;
    extensionId: string;
    onPassed: () => void;
  }>,
): JSX.Element {
  const [result, setResult] = useState<PreflightResult | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const runChecks = (): void => {
    startTransition(async () => {
      setFailure(null);
      let report;
      try {
        report = await collectPreflightReport(props.extensionId);
      } catch (error) {
        setFailure(
          error instanceof ExtensionError
            ? error.message
            : "uTrace could not finish the setup checks in this browser.",
        );
        return;
      }

      const outcome = await submitSessionPreflight(props.sessionId, report);
      if (!outcome.ok) {
        setFailure(outcome.message);
        return;
      }
      setResult(outcome.value);
      if (outcome.value.passed) {
        props.onPassed();
      }
    });
  };

  // Before the first run every check is listed with what it looks for; after
  // it, the control plane's verdicts are shown as it reported them.
  const checks =
    result === null
      ? PREFLIGHT_CHECK_IDS.map((id) => ({ id, status: "unchecked", detail: CHECK_HINTS[id] }))
      : result.checks;

  return (
    <div className="ss-card" data-testid="preflight-panel" data-passed={String(result?.passed ?? false)}>
      <h1 className="pr-h1">Before we start</h1>
      <p className="pr-lede">
        uTrace checks five things before the interview. Nothing is recorded until they pass.
      </p>

      <ul className="ss-checks" data-testid="preflight-checks">
        {checks.map((check) => (
          <li key={check.id} data-status={check.status} data-testid={`preflight-check-${check.id}`}>
            <span className="ss-check-mark" aria-hidden="true">
              {check.status === "passed" ? <Check size={14} strokeWidth={3} /> : null}
              {check.status === "failed" ? <X size={14} strokeWidth={3} /> : null}
            </span>
            <span>
              <b>{CHECK_LABELS[check.id]}</b>
              <span className="ss-check-detail">{check.detail}</span>
            </span>
          </li>
        ))}
      </ul>

      {failure === null ? null : (
        <p className="pr-message" role="alert" data-testid="preflight-failure">
          {failure}
        </p>
      )}

      {result?.passed === true ? (
        <p className="pr-message pr-message--quiet" role="status" data-testid="preflight-passed">
          Setup looks good. Connecting you to the agent.
        </p>
      ) : (
        <button
          type="button"
          className="pr-btn pr-btn--wide ss-card-action"
          onClick={runChecks}
          disabled={pending}
          data-testid="preflight-run"
        >
          {result === null ? "Check my setup" : "Check again"}
        </button>
      )}
    </div>
  );
}

const CHECK_HINTS: Readonly<Record<PreflightCheckId, string>> = {
  supported_browser: "A current version of Chrome.",
  extension_installed: "The uTrace extension, installed in this browser.",
  extension_account_linked: "The extension linked to the Discord account you signed in with.",
  extension_host_permission: "Permission for the extension to open your preview.",
  microphone: "Access to your microphone. Chrome will ask when you press the button.",
};

const CHECK_LABELS: Readonly<Record<PreflightCheckId, string>> = {
  supported_browser: "Browser",
  extension_installed: "uTrace extension",
  extension_account_linked: "Extension account",
  extension_host_permission: "Preview access",
  microphone: "Microphone",
};
