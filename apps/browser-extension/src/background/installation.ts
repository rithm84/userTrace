/**
 * Installation identity and account linking.
 *
 * Setup happens once for the uTrace User, not per client or interview. The
 * flow links the installation to the authenticated person through the uTrace
 * account-link page, registers the device public key, and stores only the
 * returned installation record. Signing out or revoking the installation blocks
 * new bindings and drops the device key, so a later link creates a new identity.
 * An installation the control plane refuses as unknown or revoked is dropped
 * the same way.
 */
import {
  createChallenge,
  deletePersistedDeviceKey,
  exportPublicKeyBase64Url,
  loadDeviceKeyPair,
  provePossession,
} from "./deviceKey";
import {
  clearInstallation,
  clearSessionState,
  readInstallation,
  writeInstallation,
  type InstallationRecord,
} from "./storage";
import { API_AUDIENCE, BUILD_ORIGINS, EXTENSION_VERSION, installationsUrl } from "../shared/buildConfig";
import { PROTOCOL_VERSION, SCHEMA_VERSION } from "../protocol/common";
import {
  parseInstallationRegistrationResponse,
  type InstallationRegistrationRequest,
} from "../protocol/installation";
import { withRetries } from "../shared/retry";

const LINK_PATH = "/extension/link";
const LINK_CODE_PARAMETER = "link_code";

export type AccountLinkErrorCode =
  | "link_flow_failed"
  | "link_code_missing"
  | "registration_rejected"
  | "protocol_unsupported";

export class AccountLinkError extends Error {
  readonly code: AccountLinkErrorCode;

  constructor(code: AccountLinkErrorCode, message: string) {
    super(message);
    this.name = "AccountLinkError";
    this.code = code;
  }
}

/** Runs the one-time account link and registers this installation. */
export async function linkAccount(): Promise<InstallationRecord> {
  const redirectUri = chrome.identity.getRedirectURL("extension-link");
  const linkUrl = new URL(LINK_PATH, BUILD_ORIGINS.accountLinkOrigin);
  linkUrl.searchParams.set("redirect_uri", redirectUri);
  linkUrl.searchParams.set("extension_version", EXTENSION_VERSION);
  linkUrl.searchParams.set("protocol_version", PROTOCOL_VERSION);

  const responseUrl = await chrome.identity
    .launchWebAuthFlow({ url: linkUrl.toString(), interactive: true })
    .catch((error: unknown) => {
      throw new AccountLinkError(
        "link_flow_failed",
        `The uTrace account-link flow did not complete: ${describe(error)}.`,
      );
    });

  if (responseUrl === undefined) {
    throw new AccountLinkError(
      "link_flow_failed",
      "The uTrace account-link flow closed without returning to the extension.",
    );
  }

  const linkCode = new URL(responseUrl).searchParams.get(LINK_CODE_PARAMETER);
  if (linkCode === null || linkCode.length === 0) {
    throw new AccountLinkError(
      "link_code_missing",
      `The account-link redirect carried no ${LINK_CODE_PARAMETER}.`,
    );
  }

  return registerInstallation(linkCode);
}

async function registerInstallation(linkCode: string): Promise<InstallationRecord> {
  const pair = await loadDeviceKeyPair();
  const request: InstallationRegistrationRequest = {
    schema_version: SCHEMA_VERSION,
    kind: "installation_registration_request",
    link_code: linkCode,
    device_public_key_spki_b64u: await exportPublicKeyBase64Url(pair),
    extension_version: EXTENSION_VERSION,
    supported_protocol_versions: [PROTOCOL_VERSION],
    proof_of_possession: await provePossession(
      pair,
      createChallenge(API_AUDIENCE, "installation_registration", null),
    ),
  };

  const body = await withRetries(
    { attempts: 3, initialDelayMs: 400, operation: "register_installation" },
    async () => {
      const response = await fetch(installationsUrl(), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(request),
      });
      if (!response.ok) {
        throw new AccountLinkError(
          "registration_rejected",
          `POST ${installationsUrl()} returned ${String(response.status)}: ${await response.text()}`,
        );
      }
      const payload: unknown = await response.json();
      return payload;
    },
  );

  const registration = parseInstallationRegistrationResponse(body);
  if (registration.negotiated_protocol_version !== PROTOCOL_VERSION) {
    throw new AccountLinkError(
      "protocol_unsupported",
      `The control plane negotiated extension protocol ${registration.negotiated_protocol_version}, which this build does not implement.`,
    );
  }

  const record: InstallationRecord = {
    installation_id: registration.installation_id,
    account_link_state: registration.account_link_state,
    negotiated_protocol_version: registration.negotiated_protocol_version,
    registered_at: registration.registered_at,
  };
  await writeInstallation(record);
  return record;
}

/**
 * Revokes this installation at the control plane and locally. A reinstall or a
 * new link gets a new installation identity and a new device key.
 */
export async function revokeInstallation(): Promise<void> {
  const record = await readInstallation();
  if (record !== null) {
    const pair = await loadDeviceKeyPair();
    const proof = await provePossession(
      pair,
      createChallenge(API_AUDIENCE, "installation_revocation", record.installation_id),
    );
    await withRetries(
      { attempts: 2, initialDelayMs: 400, operation: "revoke_installation" },
      async () => {
        const response = await fetch(`${installationsUrl()}/${record.installation_id}`, {
          method: "DELETE",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ schema_version: SCHEMA_VERSION, proof_of_possession: proof }),
        });
        if (!response.ok) {
          throw new AccountLinkError(
            "registration_rejected",
            `DELETE ${installationsUrl()}/${record.installation_id} returned ${String(response.status)}.`,
          );
        }
        return response;
      },
    );
  }

  await forgetInstallation();
}

/**
 * Drops the local installation record, session checkpoint, and device key. The
 * control plane's device keys are unique, so a later link must register a new
 * key rather than the one a revoked installation still holds.
 */
export async function forgetInstallation(): Promise<void> {
  await clearInstallation();
  await clearSessionState();
  await deletePersistedDeviceKey();
}

function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
