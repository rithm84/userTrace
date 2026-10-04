"""The Shopalytics demo client's implementation policy.

The demo configures its one client by script rather than through onboarding
(docs/PRD.md#client-configuration). This is the Autonomy Policy that script
publishes, kept beside the client's Product Overview so the seed and the tests
that stand up the same client read one definition of its repository, checks,
build, and budgets.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from app.gate.policy import ImplementationPolicy
from app.gate.scope import DependencyPolicy, RequiredCheck, ResourceBudgets
from app.implementation.ports import BuildRecipe

__all__ = [
    "SHOPALYTICS_BUILD_RECIPE",
    "SHOPALYTICS_CLONE_URL",
    "SHOPALYTICS_REPOSITORY",
    "SHOPALYTICS_REQUIRED_CHECKS",
    "shopalytics_implementation_policy",
]

SHOPALYTICS_REPOSITORY: Final[str] = "ShopalyticsStore/shopalytics"
SHOPALYTICS_CLONE_URL: Final[str] = "https://github.com/ShopalyticsStore/shopalytics.git"

SHOPALYTICS_BUILD_RECIPE: Final[BuildRecipe] = BuildRecipe(
    node_version="24",
    package_manager="npm",
    # The repository's package-lock.json is out of sync with its package.json at
    # the pinned base revision, so `npm ci` refuses to run. The recipe installs
    # without rewriting the lockfile, which also keeps the dependency identity
    # the candidate is measured against unchanged.
    install_command="npm install --no-audit --no-fund --no-save",
    build_command="npm run build",
    start_command="npm run start:preview",
    application_port=3300,
    environment_profile="utrace-preview",
)

SHOPALYTICS_REQUIRED_CHECKS: Final[tuple[RequiredCheck, ...]] = (
    RequiredCheck(
        name="lint", command="npm run lint", working_directory=".", timeout_seconds=900, after=()
    ),
    RequiredCheck(
        name="typecheck",
        command="npm run typecheck",
        working_directory=".",
        timeout_seconds=900,
        after=(),
    ),
    RequiredCheck(
        name="test", command="npm test", working_directory=".", timeout_seconds=900, after=()
    ),
    # `next build` writes the route types under `.next/types` that `tsc` includes, so
    # the build starts only once the type check has read the tree.
    RequiredCheck(
        name="build",
        command="npm run build",
        working_directory=".",
        timeout_seconds=1800,
        after=("typecheck",),
    ),
)
"""The checks the workspace runs on the clean base and again on the candidate, in order."""

_LIVE_WINDOW_SECONDS: Final[int] = 2700
#: The first version and up to two changes the participant asks for after trying it.
_IMPLEMENTATION_LIMIT: Final[int] = 3


def shopalytics_implementation_policy(
    *, base_revision: str, workspace_snapshot_id: str, callback_hosts: Sequence[str]
) -> ImplementationPolicy:
    """Return the policy the gate narrows for every Shopalytics Product Signal.

    `base_revision` is the repository commit the preview template was built from,
    so the workspace and the candidate start from the same source.
    """
    return ImplementationPolicy(
        live_implementation_allowed=True,
        repository_ref=SHOPALYTICS_REPOSITORY,
        clone_url=SHOPALYTICS_CLONE_URL,
        base_revision=base_revision,
        workspace_snapshot_id=workspace_snapshot_id,
        callback_hosts=tuple(callback_hosts),
        build_recipe=SHOPALYTICS_BUILD_RECIPE,
        permitted_paths=("src/", "db/", "tests/"),
        blocked_paths=("deploy/", "utrace/", "fixtures/", "scripts/", "src/app/api/utrace/"),
        required_checks=SHOPALYTICS_REQUIRED_CHECKS,
        dependency_policy=DependencyPolicy.NONE,
        permitted_dependencies=(),
        dependency_manifest_paths=("package.json", "package-lock.json"),
        budgets=ResourceBudgets(
            elapsed_seconds=_LIVE_WINDOW_SECONDS,
            agent_turns=400,
            agent_cost_cents=5_000,
            process_executions=200,
            artifact_bytes=20_000_000,
            correction_cycles=2,
            changed_files=12,
        ),
        live_window_seconds=_LIVE_WINDOW_SECONDS,
        implementation_limit=_IMPLEMENTATION_LIMIT,
        forbidden_path_patterns=(
            "**/auth/**",
            "**/*secret*",
            "**/*credential*",
            "**/billing/**",
            "**/.github/workflows/**",
            "**/utrace/signing.ts",
        ),
    )
