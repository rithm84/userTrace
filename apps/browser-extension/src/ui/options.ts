/**
 * The settings page: the same status, plus the control-plane owned global
 * action preferences, the device-local orb settings, installation revocation,
 * and troubleshooting. It cannot start an interview, bind a tab, or issue a
 * product command.
 */
import { requestFromWorker } from "../shared/extensionMessages";
import { DEFAULT_ORB_SETTINGS, ORB_DIAMETER, type OrbSettings } from "../shared/portMessages";
import {
  bindAccountButtons,
  buttonElement,
  element,
  expectStatus,
  hostPermissionGranted,
  loadStatus,
  renderRows,
  report,
  showNotice,
  statusRows,
} from "./status";
import type { StatusSnapshot } from "../background/session";

function inputElement(id: string): HTMLInputElement {
  const found = element(id);
  if (!(found instanceof HTMLInputElement)) {
    throw new Error(`The ${id} element is not an input.`);
  }
  return found;
}

let current: StatusSnapshot | null = null;

function apply(status: StatusSnapshot, granted: boolean): void {
  current = status;
  renderRows(element("rows"), statusRows(status, granted));
  buttonElement("link").disabled = status.installation?.account_link_state === "linked";
  buttonElement("permissions").disabled = granted;
  buttonElement("revoke").disabled = status.installation === null;

  inputElement("visual-guidance").checked = status.preferences.visual_guidance_enabled;
  inputElement("agent-cursor").checked = status.preferences.agent_cursor_enabled;

  const diameter = inputElement("diameter");
  diameter.value = String(status.orb.diameter);
  element("diameter-value").textContent = `${String(status.orb.diameter)}px`;
}

async function refresh(): Promise<void> {
  const [status, granted] = await Promise.all([loadStatus(), hostPermissionGranted()]);
  apply(status, granted);
}

async function savePreferences(): Promise<void> {
  if (current === null) {
    return;
  }
  const response = await requestFromWorker({
    kind: "update_preferences",
    preferences: {
      expected_preferences_revision: current.preferences.preferences_revision,
      visual_guidance_enabled: inputElement("visual-guidance").checked,
      agent_cursor_enabled: inputElement("agent-cursor").checked,
    },
  });
  expectStatus(response);
  showNotice(
    element("notice"),
    "Requested. The control plane owns these preferences and confirms them on the connection.",
  );
}

async function saveOrb(orb: OrbSettings): Promise<void> {
  const response = await requestFromWorker({ kind: "update_orb", orb });
  const status = expectStatus(response);
  apply(status, await hostPermissionGranted());
}

bindAccountButtons(refresh);

element("refresh").addEventListener("click", () => {
  void refresh().catch(report);
});

element("revoke").addEventListener("click", () => {
  void requestFromWorker({ kind: "revoke_installation" })
    .then(expectStatus)
    .then(refresh)
    .catch(report);
});

for (const id of ["visual-guidance", "agent-cursor"]) {
  element(id).addEventListener("change", () => {
    void savePreferences().catch(report);
  });
}

const diameterSlider = inputElement("diameter");
diameterSlider.min = String(ORB_DIAMETER.min);
diameterSlider.max = String(ORB_DIAMETER.max);
diameterSlider.step = String(ORB_DIAMETER.step);
diameterSlider.addEventListener("change", () => {
  if (current === null) {
    return;
  }
  void saveOrb({
    anchorXRatio: current.orb.anchorXRatio,
    anchorYRatio: current.orb.anchorYRatio,
    diameter: Number(inputElement("diameter").value),
  }).catch(report);
});

element("reset-position").addEventListener("click", () => {
  if (current === null) {
    return;
  }
  void saveOrb({
    anchorXRatio: DEFAULT_ORB_SETTINGS.anchorXRatio,
    anchorYRatio: DEFAULT_ORB_SETTINGS.anchorYRatio,
    diameter: current.orb.diameter,
  }).catch(report);
});

void refresh().catch(report);
