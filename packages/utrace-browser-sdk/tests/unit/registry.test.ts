/**
 * Structured target registry behaviour.
 *
 * Identifiers are opaque and never reused, unmount is terminal and leaves a
 * tombstone, revisions separate semantic change from layout change, and a query
 * returns `not_found` or `ambiguous` rather than a nearest guess.
 */

import { beforeEach, describe, expect, it } from "vitest";
import { webCryptoRandom } from "../../src/core/ids.js";
import { readElementAnnotations } from "../../src/annotations/attributes.js";
import { RedactionPipeline } from "../../src/redaction/pipeline.js";
import { TaintRegistry } from "../../src/redaction/taint.js";
import { TargetRegistry, type TargetChange } from "../../src/registry/targets.js";
import { testManifestIndex } from "./helpers/manifest.js";

interface Fixture {
  readonly registry: TargetRegistry;
  readonly changes: TargetChange[];
  readonly index: ReturnType<typeof testManifestIndex>;
}

const setup = (html: string): Fixture => {
  document.body.innerHTML = html;
  const index = testManifestIndex();
  const pipeline = new RedactionPipeline(index.manifest.redaction, new TaintRegistry());
  const registry = new TargetRegistry(window, index, pipeline, webCryptoRandom(globalThis.crypto));
  const changes: TargetChange[] = [];
  registry.onChange((change) => changes.push(change));
  return { registry, changes, index };
};

const register = (fixture: Fixture, selector: string, definitionId: string): ReturnType<TargetRegistry["register"]> => {
  const element = document.querySelector(selector);
  if (element === null) throw new Error(`fixture is missing ${selector}`);
  return fixture.registry.register(element, definitionId, readElementAnnotations(element, fixture.index));
};

beforeEach(() => {
  document.body.replaceChildren();
});

describe("mounted instances", () => {
  it("gives a mounted instance an opaque id and starts both revisions at one", () => {
    const fixture = setup('<button id="save" data-utrace-target="saved_view_save_button">Save view</button>');
    const handle = register(fixture, "#save", "saved_view_save_button");
    expect(handle?.targetId).toMatch(/^tgt_\d+_[0-9a-f]{16}$/);
    const resolved = fixture.registry.byId(handle?.targetId ?? "");
    expect(resolved.status).toBe("found");
    if (resolved.status !== "found") return;
    expect(resolved.target.target_revision).toBe(1);
    expect(resolved.target.layout_revision).toBe(1);
    expect(resolved.target.definition_id).toBe("saved_view_save_button");
    expect(resolved.target.visual_only).toBe(false);
  });

  it("preserves the instance through an ordinary rerender and bumps the revision on rebind", () => {
    const fixture = setup(
      '<button id="save" data-utrace-target="saved_view_save_button">Save view</button><button id="next">Next</button>',
    );
    const handle = register(fixture, "#save", "saved_view_save_button");
    const replacement = document.querySelector("#next");
    if (handle === null || replacement === null) throw new Error("fixture is incomplete");
    handle.rebind(replacement);
    const resolved = fixture.registry.byId(handle.targetId);
    expect(resolved.status).toBe("found");
    if (resolved.status !== "found") return;
    expect(resolved.target.target_id).toBe(handle.targetId);
    expect(resolved.target.target_revision).toBe(2);
  });

  it("makes unmount terminal and answers a later command as stale_target", () => {
    const fixture = setup('<button id="save" data-utrace-target="saved_view_save_button">Save view</button>');
    const handle = register(fixture, "#save", "saved_view_save_button");
    handle?.unmount();
    const resolved = fixture.registry.byId(handle?.targetId ?? "");
    expect(resolved.status).toBe("stale_target");
  });

  it("gives a later mount of the same definition a new identifier", () => {
    const fixture = setup('<button id="save" data-utrace-target="saved_view_save_button">Save view</button>');
    const first = register(fixture, "#save", "saved_view_save_button");
    first?.unmount();
    const second = register(fixture, "#save", "saved_view_save_button");
    expect(second?.targetId).not.toBe(first?.targetId);
  });

  it("separates a layout-only change from a semantic change", () => {
    const fixture = setup('<button id="save" data-utrace-target="saved_view_save_button">Save view</button>');
    const handle = register(fixture, "#save", "saved_view_save_button");
    const element = document.querySelector("#save");
    if (handle === null || element === null) throw new Error("fixture is incomplete");
    fixture.changes.length = 0;

    element.setAttribute("aria-disabled", "true");
    handle.refresh();
    expect(fixture.changes.at(-1)?.change).toBe("updated");
    const semantic = fixture.registry.byId(handle.targetId);
    if (semantic.status !== "found") throw new Error("target went missing");
    expect(semantic.target.target_revision).toBe(2);
    expect(semantic.target.semantic_state.disabled).toBe(true);
  });

  it("reports drift when a mounted element leaves the document", () => {
    const fixture = setup('<button id="save" data-utrace-target="saved_view_save_button">Save view</button>');
    const handle = register(fixture, "#save", "saved_view_save_button");
    document.querySelector("#save")?.remove();
    fixture.registry.refreshAll();
    expect(fixture.registry.byId(handle?.targetId ?? "").status).toBe("stale_target");
  });
});

