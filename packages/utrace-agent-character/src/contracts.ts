export const CHARACTER_STATES = [
  "idle",
  "investigating",
  "listening",
  "thinking",
  "speaking",
  "acknowledgement",
  "concern",
  "building",
  "completion",
  "ship",
  "reconnecting",
  "paused",
  "sleep",
  "pointer",
] as const;

export type CharacterState = (typeof CHARACTER_STATES)[number];

export type CharacterProjection = Readonly<{
  revision: number;
  state: CharacterState;
  gazeX: number;
  gazeY: number;
  speechEnergy: number;
  motionScale: number;
}>;

declare const acceptedProjection: unique symbol;
export type AcceptedCharacterProjection = CharacterProjection & {
  readonly [acceptedProjection]: true;
};

export type CharacterProjectionDecision =
  | Readonly<{ kind: "applied"; projection: AcceptedCharacterProjection }>
  | Readonly<{ kind: "duplicate"; projection: AcceptedCharacterProjection }>
  | Readonly<{ kind: "stale"; projection: AcceptedCharacterProjection }>;

export type CharacterProjectionErrorCode =
  | "invalid_projection"
  | "conflicting_revision";

export class CharacterProjectionError extends Error {
  readonly code: CharacterProjectionErrorCode;

  constructor(code: CharacterProjectionErrorCode, message: string) {
    super(message);
    this.name = "CharacterProjectionError";
    this.code = code;
  }
}

export function acceptCharacterProjection(
  previous: AcceptedCharacterProjection | null,
  candidate: unknown,
): CharacterProjectionDecision {
  const projection = parseCharacterProjection(candidate);

  if (previous === null) {
    return { kind: "applied", projection: acceptProjection(projection) };
  }

  if (projection.revision < previous.revision) {
    return { kind: "stale", projection: previous };
  }

  if (projection.revision === previous.revision) {
    if (isSameProjection(previous, projection)) {
      return { kind: "duplicate", projection: previous };
    }

    throw new CharacterProjectionError(
      "conflicting_revision",
      `Character projection revision ${projection.revision} conflicts with the accepted projection.`,
    );
  }

  return { kind: "applied", projection: acceptProjection(projection) };
}

function parseCharacterProjection(candidate: unknown): CharacterProjection {
  if (candidate === null || typeof candidate !== "object") {
    throwInvalidProjection("Character projection must be a non-null object.");
  }

  const revision = readProjectionProperty(candidate, "revision");
  const state = readProjectionProperty(candidate, "state");
  const gazeX = readProjectionProperty(candidate, "gazeX");
  const gazeY = readProjectionProperty(candidate, "gazeY");
  const speechEnergy = readProjectionProperty(candidate, "speechEnergy");
  const motionScale = readProjectionProperty(candidate, "motionScale");

  if (typeof revision !== "number" || !Number.isSafeInteger(revision) || revision < 0) {
    throwInvalidProjection("Character projection revision must be a non-negative safe integer.");
  }
  if (!isCharacterState(state)) {
    throwInvalidProjection("Character projection state must be an exact semantic state.");
  }
  validateRange("gazeX", gazeX, -1, 1);
  validateRange("gazeY", gazeY, -1, 1);
  validateRange("speechEnergy", speechEnergy, 0, 1);
  validateRange("motionScale", motionScale, 0, 1);

  return { revision, state, gazeX, gazeY, speechEnergy, motionScale };
}

function throwInvalidProjection(message: string): never {
  throw new CharacterProjectionError("invalid_projection", message);
}

function readProjectionProperty(candidate: object, property: string): unknown {
  try {
    return Reflect.get(candidate, property);
  } catch {
    throwInvalidProjection(`Character projection ${property} could not be read.`);
  }
}

function isCharacterState(value: unknown): value is CharacterState {
  return typeof value === "string" && CHARACTER_STATES.some((state) => state === value);
}

function validateRange(name: string, value: unknown, minimum: number, maximum: number): asserts value is number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) {
    throwInvalidProjection(
      `Character projection ${name} must be a finite number between ${minimum} and ${maximum}.`,
    );
  }
}

function acceptProjection(projection: CharacterProjection): AcceptedCharacterProjection {
  return Object.freeze({ ...projection }) as AcceptedCharacterProjection;
}

function isSameProjection(
  first: CharacterProjection,
  second: CharacterProjection,
): boolean {
  return (
    first.revision === second.revision &&
    first.state === second.state &&
    first.gazeX === second.gazeX &&
    first.gazeY === second.gazeY &&
    first.speechEnergy === second.speechEnergy &&
    first.motionScale === second.motionScale
  );
}
