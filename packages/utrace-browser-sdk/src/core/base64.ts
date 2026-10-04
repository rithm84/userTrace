/** base64url helpers for signed envelopes. No padding, per RFC 4648 §5. */

import { ContractViolationError } from "./json.js";

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

export const decodeBase64Url = (text: string, path: string): Uint8Array<ArrayBuffer> => {
  const length = text.length;
  const remainder = length % 4;
  if (remainder === 1) throw new ContractViolationError(path, "base64url text", "truncated group");
  const bytes = new Uint8Array(new ArrayBuffer(Math.floor((length * 6) / 8)));
  let accumulator = 0;
  let bits = 0;
  let written = 0;
  for (let index = 0; index < length; index += 1) {
    const value = ALPHABET.indexOf(text[index] ?? "");
    if (value < 0) throw new ContractViolationError(path, "base64url text", "invalid character");
    accumulator = (accumulator << 6) | value;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      bytes[written] = (accumulator >> bits) & 0xff;
      written += 1;
    }
  }
  if (written !== bytes.length) {
    throw new ContractViolationError(path, "base64url text", "unexpected decoded length");
  }
  return bytes;
};

export const encodeBase64Url = (bytes: Uint8Array): string => {
  let out = "";
  let accumulator = 0;
  let bits = 0;
  for (const byte of bytes) {
    accumulator = (accumulator << 8) | byte;
    bits += 8;
    while (bits >= 6) {
      bits -= 6;
      out += ALPHABET[(accumulator >> bits) & 0x3f] ?? "";
    }
  }
  if (bits > 0) out += ALPHABET[(accumulator << (6 - bits)) & 0x3f] ?? "";
  return out;
};

export const utf8Decode = (bytes: Uint8Array): string => new TextDecoder("utf-8", { fatal: true }).decode(bytes);

export const utf8Encode = (text: string): Uint8Array<ArrayBuffer> => new TextEncoder().encode(text);
