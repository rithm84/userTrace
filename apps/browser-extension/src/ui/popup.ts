/**
 * The toolbar popup: installation, permission, protocol, connection, binding,
 * and readiness status, plus the one-time account link and host-permission
 * request. It cannot start an interview or issue a product command.
 */
import { requestFromWorker } from "../shared/extensionMessages";
import {
  expectStatus,
  hostPermissionGranted,
  loadStatus,
  requestHostPermission,
  renderRows,
  showNotice,
  statusRows,
} from "./status";

function element(id: string): HTMLElement {
  const found = document.getElementById(id);
  if (found === null) {
    throw new Error(`The popup is missing its ${id} element.`);
  }
  return found;
}

function buttonElement(id: string): HTMLButtonElement {
  const found = element(id);
  if (!(found instanceof HTMLButtonElement)) {
    throw new Error(`The ${id} element is not a button.`);
  }
  return found;
}

async function refresh(): Promise<void> {
  const [status, granted] = await Promise.all([loadStatus(), hostPermissionGranted()]);
  renderRows(element("rows"), statusRows(status, granted));
  buttonElement("link").disabled = status.installation?.account_link_state === "linked";
  buttonElement("permissions").disabled = granted;
}

function report(error: unknown): void {
  showNotice(element("notice"), error instanceof Error ? error.message : String(error));
}

element("link").addEventListener("click", () => {
  void requestFromWorker({ kind: "link_account" })
    .then(expectStatus)
    .then(refresh)
    .catch(report);
});

element("permissions").addEventListener("click", () => {
  void requestHostPermission().then(refresh).catch(report);
});

element("settings").addEventListener("click", () => {
  void chrome.runtime.openOptionsPage();
});

void refresh().catch(report);
