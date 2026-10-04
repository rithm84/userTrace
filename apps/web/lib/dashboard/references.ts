import type { ResolvedReference } from "@/lib/api/schemas";

/**
 * The labels behind the opaque client-scoped references a dashboard page holds.
 *
 * A published Case File, a case and an Outreach Offer all name a person or an
 * organization by `client_user:<uuid>` or `customer_account:<uuid>`; the
 * authenticated staff read model resolves them. Where uTrace holds no recorded
 * name the reference itself is shown, because inventing one would put a name on
 * a record that never carried it.
 */
export type ReferenceLabels = ReadonlyMap<string, string>;

export function referenceLabels(resolved: readonly ResolvedReference[]): ReferenceLabels {
  const labels = new Map<string, string>();
  for (const entry of resolved) {
    if (entry.label !== undefined) {
      labels.set(entry.ref, entry.label);
    }
  }
  return labels;
}

export function labelFor(labels: ReferenceLabels, ref: string): string {
  return labels.get(ref) ?? ref;
}
