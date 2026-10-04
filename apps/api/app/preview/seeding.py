"""Dataset installation and identity verification inside a preview runtime.

Preparation follows the approved Data Seeding Contract and verifies the resulting
dataset. A base runtime carries the dataset the template's image was built with;
a candidate installs that same approved identity independently, so the two can be
compared without either being copied from the other.

Verification reads the runtime's own manifest table directly rather than the
application's health route, so a dataset claim never depends on the surface being
verified. A mismatch is a typed refusal: a partial or drifted dataset is
destroyed, never repaired in place and never presented as the approved one.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.errors import GuardRejectedError
from app.logging import get_logger
from app.preview.recipe import TemplateRecipe
from app.preview.records import SeedIdentity, SeedTarget
from app.preview.runtime import PreviewRuntimeHandle, PreviewRuntimeProvider

__all__ = ["InstalledDataset", "install_dataset", "read_installed_dataset", "verify_dataset"]

_log = get_logger(__name__)

_FIELD_SEPARATOR = "\x1f"
_SEED_TIMEOUT_SECONDS = 900
_READ_TIMEOUT_SECONDS = 120

_MANIFEST_QUERY = (
    "select fixture_id, schema_version, content_hash, fixture_clock, seed_target, "
    "installed_at from fixture_manifest"
)


class InstalledDataset(BaseModel):
    """The dataset identity a runtime actually holds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fixture_id: str
    schema_version: str
    content_hash: str
    fixture_clock: datetime
    seed_target: SeedTarget
    installed_at: datetime


def _psql(recipe: TemplateRecipe, statement: str) -> str:
    """Render a psql invocation against the runtime's own database.

    The schema is read from the runtime's environment rather than assumed, so a
    template that renames it stays verifiable without a code change.
    """
    return (
        'psql -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
        f"-At -F '{_FIELD_SEPARATOR}' "
        f'-c "set search_path to \\"$SHOPALYTICS_DATABASE_SCHEMA\\"" '
        f'-c "{statement}"'
    )


async def read_installed_dataset(
    *,
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    recipe: TemplateRecipe,
) -> InstalledDataset:
    """Read the dataset identity the runtime's database records."""
    result = await provider.execute(
        handle=handle,
        script=_psql(recipe, _MANIFEST_QUERY),
        working_directory=recipe.application_directory,
        timeout_seconds=_READ_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="read_installed_dataset",
            reason=(
                "the runtime's database did not answer the fixture manifest query "
                f"(exit {result.exit_code}): {result.stdout[-1000:]}"
            ),
        )
    rows = [line for line in result.stdout.splitlines() if _FIELD_SEPARATOR in line]
    if len(rows) != 1:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="read_installed_dataset",
            reason=(
                f"the runtime holds {len(rows)} fixture manifest rows; exactly one "
                "approved dataset identity is required"
            ),
        )
    fields = rows[0].split(_FIELD_SEPARATOR)
    if len(fields) != 6:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="read_installed_dataset",
            reason=f"the fixture manifest row has {len(fields)} columns, expected 6",
        )
    return InstalledDataset(
        fixture_id=fields[0],
        schema_version=fields[1],
        content_hash=fields[2],
        fixture_clock=datetime.fromisoformat(fields[3]),
        seed_target=SeedTarget(fields[4]),
        installed_at=datetime.fromisoformat(fields[5]),
    )


async def verify_dataset(
    *,
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    recipe: TemplateRecipe,
    seed: SeedIdentity,
    expected_target: SeedTarget,
) -> InstalledDataset:
    """Prove the runtime holds the approved dataset, or refuse with the mismatch."""
    installed = await read_installed_dataset(
        provider=provider, handle=handle, recipe=recipe
    )
    mismatches: list[str] = []
    if installed.fixture_id != seed.fixture_id:
        mismatches.append(
            f"fixture_id {installed.fixture_id!r} != {seed.fixture_id!r}"
        )
    if installed.schema_version != seed.schema_version:
        mismatches.append(
            f"schema_version {installed.schema_version!r} != {seed.schema_version!r}"
        )
    if installed.content_hash != seed.manifest_digest:
        mismatches.append(
            f"content_hash {installed.content_hash!r} != {seed.manifest_digest!r}"
        )
    if installed.fixture_clock != seed.fixture_clock:
        mismatches.append(
            f"fixture_clock {installed.fixture_clock.isoformat()} != "
            f"{seed.fixture_clock.isoformat()}"
        )
    if installed.seed_target is not expected_target:
        mismatches.append(
            f"seed_target {installed.seed_target.value!r} != {expected_target.value!r}"
        )
    if mismatches:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="verify_dataset",
            reason=(
                "the installed dataset does not reproduce the approved seed "
                f"identity: {'; '.join(mismatches)}"
            ),
        )
    _log.info(
        "preview_dataset_verified",
        runtime_ref=handle.runtime_ref,
        content_hash=installed.content_hash,
        seed_target=installed.seed_target.value,
    )
    return installed


async def install_dataset(
    *,
    provider: PreviewRuntimeProvider,
    handle: PreviewRuntimeHandle,
    recipe: TemplateRecipe,
    seed: SeedIdentity,
    seed_target: SeedTarget,
) -> InstalledDataset:
    """Install the approved dataset into the runtime and verify what landed.

    The seeding script regenerates the dataset from its pinned definition,
    compares the result against the committed manifest, installs it in one
    transaction, and records the identity. This call runs it and then reads the
    identity back independently.
    """
    script = (
        f"SHOPALYTICS_{seed_target.value.upper()}_DATABASE_URL=\"$DATABASE_URL\" "
        f"npm run db:seed:{seed_target.value}"
    )
    result = await provider.execute(
        handle=handle,
        script=(
            'export DATABASE_URL="postgres://$POSTGRES_USER@127.0.0.1:5432/$POSTGRES_DB"; '
            f"{script}"
        ),
        working_directory=recipe.application_directory,
        timeout_seconds=_SEED_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        raise GuardRejectedError(
            machine="preview_runtime_allocation",
            trigger="install_dataset",
            reason=(
                f"installing the {seed_target.value} dataset failed with exit "
                f"{result.exit_code}: {result.stdout[-2000:]}"
            ),
        )
    return await verify_dataset(
        provider=provider,
        handle=handle,
        recipe=recipe,
        seed=seed,
        expected_target=seed_target,
    )
