// Render every diagram source in pele/ to its paired SVG in svg/.
//
// Each source is Mermaid text that the pinned `pele` package renders. The export
// embeds pele/_theme.css, which colors roles (`:::runtime`) and edge kinds
// (`async_1@-.->`) for light and dark readers. Diagrams carry no key of their own:
// every run writes the one key of all roles to svg/_key.svg, which the docs show
// in a collapsed block under every embed. Files whose basename starts with "_" are
// shared files, not diagrams. Output is deterministic.
//
// Usage: npm run diagrams              render all
//        npm run diagrams -- NAME...   render only pele/NAME.mmd
// Either way the key is rendered too.

import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PeleError, render } from 'pele';

const here = dirname(fileURLToPath(import.meta.url));
const sourceDir = join(here, 'pele');
const svgDir = join(here, 'svg');
const theme = readFileSync(join(sourceDir, '_theme.css'), 'utf8');

const ROLES = {
  people: 'People · customer surfaces',
  runtime: 'uTrace control plane',
  reasoning: 'Reasoning models',
  observation: 'SDKs · previews',
  external: 'External · blocked',
  outcome: 'Validated · durable',
  async: 'Async path',
  gate: 'Gates · checks',
  neutral: 'Neutral · closed',
  client: 'Client product',
};
const OPTIONS = {
  padding: 24,
  autoDirection: false,
  config: { flowchart: { wrappingWidth: 360 } },
};
const KEY = '_key';
const KEY_TITLE = 'Diagram key · node roles';
const KEY_COLUMNS = 5;
const WIDE_WARNING = 1400;
const NESTED_STYLE_PREFIX = 'max-width:100%;height:auto;';

// A role the theme does not define would render uncolored, so it fails the render.
function checkRoles(source) {
  const named = new Set();
  for (const match of source.matchAll(/:::([A-Za-z_]\w*)/g)) named.add(match[1]);
  for (const match of source.matchAll(/^\s*class\s+[\w-]+(?:\s*,\s*[\w-]+)*\s+([A-Za-z_]\w*)\s*$/gm)) named.add(match[1]);
  const unknown = [...named].filter((role) => !(role in ROLES));
  if (unknown.length > 0) {
    throw new Error(`unknown role class ${unknown.join(', ')}; use one of ${Object.keys(ROLES).join(', ')}`);
  }
}

function title(source, name) {
  const match = source.match(/^---\n[\s\S]*?^title:\s*(.+)$[\s\S]*?^---$/m);
  return match ? match[1].trim().replace(/^["']|["']$/g, '') : name;
}

// The frontmatter title names the SVG for screen readers but is not drawn: the
// prose around each embed already introduces the diagram.
function withoutTitle(source) {
  const match = source.match(/^---\n([\s\S]*?)\n---\n/);
  if (!match) return source;
  const kept = match[1].split('\n').filter((line) => !/^title:\s/.test(line));
  const body = source.slice(match[0].length);
  return kept.some((line) => line.trim() !== '') ? `---\n${kept.join('\n')}\n---\n${body}` : body;
}

function keySource() {
  const roles = Object.keys(ROLES);
  return [
    'block-beta',
    `  columns ${KEY_COLUMNS}`,
    ...roles.map((role) => `  k_${role}["${ROLES[role]}"]`),
    ...roles.map((role) => `  classDef ${role} stroke-width:1px`),
    ...roles.map((role) => `  class k_${role} ${role}`),
  ].join('\n');
}

function nest(svg, x, y) {
  const open = '<svg xmlns="http://www.w3.org/2000/svg" ';
  if (!svg.startsWith(open) || !svg.includes(`style="${NESTED_STYLE_PREFIX}`)) {
    throw new Error('pele changed its SVG root element; update nest() in render.mjs');
  }
  return svg
    .replace(open, `<svg x="${round(x)}" y="${round(y)}" `)
    .replace(`style="${NESTED_STYLE_PREFIX}`, 'style="');
}

function round(value) {
  return Math.round(value * 100) / 100;
}

// The standalone export: the shared theme, a background, and the nested renders.
function documentSvg(title, width, height, nested) {
  const label = title.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" class="pele-doc" viewBox="0 0 ${round(width)} ${round(height)}" width="${round(width)}" height="${round(height)}" style="max-width:100%;height:auto" role="img" aria-label="${label}">`,
    `<title>${label}</title>`,
    `<style><![CDATA[\n${theme}]]></style>`,
    `<rect class="pele-doc-bg" width="${round(width)}" height="${round(height)}" rx="8"/>`,
    ...nested,
    '</svg>\n',
  ].join('');
}

function renderDiagram(name) {
  const source = readFileSync(join(sourceDir, `${name}.mmd`), 'utf8');
  checkRoles(source);
  const { svg, width, height } = render(withoutTitle(source), { idPrefix: name, ...OPTIONS });
  writeFileSync(join(svgDir, `${name}.svg`), documentSvg(title(source, name), width, height, [nest(svg, 0, 0)]));
  if (width > WIDE_WARNING) {
    console.warn(`warning: ${name} is ${Math.round(width)} px wide; docs scale it down past ${WIDE_WARNING} px`);
  }
  return { width, height };
}

function renderKey() {
  const { svg, width, height } = render(keySource(), { idPrefix: 'diagram-key', padding: 8 });
  writeFileSync(join(svgDir, `${KEY}.svg`), documentSvg(KEY_TITLE, width, height, [nest(svg, 0, 0)]));
  return { width, height };
}

const requested = process.argv.slice(2);
const names = requested.length > 0
  ? requested
  : readdirSync(sourceDir)
      .filter((file) => file.endsWith('.mmd') && !file.startsWith('_'))
      .map((file) => file.slice(0, -'.mmd'.length))
      .sort();

let failed = false;
const jobs = [[KEY, renderKey], ...names.map((name) => [name, () => renderDiagram(name)])];
for (const [name, renderOne] of jobs) {
  try {
    const { width, height } = renderOne();
    console.log(`${name}: ${Math.round(width)} x ${Math.round(height)}`);
  } catch (error) {
    failed = true;
    if (error instanceof PeleError) {
      console.error(`${name}.mmd:${error.line ?? '?'}:${error.column ?? '?'} ${error.code}: ${error.message}`);
      if (error.snippet) console.error(error.snippet);
    } else {
      console.error(`${name}: ${error.message}`);
    }
  }
}
if (failed) process.exit(1);
