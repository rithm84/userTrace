/**
 * The service worker entry.
 *
 * It wires Chrome's events to the session coordinator and keeps nothing of its
 * own. Binding offers arrive only through externally connectable messaging
 * from a uTrace session-tab origin; popup and settings requests can read status
 * and change preferences, but cannot start an interview, bind a tab, or issue a
 * product command.
 */
import { SessionCoordinator } from "./session";
import { linkAccount, revokeInstallation } from "./installation";
import { clearSessionState, readInstallation, writePreferences } from "./storage";
import {
  parseExtensionRequest,
  type ExtensionResponse,
} from "../shared/extensionMessages";
import { isSessionTabOrigin } from "../shared/buildConfig";
import { CONTENT_PORT_NAME } from "../shared/portMessages";
import { PROTOCOL_VERSION, ProtocolError } from "../protocol/common";
import {
  SESSION_TAB_PROTOCOL_MAJOR,
  parseSessionTabMessage,
  type SessionTabResponse,
} from "../shared/sessionTabMessages";

const RECONNECT_ALARM = "utrace.control.reconnect";

let coordinator: SessionCoordinator | null = null;

async function session(): Promise<SessionCoordinator> {
  coordinator ??= await SessionCoordinator.create();
  return coordinator;
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== CONTENT_PORT_NAME) {
    return;
  }
  void session().then((current) => {
    current.connectSurface(port);
  });
});

/**
 * The only page-facing entry point. `externally_connectable` already restricts
 * the sender to the uTrace session-tab origins; this checks the origin again
 * because a manifest is configuration and the check is authority.
 */
chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  const origin = sender.origin ?? null;
  if (origin === null || !isSessionTabOrigin(origin)) {
    sendResponse({
      type: "utrace_binding_offer_ack",
      accepted: false,
      detail: "This origin may not talk to the uTrace extension.",
    });
    return false;
  }

  void (async () => {
    sendResponse(await handleSessionTabMessage(message, sender.tab?.id ?? null));
  })();
  return true;
});

async function handleSessionTabMessage(
  message: unknown,
  senderTabId: number | null,
): Promise<SessionTabResponse> {
  try {
    const request = parseSessionTabMessage(message);
    const current = await session();
    if (request.type === "utrace_preflight_request") {
      if (request.protocol_version !== SESSION_TAB_PROTOCOL_MAJOR) {
        return {
          type: "utrace_preflight_result",
          account_linked: false,
          host_permission: false,
          installation_proof: null,
          protocol_version: PROTOCOL_VERSION,
        };
      }
      return await current.preflight();
    }
    if (request.binding_offer.session_id !== request.session_id) {
      return {
        type: "utrace_binding_offer_ack",
        accepted: false,
        detail: "The binding offer belongs to another Interview Session than the message names.",
      };
    }
    const answer = await current.receiveOffer(request.binding_offer, senderTabId);
    return {
      type: "utrace_binding_offer_ack",
      accepted: answer.accepted,
      ...(answer.reason === undefined ? {} : { detail: answer.reason }),
    };
  } catch (error) {
    return {
      type: "utrace_binding_offer_ack",
      accepted: false,
      detail:
        error instanceof ProtocolError
          ? `invalid_session_tab_message: ${error.issues.join("; ")}`
          : `session_tab_message_failed: ${error instanceof Error ? error.message : String(error)}`,
    };
  }
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  void (async () => {
    sendResponse(await handleExtensionRequest(message));
  })();
  return true;
});

async function handleExtensionRequest(message: unknown): Promise<ExtensionResponse> {
  const current = await session();
  try {
    const request = parseExtensionRequest(message);
    switch (request.kind) {
      case "status":
        return { kind: "status", status: current.status() };
      case "link_account": {
        const record = await linkAccount();
        current.setInstallation(record);
        return { kind: "status", status: current.status() };
      }
      case "revoke_installation":
        await revokeInstallation();
        current.setInstallation(null);
        return { kind: "status", status: current.status() };
      case "update_preferences":
        await current.updatePreferences(request.preferences);
        return { kind: "status", status: current.status() };
      case "update_orb":
        await current.updateOrbSettings(request.orb);
        return { kind: "status", status: current.status() };
    }
  } catch (error) {
    return {
      kind: "failure",
      message: error instanceof Error ? error.message : String(error),
    };
  }
}

chrome.tabs.onActivated.addListener((info) => {
  void session().then((current) => current.focusTab(info.tabId));
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // A focused tab that finishes loading has an SDK to probe only now.
  if (changeInfo.status === "complete" && tab.active) {
    void session().then((current) => current.probeLoadedTab(tabId));
  }
});

chrome.windows.onFocusChanged.addListener((windowId) => {
  if (windowId === chrome.windows.WINDOW_ID_NONE) {
    return;
  }
  void chrome.tabs.query({ active: true, windowId }).then(async (tabs) => {
    const tabId = tabs[0]?.id;
    if (tabId === undefined) {
      return;
    }
    const current = await session();
    await current.focusTab(tabId);
  });
});

chrome.tabs.onRemoved.addListener((tabId) => {
  void session().then((current) => current.closeTab(tabId));
});

/** Wakes a suspended worker so an active session can reconnect. */
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== RECONNECT_ALARM) {
    return;
  }
  void session();
});

/**
 * A reinstall gets a new installation identity; an update renegotiates
 * compatibility with the control plane on the next connection.
 */
chrome.runtime.onInstalled.addListener((details) => {
  void (async () => {
    const current = await session();
    current.setInstallation(await readInstallation());
    if (details.reason === "update") {
      // A new runtime renegotiates compatibility from a clean state rather than
      // resuming a binding or command whose surfaces belong to the old one.
      await clearSessionState();
    }
    if (details.reason === "install") {
      await writePreferences({
        preferences_revision: 0,
        visual_guidance_enabled: true,
        agent_cursor_enabled: true,
      });
    }
    await chrome.alarms.create(RECONNECT_ALARM, { periodInMinutes: 1 });
  })();
});

chrome.runtime.onStartup.addListener(() => {
  void session();
});
