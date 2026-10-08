/**
 * The control-plane client the product surfaces use.
 *
 * `ControlPlaneClient` is the whole surface the web application depends on;
 * `HttpControlPlaneClient` speaks the real HTTP contract in
 * `apps/api/openapi.json` and `FixtureControlPlaneClient` (see `./fixture.ts`)
 * answers the same interface from the Shopalytics fixture for tests and offline
 * previews.
 */

import type { z } from "zod";

import { ControlPlaneError, codeForStatus } from "./errors";
import {
  allowanceSchema,
  bindingOfferSchema,
  caseDetailSchema,
  caseFileDetailSchema,
  caseFileListSchema,
  caseListSchema,
  caseStopResultSchema,
  clientConfigurationSchema,
  clientSchema,
  extensionLinkCodeSchema,
  invitationClaimSchema,
  memberListSchema,
  offersViewSchema,
  participantAccountSchema,
  participantPreferencesSchema,
  preflightResultSchema,
  previewHandoffSchema,
  referenceResolutionSchema,
  sessionControlAckSchema,
  sessionProjectionDocumentSchema,
  sessionRecordSchema,
  voiceConnectionSchema,
  type Allowance,
  type BindingOffer,
  type CaseDetail,
  type CaseFileDetail,
  type CaseFileList,
  type CaseList,
  type CaseStopResult,
  type Client,
  type ClientConfiguration,
  type ClientMember,
  type ExtensionLinkCode,
  type InterviewSession,
  type InvitationClaim,
  type OffersView,
  type ParticipantAccount,
  type ParticipantPreferences,
  type ParticipantPreferencesUpdate,
  type PreflightReport,
  type PreflightResult,
  type PreviewHandoff,
  type ResolvedReference,
  type SessionControlAck,
  type SessionControlAction,
  type SessionProjection,
  type VoiceConnectionRecord,
} from "./schemas";

export interface ControlPlaneClient {
  /* staff */
  getClient(clientId: string): Promise<Client>;
  getConfiguration(clientId: string): Promise<ClientConfiguration>;
  listMembers(clientId: string): Promise<readonly ClientMember[]>;
  resolveReferences(
    clientId: string,
    refs: readonly string[],
  ): Promise<readonly ResolvedReference[]>;
  listCases(clientId: string): Promise<CaseList>;
  getCase(clientId: string, productSignalId: string): Promise<CaseDetail>;
  stopCase(clientId: string, productSignalId: string, reason: string): Promise<CaseStopResult>;
  listCaseFiles(clientId: string): Promise<CaseFileList>;
  getCaseFile(clientId: string, caseFileId: string): Promise<CaseFileDetail>;
  getOffers(clientId: string): Promise<OffersView>;
  authorizeOffersBudget(
    clientId: string,
    authorization: Readonly<{ currency: string; authorized_minor_units: number }>,
  ): Promise<Allowance>;

  /* participant */
  getParticipantAccount(): Promise<ParticipantAccount>;
  updateParticipantPreferences(update: ParticipantPreferencesUpdate): Promise<ParticipantPreferences>;
  claimInvitation(invitationToken: string): Promise<InvitationClaim>;
  getSession(sessionId: string): Promise<InterviewSession>;
  getSessionProjection(sessionId: string): Promise<SessionProjection>;
  submitPreflight(sessionId: string, report: PreflightReport): Promise<PreflightResult>;
  createVoiceConnection(sessionId: string): Promise<VoiceConnectionRecord>;
  sendControl(sessionId: string, action: SessionControlAction): Promise<SessionControlAck>;
  requestPreviewHandoff(sessionId: string): Promise<PreviewHandoff>;
  requestBindingOffer(sessionId: string): Promise<BindingOffer>;
  createExtensionLinkCode(
    request: Readonly<{
      redirect_uri: string;
      extension_version: string;
      protocol_version: string;
    }>,
  ): Promise<ExtensionLinkCode>;
}

export type HttpControlPlaneOptions = Readonly<{
  baseUrl: string;
  accessToken: string;
  /** Attempts for a read, including the first. Writes are never repeated. */
  readAttempts: number;
  fetchImplementation: typeof fetch;
}>;

const JSON_CONTENT_TYPE = "application/json";

export class HttpControlPlaneClient implements ControlPlaneClient {
  readonly #baseUrl: string;
  readonly #accessToken: string;
  readonly #readAttempts: number;
  readonly #fetch: typeof fetch;

