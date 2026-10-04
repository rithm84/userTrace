"""Egress detection for the workspace network wall.

The Vercel Sandbox firewall applies the wall: a workspace reaches only the package
registry, the OpenAI API, and the uTrace callback endpoints its policy declares,
and everything else is refused. That is the enforcement, and this module is not a
second one.

What this module does is classify. The sandbox surfaces no per-connection block
event to the control plane, so the attempt outcome `unsafe` cannot be derived from
the firewall's own log. Instead the controller scans the commands a run executed,
the agent's own included, for hosts the declared policy does not permit. One such
command is a violation; repeated or exfiltration-shaped violations end the attempt
as `unsafe`.

A command that names no host produces nothing here. A command whose host is on
the wall is already permitted and is not reported.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

__all__ = [
    "UNSAFE_VIOLATION_THRESHOLD",
    "EgressViolation",
    "scan_commands_for_egress",
]

UNSAFE_VIOLATION_THRESHOLD = 2
"""One violation is a finding; a second ends the attempt as `unsafe`."""

_URL = re.compile(r"\b(?:https?|ftp)://(?P<host>[A-Za-z0-9._-]+)")
_BARE_HOST = re.compile(
    r"\b(?:curl|wget|nc|ncat|ssh|scp|rsync)\s+[^|;&]*?(?P<host>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)"
)
_EXFILTRATION_SHAPE = re.compile(
    r"(?i)\b(?:curl|wget)\b[^|;&]*(?:--data|-d\s|--upload-file|-T\s|-F\s)"
)


class EgressViolation(BaseModel):
    """One command that names a host the declared network policy does not permit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    command: str
    host: str
    exfiltration_shaped: bool
    """True when the command also sends a body, which raises the severity."""


def scan_commands_for_egress(
    *, commands: Sequence[str], permitted_domains: Sequence[str]
) -> tuple[EgressViolation, ...]:
    """Return every command that names a host outside the declared policy."""
    violations: list[EgressViolation] = []
    for command in commands:
        for host in _hosts_in(command):
            if _permitted(host, permitted_domains):
                continue
            violations.append(
                EgressViolation(
                    command=command,
                    host=host,
                    exfiltration_shaped=_EXFILTRATION_SHAPE.search(command) is not None,
                )
            )
    return tuple(violations)


def _hosts_in(command: str) -> tuple[str, ...]:
    """Every host a command names, whether in a URL or as a bare transfer target."""
    hosts = [match.group("host") for match in _URL.finditer(command)]
    hosts.extend(match.group("host") for match in _BARE_HOST.finditer(command))
    return tuple(
        dict.fromkeys(host.lower() for host in hosts if host.lower() not in _LOCAL_HOSTS)
    )


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})


def _permitted(host: str, permitted_domains: Sequence[str]) -> bool:
    """Whether the wall's declared domain list covers this host."""
    for raw in permitted_domains:
        domain = raw.lower()
        if domain.startswith("*."):
            base = domain[2:]
            if host == base or host.endswith(f".{base}"):
                return True
        elif host == domain:
            return True
    return False
