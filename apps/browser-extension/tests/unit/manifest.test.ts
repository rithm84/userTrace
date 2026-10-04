/**
 * The shipped manifest is part of the contract: the permission set, the
 * externally connectable origins, the CSP, and the minimum Chrome version are
 * all fixed by the browser extension contract.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { parseBuildOrigins } from "../../src/build/buildOrigins";
import {
  FORBIDDEN_PERMISSIONS,
  MINIMUM_CHROME_VERSION,
  OPTIONAL_HOST_PERMISSIONS,
  REQUIRED_PERMISSIONS,
  createManifest,
  extensionIdForKey,
} from "../../src/build/manifest";

const CONFIG = resolve(import.meta.dirname, "../../config/build-origins.json");
const origins = parseBuildOrigins(JSON.parse(readFileSync(CONFIG, "utf8")));
const manifest = createManifest(origins, "0.1.0");

describe("the shipped manifest", () => {
  it("pins the extension id the session tab and account link are configured with", () => {
    expect(manifest.key).toBe(origins.manifestKey);
    expect(extensionIdForKey(manifest.key)).toBe("npkfhlphllbgdmicnplimbkcjenffbib");
  });

  it("requests exactly storage, scripting, identity, and alarms", () => {
    expect(manifest.permissions).toStrictEqual([...REQUIRED_PERMISSIONS]);
  });

  it("requests no tab, navigation, request, cookie, debugger, script, capture, or offscreen access", () => {
    const serialised = JSON.stringify(manifest);
    for (const permission of FORBIDDEN_PERMISSIONS) {
      expect(serialised).not.toContain(`"${permission}"`);
    }
  });

  it("asks for both host permissions together and only as optional", () => {
    expect(manifest.optional_host_permissions).toStrictEqual([...OPTIONAL_HOST_PERMISSIONS]);
    expect(manifest.host_permissions).toBeUndefined();
  });

  it("declares no content scripts, so an unbound tab is never touched", () => {
    expect(JSON.stringify(manifest)).not.toContain("content_scripts");
  });

  it("restricts externally connectable messaging to the uTrace session-tab origins", () => {
    expect(manifest.externally_connectable.matches).toStrictEqual([
      "https://utrace.dev/*",
      "http://localhost:3100/*",
    ]);
  });

  it("permits packaged WebAssembly without unsafe-eval, inline script, or remote script", () => {
    const csp = manifest.content_security_policy.extension_pages;
    expect(csp).toContain("script-src 'self' 'wasm-unsafe-eval'");
    expect(csp).not.toContain("'unsafe-eval'");
    expect(csp).not.toContain("'unsafe-inline'");
    expect(csp).toContain("object-src 'none'");
  });

  it("limits network access to the uTrace origins this build targets", () => {
    expect(manifest.content_security_policy.extension_pages).toContain(
      "connect-src 'self' https://api.utrace.dev wss://api.utrace.dev https://utrace.dev",
    );
  });

  it("supports Chrome 137 and newer", () => {
    expect(manifest.minimum_chrome_version).toBe(MINIMUM_CHROME_VERSION);
    expect(manifest.background.type).toBe("module");
  });
});