  constructor(options: HttpControlPlaneOptions) {
    if (options.readAttempts < 1) {
      throw new RangeError(
        `Control-plane read attempts must be at least 1; received ${String(options.readAttempts)}.`,
      );
    }
    this.#baseUrl = options.baseUrl;
    this.#accessToken = options.accessToken;
    this.#readAttempts = options.readAttempts;
    this.#fetch = options.fetchImplementation;
  }

  getClient(clientId: string): Promise<Client> {
    return this.#read(`/v1/clients/${segment(clientId)}`, clientSchema);
  }

  getConfiguration(clientId: string): Promise<ClientConfiguration> {
    return this.#read(`/v1/clients/${segment(clientId)}/configuration`, clientConfigurationSchema);
  }

  async listMembers(clientId: string): Promise<readonly ClientMember[]> {
    const body = await this.#read(`/v1/clients/${segment(clientId)}/members`, memberListSchema);
    return body.members;
  }

  async resolveReferences(
    clientId: string,
    refs: readonly string[],
  ): Promise<readonly ResolvedReference[]> {
    if (refs.length === 0) {
      return [];
    }
    const query = new URLSearchParams();
    for (const ref of refs) {
      query.append("ref", ref);
    }
    const body = await this.#read(
      `/v1/clients/${segment(clientId)}/references?${query.toString()}`,
      referenceResolutionSchema,
    );
    return body.references;
  }

  listCases(clientId: string): Promise<CaseList> {
    return this.#read(`/v1/clients/${segment(clientId)}/cases`, caseListSchema);
  }

  getCase(clientId: string, productSignalId: string): Promise<CaseDetail> {
    return this.#read(
      `/v1/clients/${segment(clientId)}/cases/${segment(productSignalId)}`,
      caseDetailSchema,
    );
  }

  stopCase(clientId: string, productSignalId: string, reason: string): Promise<CaseStopResult> {
    return this.#write(
      "POST",
      `/v1/clients/${segment(clientId)}/cases/${segment(productSignalId)}/stop`,
      { reason },
      caseStopResultSchema,
    );
  }

  listCaseFiles(clientId: string): Promise<CaseFileList> {
    return this.#read(`/v1/clients/${segment(clientId)}/case-files`, caseFileListSchema);
  }

  getCaseFile(clientId: string, caseFileId: string): Promise<CaseFileDetail> {
    return this.#read(
      `/v1/clients/${segment(clientId)}/case-files/${segment(caseFileId)}`,
      caseFileDetailSchema,
    );
  }

  getOffers(clientId: string): Promise<OffersView> {
    return this.#read(`/v1/clients/${segment(clientId)}/offers/dispositions`, offersViewSchema);
  }

  authorizeOffersBudget(
    clientId: string,
    authorization: Readonly<{ currency: string; authorized_minor_units: number }>,
  ): Promise<Allowance> {
    return this.#write(
      "POST",
      `/v1/clients/${segment(clientId)}/offers/budget`,
      authorization,
      allowanceSchema,
    );
  }

  claimInvitation(invitationToken: string): Promise<InvitationClaim> {
    return this.#write(
      "POST",
      `/v1/invitations/${segment(invitationToken)}/claim`,
      null,
      invitationClaimSchema,
    );
  }

  getParticipantAccount(): Promise<ParticipantAccount> {
    return this.#read("/v1/participants/me", participantAccountSchema);
  }

  updateParticipantPreferences(update: ParticipantPreferencesUpdate): Promise<ParticipantPreferences> {
    return this.#write("PATCH", "/v1/participants/me/preferences", update, participantPreferencesSchema);
  }

  async getSession(sessionId: string): Promise<InterviewSession> {
    const body = await this.#read(`/v1/sessions/${segment(sessionId)}`, sessionRecordSchema);
    return body.session;
  }

  async getSessionProjection(sessionId: string): Promise<SessionProjection> {
    const body = await this.#read(
      `/v1/sessions/${segment(sessionId)}/projection`,
      sessionProjectionDocumentSchema,
    );
    return body.projection;
  }

  submitPreflight(sessionId: string, report: PreflightReport): Promise<PreflightResult> {
    return this.#write(
      "POST",
      `/v1/sessions/${segment(sessionId)}/preflight`,
      report,
      preflightResultSchema,
    );
  }

  createVoiceConnection(sessionId: string): Promise<VoiceConnectionRecord> {
    return this.#write(
      "POST",
      `/v1/sessions/${segment(sessionId)}/voice/connections`,
      null,
      voiceConnectionSchema,
    );
  }

  sendControl(sessionId: string, action: SessionControlAction): Promise<SessionControlAck> {
    return this.#write(
      "POST",
      `/v1/sessions/${segment(sessionId)}/controls`,
      { action },
      sessionControlAckSchema,
    );
  }

  requestPreviewHandoff(sessionId: string): Promise<PreviewHandoff> {
    return this.#write(
      "POST",
      `/v1/sessions/${segment(sessionId)}/preview-handoff`,
      null,
      previewHandoffSchema,
    );
  }

  requestBindingOffer(sessionId: string): Promise<BindingOffer> {
    return this.#write(
      "POST",
      `/v1/sessions/${segment(sessionId)}/extension-offer`,
      null,
      bindingOfferSchema,
    );
  }

  createExtensionLinkCode(
    request: Readonly<{
      redirect_uri: string;
      extension_version: string;
      protocol_version: string;
    }>,
  ): Promise<ExtensionLinkCode> {
    return this.#write("POST", "/v1/extension/link-codes", request, extensionLinkCodeSchema);
  }

  async #read<Parsed>(path: string, schema: z.ZodType<Parsed>): Promise<Parsed> {
    let lastError: ControlPlaneError | null = null;
    for (let attempt = 1; attempt <= this.#readAttempts; attempt += 1) {
      try {
        return await this.#request("GET", path, null, schema);
      } catch (error) {
        if (!(error instanceof ControlPlaneError) || !isRepeatable(error)) {
          throw error;
        }
        lastError = error;
        console.warn("control_plane_read_retry", {
          method: "GET",
          path,
          attempt,
          attempts: this.#readAttempts,
          code: error.code,
          status: error.status,
        });
      }
    }
    throw lastError ?? new Error("Control-plane read loop ended without a result.");
  }

  #write<Parsed>(
    method: "POST" | "PATCH",
    path: string,
    body: unknown,
    schema: z.ZodType<Parsed>,
  ): Promise<Parsed> {
    return this.#request(method, path, body, schema);
  }

  async #request<Parsed>(
    method: string,
    path: string,
    body: unknown,
    schema: z.ZodType<Parsed>,
  ): Promise<Parsed> {
    const headers: Record<string, string> = {
      accept: JSON_CONTENT_TYPE,
      authorization: `Bearer ${this.#accessToken}`,
    };
    if (body !== null) {
      headers["content-type"] = JSON_CONTENT_TYPE;
    }

    let response: Response;
    try {
      response = await this.#fetch(`${this.#baseUrl}${path}`, {
        method,
        headers,
        body: body === null ? null : JSON.stringify(body),
        cache: "no-store",
      });
    } catch (error) {
      throw new ControlPlaneError(
        { code: "transport", method, path, status: null, responseBody: null },
        `${method} ${path} could not reach the uTrace control plane.`,
        { cause: error },
      );
    }

    const text = await response.text();
    if (!response.ok) {
      throw new ControlPlaneError(
        {
          code: codeForStatus(response.status),
          method,
          path,
          status: response.status,
          responseBody: text.slice(0, 2000),
        },
        `${method} ${path} failed with HTTP ${String(response.status)}.`,
        {},
      );
    }

    let payload: unknown;
    try {
      payload = JSON.parse(text) as unknown;
    } catch (error) {
      throw new ControlPlaneError(
        {
          code: "invalid_response",
          method,
          path,
          status: response.status,
          responseBody: text.slice(0, 2000),
        },
        `${method} ${path} returned a body that is not JSON.`,
        { cause: error },
      );
    }

    const parsed = schema.safeParse(payload);
    if (!parsed.success) {
      throw new ControlPlaneError(
        {
          code: "invalid_response",
          method,
          path,
          status: response.status,
          responseBody: JSON.stringify(parsed.error.issues).slice(0, 2000),
        },
        `${method} ${path} returned a body that does not match the uTrace contract.`,
        { cause: parsed.error },
      );
    }
    return parsed.data;
  }
}

function isRepeatable(error: ControlPlaneError): boolean {
  return error.code === "transport" || error.code === "unavailable";
}

function segment(value: string): string {
  if (value.length === 0) {
    throw new RangeError("A control-plane path segment must not be empty.");
  }
  return encodeURIComponent(value);
}
