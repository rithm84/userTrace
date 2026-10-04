"""Staff intake projections and operator re-ingestion.

The projections are read-only views of what the Intake Router observed and
decided. Nothing here edits a routing outcome or re-runs one: routing stays in
its durable Job.

One write exists, and only for recovery. The watched-channel consumer records
what the Discord gateway delivers while it is connected; a message posted while
the worker was down or restarting reaches no one. `ingest_watched_channel_message`
re-reads one such message through the connector and puts it through the exact
path the consumer uses, so a gap is closed by re-reading the Customer Surface
rather than by inventing a record.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path
from pydantic import BaseModel, ConfigDict, Field

from app.auth.dependencies import CurrentDatabase, StaffOfPath
from app.clients.configuration import load_active_release
from app.db.pool import Database
from app.db.rows import as_int, as_optional_str, as_str, as_uuid
from app.errors import AggregateNotFoundError, AuthorizationError
from app.intake.errors import UnattributedAuthorError, UnwatchedSurfaceError
from app.intake.messages import DebounceWindow, record_message, seal_batch
from app.intake.settings import get_intake_settings
from app.intake.worker import connector_for_release
from app.logging import get_logger

__all__ = [
    "IngestMessageRequest",
    "IngestedMessage",
    "IntakeItemDetail",
    "IntakeItemListResponse",
    "IntakeItemSummary",
    "router",
]

_log = get_logger(__name__)

router = APIRouter(prefix="/v1/clients", tags=["staff"])

_LIST_LIMIT = 100


class IntakeItemSummary(BaseModel):
    """One observed batch and the outcome routing committed for it."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    state: str
    source_channel_ref: str
    author_provider_identity: str
    routing_outcome: str | None
    product_signal_id: UUID | None
    blocked_reason: str | None
    created_at: str


class IntakeItemListResponse(BaseModel):
    """Recent Intake Items for one client, newest first."""

    model_config = ConfigDict(extra="forbid")

    intake_items: list[IntakeItemSummary]


class RoutingDecisionRecord(BaseModel):
    """One immutable routing evaluation, including the ones that blocked."""

    model_config = ConfigDict(extra="forbid")

    attempt_index: int
    outcome: str | None
    blocked_reason: str | None
    decision_model_id: str
    assistance_reason: str | None
    product_overview_version_id: UUID
    decided_at: str


class IntakeItemDetail(BaseModel):
    """One Intake Item with every routing evaluation it went through."""

    model_config = ConfigDict(extra="forbid")

    intake_item: IntakeItemSummary
    routing_decisions: list[RoutingDecisionRecord]


def _summary(row: dict[str, object]) -> IntakeItemSummary:
    """Build one Intake Item summary from its projection row."""
    return IntakeItemSummary(
        id=as_uuid(row, "id"),
        state=as_str(row, "state"),
        source_channel_ref=as_str(row, "source_channel_ref"),
        author_provider_identity=as_str(row, "author_provider_identity"),
        routing_outcome=as_optional_str(row, "routing_outcome"),
        product_signal_id=(
            as_uuid(row, "product_signal_id")
            if row["product_signal_id"] is not None
            else None
        ),
        blocked_reason=as_optional_str(row, "blocked_reason"),
        created_at=str(row["created_at"]),
    )


_SELECT_ITEM = """
select id, state, source_channel_ref, author_provider_identity, routing_outcome,
       product_signal_id, blocked_reason, created_at
from public.intake_items
"""


