import { describe, expect, it } from "vitest";

import {
  IDLE_BINDING,
  reduceBinding,
  shouldProbeFocusedTab,
  type BindingSnapshot,
} from "../../src/background/bindingState";
import { PROTOCOL_VERSION, SCHEMA_VERSION } from "../../src/protocol/common";
import type { BindingOffer, BindingStateFrame } from "../../src/protocol/control";
import { readPageProofClaims, type PageProof, type PageProofClaims } from "../../src/protocol/common";

const INSTALLATION_ID = "0199a0c0-0000-7aaa-8000-000000001457";
const SESSION_ID = "0199a0c0-0000-7aaa-8000-0000000055e1";
const OTHER_SESSION_ID = "0199a0c0-0000-7aaa-8000-0000000055e2";
const CLIENT_ID = "0199a0c0-0000-7aaa-8000-00000000c101";
const ALLOCATION_ID = "0199a0c0-0000-7aaa-8000-00000000a110";
const BINDING_ID = "0199a0c0-0000-7aaa-8000-0000000b1d01";
const ATTACHMENT_ID = "0199a0c0-0000-7aaa-8000-0000000a7701";
const SECOND_ATTACHMENT_ID = "0199a0c0-0000-7aaa-8000-0000000a7702";
const ORIGIN = "https://q7x2.preview.utrace.dev";
const NOW = Date.parse("2026-10-03T17:04:00Z");

function offer(overrides: Partial<BindingOffer>): BindingOffer {
  return {
    schema_version: SCHEMA_VERSION,
    offer_token: "b2ZmZXJfdG9rZW5fZXhhbXBsZV92YWx1ZQ",
    session_id: SESSION_ID,
    client_id: CLIENT_ID,
    extension_installation_id: INSTALLATION_ID,
    preview_runtime_allocation_id: ALLOCATION_ID,
    protocol_version: PROTOCOL_VERSION,
    expires_at: "2026-10-03T17:06:00Z",
    ...overrides,
  };
}

const SIGNATURE_B64URL = "a".repeat(86);

function base64Url(value: string): string {
  return Buffer.from(value, "utf8").toString("base64url");
}

/**
 * The proof the service worker forwards plus the claims it decoded from it.
 * The payload is encoded here rather than written twice, so a test can never
 * assert against claims the signed payload does not carry.
 */
function proof(overrides: Partial<PageProofClaims>): {
  proof: PageProof;
  claims: PageProofClaims;
} {
  const claims: PageProofClaims = {
    client_id: CLIENT_ID,
    session_id: SESSION_ID,
    preview_environment_id: "0199a0c0-0000-7aaa-8000-00000000e111",
    preview_runtime_allocation_id: ALLOCATION_ID,
    origin: ORIGIN,
    document_attachment_id: ATTACHMENT_ID,
    document_nonce: "doc_5fa91c2b",
    sdk_protocol_version: PROTOCOL_VERSION,
    issued_at: "2026-10-03T17:03:40Z",
    expires_at: "2026-10-03T17:08:40Z",
    ...overrides,
  };
  return {
    proof: {
      algorithm: "ed25519",
      key_id: "utrace-browser-2026-09-01",
      payload_b64url: base64Url(JSON.stringify(claims)),
      signature_b64url: SIGNATURE_B64URL,
    },
    claims,
  };
}

function boundFrame(attachmentId: string): BindingStateFrame {
  return {
    schema_version: SCHEMA_VERSION,
    kind: "binding_state",
    message_id: "0199a0c0-0000-7aaa-8000-000000000001",
    sent_at: "2026-10-03T17:04:05Z",
    sequence: 2,
    payload: {
      binding_revision: 4,
      session_id: SESSION_ID,
      status: "bound",
      client_id: CLIENT_ID,
      extension_binding_id: BINDING_ID,
      document_attachment_id: attachmentId,
      preview_runtime_allocation_id: ALLOCATION_ID,
    },
  };
}

function bind(tabId: number, attachmentId: string): BindingSnapshot {
  const offered = reduceBinding(
    IDLE_BINDING,
    { type: "offer_received", offer: offer({}), installationId: INSTALLATION_ID, receivedAt: NOW },
    PROTOCOL_VERSION,
  );
  const probed = reduceBinding(
    offered.state,
    {
      type: "page_proof_observed",
      tabId,
      documentOrigin: ORIGIN,
      ...proof({}),
      documentAttachmentId: attachmentId,
    },
    PROTOCOL_VERSION,
  );
  return reduceBinding(
    probed.state,
    { type: "binding_state_received", frame: boundFrame(attachmentId) },
    PROTOCOL_VERSION,
  ).state;
}

