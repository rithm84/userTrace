/**
 * Backend exception classification.
 *
 * An exception belongs to its active span. What survives is a stable error
 * type, a fingerprint, a registered product area, an optional exactly-approved
 * sanitized message, and bounded repository-relative frames. Raw messages,
 * arguments, local values, environment values, and absolute host paths never
 * leave the process.
 */

import { createHash } from 'node:crypto';

import type { RedactionPolicy } from './policy.js';

/** One repository-relative stack frame. */
export interface SafeFrame {
  readonly function_name: string;
  readonly file: string;
  readonly line: number;
  readonly column: number;
}

/** The redacted evidence derived from one exception. */
export interface ErrorEvidence {
  readonly errorType: string;
  readonly fingerprint: string;
  readonly productArea: string;
  readonly frames: readonly string[];
  readonly sanitizedMessage?: string;
}

const FRAME_WITH_NAME = /^\s*at\s+(.+?)\s+\((.+):(\d+):(\d+)\)\s*$/u;
const FRAME_WITHOUT_NAME = /^\s*at\s+(.+):(\d+):(\d+)\s*$/u;

function normalizeFunctionName(raw: string): string {
  // Strip `new `, `async `, and object receivers so the name stays low
  // cardinality and never carries a value from the call site.
  const withoutModifiers = raw.replace(/^(new|async)\s+/u, '');
  const lastDot = withoutModifiers.lastIndexOf('.');
  const name = lastDot === -1 ? withoutModifiers : withoutModifiers.slice(lastDot + 1);
  return /^[A-Za-z_$][A-Za-z0-9_$]*$/u.test(name) ? name : '<anonymous>';
}

function parseFrame(line: string): SafeFrame | undefined {
  const named = FRAME_WITH_NAME.exec(line);
  if (named !== null) {
    const [, functionName, file, lineNumber, column] = named;
    if (functionName === undefined || file === undefined || lineNumber === undefined || column === undefined) {
      return undefined;
    }
    return {
      function_name: normalizeFunctionName(functionName),
      file,
      line: Number.parseInt(lineNumber, 10),
      column: Number.parseInt(column, 10),
    };
  }
  const anonymous = FRAME_WITHOUT_NAME.exec(line);
  if (anonymous !== null) {
    const [, file, lineNumber, column] = anonymous;
    if (file === undefined || lineNumber === undefined || column === undefined) {
      return undefined;
    }
    return {
      function_name: '<anonymous>',
      file,
      line: Number.parseInt(lineNumber, 10),
      column: Number.parseInt(column, 10),
    };
  }
  return undefined;
}

function toRepositoryRelative(file: string, repositoryRoot: string): string | undefined {
  const path = file.startsWith('file://') ? file.slice('file://'.length) : file;
  const root = repositoryRoot.endsWith('/') ? repositoryRoot : `${repositoryRoot}/`;
  return path.startsWith(root) ? path.slice(root.length) : undefined;
}

/**
 * Extracts bounded repository-relative frames from a V8 stack string.
 *
 * Frames outside the release checkout are dropped rather than truncated: an
 * absolute host path is never evidence.
 */
export function safeFrames(
  stack: string,
  repositoryRoot: string,
  maxFrames: number,
): readonly string[] {
  const frames: string[] = [];
  for (const line of stack.split('\n')) {
    if (frames.length >= maxFrames) {
      break;
    }
    const parsed = parseFrame(line);
    if (parsed === undefined) {
      continue;
    }
    const relative = toRepositoryRelative(parsed.file, repositoryRoot);
    if (relative === undefined) {
      continue;
    }
    frames.push(`${relative}:${String(parsed.line)}:${String(parsed.column)}:${parsed.function_name}`);
  }
  return frames;
}

/**
 * Builds error evidence from a raw exception.
 *
 * An error type outside the manifest's approved list is normalized to
 * `unregistered_error` so an unexpected class name cannot become a channel.
 */
export function buildErrorEvidence(
  rawType: string | undefined,
  rawStack: string | undefined,
  policy: RedactionPolicy,
): ErrorEvidence {
  const errorType =
    rawType !== undefined && policy.allowedErrorTypes.has(rawType) ? rawType : 'unregistered_error';
  const frames =
    rawStack === undefined ? [] : safeFrames(rawStack, policy.repositoryRoot, policy.maxStackFrames);
  const fingerprint = createHash('sha256')
    .update(errorType)
    .update('\n')
    .update(frames.join('\n'))
    .digest('hex')
    .slice(0, 32);
  const sanitizedMessage = policy.sanitizedErrorMessages.get(errorType);
  return {
    errorType,
    fingerprint,
    productArea: policy.productArea,
    frames,
    ...(sanitizedMessage === undefined ? {} : { sanitizedMessage }),
  };
}
