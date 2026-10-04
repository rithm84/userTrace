/**
 * Redaction audit.
 *
 * Records rule ids, versions, output classes, and counts. It never records the
 * rejected original value, its length, or any derived fingerprint of it.
 */

import type { OutputClass, RedactionStep } from "./classes.js";

export interface AuditKey {
  readonly step: RedactionStep;
  readonly rule_id: string;
  readonly output_class: OutputClass;
}

export interface AuditEntry extends AuditKey {
  readonly redaction_version: string;
  readonly policy_version: string;
  readonly count: number;
}

export class RedactionAudit {
  readonly #counts = new Map<string, AuditEntry>();
  readonly #redactionVersion: string;
  readonly #policyVersion: string;

  constructor(redactionVersion: string, policyVersion: string) {
    this.#redactionVersion = redactionVersion;
    this.#policyVersion = policyVersion;
  }

  record(key: AuditKey): void {
    const mapKey = `${key.step}|${key.rule_id}|${key.output_class}`;
    const existing = this.#counts.get(mapKey);
    this.#counts.set(mapKey, {
      ...key,
      redaction_version: this.#redactionVersion,
      policy_version: this.#policyVersion,
      count: (existing?.count ?? 0) + 1,
    });
  }

  entries(): readonly AuditEntry[] {
    return [...this.#counts.values()];
  }

  totalFor(outputClass: OutputClass): number {
    let total = 0;
    for (const entry of this.#counts.values()) {
      if (entry.output_class === outputClass) total += entry.count;
    }
    return total;
  }
}
