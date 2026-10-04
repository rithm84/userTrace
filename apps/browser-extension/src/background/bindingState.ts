/**
 * The binding state machine.
 *
 * One Interview Session has one authoritative preview-tab binding, and one tab
 * belongs to at most one active session. This module is pure: it decides, and
 * the service worker performs. Chrome's `tabId` is only a local locator here.
 *
 * The rules it enforces, from the browser extension contract:
 *
 * - The extension is inert until an authenticated session supplies a valid
 *   binding offer and the embedded SDK supplies a matching page proof.
 * - A tab without a matching proof is left untouched: no interface, no
 *   commands, no product inspection.
 * - Focusing another valid tab of the same session transfers authority; a tab
 *   belonging to another live session cannot take over, and a second session
 *   is rejected as `user_already_in_live_session`.
 * - The control plane pushes the session's binding whenever it changes, so a
 *   revocation from any surface ends authority here, and an older binding
 *   revision than the one held is ignored.
 * - A new offer for the same session that names another runtime means the
 *   session origin advanced to a candidate: the bound tab reloads onto it and
 *   rebinds there, because the document it shows belongs to the old runtime.
 */
import type { BindingOffer, BindingStateFrame, BindingRequest } from "../protocol/control";
import type { PageProof, PageProofClaims } from "../protocol/common";

export type BindingSnapshot =
  | Readonly<{ status: "idle" }>
  | Readonly<{ status: "offer_pending"; offer: BindingOffer }>
  | Readonly<{
      status: "binding";
      offer: BindingOffer;
      request: BindingRequest;
      tabId: number;
      documentAttachmentId: string;
      boundTabId: number | null;
    }>
  | Readonly<{
      status: "bound";
      offer: BindingOffer;
      tabId: number;
      clientId: string;
      extensionBindingId: string;
      documentAttachmentId: string;
      bindingRevision: number;
    }>;

export type BindingEvent =
  | Readonly<{
      type: "offer_received";
      offer: BindingOffer;
      installationId: string;
      receivedAt: number;
    }>
  | Readonly<{
      type: "page_proof_observed";
      tabId: number;
      documentOrigin: string;
      proof: PageProof;
      /** Decoded from the proof's signed payload, never supplied separately. */
      claims: PageProofClaims;
      documentAttachmentId: string;
    }>
  | Readonly<{ type: "binding_state_received"; frame: BindingStateFrame }>
  | Readonly<{ type: "tab_closed"; tabId: number }>
  | Readonly<{ type: "authority_lost"; reason: string }>;

export type BindingEffect =
  | Readonly<{ type: "answer_offer"; accepted: false; reason: string }>
  | Readonly<{ type: "answer_offer"; accepted: true }>
  | Readonly<{
      type: "submit_binding";
      request: BindingRequest;
      proof: PageProof;
      tabId: number;
      documentOrigin: string;
    }>
  | Readonly<{ type: "attach_surface"; tabId: number; documentAttachmentId: string }>
  | Readonly<{ type: "detach_surface"; tabId: number; reason: string }>
  | Readonly<{ type: "reload_tab"; tabId: number }>;

export type BindingTransition = Readonly<{
  state: BindingSnapshot;
  effects: readonly BindingEffect[];
}>;

export const IDLE_BINDING: BindingSnapshot = Object.freeze({ status: "idle" });

export function reduceBinding(
  state: BindingSnapshot,
  event: BindingEvent,
  protocolVersion: string,
): BindingTransition {
  switch (event.type) {
    case "offer_received":
      return acceptOffer(state, event, protocolVersion);
    case "page_proof_observed":
      return observeProof(state, event);
    case "binding_state_received":
      return applyBindingState(state, event.frame);
    case "tab_closed":
      return closeTab(state, event.tabId);
    case "authority_lost":
      return loseAuthority(state, event.reason);
  }
}

/** True while a focused tab is worth a lightweight page-proof probe. */
export function shouldProbeFocusedTab(state: BindingSnapshot): boolean {
  return state.status !== "idle";
}

export function activeSessionId(state: BindingSnapshot): string | null {
  return state.status === "idle" ? null : state.offer.session_id;
}

export function boundTabId(state: BindingSnapshot): number | null {
  return state.status === "bound" ? state.tabId : null;
}

function acceptOffer(
  state: BindingSnapshot,
  event: Extract<BindingEvent, { type: "offer_received" }>,
  protocolVersion: string,
): BindingTransition {
  const { offer } = event;

  if (offer.extension_installation_id !== event.installationId) {
    return refuse(state, "offer_installation_mismatch");
  }
  if (offer.protocol_version !== protocolVersion) {
    return refuse(state, "unsupported_protocol_version");
  }
  if (Date.parse(offer.expires_at) <= event.receivedAt) {
    return refuse(state, "offer_expired");
  }

  const currentSession = activeSessionId(state);
  // A pending offer that expired holds no authority: its session ended or was
  // never joined, and nothing else will clear it if the revocation never arrived.
  const lapsed =
    state.status === "offer_pending" && Date.parse(state.offer.expires_at) <= event.receivedAt;
  if (currentSession !== null && currentSession !== offer.session_id && !lapsed) {
    return refuse(state, "user_already_in_live_session");
  }

  if (state.status === "bound" || state.status === "binding") {
    if (
      offer.session_id === state.offer.session_id &&
      offer.preview_runtime_allocation_id !== state.offer.preview_runtime_allocation_id
    ) {
      return {
        state: { ...state, offer },
        effects: [
          { type: "answer_offer", accepted: true },
          { type: "reload_tab", tabId: state.tabId },
        ],
      };
    }
    return { state, effects: [{ type: "answer_offer", accepted: true }] };
  }

  return {
    state: { status: "offer_pending", offer },
    effects: [{ type: "answer_offer", accepted: true }],
  };
}

