import { describe, expect, it } from "vitest";
import {
  CHARACTER_STATES,
  CharacterProjectionError,
  acceptCharacterProjection,
} from "../src/contracts";

const valid = {
  revision: 7,
  state: "speaking",
  gazeX: -1,
  gazeY: 1,
  speechEnergy: 0.65,
  motionScale: 1,
} as const;

describe("acceptCharacterProjection", () => {
  it("accepts every semantic state and numeric boundary", () => {
    for (const state of CHARACTER_STATES) {
      const minimum = acceptCharacterProjection(null, {
        ...valid,
        state,
        gazeX: -1,
        gazeY: -1,
        speechEnergy: 0,
        motionScale: 0,
      });
      const maximum = acceptCharacterProjection(null, {
        ...valid,
        state,
        gazeX: 1,
        gazeY: 1,
        speechEnergy: 1,
        motionScale: 1,
      });

      expect(minimum.kind).toBe("applied");
      expect(maximum.kind).toBe("applied");
    }
  });

  it("ignores stale revisions and treats exact repeats as duplicates", () => {
    const first = acceptCharacterProjection(null, valid);
    if (first.kind !== "applied") throw new Error("initial projection was not applied");
    const stale = acceptCharacterProjection(first.projection, { ...valid, revision: 6 });
    const duplicate = acceptCharacterProjection(first.projection, valid);

    expect(stale.kind).toBe("stale");
    expect(stale.projection).toBe(first.projection);
    expect(duplicate.kind).toBe("duplicate");
    expect(duplicate.projection).toBe(first.projection);
  });

  it("rejects conflicting data for one revision", () => {
    const first = acceptCharacterProjection(null, valid);
    if (first.kind !== "applied") throw new Error("initial projection was not applied");
    expect(() => acceptCharacterProjection(first.projection, { ...valid, gazeX: 0 }))
      .toThrowError(CharacterProjectionError);
  });

  it.each([
    [{ ...valid, state: "unknown" }],
    [{ ...valid, revision: -1 }],
    [{ ...valid, revision: 1.5 }],
    [{ ...valid, gazeX: -1.01 }],
    [{ ...valid, gazeY: Number.NaN }],
    [{ ...valid, speechEnergy: 1.01 }],
    [{ ...valid, motionScale: -0.01 }],
    [{ state: "idle" }],
  ])("rejects invalid projection %o", (candidate) => {
    expect(() => acceptCharacterProjection(null, candidate))
      .toThrowError(CharacterProjectionError);
  });

  it("ignores unrelated fields and freezes the accepted projection", () => {
    const result = acceptCharacterProjection(null, { ...valid, unrelated: "ignored" });
    if (result.kind !== "applied") throw new Error("initial projection was not applied");

    expect(Object.isFrozen(result.projection)).toBe(true);
    expect("unrelated" in result.projection).toBe(false);
  });

  it("reports explicit error codes", () => {
    expect(() => acceptCharacterProjection(null, { state: "idle" })).toThrowError(
      expect.objectContaining({ code: "invalid_projection" }),
    );
    const first = acceptCharacterProjection(null, valid);
    if (first.kind !== "applied") throw new Error("initial projection was not applied");
    expect(() => acceptCharacterProjection(first.projection, { ...valid, gazeY: 0 }))
      .toThrowError(expect.objectContaining({ code: "conflicting_revision" }));
  });

  it.each([
    [
      "throwing getter",
      Object.defineProperty({ ...valid }, "revision", {
        get(): never {
          throw new Error("getter failure");
        },
      }),
    ],
    [
      "throwing Proxy trap",
      new Proxy(valid, {
        get(): never {
          throw new Error("Proxy failure");
        },
      }),
    ],
  ])("normalizes a %s as an invalid projection", (_description, candidate) => {
    expect(() => acceptCharacterProjection(null, candidate)).toThrowError(CharacterProjectionError);
    expect(() => acceptCharacterProjection(null, candidate)).toThrowError(
      expect.objectContaining({ code: "invalid_projection" }),
    );
  });
});
