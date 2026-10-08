/**
 * A deterministic control-plane stand-in used by the route tests and by
 * `UTRACE_CONTROL_PLANE_FIXTURES=1` local previews.
 *
 * It answers the same interface as `HttpControlPlaneClient` from the
 * Shopalytics fixture. It keeps no durable state: a control acknowledgement is
 * derived from the action, so a preview behaves like the session tab will while
 * never pretending a real effect happened.
 */

import type { ControlPlaneClient } from "./client";
import { ControlPlaneError } from "./errors";
import {
  FIXTURE_CASE_FILE_ID,
  FIXTURE_EXTENSION_INSTALLATION_ID,
  FIXTURE_INVITATION_TOKEN,
  FIXTURE_PREVIEW_ALLOCATION_ID,
  FIXTURE_SESSION_ID,
  fixtureCaseDetails,
  fixtureCaseFiles,
  fixtureCases,
  fixtureClient,
  fixtureConfiguration,
  fixtureInterviewSession,
  fixtureMembers,
  fixtureOffers,
  fixtureParticipantAccount,
  fixtureReferences,
  fixtureSessionProjection,
} from "./fixture-data";
import { fixtureCaseFile } from "./fixture-case-file";
import type {
  Allowance,
  BindingOffer,
  CaseDetail,
  CaseFileDetail,
  CaseFileList,
  CaseList,
  CaseStopResult,
  Client,
  ClientConfiguration,
  ClientMember,
  ControlsState,
  ExtensionLinkCode,
  InterviewSession,
  InvitationClaim,
  OffersView,
  ParticipantAccount,
  ParticipantPreferences,
  ParticipantPreferencesUpdate,
  PreflightReport,
  PreflightResult,
  PreviewHandoff,
  ResolvedReference,
  SessionControlAck,
  SessionControlAction,
  SessionProjection,
  TabState,
  VoiceConnectionRecord,
} from "./schemas";

const FIXTURE_SDP_ANSWER =
  "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=utrace-fixture\r\nt=0 0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n";

/** Far enough away that a fixture preview stays usable; a real one is minutes. */
const FIXTURE_EXPIRY = "2126-09-20T09:05:00Z";

export class FixtureControlPlaneClient implements ControlPlaneClient {
  async getClient(clientId: string): Promise<Client> {
    this.#assertClient(clientId);
    return fixtureClient;
  }

  async getConfiguration(clientId: string): Promise<ClientConfiguration> {
    this.#assertClient(clientId);
    return fixtureConfiguration;
  }

  async listMembers(clientId: string): Promise<readonly ClientMember[]> {
    this.#assertClient(clientId);
    return fixtureMembers;
  }

  async resolveReferences(
    clientId: string,
    refs: readonly string[],
  ): Promise<readonly ResolvedReference[]> {
    this.#assertClient(clientId);
    const known = new Map(fixtureReferences.map((entry) => [entry.ref, entry]));
    return refs.map((ref) => {
      const resolved = known.get(ref);
      if (resolved !== undefined) {
        return resolved;
      }
      const [kind, identifier] = ref.split(":", 2);
      // A reference this client does not own resolves to no label, exactly as
      // the control plane answers it.
      return { ref, kind, id: identifier };
    });
  }

  async listCases(clientId: string): Promise<CaseList> {
    this.#assertClient(clientId);
    return fixtureCases;
  }

  async getCase(clientId: string, productSignalId: string): Promise<CaseDetail> {
    this.#assertClient(clientId);
    const detail = fixtureCaseDetails[productSignalId];
    if (detail === undefined) {
      throw this.#notFound(`/v1/clients/${clientId}/cases/${productSignalId}`, "Product Signal");
    }
    return detail;
  }

  async stopCase(
    clientId: string,
    productSignalId: string,
    reason: string,
  ): Promise<CaseStopResult> {
    const detail = await this.getCase(clientId, productSignalId);
    if (!detail.stoppable) {
      throw new ControlPlaneError(
        {
          code: "conflict",
          method: "POST",
          path: `/v1/clients/${clientId}/cases/${productSignalId}/stop`,
          status: 409,
          responseBody: null,
        },
        "This case has already reached a terminal state, so it cannot be stopped.",
        {},
      );
    }
    if (reason.trim().length === 0) {
      throw new ControlPlaneError(
        {
          code: "rejected",
          method: "POST",
          path: `/v1/clients/${clientId}/cases/${productSignalId}/stop`,
          status: 422,
          responseBody: null,
        },
        "A stop is audited, so it needs a written reason.",
        {},
      );
    }
    const participated = detail.interview_sessions.length > 0;
    return {
      product_signal_id: productSignalId,
      participation_occurred: participated,
      revoked_outreach_attempts: 1,
      fenced_leases: detail.implementation_attempts.length,
      canceled_jobs: 1,
      commands_in_reconciliation: 0,
      // Before participation the stop cancels outright; after it the case
      // closes only once its blocker Case File is published, so the stop
      // itself reaches no terminal state.
      ...(participated ? {} : { terminal_state: "canceled" }),
    };
  }

