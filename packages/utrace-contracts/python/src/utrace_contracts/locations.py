"""Where the language-neutral contract assets live.

The JSON Schemas and fixtures are shared by both bindings, so they sit beside
them in the package directory rather than inside either language's tree. Both
bindings resolve them from their own source location; there is no configuration
and no search path.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["FIXTURES_ROOT", "SCHEMAS_ROOT"]

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
"""``packages/utrace-contracts``, three levels above ``python/src/utrace_contracts``."""

SCHEMAS_ROOT = _PACKAGE_ROOT / "schemas"
"""``packages/utrace-contracts/schemas`` — one directory per contract area."""

FIXTURES_ROOT = _PACKAGE_ROOT / "fixtures"
"""``packages/utrace-contracts/fixtures`` — ``<area>/valid`` and ``<area>/invalid``."""

if not SCHEMAS_ROOT.is_dir():  # pragma: no cover - a broken checkout, not a runtime path
    raise RuntimeError(
        f"contract schemas are missing at {SCHEMAS_ROOT}. "
        "utrace_contracts is used from the repository checkout that carries "
        "packages/utrace-contracts/schemas; installing the wheel alone is not supported."
    )
