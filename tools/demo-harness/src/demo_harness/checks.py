"""Findings: one named observation, whether it holds, and what was seen.

Every command produces findings instead of raising at the first disagreement,
so an operator sees the whole picture in one run. The command then exits
non-zero when any finding failed, naming each one.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["Finding", "failed", "holds", "print_findings", "violated"]


class Finding(BaseModel):
    """One checked fact and the evidence for the verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    ok: bool
    detail: str = Field(min_length=1)


def holds(name: str, detail: str) -> Finding:
    """Return a finding that holds."""
    return Finding(name=name, ok=True, detail=detail)


def violated(name: str, detail: str) -> Finding:
    """Return a finding that does not hold."""
    return Finding(name=name, ok=False, detail=detail)


def failed(findings: Iterable[Finding]) -> list[Finding]:
    """Return only the findings that do not hold."""
    return [finding for finding in findings if not finding.ok]


def print_findings(title: str, findings: Sequence[Finding]) -> int:
    """Print every finding and return the process exit status."""
    sys.stdout.write(f"{title}\n")
    for finding in findings:
        mark = "PASS" if finding.ok else "FAIL"
        sys.stdout.write(f"  [{mark}] {finding.name}: {finding.detail}\n")
    broken = failed(findings)
    if not broken:
        sys.stdout.write(f"{len(findings)} checks passed\n")
        return 0
    sys.stdout.write(
        f"{len(broken)} of {len(findings)} checks failed: "
        f"{', '.join(finding.name for finding in broken)}\n"
    )
    return 1
