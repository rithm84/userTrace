/**
 * Registered route resolution.
 *
 * Span names and `http.route` come from the signed manifest, never from the
 * request. A path that matches neither a registered route nor registered
 * operational noise is a manifest gap, not a reason to export a raw URL.
 */

import type { HttpMethod, OperationalNoise, RegisteredRoute } from '../contracts/manifest.js';

/** Outcome of resolving one inbound request path against the manifest. */
export type RouteResolution =
  | { readonly kind: 'product'; readonly route: RegisteredRoute }
  | { readonly kind: 'operational_noise' }
  | { readonly kind: 'unregistered' };

/** Splits a request target into path segments, discarding query and fragment. */
function pathSegments(requestTarget: string): readonly string[] {
  const withoutFragment = requestTarget.split('#', 1)[0] ?? '';
  const withoutQuery = withoutFragment.split('?', 1)[0] ?? '';
  return withoutQuery.split('/').filter((segment) => segment !== '');
}

function templateSegments(template: string): readonly string[] {
  return template.split('/').filter((segment) => segment !== '');
}

/**
 * Matches path segments against a template.
 *
 * A `:name` segment matches exactly one segment; a trailing `*` matches the
 * remaining segments, including none.
 */
export function templateMatches(template: string, segments: readonly string[]): boolean {
  const expected = templateSegments(template);
  for (let index = 0; index < expected.length; index += 1) {
    const part = expected[index];
    if (part === '*') {
      return index === expected.length - 1;
    }
    const actual = segments[index];
    if (actual === undefined) {
      return false;
    }
    if (part === undefined) {
      return false;
    }
    if (part.startsWith(':')) {
      continue;
    }
    if (part !== actual) {
      return false;
    }
  }
  return segments.length === expected.length;
}

function methodAllowed(methods: readonly HttpMethod[], method: string): boolean {
  return methods.some((candidate) => candidate === method);
}

/**
 * Resolves an inbound request against the manifest.
 *
 * Registered product routes win over operational noise so an exclusion can
 * never remove a required product route.
 */
export function resolveRoute(
  requestTarget: string,
  method: string,
  routes: readonly RegisteredRoute[],
  noise: readonly OperationalNoise[],
): RouteResolution {
  const segments = pathSegments(requestTarget);
  for (const route of routes) {
    if (methodAllowed(route.methods, method) && templateMatches(route.route_template, segments)) {
      return { kind: 'product', route };
    }
  }
  for (const entry of noise) {
    if (methodAllowed(entry.methods, method) && templateMatches(entry.path_pattern, segments)) {
      return { kind: 'operational_noise' };
    }
  }
  return { kind: 'unregistered' };
}
