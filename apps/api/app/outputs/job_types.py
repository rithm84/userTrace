"""The Case File assembly Job type.

A leaf module with no imports so a controller can enqueue output work without
importing the assembler, and the assembler can import the controller's records.
"""

__all__ = ["ASSEMBLE_CASE_FILE"]

ASSEMBLE_CASE_FILE = "outputs.assemble_case_file"
"""Verify, resolve delivery, seal, publish, and then close the Product Signal."""
