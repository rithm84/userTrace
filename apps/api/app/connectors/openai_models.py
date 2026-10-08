"""OpenAI reasoning models: the pinned configuration, its prices, and Responses API requests.

The roles a waiting participant feels run OpenAI models, because their speed is what
the participant notices: answers to GPT-Live's delegations during the conversation,
and, between the participant's agreement and the preview they validate, code
investigation, the Implementation Gate proposal, and implementation. Each role pins a
model, a reasoning effort, and a service tier: `fast` buys priority processing, and
`ultrafast` is the fastest tier, available for GPT-6 Astra. The coding roles run the
model inside Codex in the workspace sandbox (`app.implementation.agent_author`); the
gate proposal is one structured request and a delegation is a function-calling run,
both through `OpenAIResponsesGateway`.

Nothing is stored at OpenAI (`store=False`): a function-calling run resends its earlier
output on each turn, with the reasoning carried as encrypted content.

The gateway disables the SDK's own retries and maps every error onto a typed
`ConnectorFailure`; the Job that called it owns retrying.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import TracebackType
from typing import Final, Self, cast

import structlog
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from openai.types.responses import (
    EasyInputMessageParam,
    FunctionToolParam,
    Response,
    ResponseFunctionToolCall,
    ResponseInputParam,
)
from pydantic import BaseModel, ConfigDict

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    RetryAdvice,
    TechnicalCategory,
    default_retry_advice,
    redacted_parameters,
    technical_category_for_status,
)

__all__ = [
    "GPT_6_1_SOL",
    "GPT_6_ASTRA",
    "FunctionCall",
    "OpenAIEffort",
    "OpenAIModelConfiguration",
    "OpenAIResponsesGateway",
    "OpenAIServiceTier",
    "OpenAIToolRun",
    "TokenUsage",
    "ToolTurn",
    "cost_usd",
]

_logger = structlog.get_logger(__name__)
_PROVIDER: Final[str] = "openai"

GPT_6_ASTRA: Final[str] = "gpt-6-astra"
GPT_6_1_SOL: Final[str] = "gpt-6.1-sol"


class OpenAIEffort(StrEnum):
    """How much the model reasons. Pinned per role, never left to the default."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class OpenAIServiceTier(StrEnum):
    """The Responses API `service_tier` a role requests."""

    DEFAULT = "default"
    FAST = "fast"
    ULTRAFAST = "ultrafast"


class OpenAIModelConfiguration(BaseModel):
    """The pinned OpenAI configuration a Job records and replays against."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    effort: OpenAIEffort
    service_tier: OpenAIServiceTier
    credential_reference: str
    """Opaque reference to the API key in custody, never the key itself."""


class TokenUsage(BaseModel):
    """Token counts the Responses API reports; input includes the cached and cache-write parts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class _Prices:
    """US dollars per million tokens."""

    input: float
    cached_input: float
    cache_write: float
    output: float


_PRICES: Final[dict[tuple[str, OpenAIServiceTier], _Prices]] = {
    (GPT_6_ASTRA, OpenAIServiceTier.DEFAULT): _Prices(10.0, 1.0, 12.5, 50.0),
    (GPT_6_ASTRA, OpenAIServiceTier.FAST): _Prices(20.0, 2.0, 25.0, 100.0),
    # OpenAI lists Ultrafast input, cached input, and output at 6x Standard; cache
    # writes are charged at the same multiple.
    (GPT_6_ASTRA, OpenAIServiceTier.ULTRAFAST): _Prices(60.0, 6.0, 75.0, 300.0),
    (GPT_6_1_SOL, OpenAIServiceTier.DEFAULT): _Prices(2.0, 0.10, 2.50, 10.0),
    (GPT_6_1_SOL, OpenAIServiceTier.FAST): _Prices(4.0, 0.20, 5.0, 20.0),
}


def cost_usd(configuration: OpenAIModelConfiguration, usage: TokenUsage) -> float:
    """What one run's tokens cost under its configuration's model and tier."""
    prices = _PRICES.get((configuration.model_id, configuration.service_tier))
    if prices is None:
        raise KeyError(
            f"no price is pinned for {configuration.model_id} at the "
            f"{configuration.service_tier.value} tier; add it before pinning this configuration"
        )
    uncached = usage.input_tokens - usage.cached_input_tokens - usage.cache_write_input_tokens
    return (
        uncached * prices.input
        + usage.cached_input_tokens * prices.cached_input
        + usage.cache_write_input_tokens * prices.cache_write
        + usage.output_tokens * prices.output
    ) / 1_000_000


