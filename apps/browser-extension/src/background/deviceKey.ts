/**
 * The installation's device key.
 *
 * A non-exportable ECDSA P-256 private key lives in IndexedDB inside the
 * service worker; only its public half ever leaves. The extension proves
 * possession to obtain short-lived authority instead of holding a reusable
 * bearer credential, so a stolen storage snapshot cannot authenticate.
 */
import { encodeBase64Url, encodeUtf8, newUuidV7, nowTimestamp } from "../shared/identifiers";
import type { ProofOfPossession } from "../protocol/installation";

const DATABASE_NAME = "utrace-extension-identity";
const STORE_NAME = "device-key";
const RECORD_KEY = "current";
const KEY_ALGORITHM: EcKeyGenParams = { name: "ECDSA", namedCurve: "P-256" };
const SIGN_ALGORITHM: EcdsaParams = { name: "ECDSA", hash: "SHA-256" };

export type DeviceKeyErrorCode =
  | "indexeddb_unavailable"
  | "key_generation_failed"
  | "key_record_corrupt";

export class DeviceKeyError extends Error {
  readonly code: DeviceKeyErrorCode;

  constructor(code: DeviceKeyErrorCode, message: string) {
    super(message);
    this.name = "DeviceKeyError";
    this.code = code;
  }
}

export type DeviceKeyPair = Readonly<{
  privateKey: CryptoKey;
  publicKey: CryptoKey;
}>;

/** The claims the control plane checks for freshness and single use. */
export type PossessionChallenge = Readonly<{
  audience: string;
  purpose:
    | "installation_registration"
    | "control_socket"
    | "installation_revocation"
    | "session_preflight";
  jti: string;
  issued_at: string;
  installation_id?: string;
}>;

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 1);
    request.onupgradeneeded = (): void => {
      request.result.createObjectStore(STORE_NAME);
    };
    request.onsuccess = (): void => {
      resolve(request.result);
    };
    request.onerror = (): void => {
      reject(
        new DeviceKeyError(
          "indexeddb_unavailable",
          `The installation key store could not be opened: ${String(request.error?.message)}.`,
        ),
      );
    };
  });
}

function readRecord(database: IDBDatabase): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const request = database.transaction(STORE_NAME, "readonly").objectStore(STORE_NAME).get(RECORD_KEY);
    request.onsuccess = (): void => {
      resolve(request.result);
    };
    request.onerror = (): void => {
      reject(
        new DeviceKeyError(
          "indexeddb_unavailable",
          `The installation key could not be read: ${String(request.error?.message)}.`,
        ),
      );
    };
  });
}

function writeRecord(database: IDBDatabase, pair: DeviceKeyPair): Promise<void> {
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    transaction.objectStore(STORE_NAME).put(pair, RECORD_KEY);
    transaction.oncomplete = (): void => {
      resolve();
    };
    transaction.onerror = (): void => {
      reject(
        new DeviceKeyError(
          "indexeddb_unavailable",
          `The installation key could not be stored: ${String(transaction.error?.message)}.`,
        ),
      );
    };
  });
}

function isDeviceKeyPair(candidate: unknown): candidate is DeviceKeyPair {
  if (candidate === null || typeof candidate !== "object") {
    return false;
  }
  const record = candidate as { privateKey?: unknown; publicKey?: unknown };
  return (
    record.privateKey instanceof CryptoKey &&
    record.publicKey instanceof CryptoKey &&
    !record.privateKey.extractable
  );
}

/** Returns the existing device key pair, generating one on first use. */
export async function loadDeviceKeyPair(): Promise<DeviceKeyPair> {
  const database = await openDatabase();
  try {
    const existing = await readRecord(database);
    if (existing !== undefined) {
      if (!isDeviceKeyPair(existing)) {
        throw new DeviceKeyError(
          "key_record_corrupt",
          "The stored installation key is not a non-exportable ECDSA key pair.",
        );
      }
      return existing;
    }

    const generated = await crypto.subtle.generateKey(KEY_ALGORITHM, false, ["sign", "verify"]);
    if (!("privateKey" in generated) || generated.privateKey.extractable) {
      throw new DeviceKeyError(
        "key_generation_failed",
        "WebCrypto returned an extractable or single installation key.",
      );
    }
    const pair: DeviceKeyPair = {
      privateKey: generated.privateKey,
      publicKey: generated.publicKey,
    };
    await writeRecord(database, pair);
    return pair;
  } finally {
    database.close();
  }
}

export async function deletePersistedDeviceKey(): Promise<void> {
  const database = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = database.transaction(STORE_NAME, "readwrite");
      transaction.objectStore(STORE_NAME).delete(RECORD_KEY);
      transaction.oncomplete = (): void => {
        resolve();
      };
      transaction.onerror = (): void => {
        reject(
          new DeviceKeyError(
            "indexeddb_unavailable",
            `The installation key could not be removed: ${String(transaction.error?.message)}.`,
          ),
        );
      };
    });
  } finally {
    database.close();
  }
}

export async function exportPublicKeyBase64Url(pair: DeviceKeyPair): Promise<string> {
  const spki = await crypto.subtle.exportKey("spki", pair.publicKey);
  return encodeBase64Url(new Uint8Array(spki));
}

export function createChallenge(
  audience: string,
  purpose: PossessionChallenge["purpose"],
  installationId: string | null,
): PossessionChallenge {
  return {
    audience,
    purpose,
    jti: newUuidV7(),
    issued_at: nowTimestamp(),
    ...(installationId === null ? {} : { installation_id: installationId }),
  };
}

export async function provePossession(
  pair: DeviceKeyPair,
  challenge: PossessionChallenge,
): Promise<ProofOfPossession> {
  const encoded = encodeUtf8(JSON.stringify(challenge));
  const signature = await crypto.subtle.sign(
    SIGN_ALGORITHM,
    pair.privateKey,
    encoded.slice().buffer,
  );
  return {
    algorithm: "ES256",
    challenge_b64u: encodeBase64Url(encoded),
    signature_b64u: encodeBase64Url(new Uint8Array(signature)),
  };
}
