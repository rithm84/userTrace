"""The render check a candidate passes before the session's route may point at it.

Contract: docs/arch-components/preview-build.md#fast-checks-before-the-swap

A spare candidate runtime serves the product's development server, which compiles
a page and its modules when they are requested. The check signs in as the
originating user's product identity, requests the page of each declared target the
change adds, and then every client module that page loads: the page must answer
200, every module must compile, and together the modules must declare every target
the change adds.

It reads what the server sends rather than a rendered document, because the
browser SDK activates only through the preview front door on the session's own
origin, and the product renders its controls only after that activation in the
browser. A failure that happens only in a browser after the page loads is outside
what this check can see.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from app.envelope import JsonValue
from app.logging import get_logger
from app.preview.handoff import mint_product_handoff
from app.preview.readiness import ProbeTarget
from app.preview.recipe import TemplateRecipe

__all__ = [
    "RenderCheck",
    "added_targets",
    "pages_for_targets",
    "run_render_check",
]

_log = get_logger(__name__)

_HANDOFF_LIFETIME_SECONDS = 60
_TARGET_PATTERN = re.compile(r"data-utrace-target\s*=\s*[\"']([a-z][a-z0-9_]*)[\"']")
"""A declared target as source code writes it: the attribute with a literal value."""
_COMPILED_TARGET_PATTERN = re.compile(
    r"data-utrace-target[\"']?\s*[:=]\s*[\"']([a-z][a-z0-9_]*)[\"']"
)
"""The same declaration once compiled, where JSX becomes an object property."""
_SCRIPT_PATTERN = re.compile(r"<script[^>]*\ssrc=\"(/[^\"]+)\"")


@dataclass(frozen=True, slots=True)
class RenderCheck:
    """What the render check saw, and whether the candidate passed it."""

    passed: bool
    pages: tuple[str, ...]
    expected_targets: tuple[str, ...]
    missing_targets: tuple[str, ...]
    failures: tuple[str, ...]
    seconds: float

    def as_column(self) -> dict[str, JsonValue]:
        return {
            "passed": self.passed,
            "pages": list(self.pages),
            "expected_targets": list(self.expected_targets),
            "missing_targets": list(self.missing_targets),
            "failures": list(self.failures),
            "seconds": round(self.seconds, 3),
        }


def added_targets(
    *, files: Mapping[str, str], originals: Mapping[str, str | None]
) -> frozenset[str]:
    """The declared targets the candidate's files declare and the base files did not."""
    candidate = _declared(files.values())
    base = _declared(content for content in originals.values() if content is not None)
    return frozenset(candidate - base)


def pages_for_targets(
    *, targets: Iterable[str], annotation_manifest: Mapping[str, JsonValue]
) -> tuple[str, ...]:
    """The literal pages the manifest declares the targets on.

    A route template with a dynamic segment names no single page, so it is skipped;
    with no page left, the render check renders the page the handoff lands on.
    """
    wanted = set(targets)
    pages: set[str] = set()
    declared = annotation_manifest.get("targets")
    if isinstance(declared, list):
        for item in declared:
            if not isinstance(item, dict) or item.get("definition_id") not in wanted:
                continue
            routes = item.get("route_templates")
            if isinstance(routes, list):
                pages.update(
                    route
                    for route in routes
                    if isinstance(route, str) and route.startswith("/") and "[" not in route
                )
    return tuple(sorted(pages))


async def run_render_check(
    *,
    browser: httpx.AsyncClient,
    target: ProbeTarget,
    recipe: TemplateRecipe,
    handoff_secret: str,
    authenticated_identity: str,
    pages: Sequence[str] | None,
    expected_targets: frozenset[str],
) -> RenderCheck:
    """Sign in, request each page and every module it loads, and judge the result.

    `browser` is a client of the caller's with an empty cookie jar, which the sign-in
    fills as a browser's would. With no pages given, the page the handoff lands on
    is rendered.
    """
    started = datetime.now(tz=UTC)
    failures: list[str] = []
    compiled: set[str] = set()
    token = mint_product_handoff(
        handoff_secret=handoff_secret,
        audience_host=target.public_preview_host,
        subject_email=authenticated_identity,
        lifetime_seconds=_HANDOFF_LIFETIME_SECONDS,
    )
    signed_in = await browser.post(
        target.url(recipe.handoff_path),
        headers=target.headers({"content-type": "application/json"}),
        json={"token": token.token},
    )
    landing = _landing_page(signed_in)
    if signed_in.status_code != 200 or landing is None:
        failures.append(f"sign-in answered {signed_in.status_code}")
        rendered: tuple[str, ...] = tuple(pages or ())
    else:
        rendered = tuple(pages or (landing,))
        for page in rendered:
            failures.extend(await _render(browser, target, page, compiled))
    missing = tuple(sorted(expected_targets - compiled))
    if missing:
        failures.append("the page's modules do not declare " + ", ".join(missing))
    check = RenderCheck(
        passed=not failures,
        pages=rendered,
        expected_targets=tuple(sorted(expected_targets)),
        missing_targets=missing,
        failures=tuple(failures),
        seconds=(datetime.now(tz=UTC) - started) / timedelta(seconds=1),
    )
    _log.info(
        "preview_render_check",
        passed=check.passed,
        pages=list(check.pages),
        missing_targets=list(check.missing_targets),
        failures=list(check.failures),
        seconds=round(check.seconds, 2),
    )
    return check


async def _render(
    browser: httpx.AsyncClient, target: ProbeTarget, page: str, compiled: set[str]
) -> list[str]:
    """Request one page and the modules it loads, adding the targets they declare."""
    response = await browser.get(target.url(page), headers=target.headers({}))
    if response.status_code != 200:
        return [f"{page} answered {response.status_code}"]
    scripts = sorted(set(_SCRIPT_PATTERN.findall(response.text)))
    modules = await asyncio.gather(
        *(browser.get(target.url(script), headers=target.headers({})) for script in scripts)
    )
    failures = [
        f"{script} answered {module.status_code}"
        for script, module in zip(scripts, modules, strict=True)
        if module.status_code != 200
    ]
    for module in modules:
        compiled.update(_COMPILED_TARGET_PATTERN.findall(module.text))
    return failures


def _declared(contents: Iterable[str]) -> set[str]:
    return {match for content in contents for match in _TARGET_PATTERN.findall(content)}


def _landing_page(response: httpx.Response) -> str | None:
    """The page the product's handoff sends the signed-in identity to."""
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    redirect = body.get("redirect_to") if isinstance(body, dict) else None
    return redirect if isinstance(redirect, str) and redirect.startswith("/") else None
