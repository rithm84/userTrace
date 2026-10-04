"""Job type names for Outreach Offers entitlement and issuance.

A leaf module with no imports so any terminal or milestone transition can enqueue
offer work without importing the offers controller.
"""

__all__ = ["EVALUATE_ENTITLEMENT", "ISSUE_CREDIT", "RECONCILE_ISSUANCE"]

EVALUATE_ENTITLEMENT = "offers.evaluate_entitlement"
"""Evaluate participation and convert or release the offer's reservation."""

ISSUE_CREDIT = "offers.issue_credit"
"""Issue one earned invoice credit through the client's billing account."""

RECONCILE_ISSUANCE = "offers.reconcile_issuance"
"""Resolve an uncertain issuance against the customer's transaction history."""
