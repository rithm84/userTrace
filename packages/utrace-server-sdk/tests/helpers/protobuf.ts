/**
 * A minimal protobuf wire-format reader and writer for the test ingestion edge.
 *
 * The ingestion edge is built by another workstream. These helpers implement
 * enough of `opentelemetry.proto.collector.trace.v1` to decode what the SDK
 * actually put on the wire and to answer with a real OTLP response, so the
 * tests exercise the documented contract rather than a mock.
 */

type WireValue =
  | { readonly kind: 'varint'; readonly value: bigint }
  | { readonly kind: 'fixed64'; readonly value: bigint }
  | { readonly kind: 'fixed32'; readonly value: number }
  | { readonly kind: 'bytes'; readonly value: Buffer };

type Fields = Map<number, WireValue[]>;

function readVarint(buffer: Buffer, offset: number): { value: bigint; next: number } {
  let result = 0n;
  let shift = 0n;
  let index = offset;
  for (;;) {
    const byte = buffer[index];
    if (byte === undefined) {
      throw new Error('truncated varint');
    }
    index += 1;
    result |= BigInt(byte & 0x7f) << shift;
    if ((byte & 0x80) === 0) {
      return { value: result, next: index };
    }
    shift += 7n;
  }
}

/** Decodes one protobuf message into its fields, keyed by field number. */
export function decodeMessage(buffer: Buffer): Fields {
  const fields: Fields = new Map();
  let offset = 0;
  while (offset < buffer.length) {
    const tag = readVarint(buffer, offset);
    offset = tag.next;
    const fieldNumber = Number(tag.value >> 3n);
    const wireType = Number(tag.value & 0x7n);
    let value: WireValue;
    switch (wireType) {
      case 0: {
        const varint = readVarint(buffer, offset);
        offset = varint.next;
        value = { kind: 'varint', value: varint.value };
        break;
      }
      case 1: {
        value = { kind: 'fixed64', value: buffer.readBigUInt64LE(offset) };
        offset += 8;
        break;
      }
      case 2: {
        const length = readVarint(buffer, offset);
        offset = length.next;
        const end = offset + Number(length.value);
        value = { kind: 'bytes', value: buffer.subarray(offset, end) };
        offset = end;
        break;
      }
      case 5: {
        value = { kind: 'fixed32', value: buffer.readUInt32LE(offset) };
        offset += 4;
        break;
      }
      default:
        throw new Error(`unsupported wire type ${String(wireType)}`);
    }
    const existing = fields.get(fieldNumber);
    if (existing === undefined) {
      fields.set(fieldNumber, [value]);
    } else {
      existing.push(value);
    }
  }
  return fields;
}

function bytesField(fields: Fields, fieldNumber: number): Buffer[] {
  return (fields.get(fieldNumber) ?? [])
    .filter((entry) => entry.kind === 'bytes')
    .map((entry) => entry.value);
}

function firstBytes(fields: Fields, fieldNumber: number): Buffer | undefined {
  return bytesField(fields, fieldNumber)[0];
}

function firstVarint(fields: Fields, fieldNumber: number): number | undefined {
  const entry = (fields.get(fieldNumber) ?? [])[0];
  return entry !== undefined && entry.kind === 'varint' ? Number(entry.value) : undefined;
}

function firstFixed64(fields: Fields, fieldNumber: number): bigint | undefined {
  const entry = (fields.get(fieldNumber) ?? [])[0];
  return entry !== undefined && entry.kind === 'fixed64' ? entry.value : undefined;
}

/** A decoded OTLP attribute value. */
export type DecodedAttributeValue = string | boolean | number | readonly string[];

function decodeAnyValue(buffer: Buffer): DecodedAttributeValue | undefined {
  const fields = decodeMessage(buffer);
  const stringValue = firstBytes(fields, 1);
  if (stringValue !== undefined) {
    return stringValue.toString('utf8');
  }
  const boolValue = firstVarint(fields, 2);
  if (boolValue !== undefined) {
    return boolValue === 1;
  }
  const intValue = firstVarint(fields, 3);
  if (intValue !== undefined) {
    return intValue;
  }
  const arrayValue = firstBytes(fields, 5);
  if (arrayValue !== undefined) {
    const arrayFields = decodeMessage(arrayValue);
    return bytesField(arrayFields, 1)
      .map((entry) => decodeAnyValue(entry))
      .filter((entry): entry is string => typeof entry === 'string');
  }
  return undefined;
}

