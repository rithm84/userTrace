"""Jev decisions through Vercel AI Gateway.

Jev is TypeSafe's System One model; uTrace reaches it through
[Vercel AI Gateway](https://vercel.com/docs/ai-gateway) as the evaluation model
`typesafe-ai/jev`. The wire contract is the one the AI SDK's `experimental_evaluate`
uses (`@ai-sdk/gateway` `gateway-evaluation-model.ts`, with the `ai` package's
`validate-evaluation.ts` checks). Question semantics follow TypeSafe's
[primitives](https://docs.typesafe.ai/primitives) and
[confidence](https://docs.typesafe.ai/confidence):

- One `POST /v4/ai/evaluation-model` carries one `state` and a map of named `questions`
  (`choice`, `score`, or `boolean`, which is TypeSafe's Noul); the model id travels in
  the `ai-model-id` header and answers come back under the same keys. Independent
  questions in one batch run in parallel and cannot see one another's answers.
- Boolean returns `probability` only. Choice and Score return `probabilities`, and
  TypeSafe's own `confidence` for each arrives in `providerMetadata.typesafe.confidence`:
  the value TypeSafe's direct API reports, so thresholds calibrated on it carry over.
  Confidence summarizes distribution concentration, not correctness.
- The gateway lists Jev under one unversioned id and neither accepts nor reports a Jev
  version. Every request is restricted to the `typesafe-ai` provider, so an outage
  never moves a decision to another host serving the same id.

The connector returns typed answers with their probabilities and the model id intact. It
applies no thresholds: acceptance belongs to `app.connectors.jev_acceptance`, which the
owning workflow calls with its own calibrated thresholds.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Annotated, Final, Literal, NoReturn, Self

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from pydantic_core import to_json
from typesafe_sdk import Choice, JSONContent, Noul, Question, Score

from app.connectors.failures import (
    ConnectorFailure,
    EffectState,
    FailureDetail,
    TechnicalCategory,
    default_retry_advice,
    redacted_parameters,
    technical_category_for_status,
)
from app.connectors.jev_acceptance import JevPrimitive
from app.connectors.retry import RetryPolicy, retry_safe_call

__all__ = ["JevBatchResult", "JevChoiceAnswer", "JevClient", "JevNoulAnswer", "JevScoreAnswer"]

_logger = structlog.get_logger(__name__)
_PROVIDER: Final[str] = "vercel_ai_gateway"
_EVALUATION_URL: Final[str] = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"
_GATEWAY_PROTOCOL_VERSION: Final[str] = "0.0.1"
_EVALUATION_SPECIFICATION_VERSION: Final[str] = "4"
_JEV_HOST: Final[str] = "typesafe-ai"
_RETRY: Final[RetryPolicy] = RetryPolicy(
    max_attempts=3,
    initial_delay_seconds=0.5,
    backoff_multiplier=2.0,
    safety_justification="an evaluation is a read: it changes nothing at the provider",
)

_SdkQuestion = Annotated[Noul | Choice | Score, Field(discriminator="type")]
_QUESTION: Final[TypeAdapter[Noul | Choice | Score]] = TypeAdapter(_SdkQuestion)


class JevNoulAnswer(BaseModel):
    """A yes/no probability. Noul carries no confidence field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str
    noul: float


class JevChoiceAnswer(BaseModel):
    """A selected option with the full distribution that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str
    choice: str
    probabilities: Mapping[str, float]
    confidence: float


class JevScoreAnswer(BaseModel):
    """A probability-weighted level with its rubric legend and distribution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: str
    score: float
    legend: Mapping[int, str]
    probabilities: Mapping[int, float]
    confidence: float


class JevBatchResult(BaseModel):
    """One batched evaluation, retained with everything a decision record needs.

    `model_id` is the id the gateway reports, which the caller stores next to the
    question definitions, criteria, and thresholds it pinned together.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    nouls: Mapping[str, JevNoulAnswer]
    choices: Mapping[str, JevChoiceAnswer]
    scores: Mapping[str, JevScoreAnswer]
    input_tokens: int
    output_tokens: int

    def returned_primitives(self) -> dict[str, str]:
        """Map each answered question id to its primitive, for `check_answer_primitives`."""
        primitives: dict[str, str] = {}
        primitives.update({name: JevPrimitive.NOUL.value for name in self.nouls})
        primitives.update({name: JevPrimitive.CHOICE.value for name in self.choices})
        primitives.update({name: JevPrimitive.SCORE.value for name in self.scores})
        return primitives


class _GatewayChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]


class _GatewayScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    type: Literal["score"]
    score: float
    probabilities: dict[int, float]


class _GatewayBoolean(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    type: Literal["boolean"]
    probability: float


class _GatewayUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")


class _TypeSafeMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    confidence: dict[str, float]


class _ProviderMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    typesafe: _TypeSafeMetadata


class _GatewayEvaluation(BaseModel):
    """The evaluation response fields this connector needs; the rest is ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    answers: dict[
        str,
        Annotated[_GatewayChoice | _GatewayScore | _GatewayBoolean, Field(discriminator="type")],
    ]
    model: str
    usage: _GatewayUsage
    provider_metadata: _ProviderMetadata = Field(alias="providerMetadata")


