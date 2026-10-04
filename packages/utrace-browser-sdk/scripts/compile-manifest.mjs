#!/usr/bin/env node
/**
 * Build-time annotation manifest compiler.
 *
 * Reads a client's annotation declarations, scans the client's source for
 * `data-utrace-*` attributes, and emits the manifest payload that
 * `dev-sign-manifest.mjs` signs. Compilation fails closed: an unknown attribute
 * name, an attribute that names an undeclared definition, or a safe-value rule
 * that tries to grant a class the platform reserves stops the build. Nothing is
 * inferred from the source; the declarations are the only source of truth.
 *
 * Usage:
 *   node scripts/compile-manifest.mjs \
 *     --declarations utrace.annotations.json \
 *     --source src \
 *     --version 2026.09.20-1 \
 *     --release shopalytics-main-8f21 \
 *     --out build/utrace-manifest.json
 */

import { readFileSync, writeFileSync, readdirSync, statSync, mkdirSync } from "node:fs";
import { dirname, extname, join } from "node:path";

const KNOWN_ATTRIBUTES = new Set([
  "data-utrace-surface",
  "data-utrace-entity",
  "data-utrace-target",
  "data-utrace-visual-target",
  "data-utrace-sensitive",
  "data-utrace-safe-value",
  "data-utrace-milestone",
  "data-utrace-severity",
  "data-utrace-priority",
]);

const SCANNED_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".html", ".vue", ".svelte"]);
const ATTRIBUTE_PATTERN = /data-utrace-[a-z-]+\s*=\s*["'`]([^"'`]*)["'`]/g;
const BARE_ATTRIBUTE_PATTERN = /(data-utrace-[a-z-]+)/g;

const fail = (message) => {
  process.stderr.write(`compile-manifest: ${message}\n`);
  process.exit(1);
};

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  const key = process.argv[index];
  const value = process.argv[index + 1];
  if (typeof key !== "string" || !key.startsWith("--") || value === undefined) {
    fail(`expected --flag value pairs, received ${String(key)}`);
  }
  args.set(key.slice(2), value);
}

for (const required of ["declarations", "source", "version", "release", "out"]) {
  if (!args.has(required)) fail(`--${required} is required`);
}

const walk = (root) => {
  const files = [];
  const visit = (path) => {
    const stats = statSync(path);
    if (stats.isDirectory()) {
      if (path.includes("node_modules") || path.includes("/.")) return;
      for (const entry of readdirSync(path)) visit(join(path, entry));
      return;
    }
    if (SCANNED_EXTENSIONS.has(extname(path))) files.push(path);
  };
  visit(root);
  return files;
};

const declarations = JSON.parse(readFileSync(args.get("declarations"), "utf8"));

const ids = {
  "data-utrace-surface": new Set((declarations.surfaces ?? []).map((entry) => entry.surface_id)),
  "data-utrace-entity": new Set((declarations.entities ?? []).map((entry) => entry.entity_definition_id)),
  "data-utrace-target": new Set((declarations.targets ?? []).map((entry) => entry.definition_id)),
  "data-utrace-visual-target": new Set((declarations.visual_targets ?? []).map((entry) => entry.definition_id)),
  "data-utrace-safe-value": new Set((declarations.redaction?.safe_value_rules ?? []).map((entry) => entry.rule_id)),
  "data-utrace-milestone": new Set((declarations.milestones ?? []).map((entry) => entry.milestone_id)),
  "data-utrace-severity": new Set(declarations.severities ?? []),
  "data-utrace-priority": new Set(declarations.priorities ?? []),
};

const problems = [];
let usages = 0;

for (const file of walk(args.get("source"))) {
  const text = readFileSync(file, "utf8");
  for (const match of text.matchAll(BARE_ATTRIBUTE_PATTERN)) {
    const name = match[1];
    if (!KNOWN_ATTRIBUTES.has(name)) problems.push(`${file}: unknown annotation attribute ${name}`);
  }
  for (const match of text.matchAll(ATTRIBUTE_PATTERN)) {
    usages += 1;
    const name = match[0].slice(0, match[0].indexOf("=")).trim();
    const value = match[1] ?? "";
    if (!KNOWN_ATTRIBUTES.has(name)) continue;
    if (name === "data-utrace-sensitive") {
      if (value !== "" && value !== "true") problems.push(`${file}: ${name} accepts only "" or "true"`);
      continue;
    }
    const declared = ids[name];
    if (declared === undefined) continue;
    const id = name === "data-utrace-entity" ? value.slice(0, value.indexOf(":")) : value;
    // A value the compiler cannot resolve statically (an interpolated binding)
    // is checked by the runtime instead, which fails that annotation closed.
    if (id.includes("${") || id.includes("{")) continue;
    if (!declared.has(id)) problems.push(`${file}: ${name}="${value}" names an undeclared definition`);
  }
}

for (const rule of declarations.redaction?.safe_value_rules ?? []) {
  if (!["bounded_safe_value", "structural_operational_safe", "pseudonymized"].includes(rule.output_class)) {
    problems.push(`safe-value rule ${rule.rule_id} cannot grant output class ${rule.output_class}`);
  }
  if (typeof rule.max_length !== "number" || rule.max_length < 1 || rule.max_length > 120) {
    problems.push(`safe-value rule ${rule.rule_id} must bound max_length to 1-120`);
  }
}

if (problems.length > 0) {
  for (const problem of problems) process.stderr.write(`compile-manifest: ${problem}\n`);
  fail(`${problems.length} annotation problem(s); the manifest was not written`);
}

const manifest = {
  manifest_version: args.get("version"),
  client_release_id: args.get("release"),
  surfaces: declarations.surfaces ?? [],
  entities: declarations.entities ?? [],
  targets: declarations.targets ?? [],
  visual_targets: declarations.visual_targets ?? [],
  milestones: declarations.milestones ?? [],
  severities: declarations.severities ?? [],
  priorities: declarations.priorities ?? [],
  route_templates: declarations.route_templates ?? [],
  approved_trace_origins: declarations.approved_trace_origins ?? [],
  redaction: declarations.redaction,
};

mkdirSync(dirname(args.get("out")), { recursive: true });
writeFileSync(args.get("out"), `${JSON.stringify(manifest, null, 2)}\n`);
process.stdout.write(
  `compile-manifest: wrote ${args.get("out")} from ${String(usages)} annotation usage(s)\n`,
);