function decodeAttributes(entries: Buffer[]): Record<string, DecodedAttributeValue> {
  const attributes: Record<string, DecodedAttributeValue> = {};
  for (const entry of entries) {
    const fields = decodeMessage(entry);
    const key = firstBytes(fields, 1)?.toString('utf8');
    const valueBytes = firstBytes(fields, 2);
    if (key === undefined || valueBytes === undefined) {
      continue;
    }
    const value = decodeAnyValue(valueBytes);
    if (value !== undefined) {
      attributes[key] = value;
    }
  }
  return attributes;
}

/** One decoded span event. */
export interface DecodedEvent {
  readonly name: string;
  readonly attributes: Record<string, DecodedAttributeValue>;
}

/** One decoded span. */
export interface DecodedSpan {
  readonly traceId: string;
  readonly spanId: string;
  readonly parentSpanId: string;
  readonly name: string;
  readonly kind: number;
  readonly startTimeUnixNano: bigint;
  readonly endTimeUnixNano: bigint;
  readonly attributes: Record<string, DecodedAttributeValue>;
  readonly events: readonly DecodedEvent[];
  readonly statusCode: number;
  readonly statusMessage: string | undefined;
  readonly scopeName: string;
}

/** One decoded resource group. */
export interface DecodedResourceSpans {
  readonly resourceAttributes: Record<string, DecodedAttributeValue>;
  readonly spans: readonly DecodedSpan[];
}

/** Decodes an `ExportTraceServiceRequest`. */
export function decodeTraceRequest(body: Buffer): readonly DecodedResourceSpans[] {
  const request = decodeMessage(body);
  return bytesField(request, 1).map((resourceSpansBytes) => {
    const resourceSpans = decodeMessage(resourceSpansBytes);
    const resourceBytes = firstBytes(resourceSpans, 1);
    const resourceAttributes =
      resourceBytes === undefined
        ? {}
        : decodeAttributes(bytesField(decodeMessage(resourceBytes), 1));
    const spans: DecodedSpan[] = [];
    for (const scopeSpansBytes of bytesField(resourceSpans, 2)) {
      const scopeSpans = decodeMessage(scopeSpansBytes);
      const scopeBytes = firstBytes(scopeSpans, 1);
      const scopeName =
        scopeBytes === undefined
          ? ''
          : (firstBytes(decodeMessage(scopeBytes), 1)?.toString('utf8') ?? '');
      for (const spanBytes of bytesField(scopeSpans, 2)) {
        const span = decodeMessage(spanBytes);
        const statusBytes = firstBytes(span, 15);
        const status = statusBytes === undefined ? undefined : decodeMessage(statusBytes);
        spans.push({
          traceId: (firstBytes(span, 1) ?? Buffer.alloc(0)).toString('hex'),
          spanId: (firstBytes(span, 2) ?? Buffer.alloc(0)).toString('hex'),
          parentSpanId: (firstBytes(span, 4) ?? Buffer.alloc(0)).toString('hex'),
          name: (firstBytes(span, 5) ?? Buffer.alloc(0)).toString('utf8'),
          kind: firstVarint(span, 6) ?? 0,
          startTimeUnixNano: firstFixed64(span, 7) ?? 0n,
          endTimeUnixNano: firstFixed64(span, 8) ?? 0n,
          attributes: decodeAttributes(bytesField(span, 9)),
          events: bytesField(span, 11).map((eventBytes) => {
            const event = decodeMessage(eventBytes);
            return {
              name: (firstBytes(event, 2) ?? Buffer.alloc(0)).toString('utf8'),
              attributes: decodeAttributes(bytesField(event, 3)),
            };
          }),
          statusCode: status === undefined ? 0 : (firstVarint(status, 3) ?? 0),
          statusMessage:
            status === undefined ? undefined : firstBytes(status, 2)?.toString('utf8'),
          scopeName,
        });
      }
    }
    return { resourceAttributes, spans };
  });
}

function writeVarint(value: number): Buffer {
  const bytes: number[] = [];
  let remaining = value;
  do {
    let byte = remaining & 0x7f;
    remaining >>>= 7;
    if (remaining !== 0) {
      byte |= 0x80;
    }
    bytes.push(byte);
  } while (remaining !== 0);
  return Buffer.from(bytes);
}

/**
 * Encodes an `ExportTraceServiceResponse` carrying a partial success.
 *
 * uTrace ingestion never emits one; the tests use it to prove the SDK fails the
 * stream when it receives one anyway.
 */
export function encodePartialSuccessResponse(rejectedSpans: number, message: string): Buffer {
  const messageBytes = Buffer.from(message, 'utf8');
  const partial = Buffer.concat([
    Buffer.from([0x08]),
    writeVarint(rejectedSpans),
    Buffer.from([0x12]),
    writeVarint(messageBytes.byteLength),
    messageBytes,
  ]);
  return Buffer.concat([Buffer.from([0x0a]), writeVarint(partial.byteLength), partial]);
}