@router.get("/{client_id}/intake-items", response_model=IntakeItemListResponse)
async def list_intake_items(
    staff: StaffOfPath, database: CurrentDatabase
) -> IntakeItemListResponse:
    """Return the client's recent Intake Items and their committed outcomes."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"{_SELECT_ITEM} where client_id = %s order by created_at desc limit %s",
            (staff.client_id, _LIST_LIMIT),
        )
        rows = await cur.fetchall()
    return IntakeItemListResponse(intake_items=[_summary(dict(row)) for row in rows])


@router.get(
    "/{client_id}/intake-items/{intake_item_id}", response_model=IntakeItemDetail
)
async def get_intake_item(
    staff: StaffOfPath,
    database: CurrentDatabase,
    intake_item_id: Annotated[UUID, Path()],
) -> IntakeItemDetail:
    """Return one Intake Item with its routing decision history."""
    item = await _load_item(
        database, client_id=staff.client_id, intake_item_id=intake_item_id
    )
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            """
            select attempt_index, outcome, blocked_reason, decision_model_id,
                   assistance_reason, product_overview_version_id, decided_at
            from public.routing_decisions
            where client_id = %s and intake_item_id = %s
            order by attempt_index
            """,
            (staff.client_id, intake_item_id),
        )
        rows = await cur.fetchall()
    return IntakeItemDetail(
        intake_item=item,
        routing_decisions=[
            RoutingDecisionRecord(
                attempt_index=as_int(row, "attempt_index"),
                outcome=as_optional_str(row, "outcome"),
                blocked_reason=as_optional_str(row, "blocked_reason"),
                decision_model_id=as_str(row, "decision_model_id"),
                assistance_reason=as_optional_str(row, "assistance_reason"),
                product_overview_version_id=as_uuid(
                    row, "product_overview_version_id"
                ),
                decided_at=str(row["decided_at"]),
            )
            for row in rows
        ],
    )


async def _load_item(
    database: Database, *, client_id: UUID, intake_item_id: UUID
) -> IntakeItemSummary:
    """Load one Intake Item inside the caller's tenant scope."""
    async with database.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            f"{_SELECT_ITEM} where client_id = %s and id = %s",
            (client_id, intake_item_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise AggregateNotFoundError(table="intake_items", row_id=intake_item_id)
    return _summary(dict(row))


class IngestMessageRequest(BaseModel):
    """One watched-channel message an operator asks uTrace to re-read."""

    model_config = ConfigDict(extra="forbid")

    channel_ref: str = Field(min_length=1)
    provider_message_id: str = Field(min_length=1)


class IngestedMessage(BaseModel):
    """The Intake Item identity one re-ingested message belongs to."""

    model_config = ConfigDict(extra="forbid")

    intake_item_id: UUID
    intake_batch_id: UUID
    provider_message_id: str
    duplicate: bool


@router.post("/{client_id}/intake/messages", response_model=IngestedMessage)
async def ingest_watched_channel_message(
    staff: StaffOfPath, database: CurrentDatabase, body: IngestMessageRequest
) -> IngestedMessage:
    """Re-read one watched-channel message and put it through intake.

    This is operator recovery for a message the consumer never saw, such as one
    posted while the worker was restarting. The message is read back from
    Discord, so its content, author, and thread references are the provider's,
    not the caller's.

    Three refusals apply. A channel the client's active release does not watch is
    refused before Discord is called, so the route cannot read a channel that is
    not an intake surface. A bot-authored message — uTrace's own posts included —
    is refused unless the client explicitly attributed that provider identity to
    a reporting client user, which is the only way a bot post becomes a customer
    report. A message whose author maps to no client-scoped identity is refused
    by the provider map.

    Re-ingesting the same `provider_message_id` is idempotent: the duplicate
    collapses onto the batch the original joined and returns its Intake Item.
    Sealing the batch here rather than waiting for its seal Job is what makes the
    Intake Item identity available to answer with; a batch still open for that
    author and channel therefore seals now.
    """
    settings = get_intake_settings()
    release = await load_active_release(database, client_id=staff.client_id)
    if not release.policy.customer_surface.watches(body.channel_ref):
        raise AuthorizationError(
            subject=f"channel:{body.channel_ref}",
            reason=(
                f"channel {body.channel_ref} is not a watched intake surface for "
                f"client {staff.client_id}"
            ),
        )

    connector = connector_for_release(settings, release=release)
    await connector.open()
    try:
        receipt = await connector.fetch_message(
            channel_id=body.channel_ref, message_id=body.provider_message_id
        )
    finally:
        await connector.close()

    try:
        recorded = await record_message(
            database,
            client_id=staff.client_id,
            release=release,
            receipt=receipt,
            window=DebounceWindow(
                quiet_seconds=settings.debounce_quiet_seconds,
                maximum_seconds=settings.debounce_maximum_seconds,
            ),
        )
    except (UnattributedAuthorError, UnwatchedSurfaceError) as refusal:
        raise AuthorizationError(
            subject=f"provider_identity:{receipt.author_id}", reason=str(refusal)
        ) from refusal
    sealed = await seal_batch(database, intake_batch_id=recorded.intake_batch_id)
    _log.info(
        "intake_message_reingested",
        client_id=str(staff.client_id),
        client_member_id=str(staff.client_member_id),
        channel_ref=body.channel_ref,
        provider_message_id=body.provider_message_id,
        intake_item_id=str(sealed.intake_item_id),
        duplicate=recorded.duplicate,
    )
    return IngestedMessage(
        intake_item_id=sealed.intake_item_id,
        intake_batch_id=recorded.intake_batch_id,
        provider_message_id=body.provider_message_id,
        duplicate=recorded.duplicate,
    )
