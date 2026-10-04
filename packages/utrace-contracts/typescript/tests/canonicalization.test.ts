/**
 * The digest has to be reproducible across both bindings and the standard.
 *
 * `fixtures/_digest/vectors.json` carries hand-written RFC 8785 canonical forms
 * and their SHA-256 digests. The Python suite reads the same file, so passing
 * here and there is the cross-language proof.
 */

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { canonicalize, payloadDigest } from "../src/canonical.js";
import { parseEvent, verifyDigest } from "../src/envelope/index.js";
import { DigestMismatchError } from "../src/errors.js";
import type { CanonicalizableValue, JsonObject } from "../src/json.js";
import { FIXTURES_ROOT } from "../src/locations.js";
import { discoverFixtures, loadInstance } from "./discovery.js";

interface Vector {
  readonly name: string;
  readonly value: CanonicalizableValue;
  readonly canonical: string;
  readonly digest: string;
}

interface VectorDocument {
  readonly algorithm: string;
  readonly canonicalization: string;
  readonly vectors: readonly Vector[];
}

const document = JSON.parse(
  readFileSync(join(FIXTURES_ROOT, "_digest", "vectors.json"), "utf8"),
) as VectorDocument;

describe("canonicalization vectors", () => {
  it("declares the envelope algorithms", () => {
    expect(document.algorithm).toBe("sha256");
    expect(document.canonicalization).toBe("jcs");
    expect(document.vectors.length).toBeGreaterThanOrEqual(8);
  });

  it.each(document.vectors.map((vector) => [vector.name, vector] as const))(
    "%s canonicalizes to the hand-written RFC 8785 form",
    (_name, vector) => {
      expect(canonicalize(vector.value)).toBe(vector.canonical);
    },
  );

  it.each(document.vectors.map((vector) => [vector.name, vector] as const))(
    "%s digests to the recorded value",
    (_name, vector) => {
      expect(createHash("sha256").update(vector.canonical, "utf8").digest("hex")).toBe(
        vector.digest,
      );
      expect(payloadDigest(vector.value)).toBe(vector.digest);
    },
  );
});

describe("envelope digests", () => {
  const envelopeContracts = new Set(["envelope.v1", "event.v1", "command.v1", "result.v1"]);
  const envelopes = discoverFixtures().filter(
    (fixture) =>
      fixture.expectValid && fixture.area === "envelope" && envelopeContracts.has(fixture.contract),
  );

  it("has envelope-family fixtures to check", () => {
    expect(envelopes.length).toBeGreaterThan(0);
  });

  it.each(envelopes.map((fixture) => [fixture.label, fixture] as const))(
    "%s carries a digest its payload actually produces",
    (_label, fixture) => {
      const instance = loadInstance(fixture) as {
        payload: JsonObject;
        digest: { value: string };
      };
      expect(payloadDigest(instance.payload)).toBe(instance.digest.value);
    },
  );

  it("rejects a tampered payload instead of accepting it", () => {
    const fixture = discoverFixtures().find(
      (candidate) => candidate.contract === "event.v1" && candidate.case === "minimal",
    );
    if (fixture === undefined) {
      throw new Error("expected the event.v1__minimal fixture to exist");
    }
    const message = parseEvent(loadInstance(fixture));
    verifyDigest(message);

    const tampered = {
      ...message,
      payload: { ...message.payload, summary: "something else" },
    };
    expect(() => verifyDigest(tampered)).toThrow(DigestMismatchError);
  });
});