class OpenAIResponsesGateway:
    """One role's structured-output requests to the Responses API."""

    def __init__(
        self,
        *,
        configuration: OpenAIModelConfiguration,
        api_key: str,
        request_timeout_seconds: float,
    ) -> None:
        self._configuration = configuration
        self._client = AsyncOpenAI(
            api_key=api_key, max_retries=0, timeout=request_timeout_seconds
        )

    async def __aenter__(self) -> Self:
        """Enter the gateway's HTTP client scope."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the HTTP client."""
        await self._client.close()

    @property
    def configuration(self) -> OpenAIModelConfiguration:
        """The pinned configuration this gateway requests under."""
        return self._configuration

    async def respond_with_schema[SchemaT: BaseModel](
        self,
        *,
        instructions: str,
        input_messages: Sequence[EasyInputMessageParam],
        schema: type[SchemaT],
        max_output_tokens: int,
    ) -> SchemaT:
        """Run one structured-output request and return the validated model.

        A response that ends without a parsed object is a contract violation, not an
        empty result.
        """
        configuration = self._configuration
        parameters = redacted_parameters(
            {
                "model": configuration.model_id,
                "effort": configuration.effort.value,
                "service_tier": configuration.service_tier.value,
                "schema": schema.__name__,
                "max_output_tokens": max_output_tokens,
            }
        )
        request_input: ResponseInputParam = [*input_messages]
        try:
            response = await self._client.responses.parse(
                model=configuration.model_id,
                instructions=instructions,
                input=request_input,
                text_format=schema,
                reasoning={"effort": configuration.effort.value},
                service_tier=configuration.service_tier.value,
                max_output_tokens=max_output_tokens,
                store=False,
            )
        except (APIStatusError, APITimeoutError, APIConnectionError) as error:
            raise _request_failure(
                error, operation="respond_with_schema", parameters=parameters
            ) from error

        parsed = response.output_parsed
        if response.status != "completed" or parsed is None:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="respond_with_schema",
                    category=TechnicalCategory.PROVIDER_REJECTED,
                    retry_advice=RetryAdvice.NEVER,
                    effect_state=EffectState.COMPLETED,
                    message=(
                        f"response {response.id} ended {response.status} without a parsed "
                        f"{schema.__name__} ({response.incomplete_details})"
                    ),
                    request_parameters=parameters,
                )
            )
        usage = response.usage
        _logger.info(
            "openai.responses.structured",
            model=configuration.model_id,
            effort=configuration.effort.value,
            requested_tier=configuration.service_tier.value,
            served_tier=response.service_tier,
            response_id=response.id,
            schema=schema.__name__,
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
        )
        return parsed

    def begin_tool_run(
        self,
        *,
        instructions: str,
        tools: Sequence[FunctionToolParam],
        max_output_tokens: int,
        cache_key: str,
    ) -> OpenAIToolRun:
        """Open one function-calling run under this gateway's pinned configuration.

        Runs that share a `cache_key` share their instructions, tools, and often much
        of their context, so OpenAI routes them to the same prompt cache.
        """
        return OpenAIToolRun(
            client=self._client,
            configuration=self._configuration,
            instructions=instructions,
            tools=tools,
            max_output_tokens=max_output_tokens,
            cache_key=cache_key,
        )


@dataclass(frozen=True, slots=True)
class FunctionCall:
    """One application-executed function call the model requested."""

    call_id: str
    name: str
    arguments: Mapping[str, object]
    """The arguments exactly as the model produced them; the caller validates them."""


@dataclass(frozen=True, slots=True)
class ToolTurn:
    """One turn of a function-calling run."""

    response_id: str
    output_text: str
    """The assistant text of this turn; empty while the model is calling functions."""
    pending_calls: tuple[FunctionCall, ...]

    def is_complete(self) -> bool:
        """True when the model asked for no further application-executed work."""
        return len(self.pending_calls) == 0