  async listCaseFiles(clientId: string): Promise<CaseFileList> {
    this.#assertClient(clientId);
    return fixtureCaseFiles;
  }

  async getCaseFile(clientId: string, caseFileId: string): Promise<CaseFileDetail> {
    this.#assertClient(clientId);
    if (caseFileId !== FIXTURE_CASE_FILE_ID) {
      throw this.#notFound(`/v1/clients/${clientId}/case-files/${caseFileId}`, "Case File");
    }
    return fixtureCaseFile;
  }

  async getOffers(clientId: string): Promise<OffersView> {
    this.#assertClient(clientId);
    return fixtureOffers;
  }

  async authorizeOffersBudget(
    clientId: string,
    authorization: Readonly<{ currency: string; authorized_minor_units: number }>,
  ): Promise<Allowance> {
    const offers = await this.getOffers(clientId);
    const committed = offers.reserved_minor_units + offers.earned_minor_units;
    if (authorization.authorized_minor_units < committed) {
      throw new ControlPlaneError(
        {
          code: "rejected",
          method: "POST",
          path: `/v1/clients/${clientId}/offers/budget`,
          status: 422,
          responseBody: null,
        },
        "A budget edit cannot cancel allowance that outstanding promises already consume.",
        {},
      );
    }
    return {
      currency: authorization.currency,
      authorized_minor_units: authorization.authorized_minor_units,
      reserved_minor_units: offers.reserved_minor_units,
      earned_minor_units: offers.earned_minor_units,
      remaining_minor_units: authorization.authorized_minor_units - committed,
    };
  }

  async getParticipantAccount(): Promise<ParticipantAccount> {
    return fixtureParticipantAccount;
  }

  async updateParticipantPreferences(update: ParticipantPreferencesUpdate): Promise<ParticipantPreferences> {
    const current = fixtureParticipantAccount.preferences;
    if (update.expected_revision !== current.revision) {
      throw new ControlPlaneError(
        {
          code: "conflict",
          method: "PATCH",
          path: "/v1/participants/me/preferences",
          status: 409,
          responseBody: null,
        },
        `Preferences are at revision ${String(current.revision)}, not ${String(update.expected_revision)}.`,
        {},
      );
    }
    return {
      revision: current.revision + 1,
      visual_guidance_enabled: update.visual_guidance_enabled,
      agent_cursor_enabled: update.agent_cursor_enabled,
    };
  }

  async claimInvitation(invitationToken: string): Promise<InvitationClaim> {
    if (invitationToken !== FIXTURE_INVITATION_TOKEN) {
      throw this.#notFound(`/v1/invitations/${invitationToken}/claim`, "Invitation");
    }
    return {
      session_id: FIXTURE_SESSION_ID,
      product_signal_id: fixtureSessionProjection.product_signal_id,
      already_claimed: false,
    };
  }

  async getSession(sessionId: string): Promise<InterviewSession> {
    if (sessionId !== FIXTURE_SESSION_ID) {
      throw this.#notFound(`/v1/sessions/${sessionId}`, "Interview Session");
    }
    return fixtureInterviewSession;
  }

  async getSessionProjection(sessionId: string): Promise<SessionProjection> {
    if (sessionId !== FIXTURE_SESSION_ID) {
      throw this.#notFound(`/v1/sessions/${sessionId}/projection`, "Interview Session");
    }
    return fixtureSessionProjection;
  }

  async submitPreflight(sessionId: string, report: PreflightReport): Promise<PreflightResult> {
    await this.getSession(sessionId);
    const checks: PreflightResult["checks"] = [
      {
        id: "supported_browser",
        status: report.browser.supported ? "passed" : "failed",
        detail: `${report.browser.name} ${String(report.browser.major_version)}`,
      },
      {
        id: "extension_installed",
        status: report.extension.installed ? "passed" : "failed",
        detail: report.extension.installed
          ? "The uTrace extension answered."
          : "The uTrace extension did not answer.",
      },
      {
        id: "extension_account_linked",
        status: report.extension.account_linked ? "passed" : "failed",
        detail: report.extension.account_linked
          ? "Linked to your uTrace account."
          : "Open the extension and link your uTrace account.",
      },
      {
        id: "extension_host_permission",
        status: report.permissions.host ? "passed" : "failed",
        detail: report.permissions.host
          ? "Host access granted."
          : "Grant the extension access to all sites.",
      },
      {
        id: "microphone",
        status: report.permissions.microphone ? "passed" : "failed",
        detail: report.permissions.microphone
          ? "Microphone available."
          : "Allow microphone access for this tab.",
      },
    ];
    const passed = checks.every((check) => check.status === "passed");
    return { passed, revision: fixtureSessionProjection.revision + 1, checks };
  }

