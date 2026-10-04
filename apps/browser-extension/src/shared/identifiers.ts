/**
 * UUIDv7 and RFC 3339 UTC helpers.
 *
 * `crypto.randomUUID` produces version 4, which the contract package's
 * identifier rule rejects, so the extension mints its own version 7 values for
 * the message, action, and exchange identities it originates.
 */

export class IdentifierError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "IdentifierError";
  }
}

/**
 * Builds a UUIDv7 from an explicit millisecond timestamp and ten random bytes,
 * per RFC 9562 section 5.7.
 */
export function createUuidV7(epochMilliseconds: number, randomBytes: Uint8Array): string {
  if (!Number.isSafeInteger(epochMilliseconds) || epochMilliseconds < 0) {
    throw new IdentifierError(
      `UUIDv7 needs a non-negative integer timestamp; received ${String(epochMilliseconds)}.`,
    );
  }
  if (epochMilliseconds > 0xffffffffffff) {
    throw new IdentifierError("UUIDv7 timestamps must fit in 48 bits.");
  }
  if (randomBytes.length !== 10) {
    throw new IdentifierError(
      `UUIDv7 needs exactly ten random bytes; received ${String(randomBytes.length)}.`,
    );
  }

  const bytes = new Uint8Array(16);
  for (let index = 0; index < 6; index += 1) {
    bytes[index] = Math.floor(epochMilliseconds / 2 ** (8 * (5 - index))) & 0xff;
  }
  bytes.set(randomBytes, 6);
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x70;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;

  let text = "";
  for (let index = 0; index < 16; index += 1) {
    text += (bytes[index] ?? 0).toString(16).padStart(2, "0");
    if (index === 3 || index === 5 || index === 7 || index === 9) {
      text += "-";
    }
  }
  return text;
}

export function newUuidV7(): string {
  return createUuidV7(Date.now(), crypto.getRandomValues(new Uint8Array(10)));
}

/** RFC 3339 UTC with millisecond precision, matching the contract pattern. */
export function timestampOf(epochMilliseconds: number): string {
  return new Date(epochMilliseconds).toISOString();
}

export function nowTimestamp(): string {
  return timestampOf(Date.now());
}

export function encodeBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

export function encodeUtf8(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}
