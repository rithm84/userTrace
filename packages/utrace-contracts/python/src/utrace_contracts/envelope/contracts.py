"""Public validators for the envelope area and its binding registry.

Each ``validate_<name>`` parses one untrusted decoded-JSON value into its model,
raising ``pydantic.ValidationError`` on any violation. The models enforce the
same rules as ``schemas/envelope/*.schema.json``; the conformance suite proves
the two agree on every fixture, in both languages.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import TypeAdapter

from utrace_contracts.base import JsonObject
from utrace_contracts.envelope.actors import Actor
from utrace_contracts.envelope.artifacts import ArtifactReference
from utrace_contracts.envelope.compatibility import CompatibilityManifest
from utrace_contracts.envelope.failures import Failure, ResultBody
from utrace_contracts.envelope.messages import Command, Envelope, Event, Result
from utrace_contracts.registry import ContractValidator

__all__ = [
    "AREA_CONTRACTS",
    "validate_actor",
    "validate_artifact_reference",
    "validate_command",
    "validate_compatibility_manifest",
    "validate_envelope",
    "validate_event",
    "validate_failure",
    "validate_result",
    "validate_result_body",
]

_ACTOR: TypeAdapter[Actor] = TypeAdapter(Actor)
_RESULT_BODY: TypeAdapter[ResultBody] = TypeAdapter(ResultBody)


def validate_envelope(data: object) -> Envelope[JsonObject]:
    """Validate the common envelope, whatever family it declares."""
    return Envelope[JsonObject].from_json(data)


def validate_event(data: object) -> Event:
    """Validate an Event: an immutable past-tense fact."""
    return Event.from_json(data)


def validate_command(data: object) -> Command:
    """Validate a Command and its required ``domain_ids.command_id``."""
    return Command.from_json(data)


def validate_result(data: object) -> Result:
    """Validate a Result, including the coherence of its terminal outcome."""
    return Result.from_json(data)


def validate_result_body(data: object) -> ResultBody:
    """Validate a Result body on its own: accepted, progress, or one terminal outcome."""
    return _RESULT_BODY.validate_python(data)


def validate_actor(data: object) -> Actor:
    """Validate an actor of any kind."""
    return _ACTOR.validate_python(data)


def validate_failure(data: object) -> Failure:
    """Validate a typed failure and its retry advice."""
    return Failure.from_json(data)


def validate_artifact_reference(data: object) -> ArtifactReference:
    """Validate an immutable artifact reference."""
    return ArtifactReference.from_json(data)


def validate_compatibility_manifest(data: object) -> CompatibilityManifest:
    """Validate a component's advertised compatibility manifest."""
    return CompatibilityManifest.from_json(data)


AREA_CONTRACTS: Mapping[str, ContractValidator] = {
    "envelope.v1": validate_envelope,
    "event.v1": validate_event,
    "command.v1": validate_command,
    "result.v1": validate_result,
    "result-body.v1": validate_result_body,
    "actor.v1": validate_actor,
    "failure.v1": validate_failure,
    "artifact-reference.v1": validate_artifact_reference,
    "compatibility-manifest.v1": validate_compatibility_manifest,
}
