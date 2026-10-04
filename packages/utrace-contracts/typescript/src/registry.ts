/**
 * How an area publishes its bindings.
 *
 * Each contract area exports `areaContracts` from `src/<area>/contracts.ts`,
 * keyed by `"<name>.v<major>"`. The conformance suite discovers those modules
 * from the filesystem, so adding an area means adding a directory, never
 * editing a central registry.
 *
 * `unknown` is the input type on purpose: it is the only correct type for a
 * decoded-JSON value that has not been validated yet, and each parser narrows
 * it immediately.
 */

/** Parses one untrusted decoded-JSON value into that contract's type. */
export type ContractParser = (value: unknown) => unknown;

/** One area's contracts, keyed by `"<name>.v<major>"`. */
export type ContractBindings = Readonly<Record<string, ContractParser>>;

/** The attribute an area's `contracts` module exposes its bindings under. */
export const AREA_CONTRACTS_EXPORT = "areaContracts";
