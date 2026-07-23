"""Structured model gateway with bounded retries and schema repair."""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic, sleep
from typing import Any, Protocol, TypeVar, cast, runtime_checkable
from uuid import UUID

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from resolveops.prompts import PromptRegistry

T = TypeVar("T", bound=BaseModel)
REQUESTED_MODEL = "openrouter/free"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_OUTPUT_TOKENS = 2_000


class ModelErrorCode(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    PROVIDER_5XX = "provider_5xx"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_OUTPUT = "invalid_output"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True)
class TraceContext:
    run_id: UUID
    node_name: str


@dataclass(frozen=True)
class ModelCallMetadata:
    run_id: UUID
    node_name: str
    provider: str
    requested_model: str
    resolved_model: str | None
    prompt_name: str
    prompt_version: int
    generation_id: str | None
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None
    cost_usd: float
    latency_ms: int
    status: str
    error_code: ModelErrorCode | None


@dataclass(frozen=True)
class ModelResult[T: BaseModel]:
    output: T
    calls: tuple[ModelCallMetadata, ...]


class ModelGatewayError(RuntimeError):
    """Safe gateway failure carrying only compact attempt metadata."""

    def __init__(
        self,
        message: str,
        *,
        error_code: ModelErrorCode,
        calls: tuple[ModelCallMetadata, ...],
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.calls = calls


class _ProviderResponseError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_type: str | None = None,
        provider_response: object | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.provider_response = provider_response


@runtime_checkable
class ModelGateway(Protocol):
    def generate_structured(
        self,
        *,
        prompt_name: str,
        variables: dict[str, Any],
        response_model: type[T],
        trace_context: TraceContext,
        timeout_seconds: float,
    ) -> ModelResult[T]: ...


class OpenRouterModelGateway:
    """OpenAI-compatible OpenRouter adapter; all outputs cross a Pydantic boundary."""

    def __init__(
        self,
        *,
        api_key: str,
        client: Any | None = None,
        prompts: PromptRegistry | None = None,
        sleeper: Callable[[float], None] = sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._client = client or OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            max_retries=0,
        )
        self._prompts = prompts or PromptRegistry()
        self._sleep = sleeper
        self._jitter = jitter

    def generate_structured(
        self,
        *,
        prompt_name: str,
        variables: dict[str, Any],
        response_model: type[T],
        trace_context: TraceContext,
        timeout_seconds: float,
    ) -> ModelResult[T]:
        prompt = self._prompts.get(prompt_name)
        rendered = prompt.render(variables)
        calls: list[ModelCallMetadata] = []
        validation_error: str | None = None

        for structured_attempt in range(2):
            request_text = rendered
            if validation_error is not None:
                request_text = (
                    f"{rendered}\n\nThe previous JSON failed validation. Correct only these "
                    f"schema errors and return a complete replacement JSON object:\n"
                    f"{validation_error}"
                )
            try:
                response, latency_ms = self._request_with_transport_retries(
                    prompt_name=prompt_name,
                    prompt_version=prompt.version,
                    prompt=request_text,
                    response_model=response_model,
                    trace_context=trace_context,
                    timeout_seconds=timeout_seconds,
                    calls=calls,
                )
            except ModelGatewayError as error:
                raise ModelGatewayError(
                    str(error),
                    error_code=error.error_code,
                    calls=tuple(calls),
                ) from error

            metadata = _response_metadata(
                response=response,
                trace_context=trace_context,
                prompt_name=prompt_name,
                prompt_version=prompt.version,
                latency_ms=latency_ms,
            )
            try:
                content = _response_content(response)
                output = response_model.model_validate_json(content)
            except (TypeError, ValueError, ValidationError) as error:
                validation_error = _safe_validation_summary(error)
                calls.append(
                    ModelCallMetadata(
                        **{
                            **metadata.__dict__,
                            "status": "invalid_output",
                            "error_code": ModelErrorCode.INVALID_OUTPUT,
                        }
                    )
                )
                if structured_attempt == 0:
                    continue
                raise ModelGatewayError(
                    "model returned invalid structured output after one repair attempt",
                    error_code=ModelErrorCode.INVALID_OUTPUT,
                    calls=tuple(calls),
                ) from error

            calls.append(metadata)
            return ModelResult(output=output, calls=tuple(calls))

        raise AssertionError("structured output loop exited unexpectedly")

    def _request_with_transport_retries(
        self,
        *,
        prompt_name: str,
        prompt_version: int,
        prompt: str,
        response_model: type[BaseModel],
        trace_context: TraceContext,
        timeout_seconds: float,
        calls: list[ModelCallMetadata],
    ) -> tuple[Any, int]:
        transport_attempts = 0
        transient_attempts = 0
        rate_limit_attempts = 0
        capability_attempts = 0
        while True:
            transport_attempts += 1
            started = monotonic()
            try:
                response = self._client.chat.completions.create(
                    model=REQUESTED_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": response_model.__name__,
                            "strict": True,
                            "schema": response_model.model_json_schema(),
                        },
                    },
                    max_tokens=MAX_OUTPUT_TOKENS,
                    temperature=0,
                    timeout=timeout_seconds,
                    extra_body={
                        "provider": {
                            "require_parameters": True,
                            "data_collection": "deny",
                        }
                    },
                )
                _raise_for_response_error(response)
                return response, max(0, int((monotonic() - started) * 1_000))
            except Exception as error:  # noqa: BLE001 - provider SDK exceptions vary by transport
                latency_ms = max(0, int((monotonic() - started) * 1_000))
                error_code = _classify_provider_error(error)
                calls.append(
                    _failed_call_metadata(
                        error=error,
                        error_code=error_code,
                        trace_context=trace_context,
                        prompt_name=prompt_name,
                        prompt_version=prompt_version,
                        latency_ms=latency_ms,
                    )
                )
                should_retry = False
                delay = 0.0
                if error_code in {ModelErrorCode.TIMEOUT, ModelErrorCode.PROVIDER_5XX}:
                    transient_attempts += 1
                    should_retry = transient_attempts <= 2
                    delay = min(2.0, 0.25 * (2 ** (transient_attempts - 1))) + (
                        self._jitter() * 0.1
                    )
                elif error_code is ModelErrorCode.RATE_LIMIT:
                    rate_limit_attempts += 1
                    should_retry = rate_limit_attempts <= 1
                    retry_after = _bounded_retry_after(error, maximum_seconds=1.0)
                    delay = (
                        retry_after
                        if retry_after is not None
                        else min(1.0, 0.25 + (self._jitter() * 0.1))
                    )
                elif error_code is ModelErrorCode.UNSUPPORTED_CAPABILITY:
                    capability_attempts += 1
                    should_retry = capability_attempts <= 1
                    delay = 0
                should_retry = should_retry and transport_attempts < 3
                if should_retry:
                    self._sleep(delay)
                    continue
                raise ModelGatewayError(
                    f"model provider unavailable ({error_code.value})",
                    error_code=error_code,
                    calls=tuple(calls),
                ) from error


