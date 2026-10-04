"""The authenticated dashboard's read projections.

[Client outputs](../../../docs/arch-components/client-outputs.md#full-client-dashboard)
gives the dashboard authorized projections and configuration status, never model
reasoning, raw telemetry, or lifecycle-editing authority. These are the reads the
kernel's durable records do not already answer: the established concern, the
audited client stop, delivery identities, and the
resolution of the opaque client-scoped references a published Case File carries.
The per-case reads live in [one case's run](./dashboard_case.py).

Every statement is tenant-scoped in SQL as well as by the caller's resolved
membership, so a missing predicate cannot leak another client's rows.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from utrace_contracts.domain.lifecycle import DeliveryState

from app.db.pool import Database
from app.db.rows import as_datetime, as_int, as_optional_str, as_str, as_uuid
from app.errors import ReferenceFormatError
from app.queries import fetch_all, fetch_one

__all__ = [
    "REFERENCE_KIND_CLIENT_USER",
    "REFERENCE_KIND_CUSTOMER_ACCOUNT",
    "CaseConcern",
    "CaseStopRecord",
    "DeliverySummary",
    "ResolvedReference",
    "load_case_concerns",
    "load_case_stop",
    "load_deliveries",
    "load_member_emails",
    "resolve_references",
]


class _ReadModel(BaseModel):
    """Frozen, closed shape for a projection the dashboard reads."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class CaseConcern(_ReadModel):
    """The established pain point and validation criterion of one case."""

    concern_revision: int
    origin: str
    pain_point: str
    validation_criterion: str
    established_at: datetime


class CaseStopRecord(_ReadModel):
    """The audited client stop recorded against one case."""

    recorded_at: datetime
    outcome: str
    reason: str
    client_member_id: UUID
    staff_role: str


class DeliverySummary(_ReadModel):
    """The delivery identities a list or a case page shows without a second read."""

    delivery_id: UUID
    case_file_id: UUID
    product_signal_id: UUID
    state: DeliveryState
    repository_ref: str
    pull_request_ref: str | None
    blocker_reason: str | None


class ResolvedReference(_ReadModel):
    """One opaque client-scoped reference and the label the dashboard may show.

    ``label`` is ``None`` when uTrace holds no recorded name. The dashboard then
    shows the reference itself; it never invents one.
    """

    ref: str
    kind: str
    id: UUID
    label: str | None


REFERENCE_KIND_CLIENT_USER = "client_user"
REFERENCE_KIND_CUSTOMER_ACCOUNT = "customer_account"


async def load_member_emails(
    database: Database, *, client_id: UUID
) -> dict[UUID, str]:
    """Return the sign-in email of each staff member, from Supabase Auth."""
    rows = await fetch_all(
        database,
        """
        select members.id, identities.email
        from public.client_members as members
        join auth.users as identities on identities.id = members.auth_user_id
        where members.client_id = %s and identities.email is not null
        """,
        (client_id,),
    )
    return {as_uuid(row, "id"): as_str(row, "email") for row in rows}


async def load_case_concerns(
    database: Database, *, client_id: UUID
) -> dict[UUID, CaseConcern]:
    """Return the current concern revision of every case of one client."""
    rows = await fetch_all(
        database,
        """
        select distinct on (concerns.product_signal_id)
               concerns.product_signal_id, concerns.concern_revision,
               concerns.origin, concerns.pain_point, concerns.validation_criterion,
               concerns.established_at
        from public.product_signal_concerns as concerns
        where concerns.client_id = %s
        order by concerns.product_signal_id, concerns.concern_revision desc
        """,
        (client_id,),
    )
    return {
        as_uuid(row, "product_signal_id"): CaseConcern(
            concern_revision=as_int(row, "concern_revision"),
            origin=as_str(row, "origin"),
            pain_point=as_str(row, "pain_point"),
            validation_criterion=as_str(row, "validation_criterion"),
            established_at=as_datetime(row, "established_at"),
        )
        for row in rows
    }


