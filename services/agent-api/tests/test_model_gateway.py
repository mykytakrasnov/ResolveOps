from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

import resolveops.models.gateway as gateway_module
from resolveops.models.contracts import CaseClassification
from resolveops.models.gateway import (
    ModelErrorCode,
    ModelGatewayError,
    OpenRouterModelGateway,
    TraceContext,
)
from resolveops.prompts import PromptRegistry

RUN_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
VALID_CLASSIFICATION = """
{
  "category": "duplicate_charge",
  "urgency": "normal",
  "confidence": 0.98,
  "suspected_account_reference": "org_atlas_001",
  "requested_outcome": "Investigate duplicate charges.",
  "risk_indicators": []
}
"""


class ProviderStub:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create),
        )

    def create(self, **kwargs: Any) -> object:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ProviderError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def provider_response(content: str) -> object:
    return SimpleNamespace(
        id="gen_test_123",
        model="openai/gpt-test",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=23,
            completion_tokens=17,
            cost=0.00012,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
        ),
    )


def provider_error_response(*, status_code: int, error_type: str) -> object:
    return SimpleNamespace(
        id="gen_error_123",
        model="openai/gpt-error",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=""),
                finish_reason="error",
                error={
                    "code": status_code,
                    "message": "provider failed after accepting the request",
                    "metadata": {"error_type": error_type},
                },
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=2,
            cost=0.00003,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
        ),
    )


def generate(gateway: OpenRouterModelGateway) -> Any:
    return gateway.generate_structured(
        prompt_name="resolveops/classify-case",
        variables={
            "ticket": {
                "subject": "Charged twice",
                "body": "Synthetic ticket content",
                "customer_reference": "org_atlas_001",
                "attachments": [],
            }
        },
        response_model=CaseClassification,
        trace_context=TraceContext(run_id=RUN_ID, node_name="classify_case"),
        timeout_seconds=5,
    )


def test_gateway_repairs_invalid_output_once_and_records_resolved_metadata() -> None:
    provider = ProviderStub(
        [
            provider_response('{"category":"duplicate_charge"}'),
            provider_response(VALID_CLASSIFICATION),
        ]
    )
    gateway = OpenRouterModelGateway(api_key="test", client=provider, sleeper=lambda _: None)

    result = generate(gateway)

    assert result.output.category.value == "duplicate_charge"
    assert [call.status for call in result.calls] == ["invalid_output", "completed"]
    assert result.calls[-1].resolved_model == "openai/gpt-test"
    assert result.calls[-1].generation_id == "gen_test_123"
    assert result.calls[-1].input_tokens == 23
    assert result.calls[-1].output_tokens == 17
    assert result.calls[-1].reasoning_tokens == 3
    assert result.calls[-1].cost_usd == pytest.approx(0.00012)
    assert "previous JSON failed validation" in provider.requests[1]["messages"][0]["content"]
    assert provider.requests[0]["model"] == "openrouter/free"
    assert provider.requests[0]["response_format"]["type"] == "json_schema"
    assert provider.requests[0]["extra_body"]["provider"]["require_parameters"] is True


def test_gateway_disables_sdk_retries_so_bounds_remain_application_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor_arguments: dict[str, Any] = {}
    provider = ProviderStub([provider_response(VALID_CLASSIFICATION)])

    def create_client(**kwargs: Any) -> ProviderStub:
        constructor_arguments.update(kwargs)
        return provider

    monkeypatch.setattr(gateway_module, "OpenAI", create_client)

    result = generate(OpenRouterModelGateway(api_key="test"))

    assert result.output.category.value == "duplicate_charge"
    assert constructor_arguments["base_url"] == "https://openrouter.ai/api/v1"
    assert constructor_arguments["max_retries"] == 0


def test_gateway_retries_timeout_twice_then_succeeds() -> None:
    delays: list[float] = []
    provider = ProviderStub(
        [
            TimeoutError("provider timeout"),
            TimeoutError("provider timeout"),
            provider_response(VALID_CLASSIFICATION),
        ]
    )
    gateway = OpenRouterModelGateway(
        api_key="test",
        client=provider,
        sleeper=delays.append,
        jitter=lambda: 0,
    )

    result = generate(gateway)

    assert len(provider.requests) == 3
    assert [call.error_code for call in result.calls[:2]] == [
        ModelErrorCode.TIMEOUT,
        ModelErrorCode.TIMEOUT,
    ]
    assert delays == [0.25, 0.5]