def _response_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError("model response did not contain a choice")
    content = getattr(getattr(choices[0], "message", None), "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model response did not contain JSON text")
    return content


def _response_metadata(
    *,
    response: Any,
    trace_context: TraceContext,
    prompt_name: str,
    prompt_version: int,
    latency_ms: int,
) -> ModelCallMetadata:
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    cost = getattr(usage, "cost", 0) if usage is not None else 0
    return ModelCallMetadata(
        run_id=trace_context.run_id,
        node_name=trace_context.node_name,
        provider="openrouter",
        requested_model=REQUESTED_MODEL,
        resolved_model=_optional_string(getattr(response, "model", None)),
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        generation_id=_optional_string(getattr(response, "id", None)),
        input_tokens=max(0, int(getattr(usage, "prompt_tokens", 0) or 0)),
        output_tokens=max(0, int(getattr(usage, "completion_tokens", 0) or 0)),
        reasoning_tokens=_optional_non_negative_int(getattr(details, "reasoning_tokens", None)),
        cost_usd=max(0.0, float(cost or 0)),
        latency_ms=latency_ms,
        status="completed",
        error_code=None,
    )


def _classify_provider_error(error: Exception) -> ModelErrorCode:
    status_code = getattr(error, "status_code", None)
    error_type = getattr(error, "error_type", None)
    name = type(error).__name__.lower()
    message = str(error).lower()
    if status_code == 408 or "timeout" in name or "timeout" in message:
        return ModelErrorCode.TIMEOUT
    if status_code == 429 or error_type == "rate_limit_exceeded":
        return ModelErrorCode.RATE_LIMIT
    if (
        isinstance(status_code, int)
        and status_code >= 500
        or error_type in {"provider_unavailable", "provider_error", "server_error"}
    ):
        return ModelErrorCode.PROVIDER_5XX
    if (
        status_code in {400, 404, 422}
        and any(
            term in message
            for term in ("response_format", "json_schema", "unsupported", "capability")
        )
        or error_type
        in {"unsupported_parameter", "unsupported_response_format", "unsupported_capability"}
    ):
        return ModelErrorCode.UNSUPPORTED_CAPABILITY
    return ModelErrorCode.PROVIDER_ERROR


