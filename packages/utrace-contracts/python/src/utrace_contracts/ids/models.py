"""Identifier format and the enumerated first-class identifier names.

Every first-class uTrace entity has its own opaque UUIDv7. Identifiers travel as
lowercase canonical strings, which is also their wire form, so there is one
representation everywhere and no parse/format step that could disagree between
the two bindings.

Knowing an identifier grants no authority. Provider identifiers stay scoped
strings inside provider references, and W3C trace identity describes technical
execution only, so neither appears here.
"""

from __future__ import annotations

import sys
from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints

from utrace_contracts.base import StrictModel

if sys.version_info >= (3, 14):
    from uuid import uuid7 as _uuid7
else:  # Python 3.13 has no uuid.uuid7; the uuid6 package is the reference backport.
    from uuid6 import uuid7 as _uuid7

__all__ = ["UUID_V7_PATTERN", "DomainIds", "IdentifierName", "UuidV7", "new_id"]

UUID_V7_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
"""Canonical lowercase UUIDv7: version nibble 7, RFC 9562 variant bits."""

UuidV7 = Annotated[str, StringConstraints(pattern=UUID_V7_PATTERN)]
"""A UUIDv7 in lowercase canonical string form."""


def new_id() -> UuidV7:
    """Mint a fresh UUIDv7 in the canonical wire form."""
    return str(_uuid7())


class IdentifierName(StrEnum):
    """The closed set of first-class identifier names.

    Mirrors ``schemas/ids/identifier-name.v1.schema.json``; the conformance
    suite fails if the two ever diverge. A generic correlation identifier never
    substitutes for one of these, and relationships stay explicit.
    """

    CLIENT_ID = "client_id"
    UTRACE_USER_ID = "utrace_user_id"
    CLIENT_USER_ID = "client_user_id"
    CUSTOMER_ACCOUNT_ID = "customer_account_id"
    CUSTOMER_MEMBERSHIP_ID = "customer_membership_id"
    CLIENT_MEMBER_ID = "client_member_id"
    INTAKE_ITEM_ID = "intake_item_id"
    PRODUCT_SIGNAL_ID = "product_signal_id"
    OUTREACH_ATTEMPT_ID = "outreach_attempt_id"
    SESSION_ID = "session_id"
    PREVIEW_RUNTIME_ALLOCATION_ID = "preview_runtime_allocation_id"
    ENVIRONMENT_LEASE_ID = "environment_lease_id"
    IMPLEMENTATION_ATTEMPT_ID = "implementation_attempt_id"
    PREVIEW_VERSION_ID = "preview_version_id"
    VALIDATION_ATTEMPT_ID = "validation_attempt_id"
    CASE_FILE_ID = "case_file_id"
    DELIVERY_ID = "delivery_id"
    JOB_ID = "job_id"
    JOB_RUN_ID = "job_run_id"
    COMMAND_ID = "command_id"
    EVIDENCE_WINDOW_ID = "evidence_window_id"
    ARTIFACT_ID = "artifact_id"
    INGESTION_RECEIPT_ID = "ingestion_receipt_id"
    NORMALIZATION_RUN_ID = "normalization_run_id"
    OBSERVATION_ID = "observation_id"
    BROWSER_STREAM_ID = "browser_stream_id"
    SERVER_STREAM_ID = "server_stream_id"
    EXTENSION_BINDING_ID = "extension_binding_id"
    DOCUMENT_ATTACHMENT_ID = "document_attachment_id"
    PREVIEW_ENVIRONMENT_ID = "preview_environment_id"
    RUNTIME_ID = "runtime_id"
    VOICE_RUN_ID = "voice_run_id"


class DomainIds(StrictModel):
    """The typed domain identifiers a message carries.

    Every name is optional: a message states only the relationships it actually
    establishes. The envelope's client scope, not this object, is what authorizes
    tenant access; ``client_id`` appears here only for records whose subject is a
    client other than the scoped one.
    """

    client_id: UuidV7 | None = None
    utrace_user_id: UuidV7 | None = None
    client_user_id: UuidV7 | None = None
    customer_account_id: UuidV7 | None = None
    customer_membership_id: UuidV7 | None = None
    client_member_id: UuidV7 | None = None
    intake_item_id: UuidV7 | None = None
    product_signal_id: UuidV7 | None = None
    outreach_attempt_id: UuidV7 | None = None
    session_id: UuidV7 | None = None
    preview_runtime_allocation_id: UuidV7 | None = None
    environment_lease_id: UuidV7 | None = None
    implementation_attempt_id: UuidV7 | None = None
    preview_version_id: UuidV7 | None = None
    validation_attempt_id: UuidV7 | None = None
    case_file_id: UuidV7 | None = None
    delivery_id: UuidV7 | None = None
    job_id: UuidV7 | None = None
    job_run_id: UuidV7 | None = None
    command_id: UuidV7 | None = None
    evidence_window_id: UuidV7 | None = None
    artifact_id: UuidV7 | None = None
    ingestion_receipt_id: UuidV7 | None = None
    normalization_run_id: UuidV7 | None = None
    observation_id: UuidV7 | None = None
    browser_stream_id: UuidV7 | None = None
    server_stream_id: UuidV7 | None = None
    extension_binding_id: UuidV7 | None = None
    document_attachment_id: UuidV7 | None = None
    preview_environment_id: UuidV7 | None = None
    runtime_id: UuidV7 | None = None
    voice_run_id: UuidV7 | None = None