describe("binding offers", () => {
  it("stays inert and probes nothing before an offer arrives", () => {
    expect(shouldProbeFocusedTab(IDLE_BINDING)).toBe(false);
    const transition = reduceBinding(
      IDLE_BINDING,
      {
        type: "page_proof_observed",
        tabId: 7,
        documentOrigin: ORIGIN,
        ...proof({}),
        documentAttachmentId: ATTACHMENT_ID,
      },
      PROTOCOL_VERSION,
    );
    expect(transition.state).toStrictEqual(IDLE_BINDING);
    expect(transition.effects).toStrictEqual([]);
  });

  it("accepts an offer for this installation and then probes focused tabs", () => {
    const transition = reduceBinding(
      IDLE_BINDING,
      { type: "offer_received", offer: offer({}), installationId: INSTALLATION_ID, receivedAt: NOW },
      PROTOCOL_VERSION,
    );
    expect(transition.effects).toStrictEqual([{ type: "answer_offer", accepted: true }]);
    expect(shouldProbeFocusedTab(transition.state)).toBe(true);
  });

  it.each([
    ["offer_installation_mismatch", offer({ extension_installation_id: BINDING_ID })],
    ["unsupported_protocol_version", offer({ protocol_version: "1.99" })],
    ["offer_expired", offer({ expires_at: "2026-10-03T17:03:00Z" })],
  ])("refuses an offer with %s", (reason, refused) => {
    const transition = reduceBinding(
      IDLE_BINDING,
      { type: "offer_received", offer: refused, installationId: INSTALLATION_ID, receivedAt: NOW },
      PROTOCOL_VERSION,
    );
    expect(transition.effects).toStrictEqual([{ type: "answer_offer", accepted: false, reason }]);
    expect(transition.state).toStrictEqual(IDLE_BINDING);
  });

  it("lets a new session's offer replace a pending offer only once that offer lapsed", () => {
    const pending = reduceBinding(
      IDLE_BINDING,
      { type: "offer_received", offer: offer({}), installationId: INSTALLATION_ID, receivedAt: NOW },
      PROTOCOL_VERSION,
    ).state;
    const next = (receivedAt: number): ReturnType<typeof reduceBinding> =>
      reduceBinding(
        pending,
        {
          type: "offer_received",
          offer: offer({ session_id: OTHER_SESSION_ID, expires_at: "2026-10-03T17:20:00Z" }),
          installationId: INSTALLATION_ID,
          receivedAt,
        },
        PROTOCOL_VERSION,
      );

    // Before the pending offer expires (17:06), its session still holds the browser.
    expect(next(NOW).effects).toStrictEqual([
      { type: "answer_offer", accepted: false, reason: "user_already_in_live_session" },
    ]);
    const replaced = next(Date.parse("2026-10-03T17:10:00Z"));
    expect(replaced.effects).toStrictEqual([{ type: "answer_offer", accepted: true }]);
    expect(replaced.state).toMatchObject({
      status: "offer_pending",
      offer: { session_id: OTHER_SESSION_ID },
    });
  });

  it("rejects a second session as user_already_in_live_session", () => {
    const bound = bind(41, ATTACHMENT_ID);
    const transition = reduceBinding(
      bound,
      {
        type: "offer_received",
        offer: offer({ session_id: OTHER_SESSION_ID }),
        installationId: INSTALLATION_ID,
        receivedAt: NOW,
      },
      PROTOCOL_VERSION,
    );
    expect(transition.effects).toStrictEqual([
      { type: "answer_offer", accepted: false, reason: "user_already_in_live_session" },
    ]);
    expect(transition.state).toStrictEqual(bound);
  });
});