class _GatewayErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    type: str


class _GatewayError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    error: _GatewayErrorBody


class JevClient:
    """Connector for batched Jev judgements over supplied application state.

    One client is pinned to one gateway model id for its whole lifetime. Changing models
    means constructing another client, so an in-flight decision cannot change models
    underneath the thresholds calibrated for it.
    """

    def __init__(self, *, api_key: str, model_id: str, timeout_seconds: float) -> None:
        self._model_id = model_id
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {api_key}",
                "ai-gateway-protocol-version": _GATEWAY_PROTOCOL_VERSION,
                "ai-gateway-auth-method": "api-key",
                "ai-evaluation-model-specification-version": _EVALUATION_SPECIFICATION_VERSION,
                "ai-model-id": model_id,
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> Self:
        """Enter the HTTP client scope."""
        await self._client.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the HTTP client and its transport."""
        await self._client.__aexit__(exc_type, exc, traceback)

    @property
    def model_id(self) -> str:
        """The pinned gateway model id recorded with every decision."""
        return self._model_id

    async def judge(
        self, *, state: JSONContent, questions: Mapping[str, Question]
    ) -> JevBatchResult:
        """Answer one batch of independent questions against one supplied state.

        Every question in a batch must be answerable from `state` alone. A question whose
        premise depends on another answer belongs in a second request with that premise
        supplied, because answers in one batch cannot consume each other.

        The call is a read: it changes nothing at the provider, so the effect state is
        always `ABSENT`, and a timed-out, throttled, or unavailable attempt is retried
        with a logged warning before the last failure is raised.
        """
        question_ids = tuple(questions)
        if not questions:
            raise self._failure(
                category=TechnicalCategory.PROVIDER_REJECTED,
                message="an evaluation request needs at least one question",
                question_ids=question_ids,
                status_code=None,
                provider_error_code=None,
                body=None,
            )
        typed = {name: _QUESTION.validate_python(question) for name, question in questions.items()}
        body = to_json(
            {
                "state": state,
                "questions": {
                    name: self._wire_question(name, question) for name, question in typed.items()
                },
                "providerOptions": {"gateway": {"only": [_JEV_HOST]}},
            }
        )

        async def evaluate_once() -> _GatewayEvaluation:
            return await self._evaluate_once(body=body, question_ids=question_ids)

        evaluation = await retry_safe_call(operation="judge", policy=_RETRY, call=evaluate_once)
        result = self._to_batch_result(evaluation=evaluation, questions=typed)
        _logger.info(
            "jev.batch",
            model_id=result.model_id,
            question_count=len(questions),
            noul_answers=len(result.nouls),
            choice_answers=len(result.choices),
            score_answers=len(result.scores),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        return result

    def _wire_question(self, name: str, question: Noul | Choice | Score) -> dict[str, object]:
        """Render one question in the gateway's form; a Noul is a `boolean` there.

        The gateway requires instructions on every question and at least two Score
        levels, which TypeSafe's own types leave optional, so both are checked here.
        """
        if question.instructions is None:
            raise self._failure(
                category=TechnicalCategory.PROVIDER_REJECTED,
                message=f"question {name!r} has no instructions; the gateway requires them",
                question_ids=(name,),
                status_code=None,
                provider_error_code=None,
                body=None,
            )
        if isinstance(question, Score) and len(question.criteria) < 2:
            raise self._failure(
                category=TechnicalCategory.PROVIDER_REJECTED,
                message=f"score question {name!r} needs at least two levels",
                question_ids=(name,),
                status_code=None,
                provider_error_code=None,
                body=None,
            )
        wire: dict[str, object] = question.model_dump(mode="json")
        if isinstance(question, Noul):
            wire["type"] = "boolean"
        return wire

    async def _evaluate_once(
        self, *, body: bytes, question_ids: tuple[str, ...]
    ) -> _GatewayEvaluation:
        """Send one evaluation request and decode it, mapping every failure to its category."""
        try:
            response = await self._client.post(_EVALUATION_URL, content=body)
        except httpx.TimeoutException as error:
            raise self._failure(
                category=TechnicalCategory.TIMEOUT,
                message=f"evaluation request timed out: {type(error).__name__}",
                question_ids=question_ids,
                status_code=None,
                provider_error_code=None,
                body=None,
            ) from error
        except httpx.TransportError as error:
            raise self._failure(
                category=TechnicalCategory.PROVIDER_UNAVAILABLE,
                message=f"evaluation request failed in transport: {type(error).__name__}",
                question_ids=question_ids,
                status_code=None,
                provider_error_code=None,
                body=None,
            ) from error
        if response.is_error:
            raise self._failure(
                category=technical_category_for_status(response.status_code),
                message="the gateway rejected the evaluation request",
                question_ids=question_ids,
                status_code=response.status_code,
                provider_error_code=_error_type(response.content),
                body=response.text,
            )
        try:
            return _GatewayEvaluation.model_validate_json(response.content)
        except ValidationError as error:
            raise self._failure(
                category=TechnicalCategory.VALIDATION,
                message=f"evaluation response failed validation: {error.errors()[0]['loc']!r}",
                question_ids=question_ids,
                status_code=response.status_code,
                provider_error_code=None,
                body=None,
            ) from error

    def _to_batch_result(
        self, *, evaluation: _GatewayEvaluation, questions: Mapping[str, Noul | Choice | Score]
    ) -> JevBatchResult:
        """Convert gateway answers into uTrace records, keeping probabilities and legends.

        Each question needs exactly one answer of its own type, and each Choice or Score
        needs TypeSafe's confidence; anything else is a validation failure, not a guess.
        """
        question_ids = tuple(questions)
        if evaluation.model != self._model_id:
            self._invalid(
                f"answers came from {evaluation.model!r} but this client pinned {self._model_id!r}",
                question_ids,
            )
        if set(evaluation.answers) != set(questions):
            self._invalid("the answers do not match the questions one to one", question_ids)
        confidences = evaluation.provider_metadata.typesafe.confidence
        nouls: dict[str, JevNoulAnswer] = {}
        choices: dict[str, JevChoiceAnswer] = {}
        scores: dict[str, JevScoreAnswer] = {}
        for question_id, question in questions.items():
            answer = evaluation.answers[question_id]
            if isinstance(question, Noul) and isinstance(answer, _GatewayBoolean):
                nouls[question_id] = JevNoulAnswer(question_id=question_id, noul=answer.probability)
            elif isinstance(question, Choice) and isinstance(answer, _GatewayChoice):
                choices[question_id] = JevChoiceAnswer(
                    question_id=question_id,
                    choice=answer.choice,
                    probabilities=answer.probabilities,
                    confidence=self._confidence(confidences, question_id, question_ids),
                )
            elif isinstance(question, Score) and isinstance(answer, _GatewayScore):
                scores[question_id] = JevScoreAnswer(
                    question_id=question_id,
                    score=answer.score,
                    legend={level: str(text) for level, text in enumerate(question.criteria)},
                    probabilities=answer.probabilities,
                    confidence=self._confidence(confidences, question_id, question_ids),
                )
            else:
                self._invalid(
                    f"question {question_id!r} was answered as {answer.type!r}", question_ids
                )
        return JevBatchResult(
            model_id=evaluation.model,
            nouls=nouls,
            choices=choices,
            scores=scores,
            input_tokens=evaluation.usage.input_tokens,
            output_tokens=evaluation.usage.output_tokens,
        )

    def _confidence(
        self, confidences: Mapping[str, float], question_id: str, question_ids: tuple[str, ...]
    ) -> float:
        """Return TypeSafe's reported confidence for one Choice or Score answer."""
        if question_id not in confidences:
            self._invalid(f"no TypeSafe confidence came back for {question_id!r}", question_ids)
        return confidences[question_id]

    def _invalid(self, message: str, question_ids: tuple[str, ...]) -> NoReturn:
        """Raise a validation failure for a well-formed response that breaks the contract."""
        raise self._failure(
            category=TechnicalCategory.VALIDATION,
            message=message,
            question_ids=question_ids,
            status_code=None,
            provider_error_code=None,
            body=None,
        )

    def _failure(
        self,
        *,
        category: TechnicalCategory,
        message: str,
        question_ids: tuple[str, ...],
        status_code: int | None,
        provider_error_code: str | None,
        body: str | None,
    ) -> ConnectorFailure:
        """Build a typed failure carrying the question ids but never the state."""
        return ConnectorFailure(
            FailureDetail(
                provider=_PROVIDER,
                operation="judge",
                category=category,
                retry_advice=default_retry_advice(category),
                effect_state=EffectState.ABSENT,
                message=message,
                request_parameters=redacted_parameters(
                    {"model_id": self._model_id, "question_ids": ",".join(sorted(question_ids))}
                ),
                status_code=status_code,
                provider_error_code=provider_error_code,
                response_body=body,
            )
        )


def _error_type(content: bytes) -> str | None:
    """Return the gateway's `error.type` code, or `None` when the body carries none."""
    try:
        return _GatewayError.model_validate_json(content).error.type
    except ValidationError:
        return None
