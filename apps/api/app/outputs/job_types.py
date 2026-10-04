"""Job type names for Case File assembly and delivery.

A leaf module with no imports so a controller can enqueue output work without
importing the assembler, and the assembler can import the controller's records.
"""

__all__ = ["ASSEMBLE_CASE_FILE", "RECONCILE_DELIVERY", "RUN_DELIVERY"]

ASSEMBLE_CASE_FILE = "outputs.assemble_case_file"
"""Verify, resolve delivery, seal, publish, and then close the Product Signal."""

RUN_DELIVERY = "outputs.run_delivery"
"""Open the one authorized draft PR for a validated implementation."""

RECONCILE_DELIVERY = "outputs.reconcile_delivery"
"""Resolve a delivery whose provider outcome is unknown, before any repetition."""
