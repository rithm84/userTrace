/**
 * Readiness state machine, failure vocabulary, and hook installation ownership.
 */

import { describe, expect, it } from "vitest";
import { ReadinessController, type ReadinessComponents } from "../../src/runtime/readiness.js";
import { HookInstaller, HOOK_NAMES, assertUnowned, markOwner, readOwner } from "../../src/hooks/install.js";
import { UTraceRuntimeError } from "../../src/core/failure.js";
import { clampLimit, MAX_BATCH_BYTES, MAX_OBSERVATION_BYTES } from "../../src/core/limits.js";

const allComponents: readonly (keyof ReadinessComponents)[] = [
  "attestation_current",
  "credential_current",
  "protocol_compatible",
  "schema_compatible",
  "single_top_coordinator",
  "required_frames_connected",
  "required_workers_connected",
  "hooks_installed_once",
  "baseline_accepted",
  "ingestion_acknowledging",
  "trace_probe_passed",
  "registry_consistent",
];

const ready = (): ReadinessController => {
  const controller = new ReadinessController();
  for (const component of allComponents) controller.setComponent(component, true);
  return controller;
};

describe("readiness state machine", () => {
  it("starts dormant and reaches ready only through activating", () => {
    const controller = ready();
    expect(controller.state).toBe("dormant");
    controller.transition("activating");
    controller.transition("ready");
    expect(controller.state).toBe("ready");
  });

  it("refuses ready while any component is missing", () => {
    const controller = ready();
    controller.setComponent("trace_probe_passed", false);
    controller.transition("activating");
    expect(() => { controller.transition("ready"); }).toThrow(/ready requires every readiness component/);
  });

  it("refuses an illegal transition", () => {
    const controller = ready();
    expect(() => { controller.transition("ready"); }).toThrow(/illegal runtime readiness transition/);
  });

  it("resumes only through a fresh activation", () => {
    const controller = ready();
    controller.transition("activating");
    controller.transition("ready");
    controller.transition("paused");
    expect(() => { controller.transition("ready"); }).toThrow(/illegal runtime readiness transition/);
    controller.transition("activating");
    controller.transition("ready");
    expect(controller.state).toBe("ready");
  });

  it("withdraws every component on a typed failure and stays terminal", () => {
    const controller = ready();
    controller.transition("activating");
    controller.transition("ready");
    controller.fail("acknowledgement_integrity_failure", "acknowledged 9 beyond produced 4");
    expect(controller.state).toBe("failed");
    expect(controller.failureCode).toBe("acknowledgement_integrity_failure");
    expect(controller.failureDetail).toContain("beyond produced");
    expect(Object.values(controller.components).some(Boolean)).toBe(false);
    expect(controller.combinedReady).toBe(false);
    controller.transition("terminated");
    expect(controller.state).toBe("terminated");
  });

  it("notifies listeners of each transition", () => {
    const controller = ready();
    const seen: string[] = [];
    controller.onChange((state) => seen.push(state));
    controller.transition("activating");
    controller.transition("ready");
    controller.transition("paused");
    expect(seen).toEqual(["activating", "ready", "paused"]);
  });
});

describe("hook ownership", () => {
  it("names the six capture hooks readiness proves", () => {
    expect([...HOOK_NAMES]).toEqual(["fetch", "xhr", "history", "errors", "interactions", "mutations"]);
  });

  it("installs each hook exactly once and reports the proof", () => {
    const installer = new HookInstaller("runtime-1");
    expect(installer.allInstalled()).toBe(false);
    for (const name of HOOK_NAMES) installer.install(name, () => (): void => undefined);
    expect(installer.allInstalled()).toBe(true);
    expect(installer.status()).toEqual({
      fetch: true,
      xhr: true,
      history: true,
      errors: true,
      interactions: true,
      mutations: true,
    });
  });

  it("treats a second installation of the same hook as a duplicate failure", () => {
    const installer = new HookInstaller("runtime-1");
    installer.install("fetch", () => (): void => undefined);
    expect(() => { installer.install("fetch", () => (): void => undefined); }).toThrow(UTraceRuntimeError);
    expect(() => { installer.install("fetch", () => (): void => undefined); }).toThrow(/duplicate_runtime_or_hook/);
  });

  it("detects a patched object another bundle copy already owns", () => {
    const patched = (): void => undefined;
    expect(readOwner(patched)).toBeNull();
    markOwner(patched, "runtime-1");
    expect(readOwner(patched)).toBe("runtime-1");
    expect(() => { assertUnowned("fetch", patched, "runtime-2"); }).toThrow(/already installed in this document/);
  });

  it("uninstalls everything it installed", () => {
    const installer = new HookInstaller("runtime-1");
    let removed = 0;
    for (const name of HOOK_NAMES) {
      installer.install(name, () => () => {
        removed += 1;
      });
    }
    installer.uninstallAll();
    expect(removed).toBe(HOOK_NAMES.length);
    expect(installer.allInstalled()).toBe(false);
  });
});

describe("platform ceilings", () => {
  it("lets a negotiated limit tighten but never raise the ceiling", () => {
    expect(clampLimit(64 * 1024, MAX_OBSERVATION_BYTES)).toBe(64 * 1024);
    expect(clampLimit(MAX_OBSERVATION_BYTES * 4, MAX_OBSERVATION_BYTES)).toBe(MAX_OBSERVATION_BYTES);
    expect(clampLimit(0, MAX_BATCH_BYTES)).toBe(MAX_BATCH_BYTES);
    expect(MAX_OBSERVATION_BYTES).toBe(256 * 1024);
    expect(MAX_BATCH_BYTES).toBe(1024 * 1024);
  });
});
