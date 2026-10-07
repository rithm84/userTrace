"""Assembling one revisioned, source-linked context bundle.

A bundle is the immutable input one worker receives. It carries the revisions it
was assembled against, so a result returned later can be rejected when a
correction or a changed requirement has moved underneath it.

Budgeting preserves critical state first. Sections are filled in a fixed
priority order and every source the budget excluded is still written down as an
omitted-source record, so a dropped source is visible rather than silent.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from app.context.purposes import BundlePurpose
from app.context.sources import (
    SECTION_ACCEPTANCE_CRITERIA,
    SECTION_AGREED_SCOPE,
    SECTION_CONTRADICTIONS,
    SECTION_IMPLEMENTATION_STATUS,
    SECTION_LIFECYCLE_AND_POLICY,
    SECTION_OPEN_QUESTIONS,
    SECTION_PRIOR_COMMANDS_AND_RESULTS,
    SECTION_PRODUCT_KNOWLEDGE,
    SECTION_RECENT_CONVERSATION,
    SECTION_SELECTED_OBSERVATIONS,
    ContextItem,
    ContextSection,
    acceptance_criteria_items,
    agreed_scope_items,
    contradiction_items,
    implementation_status_items,
    lifecycle_and_policy_items,
    observation_items,
    open_question_items,
    prior_command_items,
    recent_conversation_items,
)
from app.db.pool import Database
from app.db.rows import as_int, as_uuid
from app.envelope import JsonValue
from app.ids import uuid7
from app.interview.observations import ObservedActivity
from app.logging import get_logger

__all__ = [
    "SECTION_PRIORITY",
    "AssembledBundle",
    "BundleDependencies",
    "OmittedSource",
    "assemble_bundle",
    "assemble_conversation_bundle",
    "current_dependencies",
    "load_bundle",
]

_log = get_logger(__name__)

# Critical state first. A budget that cannot hold everything drops from the end
# of this list, never from the agreed scope, criteria, or contradictions.
SECTION_PRIORITY: tuple[ContextSection, ...] = (
    SECTION_LIFECYCLE_AND_POLICY,
    SECTION_AGREED_SCOPE,
    SECTION_ACCEPTANCE_CRITERIA,
    SECTION_CONTRADICTIONS,
    SECTION_OPEN_QUESTIONS,
    SECTION_IMPLEMENTATION_STATUS,
    SECTION_RECENT_CONVERSATION,
    SECTION_SELECTED_OBSERVATIONS,
    SECTION_PRODUCT_KNOWLEDGE,
    SECTION_PRIOR_COMMANDS_AND_RESULTS,
)

_BUDGET_EXCEEDED = "context_budget_exceeded"


@dataclass(frozen=True, slots=True)
class BundleDependencies:
    """The revisions a bundle was assembled against.

    A result citing this bundle is stale when one its purpose depends on has moved
    (`BundlePurpose.dependencies`), which is what stops a worker's answer from
    updating a scope that changed while it ran.
    """

    product_signal_revision: int
    interview_session_revision: int
    pinned_configuration_release_id: UUID
    classification_revision: int
    acceptance_criteria_revision: int
    concern_revision: int
    readiness_revision: int

    def as_payload(self) -> dict[str, JsonValue]:
        """Render the dependencies for the bundle's jsonb column."""
        return {
            "product_signal_revision": self.product_signal_revision,
            "interview_session_revision": self.interview_session_revision,
            "pinned_configuration_release_id": str(self.pinned_configuration_release_id),
            "classification_revision": self.classification_revision,
            "acceptance_criteria_revision": self.acceptance_criteria_revision,
            "concern_revision": self.concern_revision,
            "readiness_revision": self.readiness_revision,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "BundleDependencies":
        """Read the dependencies back from a stored bundle."""
        return cls(
            product_signal_revision=_require_int(payload, "product_signal_revision"),
            interview_session_revision=_require_int(
                payload, "interview_session_revision"
            ),
            pinned_configuration_release_id=UUID(
                str(payload["pinned_configuration_release_id"])
            ),
            classification_revision=_require_int(payload, "classification_revision"),
            acceptance_criteria_revision=_require_int(
                payload, "acceptance_criteria_revision"
            ),
            concern_revision=_require_int(payload, "concern_revision"),
            readiness_revision=_require_int(payload, "readiness_revision"),
        )


class OmittedSource(BaseModel):
    """One source the budget excluded, kept so the omission is auditable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    section: ContextSection
    source_kind: str
    source_id: str
    source_revision: str
    reason: str


class AssembledBundle(BaseModel):
    """One committed bundle and everything a worker needs to cite it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bundle_id: UUID
    client_id: UUID
    product_signal_id: UUID
    interview_session_id: UUID | None
    purpose: str
    context_revision: int
    cutoff_at: datetime
    digest: str
    dependencies: BundleDependencies
    included: tuple[ContextItem, ...]
    omitted: tuple[OmittedSource, ...]

    def section(self, section: ContextSection) -> tuple[ContextItem, ...]:
        """Return the included items of one section, in bundle order."""
        return tuple(item for item in self.included if item.section == section)

    def cited_evidence_ids(self) -> tuple[UUID, ...]:
        """Return the evidence records this bundle cited."""
        return tuple(
            UUID(item.source_id)
            for item in self.included
            if item.source_kind == "evidence_record"
        )


async def assemble_bundle(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    purpose: BundlePurpose,
    budget_items: int,
    observations: Sequence[ObservedActivity],
    extra_items: Sequence[ContextItem],
    recent_fragment_limit: int,
    prior_command_limit: int,
) -> AssembledBundle:
    """Collect, budget, and commit one context bundle in a single transaction."""
    async with database.transaction() as conn:
        dependencies = await current_dependencies(
            conn,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
        )
        offered = await _collect(
            conn,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            acceptance_criteria_revision=dependencies.acceptance_criteria_revision,
            observations=observations,
            extra_items=extra_items,
            recent_fragment_limit=recent_fragment_limit,
            prior_command_limit=prior_command_limit,
        )
        return await _commit(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            purpose=purpose,
            budget_items=budget_items,
            dependencies=dependencies,
            offered=offered,
        )


async def assemble_conversation_bundle(
    database: Database,
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    purpose: BundlePurpose,
    recent_fragment_limit: int,
) -> AssembledBundle:
    """Commit a bundle that holds only the recent conversation.

    A judgement about what was just said, such as which on-screen control a request
    means, needs those words and nothing else; the full working context would only
    make the judgement slower. The bundle pins the same revisions as any other, and
    its purpose names the ones its result depends on.
    """
    async with database.transaction() as conn:
        dependencies = await current_dependencies(
            conn,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
        )
        offered = await recent_conversation_items(
            conn, interview_session_id=interview_session_id, limit=recent_fragment_limit
        )
        return await _commit(
            conn,
            client_id=client_id,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
            purpose=purpose,
            budget_items=max(len(offered), 1),
            dependencies=dependencies,
            offered=offered,
        )


async def _commit(
    conn: AsyncConnection[DictRow],
    *,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    purpose: str,
    budget_items: int,
    dependencies: BundleDependencies,
    offered: Sequence[ContextItem],
) -> AssembledBundle:
    """Budget the offered items and write the bundle inside the caller's transaction."""
    included, omitted = _apply_budget(offered, budget_items=budget_items)
    cutoff_at = datetime.now(tz=UTC)
    context_revision = await _next_context_revision(
        conn, product_signal_id=product_signal_id, purpose=purpose
    )
    bundle_id = uuid7()
    digest = _digest(dependencies=dependencies, included=included, cutoff_at=cutoff_at)
    await _write(
        conn,
        bundle_id=bundle_id,
        client_id=client_id,
        product_signal_id=product_signal_id,
        interview_session_id=interview_session_id,
        purpose=purpose,
        context_revision=context_revision,
        cutoff_at=cutoff_at,
        budget_items=budget_items,
        dependencies=dependencies,
        digest=digest,
        included=included,
        omitted=omitted,
    )
    _log.info(
        "context_bundle_assembled",
        client_id=str(client_id),
        product_signal_id=str(product_signal_id),
        interview_session_id=str(interview_session_id),
        purpose=purpose,
        context_bundle_id=str(bundle_id),
        context_revision=context_revision,
        included_items=len(included),
        omitted_items=len(omitted),
        budget_items=budget_items,
    )
    return AssembledBundle(
        bundle_id=bundle_id,
        client_id=client_id,
        product_signal_id=product_signal_id,
        interview_session_id=interview_session_id,
        purpose=purpose,
        context_revision=context_revision,
        cutoff_at=cutoff_at,
        digest=digest,
        dependencies=dependencies,
        included=included,
        omitted=omitted,
    )


async def load_bundle(database: Database, *, bundle_id: UUID) -> AssembledBundle:
    """Read one committed bundle back, items in order."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select id, client_id, product_signal_id, interview_session_id, purpose,
                   context_revision, cutoff_at, budget_items, dependencies, digest
            from public.context_bundles
            where id = %s
            """,
            (bundle_id,),
        )
        header = await cur.fetchone()
        if header is None:
            raise LookupError(f"context bundle {bundle_id} does not exist")
        await cur.execute(
            """
            select section, source_kind, source_id, source_revision, included,
                   omission_reason, weight, body
            from public.context_bundle_items
            where context_bundle_id = %s
            order by ordinal
            """,
            (bundle_id,),
        )
        rows = list(await cur.fetchall())

    dependency_payload = header["dependencies"]
    if not isinstance(dependency_payload, Mapping):
        raise TypeError("context_bundles.dependencies is not an object")
    included: list[ContextItem] = []
    omitted: list[OmittedSource] = []
    for row in rows:
        body = row["body"]
        if not isinstance(body, Mapping):
            raise TypeError("context_bundle_items.body is not an object")
        if row["included"] is True:
            included.append(
                ContextItem(
                    section=str(row["section"]),
                    source_kind=str(row["source_kind"]),
                    source_id=str(row["source_id"]),
                    source_revision=str(row["source_revision"]),
                    weight=as_int(row, "weight"),
                    body=dict(body),
                )
            )
        else:
            omitted.append(
                OmittedSource(
                    section=str(row["section"]),
                    source_kind=str(row["source_kind"]),
                    source_id=str(row["source_id"]),
                    source_revision=str(row["source_revision"]),
                    reason=str(row["omission_reason"]),
                )
            )
    session_id = header["interview_session_id"]
    return AssembledBundle(
        bundle_id=as_uuid(header, "id"),
        client_id=as_uuid(header, "client_id"),
        product_signal_id=as_uuid(header, "product_signal_id"),
        interview_session_id=UUID(str(session_id)) if session_id is not None else None,
        purpose=str(header["purpose"]),
        context_revision=as_int(header, "context_revision"),
        cutoff_at=_require_datetime(header, "cutoff_at"),
        digest=str(header["digest"]),
        dependencies=BundleDependencies.from_payload(dependency_payload),
        included=tuple(included),
        omitted=tuple(omitted),
    )