async def load_case_stop(
    database: Database, *, client_id: UUID, product_signal_id: UUID
) -> CaseStopRecord | None:
    """Return the audited client stop of one case, if a staff member invoked it."""
    row = await fetch_one(
        database,
        """
        select recorded_at, outcome, actor_ref, detail
        from public.audit_records
        where client_id = %s and subject_kind = 'product_signal'
          and subject_id = %s and action = 'client_case_stop'
        order by recorded_at desc
        limit 1
        """,
        (client_id, product_signal_id),
    )
    if row is None:
        return None
    detail = row["detail"]
    if not isinstance(detail, Mapping):
        raise TypeError("audit_records.detail is not an object")
    actor_ref = as_optional_str(row, "actor_ref")
    if actor_ref is None:
        raise TypeError("a client case stop was recorded without its staff actor")
    return CaseStopRecord(
        recorded_at=as_datetime(row, "recorded_at"),
        outcome=as_str(row, "outcome"),
        reason=str(detail["reason"]),
        client_member_id=UUID(actor_ref),
        staff_role=str(detail["staff_role"]),
    )


async def load_deliveries(
    database: Database, *, client_id: UUID
) -> dict[UUID, DeliverySummary]:
    """Return every delivery of one client, keyed by its Case File."""
    rows = await fetch_all(
        database,
        """
        select id, case_file_id, product_signal_id, state, repository_ref,
               pull_request_ref, blocker_reason
        from public.deliveries
        where client_id = %s
        """,
        (client_id,),
    )
    return {
        as_uuid(row, "case_file_id"): DeliverySummary(
            delivery_id=as_uuid(row, "id"),
            case_file_id=as_uuid(row, "case_file_id"),
            product_signal_id=as_uuid(row, "product_signal_id"),
            state=DeliveryState(as_str(row, "state")),
            repository_ref=as_str(row, "repository_ref"),
            pull_request_ref=as_optional_str(row, "pull_request_ref"),
            blocker_reason=as_optional_str(row, "blocker_reason"),
        )
        for row in rows
    }


async def resolve_references(
    database: Database, *, client_id: UUID, refs: Sequence[str]
) -> list[ResolvedReference]:
    """Resolve opaque client-scoped references to the labels staff may see.

    The published Case File core carries `client_user:<uuid>` and
    `customer_account:<uuid>` references rather than names, and the authorized
    dashboard resolves them. A reference this client does not own resolves to no
    label instead of reaching another tenant's rows.
    """
    parsed: list[tuple[str, str, UUID]] = []
    for ref in dict.fromkeys(refs):
        kind, separator, identifier = ref.partition(":")
        if not separator or kind not in {
            REFERENCE_KIND_CLIENT_USER,
            REFERENCE_KIND_CUSTOMER_ACCOUNT,
        }:
            raise ReferenceFormatError(ref=ref)
        try:
            parsed.append((ref, kind, UUID(identifier)))
        except ValueError as error:
            raise ReferenceFormatError(ref=ref) from error

    users = [item[2] for item in parsed if item[1] == REFERENCE_KIND_CLIENT_USER]
    accounts = [
        item[2] for item in parsed if item[1] == REFERENCE_KIND_CUSTOMER_ACCOUNT
    ]
    labels: dict[tuple[str, UUID], str] = {}
    if users:
        rows = await fetch_all(
            database,
            """
            select client_users.id, identities.display_name
            from public.client_users
            join public.users as identities
              on identities.id = client_users.utrace_user_id
            where client_users.client_id = %s and client_users.id = any(%s)
            """,
            (client_id, users),
        )
        for row in rows:
            name = as_optional_str(row, "display_name")
            if name is not None:
                labels[(REFERENCE_KIND_CLIENT_USER, as_uuid(row, "id"))] = name
    if accounts:
        rows = await fetch_all(
            database,
            "select id, name from public.customer_accounts "
            "where client_id = %s and id = any(%s)",
            (client_id, accounts),
        )
        for row in rows:
            labels[(REFERENCE_KIND_CUSTOMER_ACCOUNT, as_uuid(row, "id"))] = as_str(
                row, "name"
            )

    return [
        ResolvedReference(
            ref=ref, kind=kind, id=identifier, label=labels.get((kind, identifier))
        )
        for ref, kind, identifier in parsed
    ]
