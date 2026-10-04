"""Fixture discovery shared by the conformance tests.

Discovery is by directory layout, not registration: any
``fixtures/<area>/valid`` or ``fixtures/<area>/invalid`` directory is picked up,
and each file's name up to ``__`` names the contract it exercises. An area
workstream adds fixtures by adding files, never by editing a list here.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from utrace_contracts.locations import FIXTURES_ROOT

CONTRACT_SEPARATOR = "__"
NON_AREA_PREFIX = "_"


@dataclass(frozen=True, slots=True)
class Fixture:
    """One shared fixture and the contract it must be accepted or rejected by."""

    area: str
    contract: str
    case: str
    path: Path
    expect_valid: bool

    @property
    def instance(self) -> object:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def __str__(self) -> str:
        bucket = "valid" if self.expect_valid else "invalid"
        return f"{self.area}/{bucket}/{self.contract}{CONTRACT_SEPARATOR}{self.case}"


def _read_bucket(area_dir: Path, bucket: str, expect_valid: bool) -> Iterator[Fixture]:
    for path in sorted((area_dir / bucket).glob("*.json")):
        stem = path.stem
        if CONTRACT_SEPARATOR not in stem:
            raise ValueError(
                f"{path} must be named <name>.v<major>{CONTRACT_SEPARATOR}<case>.json so the "
                "conformance suite can tell which schema it exercises"
            )
        contract, case = stem.split(CONTRACT_SEPARATOR, 1)
        yield Fixture(area_dir.name, contract, case, path, expect_valid)


def discover_fixtures() -> list[Fixture]:
    """Every fixture under ``fixtures/<area>/{valid,invalid}``.

    Directories whose name starts with ``_`` are not contract areas; they hold
    shared test data such as the canonicalization vectors.
    """
    fixtures: list[Fixture] = []
    for area_dir in sorted(FIXTURES_ROOT.iterdir()):
        if not area_dir.is_dir() or area_dir.name.startswith(NON_AREA_PREFIX):
            continue
        buckets = [name for name in ("valid", "invalid") if (area_dir / name).is_dir()]
        if not buckets:
            raise ValueError(
                f"{area_dir} is a fixture area but carries neither a valid/ nor an invalid/ "
                "directory; prefix the directory with '_' if it is not a contract area"
            )
        fixtures.extend(_read_bucket(area_dir, "valid", expect_valid=True))
        fixtures.extend(_read_bucket(area_dir, "invalid", expect_valid=False))
    if not fixtures:
        raise ValueError(f"no fixtures discovered under {FIXTURES_ROOT}")
    return fixtures


FIXTURES = discover_fixtures()
