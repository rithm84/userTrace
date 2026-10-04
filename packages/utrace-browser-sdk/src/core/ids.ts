/** Identifier generation. All identifiers are opaque and carry no authority. */

/** Injected randomness so identifier generation stays a pure function of inputs. */
export interface RandomSource {
  readonly fill: (bytes: Uint8Array) => void;
}

export const webCryptoRandom = (crypto: Crypto): RandomSource => ({
  fill: (bytes: Uint8Array): void => {
    crypto.getRandomValues(bytes);
  },
});

const HEX = "0123456789abcdef";

const toHex = (bytes: Uint8Array): string => {
  let out = "";
  for (const byte of bytes) {
    out += HEX[(byte >>> 4) & 0x0f] ?? "";
    out += HEX[byte & 0x0f] ?? "";
  }
  return out;
};

/**
 * RFC 9562 UUIDv7: 48-bit big-endian millisecond timestamp, version 7, variant
 * 0b10, and 74 random bits. Sorting by identifier sorts by creation time.
 */
export const uuidv7 = (epochMs: number, random: RandomSource): string => {
  const bytes = new Uint8Array(16);
  random.fill(bytes);
  const timestamp = Math.trunc(epochMs);
  bytes[0] = Math.trunc(timestamp / 2 ** 40) & 0xff;
  bytes[1] = Math.trunc(timestamp / 2 ** 32) & 0xff;
  bytes[2] = Math.trunc(timestamp / 2 ** 24) & 0xff;
  bytes[3] = Math.trunc(timestamp / 2 ** 16) & 0xff;
  bytes[4] = Math.trunc(timestamp / 2 ** 8) & 0xff;
  bytes[5] = timestamp & 0xff;
  bytes[6] = 0x70 | ((bytes[6] ?? 0) & 0x0f);
  bytes[8] = 0x80 | ((bytes[8] ?? 0) & 0x3f);
  const hex = toHex(bytes);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

/** W3C trace context identifiers: 16-byte trace id, 8-byte span id, never all-zero. */
export const traceId = (random: RandomSource): string => {
  const bytes = new Uint8Array(16);
  random.fill(bytes);
  bytes[0] = ((bytes[0] ?? 0) | 0x01) & 0xff;
  return toHex(bytes);
};

export const spanId = (random: RandomSource): string => {
  const bytes = new Uint8Array(8);
  random.fill(bytes);
  bytes[0] = ((bytes[0] ?? 0) | 0x01) & 0xff;
  return toHex(bytes);
};

/** Per-document nonce for the activation gate and the extension bridge handshake. */
export const nonce = (random: RandomSource): string => {
  const bytes = new Uint8Array(24);
  random.fill(bytes);
  return toHex(bytes);
};

/**
 * Opaque document-scoped identifier for a mounted target or pseudonym. The
 * counter is supplied by the caller so identifiers are never reused inside one
 * document and generation stays pure.
 */
export const opaqueId = (prefix: string, ordinal: number, random: RandomSource): string => {
  const bytes = new Uint8Array(8);
  random.fill(bytes);
  return `${prefix}_${String(ordinal)}_${toHex(bytes)}`;
};