async def _collect(
    conn: AsyncConnection[DictRow],
    *,
    product_signal_id: UUID,
    interview_session_id: UUID,
    acceptance_criteria_revision: int,
    observations: Sequence[ObservedActivity],
    extra_items: Sequence[ContextItem],
    recent_fragment_limit: int,
    prior_command_limit: int,
) -> tuple[ContextItem, ...]:
    """Gather every candidate item from durable records and supplied retrieval."""
    collected: list[ContextItem] = []
    collected.extend(
        await lifecycle_and_policy_items(
            conn,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
        )
    )
    collected.extend(await agreed_scope_items(conn, product_signal_id=product_signal_id))
    collected.extend(
        await acceptance_criteria_items(
            conn,
            product_signal_id=product_signal_id,
            criteria_revision=acceptance_criteria_revision,
        )
    )
    collected.extend(await contradiction_items(conn, product_signal_id=product_signal_id))
    collected.extend(await open_question_items(conn, product_signal_id=product_signal_id))
    collected.extend(
        await implementation_status_items(
            conn,
            product_signal_id=product_signal_id,
            interview_session_id=interview_session_id,
        )
    )
    collected.extend(
        await recent_conversation_items(
            conn,
            interview_session_id=interview_session_id,
            limit=recent_fragment_limit,
        )
    )
    collected.extend(observation_items(observations))
    collected.extend(extra_items)
    collected.extend(
        await prior_command_items(
            conn, interview_session_id=interview_session_id, limit=prior_command_limit
        )
    )
    return tuple(collected)


