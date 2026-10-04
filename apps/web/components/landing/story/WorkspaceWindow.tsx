"use client";

import { Check, ChevronDown, Circle } from "lucide-react";
import type { JSX } from "react";

import { SHOPALYTICS } from "./shopalyticsFixture";
import type { Beat } from "./beats";

type Diff = Readonly<{ file: string; hunk: string; lines: readonly (readonly ["+" | "-" | " ", string])[] }>;

const BUILD: readonly Diff[] = [
  { file: "src/views/savedViewsService.ts", hunk: "@@ -42,7 +42,11 @@", lines: [["-", "async save(view: View): Promise<void> {"], ["+", "async save(view: View, name: string): Promise<void> {"], ["+", "  const payload = { name, filters: view.filters }"], ["+", "  await this.api.post('/saved-views', payload)"], [" ", "}"]] },
  { file: "src/state/savedViewsStore.ts", hunk: "@@ -15,6 +15,10 @@", lines: [["-", "current: null as View | null,"], ["+", "list: [] as SavedView[],"], ["+", "current: null as SavedView | null,"], ["+", "restore(view: SavedView) { this.current = view }"]] },
  { file: "src/components/ViewsMenu.vue", hunk: "@@ -88,7 +88,11 @@", lines: [["+", "<input v-model=\"name\" placeholder=\"View name\" />"], ["+", "<button @click=\"save(name)\">Save view</button>"], ["+", "<li v-for=\"v in views\" :key=\"v.id\">"], ["+", "  <button @click=\"restore(v)\">{{ v.name }}</button>"]] },
];
const REPAIR: readonly Diff[] = [
  { file: "src/state/savedViewsStore.ts", hunk: "@@ -31,4 +31,6 @@", lines: [["-", "filters: pick(view.filters, WITHOUT_TOPIC),"], ["+", "filters: pick(view.filters, CRITERIA), // now includes the review topic"], ["+", "topic: view.filters.topic ?? null,"]] },
];

/**
 * uTrace's own window: the Implementation Gate decision, the isolated workspace's diff, the
 * required checks and the immutable Preview Version. Machinery, but bounded and legible.
 */
export function WorkspaceWindow(props: Readonly<{ beat: Beat; progress: number }>): JSX.Element {
  const { beat, progress } = props;
  const gateOnly = beat.id === "gate";
  const stamped = !gateOnly || progress >= 0.3;
  const diffs = beat.id === "repair" ? REPAIR : BUILD;
  const shown = gateOnly ? 0 : beat.id === "repair" ? (progress >= 0.15 ? 1 : 0) : Math.min(diffs.length, Math.floor(progress * 5));
  const checks = gateOnly ? 0 : beat.id === "repair" ? Math.min(4, Math.floor((progress - 0.35) * 10)) : Math.min(4, Math.floor((progress - 0.55) * 12));
  const ready = !gateOnly && progress >= 0.9;
  const version = beat.id === "repair" ? SHOPALYTICS.implementation.repairPreviewVersion : SHOPALYTICS.implementation.previewVersion;
  return (
    <div className="st-workspace" data-testid="story-workspace" data-files={shown} data-checks={Math.max(0, checks)}>
      <aside className="st-gate">
        <h4>Implementation Gate</h4>
        <span className={"st-stamp" + (stamped ? " is-on" : "")} data-testid="story-gate-stamp">
          Approved · live
        </span>
        <small>Considered</small>
        <ul className="st-gate-considered">
          {SHOPALYTICS.gate.considered.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <small>Scope</small>
        <ul>
          {SHOPALYTICS.gate.scope.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
        <small>Not in scope</small>
        <p className="st-gate-excluded">{SHOPALYTICS.gate.excluded.join(" · ")}</p>
      </aside>
      <section className="st-diff">
        <header>
          {gateOnly ? "Waiting for the workspace lease" : `${diffs.length} changed ${diffs.length === 1 ? "file" : "files"} · ${SHOPALYTICS.implementation.branch}`}
        </header>
        {diffs.slice(0, shown).map((diff) => (
          <div key={diff.file} className="st-file">
            <div className="st-file-head">
              {diff.file}
              <ChevronDown size={14} />
            </div>
            <div className="st-hunk">{diff.hunk}</div>
            {diff.lines.map(([kind, text], index) => (
              <div key={index} className={"st-line" + (kind === "+" ? " is-add" : kind === "-" ? " is-del" : "")}>
                <span>{kind}</span>
                {text}
              </div>
            ))}
          </div>
        ))}
        {gateOnly && progress >= 0.6 ? <p className="st-diff-note">Exact files inspected next. No customer data in here, ever.</p> : null}
      </section>
      <aside className="st-checks">
        <h4>Checks</h4>
        {SHOPALYTICS.implementation.checks.map((check, index) => (
          <div key={check} className={"st-check" + (index < checks ? " is-pass" : "")}>
            {index < checks ? <Check size={14} strokeWidth={3} /> : <Circle size={12} />}
            {check}
          </div>
        ))}
        <div className={"st-version" + (ready ? " is-on" : "")} data-testid="story-preview-version">
          <small>Preview Version</small>
          <strong>{version}</strong>
          <span>{ready ? "ready" : "building"}</span>
        </div>
      </aside>
    </div>
  );
}
