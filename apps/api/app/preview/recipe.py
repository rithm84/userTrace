"""The pinned runtime template recipe and the build context it stages.

A client's preview runtime template is a Vercel Container Registry image built
from the fixture repository's Dockerfile with the recipe's build arguments, and a
snapshot taken from a sandbox created from that image. The recipe digest covers
everything the image was built from, so an image can always be traced to the
exact text that produced it.

The context itself is ``git archive`` of the fixture checkout: tracked files only,
so a developer's ``node_modules``, ``.next``, ``.env.local`` or build cache can
never end up inside a runtime that holds customer-shaped data.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from app.preview.records import ReadinessProbe, SeedIdentity

__all__ = [
    "StagedBuildContext",
    "TemplateRecipe",
    "stage_build_context",
]

RECIPE_SCHEMA_VERSION = "utrace-preview-template-recipe-v1"


class TemplateRecipe(BaseModel):
    """The committed description of one preview runtime template."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(pattern=f"^{RECIPE_SCHEMA_VERSION}$")
    template_id: str
    template_revision: int = Field(ge=1)
    image_repository: str
    """The Vercel Container Registry repository the template image is pushed to."""
    fixture_revision: str
    """The exact fixture commit the template is built from. Never a branch tip."""
    dockerfile_path: str
    supervisor_path: str
    supervisor_command: str
    """Where the image installs the supervisor, which the control plane starts.

    Vercel Sandbox does not run an image's entrypoint, so every runtime's product
    is started by running this command detached, as the image's default user.
    """
    seed_manifest_path: str
    application_port: int = Field(ge=1, le=65535)
    health_path: str
    handoff_path: str
    data_api_path: str
    data_api_probe_body: Mapping[str, str]
    """A minimal valid request the data API accepts, used to probe that it still
    refuses an unauthenticated caller after the boundary validation passes."""
    application_directory: str
    dockerfile_build_args: Mapping[str, str]
    vcpus: int = Field(ge=1)
    gating_probes: tuple[ReadinessProbe, ...]
    advisory_probes: tuple[ReadinessProbe, ...]
    not_applicable_probes: Mapping[ReadinessProbe, str]
    """Probe name to the recorded reason the pinned template declares it absent."""

    @staticmethod
    def load(path: Path) -> TemplateRecipe:
        """Read and validate one committed recipe file."""
        return TemplateRecipe.model_validate_json(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class StagedBuildContext:
    """A staged, disposable build context and the identity it produced."""

    root: Path
    dockerfile: Path
    recipe_digest: str
    fixture_commit: str
    seed: SeedIdentity
    seed_manifest: Mapping[str, object]
    sdk_versions: Mapping[str, str]
    """The uTrace packages the fixture's lockfile resolves, which the image installs."""
    _temporary: TemporaryDirectory[str]

    def __enter__(self) -> Self:
        """Enter the staged context's scope."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Remove the staged directory."""
        self._temporary.cleanup()


def _run(command: Sequence[str], *, cwd: Path) -> str:
    """Run one local command, raising with its stderr when it fails."""
    completed = subprocess.run(
        list(command), cwd=cwd, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command {' '.join(command)} in {cwd} failed with exit code "
            f"{completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


_SDK_SCOPE = "node_modules/@utrace/"


def _locked_sdk_versions(root: Path) -> dict[str, str]:
    """Read the uTrace package versions the fixture's own lockfile resolves.

    The versions belong in the durable template record: validation evidence
    names the runtime it ran on, and "which SDK observed it" is part of that.
    A fixture with no lockfile entry for a uTrace package simply has none, and
    a recipe that bundles one adds it afterwards.
    """
    lockfile = root / "package-lock.json"
    if not lockfile.is_file():
        return {}
    document = json.loads(lockfile.read_text(encoding="utf-8"))
    packages = document.get("packages")
    if not isinstance(packages, dict):
        return {}
    versions: dict[str, str] = {}
    for path, entry in packages.items():
        if not path.startswith(_SDK_SCOPE) or path.count("node_modules/") > 1:
            continue
        version = entry.get("version") if isinstance(entry, dict) else None
        if isinstance(version, str):
            versions[path.removeprefix("node_modules/")] = version
    return versions


def _seed_identity(manifest: Mapping[str, object]) -> SeedIdentity:
    """Read the pinned dataset identity out of the fixture's seed manifest."""
    tables = manifest["tables"]
    identity = manifest["identity"]
    if not isinstance(tables, list) or not isinstance(identity, dict):
        raise TypeError("the seed manifest has no table list or identity block")
    lead = identity["growth_lead"]
    if not isinstance(lead, dict):
        raise TypeError("the seed manifest has no authenticated identity")
    return SeedIdentity(
        fixture_id=str(manifest["fixture_id"]),
        schema_version=str(manifest["schema_version"]),
        manifest_digest=str(manifest["content_hash"]),
        fixture_clock=datetime.fromisoformat(str(manifest["fixture_clock"])),
        table_digests={
            str(table["name"]): str(table["content_hash"]) for table in tables
        },
        authenticated_identity=str(lead["email"]),
    )


def stage_build_context(
    *,
    recipe: TemplateRecipe,
    fixture_repository: Path,
    staging_root: Path,
) -> StagedBuildContext:
    """Stage a clean build context for one template image build.

    The fixture checkout is exported at the recipe's pinned revision with
    ``git archive`` into a disposable directory under ``staging_root``. The
    revision is pinned rather than read from the branch tip, because a template
    that floats on someone else's next commit is not a pinned template.

    The build arguments come from the committed recipe alone. They describe the
    pinned template, not the process running the build, so an image built on one
    workstation and one built in a pipeline are the same bytes.
    """
    staging_root.mkdir(parents=True, exist_ok=True)
    temporary = TemporaryDirectory(prefix="utrace-preview-context-", dir=staging_root)
    root = Path(temporary.name)
    try:
        commit = _run(
            ["git", "rev-parse", "--verify", f"{recipe.fixture_revision}^{{commit}}"],
            cwd=fixture_repository,
        ).strip()
        archive = root / "fixture.tar"
        with archive.open("wb") as handle:
            completed = subprocess.run(
                ["git", "archive", "--format=tar", commit],
                cwd=fixture_repository,
                stdout=handle,
                stderr=subprocess.PIPE,
                check=False,
            )
        if completed.returncode != 0:
            raise RuntimeError(
                f"git archive of {fixture_repository} at {commit} failed: "
                f"{completed.stderr.decode('utf-8', errors='replace').strip()}"
            )
        with tarfile.open(archive) as bundle:
            bundle.extractall(root, filter="data")
        archive.unlink()

        manifest_path = root / recipe.seed_manifest_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise TypeError(f"{recipe.seed_manifest_path} is not a JSON object")
        seed = _seed_identity(manifest)
        versions = _locked_sdk_versions(root)
        dockerfile = root / recipe.dockerfile_path
        digest = hashlib.sha256(
            json.dumps(
                {
                    "recipe": recipe.model_dump(mode="json"),
                    "dockerfile_sha256": hashlib.sha256(dockerfile.read_bytes()).hexdigest(),
                    "fixture_commit": commit,
                    "seed_manifest_digest": seed.manifest_digest,
                    "sdk_versions": versions,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return StagedBuildContext(
            root=root,
            dockerfile=dockerfile,
            recipe_digest=f"sha256:{digest}",
            fixture_commit=commit,
            seed=seed,
            seed_manifest=manifest,
            sdk_versions=versions,
            _temporary=temporary,
        )
    except BaseException:
        temporary.cleanup()
        raise