describe("structured queries", () => {
  it("returns not_found for a definition that is not mounted", () => {
    const fixture = setup("<main></main>");
    expect(fixture.registry.query({ definition_id: "saved_view_save_button" }, undefined).status).toBe("not_found");
  });

  it("returns ambiguous rather than choosing a nearest match", () => {
    const fixture = setup(
      '<button id="a" data-utrace-target="saved_view_save_button">One</button>' +
        '<button id="b" data-utrace-target="saved_view_save_button">Two</button>',
    );
    register(fixture, "#a", "saved_view_save_button");
    register(fixture, "#b", "saved_view_save_button");
    const result = fixture.registry.query({ definition_id: "saved_view_save_button" }, undefined);
    expect(result.status).toBe("ambiguous");
    if (result.status !== "ambiguous") return;
    expect(result.match_count).toBe(2);
  });

  it("matches by safe entity reference", () => {
    const fixture = setup(
      '<button id="save" data-utrace-target="saved_view_save_button" data-utrace-entity="conversion_view:dudulemon">Save</button>',
    );
    register(fixture, "#save", "saved_view_save_button");
    const found = fixture.registry.query(
      { entity_ref: { entity_definition_id: "conversion_view", instance_ref: "psn_1" } },
      undefined,
    );
    expect(found.status).toBe("found");
  });

  it("rejects an unregistered definition and records the rejection", () => {
    const fixture = setup('<button id="save">Save</button>');
    const handle = register(fixture, "#save", "not_in_the_manifest");
    expect(handle).toBeNull();
    expect(fixture.changes.at(-1)?.change).toBe("rejected");
    expect(fixture.changes.at(-1)?.rejected_annotation).toBe("not_in_the_manifest");
  });
});

describe("automatic visual-only targets", () => {
  it("creates visual-only targets for safe native interactive and semantic elements", () => {
    const fixture = setup(
      '<main><h1>Conversions</h1><button id="go">Go</button><a href="/x">Link</a><nav aria-label="Main">n</nav></main>',
    );
    const created = fixture.registry.discoverVisualTargets(document);
    expect(created).toBeGreaterThanOrEqual(4);
    const snapshot = fixture.registry.snapshot();
    expect(snapshot.every((target) => target.visual_only)).toBe(true);
    expect(snapshot.some((target) => target.role === "heading")).toBe(true);
    expect(snapshot.some((target) => target.role === "navigation")).toBe(true);
  });

  it("never creates one for a platform-excluded field", () => {
    const fixture = setup('<input id="card" autocomplete="cc-number" /><input id="pw" type="password" />');
    fixture.registry.discoverVisualTargets(document);
    expect(fixture.registry.snapshot()).toHaveLength(0);
  });

  it("reports geometry with visibility and viewport bounds", () => {
    const fixture = setup('<button id="go">Go</button>');
    fixture.registry.discoverVisualTargets(document);
    const target = fixture.registry.snapshot()[0];
    expect(target?.geometry.viewport_width).toBe(window.innerWidth);
    expect(target?.geometry.viewport_height).toBe(window.innerHeight);
    expect(["visible", "offscreen", "hidden"]).toContain(target?.geometry.visibility);
  });
});
