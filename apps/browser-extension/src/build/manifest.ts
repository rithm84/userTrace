/**
 * The Manifest V3 document, built from the validated build origins.
 *
 * The permission set is fixed by the
 * [browser extension contract](../../../../docs/arch-components/browser-extension.md):
 * `storage`, `scripting`, `identity`, `alarms`, optional host permissions for
 * every http and https page requested together during onboarding, and no tab,
 * navigation, request, cookie, debugger, user-script, capture, or offscreen
 * access. Content scripts are injected by the service worker after a
 * page proof matches, never declaratively, so an unbound tab is untouched.
 */
import { createHash } from "node:crypto";

import type { BuildOrigins } from "./buildOrigins";

export const REQUIRED_PERMISSIONS = [
  "storage",
  "scripting",
  "identity",
  "alarms",
] as const;

export const OPTIONAL_HOST_PERMISSIONS = ["https://*/*", "http://*/*"] as const;

export const FORBIDDEN_PERMISSIONS = [
  "tabs",
  "webNavigation",
  "webRequest",
  "cookies",
  "debugger",
  "userScripts",
  "tabCapture",
  "offscreen",
] as const;

export const MINIMUM_CHROME_VERSION = "137";

export type ChromeManifest = Readonly<{
  manifest_version: 3;
  name: string;
  version: string;
  description: string;
  minimum_chrome_version: string;
  key: string;
  permissions: readonly string[];
  optional_host_permissions?: readonly string[];
  host_permissions?: readonly string[];
  background: Readonly<{ service_worker: string; type: "module" }>;
  action: Readonly<{ default_popup: string; default_title: string; default_icon: Readonly<Record<string, string>> }>;
  options_ui: Readonly<{ page: string; open_in_tab: true }>;
  icons: Readonly<Record<string, string>>;
  externally_connectable: Readonly<{ matches: readonly string[] }>;
  content_security_policy: Readonly<{ extension_pages: string }>;
  web_accessible_resources: readonly Readonly<{
    resources: readonly string[];
    matches: readonly string[];
  }>[];
}>;

/** Files the content script and main-world bridge fetch from the package. */
export const WEB_ACCESSIBLE_RESOURCES = [
  "main-world-bridge.js",
  "agent-character.riv",
  "rive.wasm",
] as const;

export function createManifest(
  origins: BuildOrigins,
  version: string,
): ChromeManifest {
  return Object.freeze({
    manifest_version: 3,
    name: "uTrace",
    version,
    description:
      "Shows the uTrace agent, its controls, and its visual guidance inside a bound uTrace preview tab.",
    minimum_chrome_version: MINIMUM_CHROME_VERSION,
    key: origins.manifestKey,
    permissions: [...REQUIRED_PERMISSIONS],
    ...(origins.hostPermissions === "required"
      ? { host_permissions: [...OPTIONAL_HOST_PERMISSIONS] }
      : { optional_host_permissions: [...OPTIONAL_HOST_PERMISSIONS] }),
    background: Object.freeze({ service_worker: "background.js", type: "module" as const }),
    action: Object.freeze({
      default_popup: "popup.html",
      default_title: "uTrace",
      default_icon: iconSet(),
    }),
    options_ui: Object.freeze({ page: "options.html", open_in_tab: true as const }),
    icons: iconSet(),
    externally_connectable: Object.freeze({
      matches: origins.sessionTabOrigins.map((origin) => `${origin}/*`),
    }),
    content_security_policy: Object.freeze({
      extension_pages: createExtensionPagesCsp(origins),
    }),
    web_accessible_resources: [
      Object.freeze({
        resources: [...WEB_ACCESSIBLE_RESOURCES],
        matches: [...OPTIONAL_HOST_PERMISSIONS],
      }),
    ],
  });
}

/**
 * `wasm-unsafe-eval` is the narrowest directive that lets the packaged Rive
 * runtime compile its packaged WebAssembly. Ordinary `unsafe-eval`, inline
 * scripts, and remote script origins stay excluded, and network access is
 * limited to the uTrace origins this build targets. A source with a path would
 * match only that exact path, so the control-plane base URLs enter as origins.
 */
export function createExtensionPagesCsp(origins: BuildOrigins): string {
  const connectSources = [
    "'self'",
    new URL(origins.apiBaseUrl).origin,
    new URL(origins.controlSocketBaseUrl).origin,
    origins.accountLinkOrigin,
  ];

  return [
    "default-src 'none'",
    "script-src 'self' 'wasm-unsafe-eval'",
    "style-src 'self'",
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src ${[...new Set(connectSources)].join(" ")}`,
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-src 'none'",
    "worker-src 'self'",
  ].join("; ");
}

export const ICON_SIZES = [16, 32, 48, 128] as const;

function iconSet(): Readonly<Record<string, string>> {
  const icons: Record<string, string> = {};
  for (const size of ICON_SIZES) {
    icons[String(size)] = `icons/icon-${size}.png`;
  }
  return Object.freeze(icons);
}

/**
 * The id Chrome assigns to a manifest with this `key`: the first 32 hex digits
 * of the SPKI's SHA-256, each mapped onto `a`..`p`.
 */
export function extensionIdForKey(manifestKey: string): string {
  const digest = createHash("sha256").update(Buffer.from(manifestKey, "base64")).digest("hex");
  return Array.from(digest.slice(0, 32), (nibble) =>
    String.fromCharCode("a".charCodeAt(0) + Number.parseInt(nibble, 16)),
  ).join("");
}
