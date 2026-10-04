/**
 * Accessible role and name.
 *
 * Semantics come from DOM, HTML, ARIA, and the W3C accessible-name computation
 * available to page JavaScript. The runtime never requires the Chrome debugger
 * permission and never claims access to the privileged DevTools accessibility
 * tree. The traversal is bounded; it is not a page text dump.
 */

const IMPLICIT_ROLES: Readonly<Record<string, string>> = {
  a: "link",
  article: "article",
  aside: "complementary",
  button: "button",
  dialog: "dialog",
  fieldset: "group",
  footer: "contentinfo",
  form: "form",
  h1: "heading",
  h2: "heading",
  h3: "heading",
  h4: "heading",
  h5: "heading",
  h6: "heading",
  header: "banner",
  hr: "separator",
  img: "img",
  li: "listitem",
  main: "main",
  nav: "navigation",
  ol: "list",
  option: "option",
  output: "status",
  progress: "progressbar",
  section: "region",
  select: "combobox",
  summary: "button",
  table: "table",
  tbody: "rowgroup",
  td: "cell",
  textarea: "textbox",
  th: "columnheader",
  tr: "row",
  ul: "list",
};

const INPUT_ROLES: Readonly<Record<string, string>> = {
  button: "button",
  checkbox: "checkbox",
  email: "textbox",
  image: "button",
  number: "spinbutton",
  radio: "radio",
  range: "slider",
  reset: "button",
  search: "searchbox",
  submit: "button",
  tel: "textbox",
  text: "textbox",
  url: "textbox",
};

/** Computed role: an explicit ARIA role wins, then the implicit HTML role. */
export const computeRole = (element: Element): string => {
  const explicit = element.getAttribute("role");
  if (explicit !== null && explicit.trim().length > 0) return explicit.trim().split(/\s+/)[0] ?? "generic";
  const tagName = element.tagName.toLowerCase();
  if (tagName === "input") {
    const type = (element.getAttribute("type") ?? "text").toLowerCase();
    return INPUT_ROLES[type] ?? "textbox";
  }
  if (tagName === "a" && !element.hasAttribute("href")) return "generic";
  return IMPLICIT_ROLES[tagName] ?? "generic";
};

const MAX_CONTENT_DEPTH = 2;
const MAX_NAME_SOURCE_LENGTH = 300;

const textFrom = (node: Node, depth: number): string => {
  if (depth > MAX_CONTENT_DEPTH) return "";
  if (node.nodeType === Node.TEXT_NODE) return node.nodeValue ?? "";
  if (!(node instanceof Element)) return "";
  if (node.hasAttribute("aria-hidden")) return "";
  let out = "";
  for (const child of node.childNodes) {
    out += textFrom(child, depth + 1);
    if (out.length > MAX_NAME_SOURCE_LENGTH) break;
  }
  return out;
};

const labelledByText = (element: Element): string | null => {
  const ids = element.getAttribute("aria-labelledby");
  if (ids === null) return null;
  const root = element.getRootNode();
  if (!(root instanceof Document) && !(root instanceof ShadowRoot)) return null;
  let out = "";
  for (const id of ids.split(/\s+/).filter((value) => value.length > 0)) {
    const referenced = root.getElementById(id);
    if (referenced !== null) out += ` ${textFrom(referenced, 0)}`;
  }
  const trimmed = out.trim();
  return trimmed.length > 0 ? trimmed : null;
};

const nativeLabelText = (element: Element): string | null => {
  const tagName = element.tagName.toLowerCase();
  if (tagName === "img" || (tagName === "input" && element.getAttribute("type") === "image")) {
    return element.getAttribute("alt");
  }
  if (tagName === "input") {
    const type = (element.getAttribute("type") ?? "text").toLowerCase();
    if (type === "button" || type === "submit" || type === "reset") return element.getAttribute("value");
  }
  if (element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement) {
    for (const label of element.labels ?? []) {
      const text = textFrom(label, 0).trim();
      if (text.length > 0) return text;
    }
  }
  if (tagName === "fieldset") {
    const legend = element.querySelector("legend");
    if (legend !== null) return textFrom(legend, 0).trim();
  }
  return null;
};

const CONTENT_ROLES: readonly string[] = [
  "button",
  "link",
  "heading",
  "option",
  "cell",
  "columnheader",
  "rowheader",
  "listitem",
  "tab",
  "menuitem",
  "treeitem",
];

/**
 * Bounded accessible-name computation. The returned string is a name source,
 * not an exported value: it still passes through redaction before export.
 */
export const computeAccessibleName = (element: Element): string | null => {
  const labelled = labelledByText(element);
  if (labelled !== null) return labelled;
  const ariaLabel = element.getAttribute("aria-label");
  if (ariaLabel !== null && ariaLabel.trim().length > 0) return ariaLabel.trim();
  const native = nativeLabelText(element);
  if (native !== null && native.trim().length > 0) return native.trim();
  const title = element.getAttribute("title");
  if (title !== null && title.trim().length > 0) return title.trim();
  if (CONTENT_ROLES.includes(computeRole(element))) {
    const content = textFrom(element, 0).trim();
    if (content.length > 0) return content.slice(0, MAX_NAME_SOURCE_LENGTH);
  }
  return null;
};