@pytest.mark.parametrize(
    ("status_code", "expected_code", "request_count"),
    [
        (429, ModelErrorCode.RATE_LIMIT, 2),
        (503, ModelErrorCode.PROVIDER_5XX, 3),
        (422, ModelErrorCode.UNSUPPORTED_CAPABILITY, 2),
    ],
)
def test_gateway_exhausts_bounded_provider_retries(
    status_code: int,
    expected_code: ModelErrorCode,
    request_count: int,
) -> None:
    message = (
        "response_format json_schema capability unsupported"
        if expected_code is ModelErrorCode.UNSUPPORTED_CAPABILITY
        else "provider unavailable"
    )
    provider = ProviderStub([ProviderError(message, status_code) for _ in range(request_count)])
    gateway = OpenRouterModelGateway(
        api_key="test",
        client=provider,
        sleeper=lambda _: None,
        jitter=lambda: 0,
    )

    with pytest.raises(ModelGatewayError) as raised:
        generate(gateway)

    assert raised.value.error_code is expected_code
    assert len(raised.value.calls) == request_count
    assert all(call.error_code is expected_code for call in raised.value.calls)


def test_gateway_classifies_and_retries_openrouter_in_band_error() -> None:
    provider = ProviderStub(
        [
            provider_error_response(
                status_code=429,
                error_type="rate_limit_exceeded",
            ),
            provider_response(VALID_CLASSIFICATION),
        ]
    )
    gateway = OpenRouterModelGateway(
        api_key="test",
        client=provider,
        sleeper=lambda _: None,
        jitter=lambda: 0,
    )

    result = generate(gateway)

    assert len(provider.requests) == 2
    assert result.calls[0].status == "failed"
    assert result.calls[0].error_code is ModelErrorCode.RATE_LIMIT
    assert result.calls[0].resolved_model == "openai/gpt-error"
    assert result.calls[0].generation_id == "gen_error_123"
    assert result.calls[0].input_tokens == 11
    assert result.calls[0].reasoning_tokens == 1
    assert result.calls[1].status == "completed"


def test_gateway_caps_mixed_transport_failures_at_three_attempts() -> None:
    provider = ProviderStub(
        [
            TimeoutError("provider timeout"),
            ProviderError("provider rate limited", 429),
            ProviderError("provider unavailable", 503),
            provider_response(VALID_CLASSIFICATION),
        ]
    )
    gateway = OpenRouterModelGateway(
        api_key="test",
        client=provider,
        sleeper=lambda _: None,
        jitter=lambda: 0,
    )

    with pytest.raises(ModelGatewayError) as raised:
        generate(gateway)

    assert raised.value.error_code is ModelErrorCode.PROVIDER_5XX
    assert len(raised.value.calls) == 3
    assert len(provider.requests) == 3


def test_local_prompt_registry_renders_all_versioned_fallbacks() -> None:
    registry = PromptRegistry()
    prompt_variables: dict[str, dict[str, Any]] = {
        "resolveops/classify-case": {"ticket": {"subject": "synthetic"}},
        "resolveops/assess-evidence-gaps": {
            "allowlisted_tools": ["get_policy"],
            "evidence": [],
        },
        "resolveops/propose-resolution": {"validation": {}, "evidence": []},
        "resolveops/draft-response": {"outcome": {}, "evidence": []},
    }

    for name, variables in prompt_variables.items():
        template = registry.get(name)
        rendered = template.render(variables)
        assert template.version == 1
        assert template.bundle_version == "1.0.0"
        assert "{{" not in rendered
        assert "untrusted" in rendered
        assert "hidden" in rendered
        assert "reasoning" in rendered


def test_prompt_rendering_treats_template_markers_inside_ticket_as_untrusted_data() -> None:
    rendered = (
        PromptRegistry()
        .get("resolveops/classify-case")
        .render({"ticket": {"subject": "{{not_a_prompt_variable}}"}})
    )

    assert "{{not_a_prompt_variable}}" in rendered
