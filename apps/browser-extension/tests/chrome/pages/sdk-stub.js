/**
 * A stand-in for the embedded uTrace browser SDK, implementing exactly the
 * main-world bridge contract the browser-sdk workstream owns: it answers
 * `page_proof`, resolves `target_query` into `target_geometry` from the real
 * layout, and records `pause_notice`. It is a test fixture, not the SDK.
 */
(() => {
  const SCHEMA_VERSION = 1;
  const state = {
    port: null,
    proofResponses: 0,
    targetQueries: 0,
    pauseNotices: [],
    documentAttachmentId: "0199a0c0-0000-7aaa-8000-0000000a7701",
    registryRevision: 18,
    pageProof: signedPageProof(),
  };

  /**
   * The signed page proof, in the shape the control plane issues: a detached
   * Ed25519 signature over a base64url JSON claim payload. The signature bytes
   * here are filler, because nothing in the extension verifies them — the
   * control plane does.
   */
  function signedPageProof() {
    const claims = {
      client_id: "0199a0c0-0000-7aaa-8000-00000000c101",
      session_id: "0199a0c0-0000-7aaa-8000-0000000055e1",
      preview_environment_id: "0199a0c0-0000-7aaa-8000-00000000e111",
      preview_runtime_allocation_id: "0199a0c0-0000-7aaa-8000-00000000a110",
      origin: window.origin,
      document_attachment_id: "0199a0c0-0000-7aaa-8000-0000000a7701",
      document_nonce: "doc_5fa91c2b",
      sdk_protocol_version: "1.0",
      issued_at: "2026-10-03T17:03:40Z",
      expires_at: "2126-09-20T17:08:40Z",
    };
    return {
      algorithm: "ed25519",
      key_id: "utrace-browser-2026-09-01",
      payload_b64url: btoa(JSON.stringify(claims))
        .replaceAll("+", "-")
        .replaceAll("/", "_")
        .replace(/=+$/u, ""),
      signature_b64url: "a".repeat(86),
    };
  }

  window.__utraceSdkStub = state;

  function resolveTarget(targetId) {
    const element = document.querySelector(`[data-target-id="${targetId}"]`);
    if (element === null) {
      return null;
    }
    const rect = element.getBoundingClientRect();
    const inViewport =
      rect.bottom > 0 &&
      rect.right > 0 &&
      rect.top < window.innerHeight &&
      rect.left < window.innerWidth;
    return {
      target_id: targetId,
      target_revision: Number(element.dataset.targetRevision),
      layout_revision: 11,
      frame_id: "frm_top",
      rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      visible_fraction: inViewport ? 1 : 0,
      occluded: element.dataset.occluded === "true",
      in_viewport: inViewport,
      supports_visual_guidance: true,
    };
  }

  function handleFrame(frame, port) {
    if (frame.kind === "target_query") {
      state.targetQueries += 1;
      const requested = frame.payload.query.target_ids ?? [];
      const targets = [];
      const notFound = [];
      for (const targetId of requested) {
        const resolved = resolveTarget(targetId);
        if (resolved === null) {
          notFound.push(targetId);
          continue;
        }
        targets.push(resolved);
      }
      port.postMessage({
        schema_version: SCHEMA_VERSION,
        kind: "target_geometry",
        phase: "response",
        exchange_id: frame.exchange_id,
        payload: {
          document_attachment_id: state.documentAttachmentId,
          registry_revision: state.registryRevision,
          viewport: { width: window.innerWidth, height: window.innerHeight },
          targets,
          not_found: notFound,
          ambiguous: [],
        },
      });
      return;
    }

    if (frame.kind === "pause_notice") {
      state.pauseNotices.push(frame.payload);
      return;
    }

    if (frame.kind === "detail_query") {
      port.postMessage({
        schema_version: SCHEMA_VERSION,
        kind: "detail_query",
        phase: "response",
        exchange_id: frame.exchange_id,
        payload: {
          disposition: "resolved",
          target_revision: 3,
          fields: { applied_filters: "tiktok,women_25_34" },
        },
      });
    }
  }

  window.addEventListener("message", (event) => {
    const frame = event.data;
    if (
      event.source !== window ||
      frame === null ||
      typeof frame !== "object" ||
      frame.kind !== "page_proof" ||
      frame.phase !== "request"
    ) {
      return;
    }
    const port = event.ports[0];
    if (port === undefined) {
      return;
    }
    state.port = port;
    state.proofResponses += 1;
    port.onmessage = (message) => {
      handleFrame(message.data, port);
    };
    port.start();
    port.postMessage({
      schema_version: SCHEMA_VERSION,
      kind: "page_proof",
      phase: "response",
      exchange_id: frame.exchange_id,
      payload: {
        page_proof: state.pageProof,
        document_attachment_id: state.documentAttachmentId,
        registry_revision: state.registryRevision,
      },
    });
  });
})();
