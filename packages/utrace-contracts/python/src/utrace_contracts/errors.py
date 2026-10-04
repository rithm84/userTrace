"""Explicit error types raised by the contract package.

Every failure here is a contract violation, not a recoverable condition: the
callers are producers and consumers that must fail closed rather than fall back
to a lower-fidelity path.
"""

from __future__ import annotations

__all__ = [
    "ContractError",
    "DigestMismatchError",
    "SchemaViolationError",
    "UnknownContractError",
]


class ContractError(Exception):
    """Base class for every uTrace contract violation."""


class UnknownContractError(ContractError):
    """A schema document was requested that this package does not carry."""

    def __init__(self, area: str, contract: str, known: tuple[str, ...]) -> None:
        self.area = area
        self.contract = contract
        self.known = known
        super().__init__(
            f"no schema document for area={area!r} contract={contract!r}; "
            f"expected schemas/{area}/{contract}.schema.json. "
            f"known contracts in this area: {', '.join(known) if known else '(none)'}"
        )


class SchemaViolationError(ContractError):
    """An instance failed JSON Schema validation.

    ``problems`` lists one entry per error as ``"<instance path>: <message>"`` so
    the caller can report which field was rejected without echoing the value.
    """

    def __init__(self, area: str, contract: str, problems: tuple[str, ...]) -> None:
        self.area = area
        self.contract = contract
        self.problems = problems
        joined = "; ".join(problems)
        super().__init__(
            f"instance does not satisfy https://utrace.dev/schemas/{area}/"
            f"{contract.replace('.v', '/v')}: {joined}"
        )


class DigestMismatchError(ContractError):
    """A recomputed payload digest disagrees with the digest on the envelope."""

    def __init__(self, message_id: str, declared: str, computed: str) -> None:
        self.message_id = message_id
        self.declared = declared
        self.computed = computed
        super().__init__(
            f"payload digest mismatch for message_id={message_id}: "
            f"envelope declares sha256/jcs {declared}, payload canonicalizes to {computed}"
        )
