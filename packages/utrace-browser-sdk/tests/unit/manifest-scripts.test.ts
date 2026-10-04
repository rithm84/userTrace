/**
 * The build-time manifest toolchain.
 *
 * The compiler fails the build closed on an unknown annotation, the development
 * signer produces the envelope the activation response carries, and the
 * verifier repeats the runtime's checks: signature, schema, and digest.
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { FIXTURE_PATH } from "./helpers/manifest.js";

const here = dirname(fileURLToPath(import.meta.url));
const SCRIPTS = resolve(here, "../../scripts");

const run = (script: string, args: readonly string[]): string =>
  execFileSync(process.execPath, [join(SCRIPTS, script), ...args], { encoding: "utf8" });

const workspace = (): string => mkdtempSync(join(tmpdir(), "utrace-manifest-"));

const compile = (dir: string, source: string): string => {
  const out = join(dir, "manifest.json");
  run("compile-manifest.mjs", [
    "--declarations",
    FIXTURE_PATH,
    "--source",
    source,
    "--version",
    "test-manifest-1",
    "--release",
    "shopalytics-main-test",
    "--out",
    out,
  ]);
  return out;
};

describe("compile-manifest", () => {
  it("compiles a manifest from declarations plus declared source usage", () => {
    const dir = workspace();
    writeFileSync(
      join(dir, "page.html"),
      '<button data-utrace-target="saved_view_save_button" data-utrace-safe-value="safe.button_label">Save</button>',
    );
    const manifest = JSON.parse(readFileSync(compile(dir, dir), "utf8")) as Record<string, unknown>;
    expect(manifest["manifest_version"]).toBe("test-manifest-1");
    expect(manifest["client_release_id"]).toBe("shopalytics-main-test");
    expect((manifest["targets"] as { definition_id: string }[])[0]?.definition_id).toBe("saved_view_save_button");
  });

  it("fails the build on an unknown annotation attribute", () => {
    const dir = workspace();
    writeFileSync(join(dir, "page.html"), '<button data-utrace-capture="all">Save</button>');
    expect(() => compile(dir, dir)).toThrow();
  });

  it("fails the build when an attribute names an undeclared definition", () => {
    const dir = workspace();
    writeFileSync(join(dir, "page.html"), '<button data-utrace-target="not_declared">Save</button>');
    expect(() => compile(dir, dir)).toThrow();
  });
});

describe("dev signer and verifier", () => {
  it("signs a compiled manifest and verifies signature, schema, and digest", () => {
    const dir = workspace();
    writeFileSync(join(dir, "page.html"), '<button data-utrace-target="saved_view_save_button">Save</button>');
    const manifest = compile(dir, dir);
    const key = join(dir, "dev-signing-key.json");
    run("dev-generate-key.mjs", ["--key-id", "dev-local", "--out", key]);
    const signed = join(dir, "manifest.signed.json");
    const signOutput = run("dev-sign-manifest.mjs", ["--manifest", manifest, "--key", key, "--out", signed]);
    expect(signOutput).toContain("annotation_manifest_digest sha256:");

    const envelope = JSON.parse(readFileSync(signed, "utf8")) as Record<string, string>;
    expect(envelope["algorithm"]).toBe("ed25519");
    expect(envelope["key_id"]).toBe("dev-local");

    const verified = run("verify-manifest.mjs", ["--signed", signed, "--key", key]);
    expect(verified).toContain("signature and schema are valid");
    expect(verified).toContain(signOutput.split("annotation_manifest_digest ")[1]?.trim() ?? "no digest");
  });

  it("refuses a manifest signed by another key", () => {
    const dir = workspace();
    writeFileSync(join(dir, "page.html"), '<button data-utrace-target="saved_view_save_button">Save</button>');
    const manifest = compile(dir, dir);
    const key = join(dir, "key.json");
    const otherKey = join(dir, "other.json");
    run("dev-generate-key.mjs", ["--key-id", "dev-local", "--out", key]);
    run("dev-generate-key.mjs", ["--key-id", "dev-other", "--out", otherKey]);
    const signed = join(dir, "manifest.signed.json");
    run("dev-sign-manifest.mjs", ["--manifest", manifest, "--key", key, "--out", signed]);
    expect(() => run("verify-manifest.mjs", ["--signed", signed, "--key", otherKey])).toThrow();
  });

  it("never writes a private key into the package", () => {
    const packaged = readFileSync(resolve(here, "../../src/activation/signing-keys.ts"), "utf8");
    expect(packaged).not.toContain("private_key");
    expect(packaged).not.toContain("BEGIN PRIVATE KEY");
    expect(packaged).toContain("public_key_b64url");
  });
});
