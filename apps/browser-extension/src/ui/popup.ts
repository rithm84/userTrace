/**
 * The toolbar popup: installation, permission, protocol, connection, binding,
 * and readiness status, plus the one-time account link and host-permission
 * request. It cannot start an interview or issue a product command.
 */
import {
  bindAccountButtons,
  buttonElement,
  element,
  hostPermissionGranted,
  loadStatus,
  renderRows,
  report,
  statusRows,
} from "./status";

async function refresh(): Promise<void> {
  const [status, granted] = await Promise.all([loadStatus(), hostPermissionGranted()]);
  renderRows(element("rows"), statusRows(status, granted));
  buttonElement("link").disabled = status.installation?.account_link_state === "linked";
  buttonElement("permissions").disabled = granted;
}

bindAccountButtons(refresh);

element("settings").addEventListener("click", () => {
  void chrome.runtime.openOptionsPage();
});

void refresh().catch(report);