def _apply_budget(
    offered: Sequence[ContextItem], *, budget_items: int
) -> tuple[tuple[ContextItem, ...], tuple[OmittedSource, ...]]:
    """Fill the budget in section priority order, recording every omission.

    Within a section the earliest items fill first, except in the conversation: a
    budget that cannot hold all of it keeps the latest turns and omits the oldest.
    """
    if budget_items < 1:
        raise ValueError(f"budget_items must be at least 1, got {budget_items}")
    by_section: dict[ContextSection, list[ContextItem]] = {
        section: [] for section in SECTION_PRIORITY
    }
    for item in offered:
        if item.section not in by_section:
            raise ValueError(f"unknown context section {item.section!r}")
        by_section[item.section].append(item)

    included: list[ContextItem] = []
    omitted: list[OmittedSource] = []
    remaining = budget_items
    for section in SECTION_PRIORITY:
        items = by_section[section]
        dropped = max(len(items) - remaining, 0)
        if section == SECTION_RECENT_CONVERSATION and dropped > 0:
            items = [*items[dropped:], *items[:dropped]]
        for item in items:
            if remaining > 0:
                included.append(item)
                remaining -= 1
                continue
            omitted.append(
                OmittedSource(
                    section=item.section,
                    source_kind=item.source_kind,
                    source_id=item.source_id,
                    source_revision=item.source_revision,
                    reason=_BUDGET_EXCEEDED,
                )
            )
    return tuple(included), tuple(omitted)