describe("page proofs", () => {
  it("submits the offer once a matching proof appears and attaches on confirmation", () => {
    const offered = reduceBinding(
      IDLE_BINDING,
      { type: "offer_received", offer: offer({}), installationId: INSTALLATION_ID, receivedAt: NOW },
      PROTOCOL_VERSION,
    );
    const probed = reduceBinding(
      offered.state,
      {
        type: "page_proof_observed",
        tabId: 41,
        documentOrigin: ORIGIN,
        ...proof({}),
        documentAttachmentId: ATTACHMENT_ID,
      },
      PROTOCOL_VERSION,
    );
    expect(probed.effects).toStrictEqual([
      {
        type: "submit_binding",
        request: { kind: "offer", offer: offer({}) },
        proof: proof({}).proof,
        tabId: 41,
        documentOrigin: ORIGIN,
      },
    ]);

    const confirmed = reduceBinding(
      probed.state,
      { type: "binding_state_received", frame: boundFrame(ATTACHMENT_ID) },
      PROTOCOL_VERSION,
    );
    expect(confirmed.effects).toStrictEqual([
      { type: "attach_surface", tabId: 41, documentAttachmentId: ATTACHMENT_ID },
    ]);
    expect(confirmed.state).toMatchObject({ status: "bound", tabId: 41 });
  });

  it.each([
    ["another session", proof({ session_id: OTHER_SESSION_ID })],
    ["another client", proof({ client_id: ALLOCATION_ID })],
    ["another allocation", proof({ preview_runtime_allocation_id: CLIENT_ID })],
    ["another origin", proof({ origin: "https://example.test" })],
  ])("leaves a tab whose proof belongs to %s untouched", (_case, mismatched) => {
    const offered = reduceBinding(
      IDLE_BINDING,
      { type: "offer_received", offer: offer({}), installationId: INSTALLATION_ID, receivedAt: NOW },
      PROTOCOL_VERSION,
    );
    const transition = reduceBinding(
      offered.state,
      {
        type: "page_proof_observed",
        tabId: 41,
        documentOrigin: ORIGIN,
        proof: mismatched.proof,
        claims: mismatched.claims,
        documentAttachmentId: ATTACHMENT_ID,
      },
      PROTOCOL_VERSION,
    );
    expect(transition.effects).toStrictEqual([]);
  });
});

