/**
 * Deterministic toolbar icons, generated at build time so no binary artwork is
 * committed. The mark is the cast's coral body with the ink rim and two eyes
 * from `docs/DESIGN.md`, drawn with 4x supersampling and encoded as RGBA PNG.
 *
 * Node-only: this module runs inside the build script, never in the extension.
 */
import { deflateSync } from "node:zlib";

const CORAL: Rgb = { r: 0xfc, g: 0xad, b: 0xa3 };
const INK: Rgb = { r: 0x00, g: 0x00, b: 0x00 };
const SAMPLES_PER_AXIS = 4;

type Rgb = Readonly<{ r: number; g: number; b: number }>;

export function createIconPng(size: number): Uint8Array {
  if (!Number.isInteger(size) || size < 8) {
    throw new RangeError(`Icon size must be an integer of at least 8; received ${String(size)}.`);
  }
  return encodePng(size, size, rasteriseMark(size));
}

/** RGBA rows, one `Uint8Array` per row, in top-to-bottom order. */
function rasteriseMark(size: number): Uint8Array {
  const pixels = new Uint8Array(size * size * 4);
  const centre = size / 2;
  const bodyRadius = size * 0.46;
  const rimWidth = Math.max(1, size * 0.075);
  const eyeRadius = size * 0.085;
  const eyeOffsetX = size * 0.15;
  const eyeOffsetY = size * 0.06;
  const drawEyes = size >= 32;

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      let bodyCoverage = 0;
      let inkCoverage = 0;

      for (let sy = 0; sy < SAMPLES_PER_AXIS; sy += 1) {
        for (let sx = 0; sx < SAMPLES_PER_AXIS; sx += 1) {
          const px = x + (sx + 0.5) / SAMPLES_PER_AXIS;
          const py = y + (sy + 0.5) / SAMPLES_PER_AXIS;
          const distance = Math.hypot(px - centre, py - centre);
          if (distance > bodyRadius) {
            continue;
          }
          bodyCoverage += 1;
          if (distance > bodyRadius - rimWidth) {
            inkCoverage += 1;
            continue;
          }
          if (
            drawEyes &&
            (Math.hypot(px - (centre - eyeOffsetX), py - (centre - eyeOffsetY)) < eyeRadius ||
              Math.hypot(px - (centre + eyeOffsetX), py - (centre - eyeOffsetY)) < eyeRadius)
          ) {
            inkCoverage += 1;
          }
        }
      }

      const totalSamples = SAMPLES_PER_AXIS * SAMPLES_PER_AXIS;
      const alpha = bodyCoverage / totalSamples;
      if (alpha === 0) {
        continue;
      }
      const inkShare = inkCoverage / bodyCoverage;
      const offset = (y * size + x) * 4;
      pixels[offset] = mix(CORAL.r, INK.r, inkShare);
      pixels[offset + 1] = mix(CORAL.g, INK.g, inkShare);
      pixels[offset + 2] = mix(CORAL.b, INK.b, inkShare);
      pixels[offset + 3] = Math.round(alpha * 255);
    }
  }

  return pixels;
}

function mix(from: number, to: number, share: number): number {
  return Math.round(from + (to - from) * share);
}

function encodePng(width: number, height: number, rgba: Uint8Array): Uint8Array {
  const stride = width * 4;
  const raw = new Uint8Array((stride + 1) * height);
  for (let y = 0; y < height; y += 1) {
    raw[y * (stride + 1)] = 0;
    raw.set(rgba.subarray(y * stride, (y + 1) * stride), y * (stride + 1) + 1);
  }

  const header = new Uint8Array(13);
  const headerView = new DataView(header.buffer);
  headerView.setUint32(0, width);
  headerView.setUint32(4, height);
  header[8] = 8;
  header[9] = 6;

  return concat([
    Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", new Uint8Array(deflateSync(raw, { level: 9 }))),
    chunk("IEND", new Uint8Array(0)),
  ]);
}

function chunk(type: string, data: Uint8Array): Uint8Array {
  const typeBytes = new Uint8Array(type.length);
  for (let index = 0; index < type.length; index += 1) {
    typeBytes[index] = type.charCodeAt(index);
  }
  const body = concat([typeBytes, data]);
  const framed = new Uint8Array(body.length + 8);
  const view = new DataView(framed.buffer);
  view.setUint32(0, data.length);
  framed.set(body, 4);
  view.setUint32(framed.length - 4, crc32(body));
  return framed;
}

const CRC_TABLE = buildCrcTable();

function buildCrcTable(): Uint32Array {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = (value & 1) === 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    }
    table[index] = value >>> 0;
  }
  return table;
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    const index = (crc ^ byte) & 0xff;
    const entry = CRC_TABLE[index];
    if (entry === undefined) {
      throw new RangeError(`CRC table is missing entry ${String(index)}.`);
    }
    crc = entry ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function concat(parts: readonly Uint8Array[]): Uint8Array {
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const part of parts) {
    merged.set(part, offset);
    offset += part.length;
  }
  return merged;
}