async def current_dependencies(
    conn: AsyncConnection[DictRow],
    *,
    product_signal_id: UUID,
    interview_session_id: UUID,
) -> BundleDependencies:
    """Read the case's and session's current revisions, as a bundle pins them."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select revision, classification_revision, acceptance_criteria_revision,
                   concern_revision, pinned_configuration_release_id
            from public.product_signals
            where id = %s
            """,
            (product_signal_id,),
        )
        signal = await cur.fetchone()
        if signal is None:
            raise LookupError(f"product signal {product_signal_id} does not exist")
        await cur.execute(
            "select revision from public.interview_sessions where id = %s",
            (interview_session_id,),
        )
        session = await cur.fetchone()
        if session is None:
            raise LookupError(f"interview session {interview_session_id} does not exist")
        await cur.execute(
            """
            select readiness_revision from public.session_readiness
            where interview_session_id = %s
            """,
            (interview_session_id,),
        )
        readiness = await cur.fetchone()
    return BundleDependencies(
        product_signal_revision=as_int(signal, "revision"),
        interview_session_revision=as_int(session, "revision"),
        pinned_configuration_release_id=as_uuid(
            signal, "pinned_configuration_release_id"
        ),
        classification_revision=as_int(signal, "classification_revision"),
        acceptance_criteria_revision=as_int(signal, "acceptance_criteria_revision"),
        concern_revision=as_int(signal, "concern_revision"),
        readiness_revision=(
            as_int(readiness, "readiness_revision") if readiness is not None else 0
        ),
    )


async def _next_context_revision(
    conn: AsyncConnection[DictRow], *, product_signal_id: UUID, purpose: str
) -> int:
    """Return the next monotonic context revision for one signal and purpose."""
    async with conn.cursor() as cur:
        # Serialize allocation for this signal and purpose until the caller commits.
        # Concurrent bundles, such as the cursor judging every fragment of the
        # agent's speech, would otherwise read the same maximum and the loser would
        # surface as a constraint error rather than as the next revision.
        await cur.execute(
            "select pg_advisory_xact_lock(hashtext(%s))",
            (f"context_bundles:{product_signal_id}:{purpose}",),
        )
        await cur.execute(
            """
            select coalesce(max(context_revision), 0) + 1 as next_revision
            from public.context_bundles
            where product_signal_id = %s and purpose = %s
            """,
            (product_signal_id, purpose),
        )
        row = await cur.fetchone()
    if row is None:
        return 1
    return as_int(row, "next_revision")