  async createVoiceConnection(sessionId: string, sdpOffer: string): Promise<VoiceConnectionRecord> {
    await this.getSession(sessionId);
    if (!sdpOffer.startsWith("v=0")) {
      throw new ControlPlaneError(
        {
          code: "rejected",
          method: "POST",
          path: `/v1/sessions/${sessionId}/voice/connections`,
          status: 422,
          responseBody: null,
        },
        "The session description offer is not a valid SDP document.",
        {},
      );
    }
    return {
      sdp_answer: FIXTURE_SDP_ANSWER,
      connection_id: "0199a0c0-0000-7aaa-8000-000000000101",
      voice_run_id: "0199a0c0-0000-7aaa-8000-000000000102",
    };
  }

  async sendControl(sessionId: string, action: SessionControlAction): Promise<SessionControlAck> {
    await this.getSession(sessionId);
    return {
      revision: fixtureSessionProjection.revision + 1,
      state: nextTabState(fixtureSessionProjection.state, action),
      controls: nextControls(fixtureSessionProjection.controls, action),
    };
  }

  async requestPreviewHandoff(sessionId: string): Promise<PreviewHandoff> {
    await this.getSession(sessionId);
    return {
      front_door_url: "https://fixture.preview.utrace.dev/?handoff=fixture",
      expires_at: FIXTURE_EXPIRY,
    };
  }

  async requestBindingOffer(sessionId: string): Promise<BindingOffer> {
    await this.getSession(sessionId);
    return {
      schema_version: 1,
      offer_token: "Zml4dHVyZV9iaW5kaW5nX29mZmVyX3Rva2Vu",
      expires_at: FIXTURE_EXPIRY,
      protocol_version: "1.0",
      session_id: FIXTURE_SESSION_ID,
      client_id: fixtureClient.client.id,
      extension_installation_id: FIXTURE_EXTENSION_INSTALLATION_ID,
      preview_runtime_allocation_id: FIXTURE_PREVIEW_ALLOCATION_ID,
    };
  }

  /**
   * A deterministic link code. The control plane mints a single-use one bound
   * to the authenticated person; the fixture cannot, so the code it returns is
   * only good for exercising the page and the extension's exchange.
   */
  async createExtensionLinkCode(
    request: Readonly<{
      redirect_uri: string;
      extension_version: string;
      protocol_version: string;
    }>,
  ): Promise<ExtensionLinkCode> {
    return {
      link_code:
        `fixture-link-${request.protocol_version}-${request.extension_version}`.replaceAll(
          ".",
          "-",
        ),
      expires_at: FIXTURE_EXPIRY,
      redirect_uri: request.redirect_uri,
    };
  }

  #assertClient(clientId: string): void {
    if (clientId !== fixtureClient.client.id) {
      throw new ControlPlaneError(
        {
          code: "forbidden",
          method: "GET",
          path: `/v1/clients/${clientId}`,
          status: 403,
          responseBody: null,
        },
        `The fixture control plane only serves ${fixtureClient.client.id}; received ${clientId}.`,
        {},
      );
    }
  }

  #notFound(path: string, subject: string): ControlPlaneError {
    return new ControlPlaneError(
      { code: "not_found", method: "GET", path, status: 404, responseBody: null },
      `${subject} not found in the Shopalytics fixture.`,
      {},
    );
  }
}

function nextTabState(state: TabState, action: SessionControlAction): TabState {
  if (action === "pause") return "paused";
  if (action === "resume") return "interviewing";
  if (action === "finish_later" || action === "end_confirm") return "ended";
  return state;
}

function nextControls(controls: ControlsState, action: SessionControlAction): ControlsState {
  if (action === "mute") {
    return { microphone: "muted", available: ["unmute", "pause", "finish_later", "end_confirm"] };
  }
  if (action === "unmute") {
    return { microphone: "unmuted", available: ["mute", "pause", "finish_later", "end_confirm"] };
  }
  if (action === "pause") {
    return { ...controls, available: ["resume", "finish_later", "end_confirm"] };
  }
  if (action === "resume") {
    return { ...controls, available: ["mute", "pause", "finish_later", "end_confirm"] };
  }
  return { ...controls, available: [] };
}
