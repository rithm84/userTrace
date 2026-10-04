/**
 * Route normalization.
 *
 * Routes are normalized to registered route templates from the signed manifest.
 * Raw query strings, fragments, secrets, and unsafe literal path values are
 * never exported. An unrecognized route is reported as unknown with safe
 * structural metadata only.
 */

import type { RouteParam, RouteState, SafeValue } from "../observation/types.js";
import type { RedactionPipeline } from "../redaction/pipeline.js";
import { toSafeValue } from "../redaction/pipeline.js";

interface CompiledTemplate {
  readonly template: string;
  readonly segments: readonly string[];
}

const splitPath = (pathname: string): readonly string[] =>
  pathname.split("/").filter((segment) => segment.length > 0);

const compile = (template: string): CompiledTemplate => ({ template, segments: splitPath(template) });

export class RouteTemplates {
  readonly #compiled: readonly CompiledTemplate[];

  constructor(templates: readonly string[]) {
    this.#compiled = templates.map(compile);
  }

  /** Exact-arity match with `:name` parameter segments. No nearest guess. */
  #match(segments: readonly string[]): { template: CompiledTemplate; params: readonly RawParam[] } | null {
    for (const candidate of this.#compiled) {
      if (candidate.segments.length !== segments.length) continue;
      const params: RawParam[] = [];
      let matched = true;
      for (let index = 0; index < candidate.segments.length; index += 1) {
        const templateSegment = candidate.segments[index] ?? "";
        const pathSegment = segments[index] ?? "";
        if (templateSegment.startsWith(":")) {
          params.push({ name: templateSegment.slice(1), value: pathSegment });
          continue;
        }
        if (templateSegment !== pathSegment) {
          matched = false;
          break;
        }
      }
      if (matched) return { template: candidate, params };
    }
    return null;
  }

  /** Produces the exported route state for a URL. */
  resolve(url: URL, redaction: RedactionPipeline): RouteState {
    const segments = splitPath(url.pathname);
    const matched = this.#match(segments);
    const structural = {
      segment_count: segments.length,
      has_query: url.search.length > 0,
      has_fragment: url.hash.length > 0,
    };
    if (matched === null) {
      return { unknown_route: true, params: [], ...structural };
    }
    const params: RouteParam[] = matched.params.map((param) => ({
      name: param.name,
      value: redactRouteParam(param.value, redaction),
    }));
    return { route_template: matched.template.template, unknown_route: false, params, ...structural };
  }
}

interface RawParam {
  readonly name: string;
  readonly value: string;
}

const redactRouteParam = (value: string, redaction: RedactionPipeline): SafeValue =>
  toSafeValue(
    redaction.apply({
      kind: "route_param",
      value,
      element: null,
      inAttestedDocument: true,
      rawBody: false,
      approvedRuleId: null,
      annotatedSensitive: false,
    }),
  );
