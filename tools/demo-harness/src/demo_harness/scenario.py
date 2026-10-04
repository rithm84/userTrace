"""Constants the demo journey is pinned to.

Every value here is quoted from ``docs/SCENARIO.md``. Nothing in the harness
may derive a scenario fact from the target's own state: the point of the
harness is to check the target against this document, so the document's values
live here and the target's values are read.
"""

from typing import Final

from utrace_contracts.domain.lifecycle import (
    CaseFileStage,
    CaseFileVariant,
    FlowType,
    GateDecisionKind,
    ValidationOutcome,
)

__all__ = [
    "CANONICAL_DISCORD_MESSAGE",
    "CLIENT_NAME",
    "CUSTOMER_ACCOUNT_KEY",
    "CUSTOMER_ACCOUNT_NAME",
    "EXPECTED_INVOICE_DUE_MINOR_UNITS",
    "GATE_DECISION",
    "INITIAL_FLOW_TYPE",
    "INVOICE_MINOR_UNITS",
    "REWARD_CURRENCY",
    "REWARD_MINOR_UNITS",
    "SAVED_VIEW_NAME",
    "TERMINAL_CASE_FILE_STAGE",
    "TERMINAL_CASE_FILE_VARIANT",
    "TERMINAL_VALIDATION_OUTCOME",
]

CLIENT_NAME: Final[str] = "Shopalytics"
"""The client product. Shopalytics is the client; Dudulemon is the customer."""

CUSTOMER_ACCOUNT_KEY: Final[str] = "dudulemon"
CUSTOMER_ACCOUNT_NAME: Final[str] = "Dudulemon"

CANONICAL_DISCORD_MESSAGE: Final[str] = (
    "I keep rebuilding the same conversion filters every Monday. Can Shopalytics "
    "let me save a view for TikTok traffic from women 25-34 on products with "
    "negative sizing reviews, then reopen it later?"
)
"""The canonical Discord input, verbatim from the scenario."""

SAVED_VIEW_NAME: Final[str] = "TikTok sizing drop-off"
"""The saved-view name used during validation."""

INITIAL_FLOW_TYPE: Final[FlowType] = FlowType.FEATURE_REQUEST_FLOW
GATE_DECISION: Final[GateDecisionKind] = GateDecisionKind.APPROVED_LIVE
TERMINAL_VALIDATION_OUTCOME: Final[ValidationOutcome] = ValidationOutcome.VALIDATED
TERMINAL_CASE_FILE_VARIANT: Final[CaseFileVariant] = CaseFileVariant.VALIDATED_IMPLEMENTATION
TERMINAL_CASE_FILE_STAGE: Final[CaseFileStage] = CaseFileStage.PUBLISHED

REWARD_MINOR_UNITS: Final[int] = 25_000
"""The fixed reward: 250.00 USD."""

REWARD_CURRENCY: Final[str] = "usd"

INVOICE_MINOR_UNITS: Final[int] = 100_000
"""The Shopalytics service invoice the harness finalizes: 1,000.00 USD."""

EXPECTED_INVOICE_DUE_MINOR_UNITS: Final[int] = INVOICE_MINOR_UNITS - REWARD_MINOR_UNITS
"""750.00 USD remaining due once the reward is applied."""