class OpenAIToolRun:
    """One multi-turn function-calling exchange on the Responses API.

    Every turn sends the whole exchange so far, because nothing is stored at OpenAI:
    the model's earlier output items, its encrypted reasoning, and each function
    result in turn.
    """

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        configuration: OpenAIModelConfiguration,
        instructions: str,
        tools: Sequence[FunctionToolParam],
        max_output_tokens: int,
        cache_key: str,
    ) -> None:
        self._client = client
        self._cache_key = cache_key
        self._configuration = configuration
        self._instructions = instructions
        self._tools = list(tools)
        self._max_output_tokens = max_output_tokens
        self._input: ResponseInputParam = []
        self._pending: frozenset[str] = frozenset()

    async def start(self, *, prompt: str) -> ToolTurn:
        """Send the opening prompt and return the model's first turn."""
        self._input.append({"role": "user", "content": prompt})
        return await self._turn()

    async def submit_tool_results(self, *, results: Mapping[str, str]) -> ToolTurn:
        """Return each function's output to the model and read its next turn.

        `results` must answer exactly the pending calls; a missing or unknown call id
        is refused rather than silently dropped.
        """
        if set(results) != self._pending:
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="tool_run_submit",
                    category=TechnicalCategory.PROVIDER_REJECTED,
                    retry_advice=RetryAdvice.NEVER,
                    effect_state=EffectState.ABSENT,
                    message=(
                        "tool results must answer exactly the pending calls: expected "
                        f"{sorted(self._pending)}, got {sorted(results)}"
                    ),
                )
            )
        for call_id, output in results.items():
            self._input.append(
                {"type": "function_call_output", "call_id": call_id, "output": output}
            )
        return await self._turn()

    async def _turn(self) -> ToolTurn:
        configuration = self._configuration
        parameters = redacted_parameters(
            {
                "model": configuration.model_id,
                "effort": configuration.effort.value,
                "service_tier": configuration.service_tier.value,
                "tools": ",".join(tool["name"] for tool in self._tools),
                "turn_items": str(len(self._input)),
            }
        )
        try:
            response = await self._client.responses.create(
                model=configuration.model_id,
                instructions=self._instructions,
                input=self._input,
                tools=self._tools,
                reasoning={"effort": configuration.effort.value},
                service_tier=configuration.service_tier.value,
                max_output_tokens=self._max_output_tokens,
                include=["reasoning.encrypted_content"],
                prompt_cache_key=self._cache_key,
                store=False,
            )
        except (APIStatusError, APITimeoutError, APIConnectionError) as error:
            raise _request_failure(error, operation="tool_turn", parameters=parameters) from error
        if response.status != "completed":
            raise ConnectorFailure(
                FailureDetail(
                    provider=_PROVIDER,
                    operation="tool_turn",
                    category=TechnicalCategory.PROVIDER_REJECTED,
                    retry_advice=RetryAdvice.NEVER,
                    effect_state=EffectState.COMPLETED,
                    message=(
                        f"response {response.id} ended {response.status} "
                        f"({response.incomplete_details})"
                    ),
                    request_parameters=parameters,
                )
            )
        # The output items go back as the next turn's input. They are the SDK's own
        # response objects, which the Responses API accepts verbatim as input items.
        self._input.extend(cast(ResponseInputParam, [item.to_dict() for item in response.output]))
        calls = tuple(
            _function_call(item, parameters=parameters)
            for item in response.output
            if isinstance(item, ResponseFunctionToolCall)
        )
        self._pending = frozenset(call.call_id for call in calls)
        _log_turn(configuration, response=response, calls=len(calls))
        return ToolTurn(
            response_id=response.id, output_text=response.output_text, pending_calls=calls
        )


def _function_call(
    item: ResponseFunctionToolCall, *, parameters: Mapping[str, str]
) -> FunctionCall:
    """Decode one function call's JSON arguments; malformed JSON is a contract violation."""
    try:
        arguments = json.loads(item.arguments)
    except json.JSONDecodeError as error:
        raise _malformed_call(item, parameters=parameters, reason=str(error)) from error
    if not isinstance(arguments, dict):
        raise _malformed_call(item, parameters=parameters, reason="arguments are not an object")
    return FunctionCall(call_id=item.call_id, name=item.name, arguments=arguments)


def _malformed_call(
    item: ResponseFunctionToolCall, *, parameters: Mapping[str, str], reason: str
) -> ConnectorFailure:
    return ConnectorFailure(
        FailureDetail(
            provider=_PROVIDER,
            operation="tool_turn",
            category=TechnicalCategory.VALIDATION,
            retry_advice=RetryAdvice.NEVER,
            effect_state=EffectState.COMPLETED,
            message=f"function call {item.call_id} to {item.name} has unusable arguments: {reason}",
            request_parameters=dict(parameters),
        )
    )


def _log_turn(configuration: OpenAIModelConfiguration, *, response: Response, calls: int) -> None:
    usage = response.usage
    _logger.info(
        "openai.responses.tool_turn",
        model=configuration.model_id,
        effort=configuration.effort.value,
        requested_tier=configuration.service_tier.value,
        served_tier=response.service_tier,
        response_id=response.id,
        function_calls=calls,
        input_tokens=usage.input_tokens if usage is not None else None,
        output_tokens=usage.output_tokens if usage is not None else None,
    )


def _request_failure(
    error: APIStatusError | APITimeoutError | APIConnectionError,
    *,
    operation: str,
    parameters: Mapping[str, str],
) -> ConnectorFailure:
    """Map one Responses API error onto a typed failure."""
    if isinstance(error, APIStatusError):
        category = technical_category_for_status(error.status_code)
        return ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation=operation,
                category=category,
                retry_advice=default_retry_advice(category),
                effect_state=EffectState.ABSENT,
                message=f"the Responses API refused the request (x-request-id {error.request_id})",
                request_parameters=dict(parameters),
                status_code=error.status_code,
                provider_error_code=error.code,
                response_body=error.response.text[:2000],
            )
        )
    return ConnectorFailure(
        FailureDetail(
            provider=_PROVIDER,
            operation=operation,
            category=(
                TechnicalCategory.TIMEOUT
                if isinstance(error, APITimeoutError)
                else TechnicalCategory.PROVIDER_UNAVAILABLE
            ),
            retry_advice=RetryAdvice.AFTER_DELAY,
            effect_state=EffectState.INDETERMINATE,
            message=f"the Responses API request failed before a verdict: {error}",
            request_parameters=dict(parameters),
        )
    )
