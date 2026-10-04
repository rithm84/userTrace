/**
 * Explicit error types raised by the contract package.
 *
 * Every failure here is a contract violation, not a recoverable condition: the
 * callers are producers and consumers that must fail closed rather than fall
 * back to a lower-fidelity path.
 */

/** Base class for every uTrace contract violation. */
export class ContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = new.target.name;
  }
}

/** A schema document was requested that this package does not carry. */
export class UnknownContractError extends ContractError {
  readonly area: string;
  readonly contract: string;
  readonly known: readonly string[];

  constructor(area: string, contract: string, known: readonly string[]) {
    super(
      `no schema document for area=${JSON.stringify(area)} contract=${JSON.stringify(contract)}; ` +
        `expected schemas/${area}/${contract}.schema.json. known contracts in this area: ` +
        (known.length > 0 ? known.join(", ") : "(none)"),
    );
    this.area = area;
    this.contract = contract;
    this.known = known;
  }
}

/** A value could not be canonicalized into RFC 8785 form. */
export class CanonicalizationError extends ContractError {
  constructor(detail: string) {
    super(`value is not canonicalizable JSON: ${detail}`);
  }
}

/** A recomputed payload digest disagrees with the digest on the envelope. */
export class DigestMismatchError extends ContractError {
  readonly messageId: string;
  readonly declared: string;
  readonly computed: string;

  constructor(messageId: string, declared: string, computed: string) {
    super(
      `payload digest mismatch for message_id=${messageId}: envelope declares sha256/jcs ` +
        `${declared}, payload canonicalizes to ${computed}`,
    );
    this.messageId = messageId;
    this.declared = declared;
    this.computed = computed;
  }
}