def _digest(
    *,
    dependencies: BundleDependencies,
    included: Sequence[ContextItem],
    cutoff_at: datetime,
) -> str:
    """Return the provenance stamp a citing result repeats back."""
    canonical = json.dumps(
        {
            "cutoff_at": cutoff_at.isoformat(),
            "dependencies": dependencies.as_payload(),
            "items": [
                {
                    "section": item.section,
                    "source_kind": item.source_kind,
                    "source_id": item.source_id,
                    "source_revision": item.source_revision,
                }
                for item in included
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


async def _write(
    conn: AsyncConnection[DictRow],
    *,
    bundle_id: UUID,
    client_id: UUID,
    product_signal_id: UUID,
    interview_session_id: UUID,
    purpose: str,
    context_revision: int,
    cutoff_at: datetime,
    budget_items: int,
    dependencies: BundleDependencies,
    digest: str,
    included: Sequence[ContextItem],
    omitted: Sequence[OmittedSource],
) -> None:
    """Write the immutable bundle header and every considered source."""
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into public.context_bundles (
                id, client_id, product_signal_id, interview_session_id, purpose,
                context_revision, cutoff_at, budget_items, included_items,
                omitted_items, dependencies, digest
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                bundle_id,
                client_id,
                product_signal_id,
                interview_session_id,
                purpose,
                context_revision,
                cutoff_at,
                budget_items,
                len(included),
                len(omitted),
                Jsonb(dependencies.as_payload()),
                digest,
            ),
        )
        # One batched statement: psycopg pipelines `executemany`, so the items cost
        # one round trip rather than one each.
        await cur.executemany(
            _ITEM_INSERT,
            [
                *(
                    _item_parameters(
                        bundle_id=bundle_id,
                        client_id=client_id,
                        ordinal=ordinal,
                        section=item.section,
                        source_kind=item.source_kind,
                        source_id=item.source_id,
                        source_revision=item.source_revision,
                        included=True,
                        omission_reason=None,
                        weight=item.weight,
                        body=dict(item.body),
                    )
                    for ordinal, item in enumerate(included)
                ),
                *(
                    _item_parameters(
                        bundle_id=bundle_id,
                        client_id=client_id,
                        ordinal=len(included) + ordinal,
                        section=dropped.section,
                        source_kind=dropped.source_kind,
                        source_id=dropped.source_id,
                        source_revision=dropped.source_revision,
                        included=False,
                        omission_reason=dropped.reason,
                        weight=1,
                        body={},
                    )
                    for ordinal, dropped in enumerate(omitted)
                ),
            ],
        )


_ITEM_INSERT = """
    insert into public.context_bundle_items (
        id, client_id, context_bundle_id, ordinal, section, source_kind, source_id,
        source_revision, included, omission_reason, weight, body
    )
    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _item_parameters(
    *,
    bundle_id: UUID,
    client_id: UUID,
    ordinal: int,
    section: ContextSection,
    source_kind: str,
    source_id: str,
    source_revision: str,
    included: bool,
    omission_reason: str | None,
    weight: int,
    body: Mapping[str, JsonValue],
) -> tuple[object, ...]:
    """Build the parameter tuple for one bundle item row."""
    return (
        uuid7(),
        client_id,
        bundle_id,
        ordinal,
        section,
        source_kind,
        source_id,
        source_revision,
        included,
        omission_reason,
        weight,
        Jsonb(body),
    )


def _require_int(payload: Mapping[str, object], key: str) -> int:
    """Return an integer dependency value, refusing anything else."""
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"context bundle dependency {key!r} is not an integer")
    return value


def _require_datetime(row: Mapping[str, object], column: str) -> datetime:
    """Return a timestamptz column, refusing anything else."""
    value = row[column]
    if not isinstance(value, datetime):
        raise TypeError(f"column {column!r} is not a datetime")
    return value