describe("rebinding and revocation", () => {
  it("transfers authority to another valid tab of the same session", () => {
    const bound = bind(41, ATTACHMENT_ID);
    const probed = reduceBinding(
      bound,
      {
        type: "page_proof_observed",
        tabId: 57,
        documentOrigin: ORIGIN,
        ...proof({}),
        documentAttachmentId: SECOND_ATTACHMENT_ID,
      },
      PROTOCOL_VERSION,
    );
    expect(probed.effects[0]).toMatchObject({
      type: "submit_binding",
      request: { kind: "rebind", session_id: SESSION_ID, extension_binding_id: BINDING_ID },
      tabId: 57,
    });

    const confirmed = reduceBinding(
      probed.state,
      { type: "binding_state_received", frame: boundFrame(SECOND_ATTACHMENT_ID) },
      PROTOCOL_VERSION,
    );
    expect(confirmed.effects).toStrictEqual([
      { type: "detach_surface", tabId: 41, reason: "superseded_by_tab" },
      { type: "attach_surface", tabId: 57, documentAttachmentId: SECOND_ATTACHMENT_ID },
    ]);
  });

  it("does not resubmit for the tab and document it is still binding", () => {
    const offered = reduceBinding(
      IDLE_BINDING,
      { type: "offer_received", offer: offer({}), installationId: INSTALLATION_ID, receivedAt: NOW },
      PROTOCOL_VERSION,
    );
    const observed = {
      type: "page_proof_observed",
      tabId: 41,
      documentOrigin: ORIGIN,
      ...proof({}),
      documentAttachmentId: ATTACHMENT_ID,
    } as const;
    const binding = reduceBinding(offered.state, observed, PROTOCOL_VERSION);
    const again = reduceBinding(binding.state, observed, PROTOCOL_VERSION);
    expect(again.effects).toStrictEqual([]);
    expect(again.state).toStrictEqual(binding.state);
  });

  it("does not resubmit for the tab and document it is already bound to", () => {
    const bound = bind(41, ATTACHMENT_ID);
    const transition = reduceBinding(
      bound,
      {
        type: "page_proof_observed",
        tabId: 41,
        documentOrigin: ORIGIN,
        ...proof({}),
        documentAttachmentId: ATTACHMENT_ID,
      },
      PROTOCOL_VERSION,
    );
    expect(transition.effects).toStrictEqual([]);
  });

  it("reloads the bound tab onto a candidate runtime and rebinds the document it serves", () => {
    const CANDIDATE_ID = "0199a0c0-0000-7aaa-8000-00000000a111";
    const bound = bind(41, ATTACHMENT_ID);
    const switched = reduceBinding(
      bound,
      {
        type: "offer_received",
        offer: offer({ preview_runtime_allocation_id: CANDIDATE_ID }),
        installationId: INSTALLATION_ID,
        receivedAt: NOW,
      },
      PROTOCOL_VERSION,
    );
    expect(switched.effects).toStrictEqual([
      { type: "answer_offer", accepted: true },
      { type: "reload_tab", tabId: 41 },
    ]);

    const reloaded = reduceBinding(
      switched.state,
      {
        type: "page_proof_observed",
        tabId: 41,
        documentOrigin: ORIGIN,
        ...proof({ preview_runtime_allocation_id: CANDIDATE_ID }),
        documentAttachmentId: SECOND_ATTACHMENT_ID,
      },
      PROTOCOL_VERSION,
    );
    expect(reloaded.effects[0]).toMatchObject({
      type: "submit_binding",
      request: { kind: "rebind", session_id: SESSION_ID, extension_binding_id: BINDING_ID },
      tabId: 41,
    });
  });

  it("detaches and returns to idle when the control plane revokes the binding", () => {
    const bound = bind(41, ATTACHMENT_ID);
    const revoked: BindingStateFrame = {
      ...boundFrame(ATTACHMENT_ID),
      sequence: 3,
      payload: {
        binding_revision: 5,
        session_id: SESSION_ID,
        status: "revoked",
        reason: "session_ended",
      },
    };
    const transition = reduceBinding(
      bound,
      { type: "binding_state_received", frame: revoked },
      PROTOCOL_VERSION,
    );
    expect(transition.effects).toStrictEqual([
      { type: "detach_surface", tabId: 41, reason: "session_ended" },
    ]);
    expect(transition.state).toStrictEqual(IDLE_BINDING);
  });

  it("ignores a pushed binding revision older than the one it holds", () => {
    const bound = bind(41, ATTACHMENT_ID);
    const stale: BindingStateFrame = {
      ...boundFrame(ATTACHMENT_ID),
      sequence: 3,
      payload: {
        binding_revision: 3,
        session_id: SESSION_ID,
        status: "revoked",
        reason: "replaced_by_new_document",
      },
    };
    const transition = reduceBinding(
      bound,
      { type: "binding_state_received", frame: stale },
      PROTOCOL_VERSION,
    );
    expect(transition.effects).toStrictEqual([]);
    expect(transition.state).toStrictEqual(bound);
  });

  it("keeps waiting when the binding a rebind replaces is re-projected mid-rebind", () => {
    const bound = bind(41, ATTACHMENT_ID);
    const rebinding = reduceBinding(
      bound,
      {
        type: "page_proof_observed",
        tabId: 57,
        documentOrigin: ORIGIN,
        ...proof({}),
        documentAttachmentId: SECOND_ATTACHMENT_ID,
      },
      PROTOCOL_VERSION,
    );
    // A reconnect re-projects the durable binding, which is still the old one.
    const reprojected = reduceBinding(
      rebinding.state,
      { type: "binding_state_received", frame: boundFrame(ATTACHMENT_ID) },
      PROTOCOL_VERSION,
    );
    expect(reprojected.effects).toStrictEqual([]);
    expect(reprojected.state).toStrictEqual(rebinding.state);
  });

  it("keeps the offer when the bound preview tab closes so another tab can bind", () => {
    const bound = bind(41, ATTACHMENT_ID);
    const transition = reduceBinding(bound, { type: "tab_closed", tabId: 41 }, PROTOCOL_VERSION);
    expect(transition.effects).toStrictEqual([
      { type: "detach_surface", tabId: 41, reason: "preview_tab_closed" },
    ]);
    expect(transition.state).toMatchObject({ status: "offer_pending" });
  });
});

describe("page proof claims", () => {
  it("decodes the claims from the signed payload rather than a separate field", () => {
    const observed = proof({ session_id: OTHER_SESSION_ID });
    expect(readPageProofClaims(observed.proof)).toStrictEqual(observed.claims);
  });

  it("refuses a payload that is not base64url JSON matching the claim set", () => {
    expect(() =>
      readPageProofClaims({
        algorithm: "ed25519",
        key_id: "utrace-browser-2026-09-01",
        payload_b64url: base64Url(JSON.stringify({ client_id: CLIENT_ID })),
        signature_b64url: SIGNATURE_B64URL,
      }),
    ).toThrow(/cannot accept/);
  });
});
