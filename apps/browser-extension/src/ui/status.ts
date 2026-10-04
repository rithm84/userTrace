/**
 * Shared status rendering for the popup and the settings page.
 *
 * Both surfaces show installation, account-link, Chrome permission, protocol,
 * connection, binding, and readiness state. Readiness is printed exactly as the
 * control plane published it; the pages never compute it locally.
 */
import { requestFromWorker, type ExtensionResponse } from "../shared/extensionMessages";
import type { StatusSnapshot } from "../background/session";

export type Row = Readonly<{
  label: string;
  value: string;
  tone: "good" | "bad" | "neutral";
  testId: string;
}>;

export const HOST_PERMISSIONS = ["https://*/*", "http://*/*"] as const;

export async function loadStatus(): Promise<StatusSnapshot> {
  const response = await requestFromWorker({ kind: "status" });
  return expectStatus(response);
}

export function expectStatus(response: ExtensionResponse): StatusSnapshot {
  if (response.kind !== "status") {
    throw new Error(
      response.kind === "failure" ? response.message : "The service worker returned no status.",
    );
  }
  return response.status;
}

export async function hostPermissionGranted(): Promise<boolean> {
  return chrome.permissions.contains({ origins: [...HOST_PERMISSIONS] });
}

/**
 * Asks Chrome for page access. Chrome shows its prompt only while the click
 * that asked for it is being handled, so the extension page calls this from
 * its own click handler; a request relayed through the service worker has no
 * user gesture and is refused.
 */
export function requestHostPermission(): Promise<boolean> {
  return chrome.permissions.request({ origins: [...HOST_PERMISSIONS] });
}

export function statusRows(
  status: StatusSnapshot,
  permissionGranted: boolean,
): readonly Row[] {
  const { installation, readiness } = status;
  return [
    {
      label: "Installation",
      value: installation === null ? "Not linked" : `Linked ${installation.installation_id}`,
      tone: installation === null ? "bad" : "good",
      testId: "status-installation",
    },
    {
      label: "Account link",
      value: installation?.account_link_state ?? "none",
      tone: installation?.account_link_state === "linked" ? "good" : "bad",
      testId: "status-account-link",
    },
    {
      label: "Chrome permissions",
      value: permissionGranted ? "Granted for http and https pages" : "Not granted",
      tone: permissionGranted ? "good" : "bad",
      testId: "status-permissions",
    },
    {
      label: "Protocol",
      value:
        installation === null
          ? status.protocolVersion
          : `${status.protocolVersion} (control plane ${installation.negotiated_protocol_version})`,
      tone:
        installation === null || installation.negotiated_protocol_version === status.protocolVersion
          ? "good"
          : "bad",
      testId: "status-protocol",
    },
    {
      label: "Connection",
      value:
        status.connectionReason === null
          ? status.connection
          : `${status.connection} (${status.connectionReason})`,
      tone: status.connection === "open" ? "good" : "neutral",
      testId: "status-connection",
    },
    {
      label: "Binding",
      value:
        status.sessionId === null
          ? status.bindingStatus
          : `${status.bindingStatus} (session ${status.sessionId})`,
      tone: status.bindingStatus === "bound" ? "good" : "neutral",
      testId: "status-binding",
    },
    {
      label: "Readiness",
      value: readiness === null ? "No session readiness record" : describeReadiness(readiness),
      tone: readiness?.combined_readiness === "ready" ? "good" : "neutral",
      testId: "status-readiness",
    },
  ];
}

function describeReadiness(readiness: NonNullable<StatusSnapshot["readiness"]>): string {
  const blockers =
    readiness.blockers.length === 0 ? "" : ` — ${readiness.blockers.join(", ")}`;
  return `extension ${readiness.extension_readiness}, combined ${readiness.combined_readiness}${blockers}`;
}

export function renderRows(container: HTMLElement, rows: readonly Row[]): void {
  container.replaceChildren(
    ...rows.map((row) => {
      const element = document.createElement("div");
      element.className = "row";

      const label = document.createElement("span");
      label.className = "label";
      label.textContent = row.label;

      const value = document.createElement("span");
      value.className = "value";
      value.dataset["tone"] = row.tone;
      value.dataset["utraceTestid"] = row.testId;
      value.textContent = row.value;

      element.append(label, value);
      return element;
    }),
  );
}

export function showNotice(element: HTMLElement, message: string): void {
  element.textContent = message;
}