def _raise_for_response_error(response: Any) -> None:
    choices = _field(response, "choices")
    first_choice = choices[0] if isinstance(choices, list) and choices else None
    error = _field(response, "error") or _field(first_choice, "error")
    finish_reason = _field(first_choice, "finish_reason")
    if error is None and finish_reason != "error":
        return
    metadata = _field(error, "metadata")
    raw_code = _field(error, "code")
    status_code = raw_code if isinstance(raw_code, int) else 502
    message = _field(error, "message")
    raise _ProviderResponseError(
        message if isinstance(message, str) and message else "model provider returned an error",
        status_code=status_code,
        error_type=_optional_string(_field(metadata, "error_type")),
        provider_response=response,
    )


def _failed_call_metadata(
    *,
    error: Exception,
    error_code: ModelErrorCode,
    trace_context: TraceContext,
    prompt_name: str,
    prompt_version: int,
    latency_ms: int,
) -> ModelCallMetadata:
    provider_response = getattr(error, "provider_response", None)
    if provider_response is not None:
        metadata = _response_metadata(
            response=provider_response,
            trace_context=trace_context,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            latency_ms=latency_ms,
        )
        return ModelCallMetadata(
            **{
                **metadata.__dict__,
                "status": "failed",
                "error_code": error_code,
            }
        )
    return ModelCallMetadata(
        run_id=trace_context.run_id,
        node_name=trace_context.node_name,
        provider="openrouter",
        requested_model=REQUESTED_MODEL,
        resolved_model=None,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        generation_id=None,
        input_tokens=0,
        output_tokens=0,
        reasoning_tokens=None,
        cost_usd=0,
        latency_ms=latency_ms,
        status="failed",
        error_code=error_code,
    )


def _bounded_retry_after(error: Exception, *, maximum_seconds: float) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if not hasattr(headers, "get"):
        return None
    raw_value = cast(Any, headers).get("Retry-After")
    if not isinstance(raw_value, (str, int, float)):
        return None
    try:
        seconds = float(raw_value)
    except ValueError:
        return None
    return min(maximum_seconds, max(0.0, seconds))


def _field(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    direct = getattr(value, name, None)
    if direct is not None:
        return cast(object, direct)
    model_extra = getattr(value, "model_extra", None)
    return cast(object | None, model_extra.get(name)) if isinstance(model_extra, dict) else None


def _safe_validation_summary(error: Exception) -> str:
    if isinstance(error, ValidationError):
        errors = [
            {
                "location": ".".join(str(part) for part in item["loc"]),
                "type": item["type"],
            }
            for item in error.errors(include_url=False, include_context=False, include_input=False)
        ]
        return json.dumps(errors, separators=(",", ":"))[:2_000]
    return type(error).__name__


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_non_negative_int(value: object) -> int | None:
    return max(0, value) if isinstance(value, int) else None