function refuse(state: BindingSnapshot, reason: string): BindingTransition {
  return { state, effects: [{ type: "answer_offer", accepted: false, reason }] };
}

function observeProof(
  state: BindingSnapshot,
  event: Extract<BindingEvent, { type: "page_proof_observed" }>,
): BindingTransition {
  if (state.status === "idle") {
    return { state, effects: [] };
  }
  if (event.claims.session_id !== state.offer.session_id) {
    return { state, effects: [] };
  }
  if (event.claims.client_id !== state.offer.client_id) {
    return { state, effects: [] };
  }
  if (
    event.claims.preview_runtime_allocation_id !==
    state.offer.preview_runtime_allocation_id
  ) {
    return { state, effects: [] };
  }
  if (event.claims.origin !== event.documentOrigin) {
    return { state, effects: [] };
  }
  // Focus and page-load probes can both answer for one document; a second
  // submission would be refused as a consumed offer and undo the first binding.
  if (
    (state.status === "bound" || state.status === "binding") &&
    state.tabId === event.tabId &&
    state.documentAttachmentId === event.documentAttachmentId
  ) {
    return { state, effects: [] };
  }

  const request: BindingRequest =
    state.status === "bound"
      ? {
          kind: "rebind",
          session_id: state.offer.session_id,
          extension_binding_id: state.extensionBindingId,
        }
      : { kind: "offer", offer: state.offer };

  return {
    state: {
      status: "binding",
      offer: state.offer,
      request,
      tabId: event.tabId,
      documentAttachmentId: event.documentAttachmentId,
      boundTabId: state.status === "bound" ? state.tabId : null,
    },
    effects: [
      {
        type: "submit_binding",
        request,
        proof: event.proof,
        tabId: event.tabId,
        documentOrigin: event.documentOrigin,
      },
    ],
  };
}

function applyBindingState(
  state: BindingSnapshot,
  frame: BindingStateFrame,
): BindingTransition {
  const { payload } = frame;

  if (state.status === "idle" || payload.session_id !== state.offer.session_id) {
    return { state, effects: [] };
  }

  // The control plane pushes its current binding whenever it changes and on
  // every reconnect, so an older revision than the one held is stale.
  if (state.status === "bound" && payload.binding_revision < state.bindingRevision) {
    return { state, effects: [] };
  }

  if (payload.status !== "bound") {
    return loseAuthority(state, payload.reason ?? "binding_revoked");
  }

  if (state.status !== "binding") {
    return { state, effects: [] };
  }

  const clientId = payload.client_id;
  const extensionBindingId = payload.extension_binding_id;
  const documentAttachmentId = payload.document_attachment_id;
  if (
    clientId === undefined ||
    extensionBindingId === undefined ||
    documentAttachmentId === undefined
  ) {
    return loseAuthority(state, "incomplete_binding_record");
  }
  if (documentAttachmentId !== state.documentAttachmentId) {
    // A re-projection of the binding this rebind replaces is not the answer;
    // the answer for the new document is still on its way.
    if (
      state.request.kind === "rebind" &&
      extensionBindingId === state.request.extension_binding_id
    ) {
      return { state, effects: [] };
    }
    return loseAuthority(state, "document_attachment_mismatch");
  }

  const effects: BindingEffect[] = [];
  if (state.boundTabId !== null && state.boundTabId !== state.tabId) {
    effects.push({
      type: "detach_surface",
      tabId: state.boundTabId,
      reason: "superseded_by_tab",
    });
  }
  effects.push({
    type: "attach_surface",
    tabId: state.tabId,
    documentAttachmentId,
  });

  return {
    state: {
      status: "bound",
      offer: state.offer,
      tabId: state.tabId,
      clientId,
      extensionBindingId,
      documentAttachmentId,
      bindingRevision: payload.binding_revision,
    },
    effects,
  };
}

function closeTab(state: BindingSnapshot, tabId: number): BindingTransition {
  if (state.status === "bound" && state.tabId === tabId) {
    return {
      state: { status: "offer_pending", offer: state.offer },
      effects: [{ type: "detach_surface", tabId, reason: "preview_tab_closed" }],
    };
  }
  if (state.status === "binding" && state.tabId === tabId) {
    return {
      state: { status: "offer_pending", offer: state.offer },
      effects: [],
    };
  }
  return { state, effects: [] };
}

function loseAuthority(state: BindingSnapshot, reason: string): BindingTransition {
  const effects: BindingEffect[] = [];
  const tabId = state.status === "bound" ? state.tabId : null;
  if (tabId !== null) {
    effects.push({ type: "detach_surface", tabId, reason });
  }
  return { state: IDLE_BINDING, effects };
}
