"""
Acceptance unit tests for Step 2E-1: LLM and VLM Client Tracing Instrumentation.

Validates:
1. GroqLLMClient (generate_json and generate_json_async) emits a single child span
   named 'groq_llm_generate' with run_type='llm', inputs={}, and safe operational summaries.
2. GeminiVisionClient (read_fields and cross_check_table) emits a single child span
   named 'gemini_vision_generate' with run_type='llm', inputs={}, and safe operational summaries.
3. HuggingFaceVisionClient (read_fields and cross_check_table) emits 'huggingface_vision_generate'.
4. GroqVisionClient (read_fields and cross_check_table) emits 'groq_vision_generate'.
5. Multi-provider orchestrator ResilientVisionClient produces only the underlying provider's span
   with no duplicate wrapper span.
6. Absolute exclusion of raw prompts, completions, messages, image bytes, base64 strings,
   or sensitive clinical/demographic text from inputs, outputs, and metadata.
7. Model field is strictly omitted from telemetry summaries (Deferred Item 1 compliance).
8. Original exceptions propagate unchanged while telemetry records normalized error categories.
9. Tracing disabled by default produces no traces and zero operational divergence.
10. Telemetry transport failures do not interrupt model client execution.
"""

from __future__ import annotations

import os
from typing import Any
from unittest import mock

import httpx
import pytest

from medishield.core.config import settings
from medishield.core.llm import GroqLLMClient, GroqLLMError
from medishield.core.tracing import reset_tracing_client
from medishield.ocr.ocr_reconcile import (
    GeminiVisionClient,
    GroqVisionClient,
    HuggingFaceVisionClient,
    ResilientVisionClient,
    VLMProviderError,
)

CANARY_SENSITIVE_TEXT = "PATIENT: John Doe SSN: 000-12-3456 DIAGNOSIS: HIV CPT: 99213 AMOUNT: $9999.00"


@pytest.fixture(autouse=True)
def clean_tracing_state():
    """Ensure clean tracing state before and after each test."""
    reset_tracing_client()
    orig_tracing = settings.LANGSMITH_TRACING
    orig_key = settings.LANGSMITH_API_KEY
    orig_pseudo = settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM
    orig_env = os.environ.get("LANGSMITH_TRACING")
    yield
    settings.LANGSMITH_TRACING = orig_tracing
    settings.LANGSMITH_API_KEY = orig_key
    settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM = orig_pseudo
    if orig_env is not None:
        os.environ["LANGSMITH_TRACING"] = orig_env
    else:
        os.environ.pop("LANGSMITH_TRACING", None)
    reset_tracing_client()


# ── Test 1: GroqLLMClient.generate_json Span Emission & Token Capture ─────────
def test_groq_llm_generate_json_tracing_success():
    """Verifies synchronous GroqLLMClient.generate_json emits a single span with safe tokens."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    client = GroqLLMClient(api_key="gsk-test-key", model="llama-3.3-70b-versatile")

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"summary": "approved"}'}}],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 45,
            "total_tokens": 165,
        },
    }

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        mock.patch.object(httpx.Client, "post", return_value=mock_response),
    ):
        result = client.generate_json(prompt=f"Analyze {CANARY_SENSITIVE_TEXT}")

    assert result == {"summary": "approved"}
    assert "groq_llm_generate" in captured_spans
    span_data = captured_spans["groq_llm_generate"]
    kwargs = span_data["kwargs"]

    # Verify run_type and strict input minimization
    assert kwargs.get("run_type") == "llm"
    assert kwargs.get("inputs") == {}

    # Verify end call and outputs
    tree = span_data["tree"]
    tree.end.assert_called_once()
    outputs = tree.end.call_args.kwargs["outputs"]
    assert outputs.get("provider") == "groq"
    assert outputs.get("status") == "SUCCESS"
    assert outputs.get("prompt_tokens") == 120
    assert outputs.get("completion_tokens") == 45
    assert outputs.get("total_tokens") == 165

    # Verify Deferred Item 1: model is strictly omitted
    assert "model" not in outputs

    # Verify zero canary leakage
    for val in outputs.values():
        assert CANARY_SENSITIVE_TEXT not in str(val)
        assert "000-12-3456" not in str(val)


# ── Test 2: GroqLLMClient.generate_json_async Span Emission ───────────────────
@pytest.mark.asyncio
async def test_groq_llm_generate_json_async_tracing_success():
    """Verifies asynchronous GroqLLMClient.generate_json_async emits a single span."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    client = GroqLLMClient(api_key="gsk-test-key", model="llama-3.3-70b-versatile")

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"async_extracted": true}'}}],
        "usage": {
            "prompt_tokens": 200,
            "completion_tokens": 50,
            "total_tokens": 250,
        },
    }

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        mock.patch.object(httpx.AsyncClient, "post", return_value=mock_response),
    ):
        result = await client.generate_json_async(prompt="Extract data")

    assert result == {"async_extracted": True}
    assert "groq_llm_generate" in captured_spans
    span_data = captured_spans["groq_llm_generate"]
    kwargs = span_data["kwargs"]

    assert kwargs.get("run_type") == "llm"
    assert kwargs.get("inputs") == {}

    tree = span_data["tree"]
    tree.end.assert_called_once()
    outputs = tree.end.call_args.kwargs["outputs"]
    assert outputs.get("provider") == "groq"
    assert outputs.get("status") == "SUCCESS"
    assert outputs.get("prompt_tokens") == 200
    assert outputs.get("completion_tokens") == 50
    assert outputs.get("total_tokens") == 250
    assert "model" not in outputs


# ── Test 3: GeminiVisionClient.read_fields Single Span ────────────────────────
def test_gemini_vision_read_fields_single_span():
    """Verifies GeminiVisionClient emits gemini_vision_generate with safe operational summaries."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_calls: list[str] = []
    captured_spans: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        captured_calls.append(name)
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    client = GeminiVisionClient(api_key="ai-test-key")

    mock_genai_client = mock.MagicMock()
    mock_response = mock.MagicMock()
    mock_response.text = '{"fields": {"total_amount": {"value": 150.0, "span_id": "s-1"}}}'
    mock_usage = mock.MagicMock()
    mock_usage.prompt_token_count = 350
    mock_usage.candidates_token_count = 40
    mock_usage.total_token_count = 390
    mock_response.usage_metadata = mock_usage
    mock_genai_client.models.generate_content.return_value = mock_response

    mock_google = mock.MagicMock()
    mock_genai_mod = mock.MagicMock()
    mock_google.genai = mock_genai_mod
    mock_genai_mod.Client.return_value = mock_genai_client

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        mock.patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai_mod, "google.genai.types": mock.MagicMock()}),
    ):
        # Test direct read_fields
        res1 = client.read_fields(b"fake-png", [{"span_id": "s-1", "text": "$150.00"}], ["total_amount"])
        assert res1 == {"total_amount": {"value": 150.0, "span_id": "s-1"}}
        assert captured_calls == ["gemini_vision_generate"]

    span_data = captured_spans["gemini_vision_generate"]
    assert span_data["kwargs"].get("run_type") == "llm"
    assert span_data["kwargs"].get("inputs") == {}
    outputs = span_data["tree"].end.call_args.kwargs["outputs"]
    assert outputs.get("provider") == "google"
    assert outputs.get("status") == "SUCCESS"
    assert outputs.get("prompt_tokens") == 350
    assert outputs.get("completion_tokens") == 40
    assert outputs.get("total_tokens") == 390
    assert "model" not in outputs


# ── Test 4: GeminiVisionClient.cross_check_table Span Emission ─────────────────
def test_gemini_vision_cross_check_table_span():
    """Verifies GeminiVisionClient.cross_check_table emits gemini_vision_generate span."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    client = GeminiVisionClient(api_key="ai-test-key")

    mock_genai_client = mock.MagicMock()
    mock_response = mock.MagicMock()
    mock_response.text = '{"candidate_rows": [{"cpt_code": "99213", "line_amount": 120.0, "description": "Office visit"}]}'
    mock_genai_client.models.generate_content.return_value = mock_response

    mock_google = mock.MagicMock()
    mock_genai_mod = mock.MagicMock()
    mock_google.genai = mock_genai_mod
    mock_genai_mod.Client.return_value = mock_genai_client

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        mock.patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai_mod, "google.genai.types": mock.MagicMock()}),
    ):
        candidates = client.cross_check_table(b"fake-png-bytes")

    assert len(candidates) == 3  # cpt_code, line_amount, description
    assert "gemini_vision_generate" in captured_spans
    outputs = captured_spans["gemini_vision_generate"]["tree"].end.call_args.kwargs["outputs"]
    assert outputs.get("provider") == "google"
    assert outputs.get("status") == "SUCCESS"
    assert "model" not in outputs


# ── Test 5: HuggingFaceVisionClient & GroqVisionClient Span Emission ───────────
def test_huggingface_and_groq_vision_clients_span_emission():
    """Verifies HuggingFaceVisionClient and GroqVisionClient emit individual spans with correct providers."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    # Hugging Face Vision Client test
    hf_client = HuggingFaceVisionClient(api_key="hf_test_key")
    mock_hf_resp = mock.MagicMock()
    mock_hf_resp.status_code = 200
    mock_hf_resp.json.return_value = {
        "choices": [{"message": {"content": '{"fields": {"member_id": {"value": "MEM-123", "span_id": "s-2"}}}'}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        mock.patch("requests.post", return_value=mock_hf_resp),
    ):
        res_hf = hf_client.read_fields(b"img", [{"span_id": "s-2", "text": "MEM-123"}], ["member_id"])

    assert res_hf == {"member_id": {"value": "MEM-123", "span_id": "s-2"}}
    assert "huggingface_vision_generate" in captured_spans
    hf_outputs = captured_spans["huggingface_vision_generate"]["tree"].end.call_args.kwargs["outputs"]
    assert hf_outputs.get("provider") == "huggingface"
    assert hf_outputs.get("status") == "SUCCESS"
    assert hf_outputs.get("prompt_tokens") == 100
    assert "model" not in hf_outputs

    # Groq Vision Client test
    captured_spans.clear()
    groq_v_client = GroqVisionClient(api_key="gsk-test")
    mock_groq_resp = mock.MagicMock()
    mock_groq_resp.status_code = 200
    mock_groq_resp.json.return_value = {
        "choices": [{"message": {"content": '{"fields": {"date": {"value": "2026-03-01", "span_id": "s-3"}}}'}}],
        "usage": {"prompt_tokens": 150, "completion_tokens": 30, "total_tokens": 180},
    }

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        mock.patch("requests.post", return_value=mock_groq_resp),
    ):
        res_groq = groq_v_client.read_fields(b"img", [{"span_id": "s-3", "text": "2026-03-01"}], ["date"])

    assert res_groq == {"date": {"value": "2026-03-01", "span_id": "s-3"}}
    assert "groq_vision_generate" in captured_spans
    groq_outputs = captured_spans["groq_vision_generate"]["tree"].end.call_args.kwargs["outputs"]
    assert groq_outputs.get("provider") == "groq"
    assert groq_outputs.get("status") == "SUCCESS"
    assert groq_outputs.get("prompt_tokens") == 150
    assert "model" not in groq_outputs


# ── Test 6: ResilientVisionClient Produces Zero Wrapper Spans ─────────────────
def test_resilient_vision_client_zero_wrapper_spans():
    """Verifies that ResilientVisionClient orchestrator does NOT produce an extra wrapper span."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_span_names: list[str] = []

    def mock_trace(name, **kwargs):
        captured_span_names.append(name)
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        cm.__enter__.return_value = mock_tree
        return cm

    mock_gemini = mock.MagicMock(spec=GeminiVisionClient)
    mock_gemini.is_available.return_value = True

    # When mock_gemini.read_fields is invoked, simulate its native instrumented span
    from medishield.core.tracing import trace_span

    def simulated_instrumented_gemini_read(*args, **kwargs):
        with trace_span(name="gemini_vision_generate", run_type="llm", inputs={}):
            return {"cpt": {"value": "99213", "span_id": "s-1"}}

    mock_gemini.read_fields.side_effect = simulated_instrumented_gemini_read

    resilient = ResilientVisionClient(gemini_client=mock_gemini)

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        result = resilient.read_fields(b"img", [{"span_id": "s-1", "text": "99213"}], ["cpt"])

    assert result == {"cpt": {"value": "99213", "span_id": "s-1"}}
    # Only the child provider span was emitted; ResilientVisionClient added zero wrapper spans
    assert captured_span_names == ["gemini_vision_generate"]
    assert "resilient_vision" not in captured_span_names


# ── Test 7: Error Normalization and Exception Preservation (GroqLLMClient) ─────
@pytest.mark.parametrize(
    "status_code, response_text, expected_category",
    [
        (429, "Rate limit reached for requests per minute", "RATE_LIMIT"),
        (401, "Invalid API key provided", "AUTH_ERROR"),
        (403, "Access forbidden for resource", "AUTH_ERROR"),
        (504, "Gateway Timeout", "TIMEOUT"),
        (500, "Internal Server Error", "PROVIDER_ERROR"),
        (422, "Validation failed", "VALIDATION_ERROR"),
    ],
)
def test_groq_llm_client_error_normalization_and_propagation(
    status_code: int, response_text: str, expected_category: str
):
    """Verifies that GroqLLMClient re-raises original GroqLLMError and normalizes error category."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    client = GroqLLMClient(api_key="gsk-key")

    mock_request = httpx.Request("POST", "https://api.groq.com/chat/completions")
    mock_resp = httpx.Response(status_code=status_code, text=response_text, request=mock_request)

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        mock.patch.object(httpx.Client, "post", side_effect=httpx.HTTPStatusError("error", request=mock_request, response=mock_resp)),
        pytest.raises(GroqLLMError) as exc_info,
    ):
        client.generate_json(prompt="Test prompt")

    # Original exception preserved
    assert f"Groq API returned HTTP {status_code}" in str(exc_info.value)

    # Telemetry captures normalized category
    span_data = captured_spans["groq_llm_generate"]
    tree = span_data["tree"]
    tree.end.assert_called_once()
    outputs = tree.end.call_args.kwargs["outputs"]
    assert outputs.get("status") == "FAILED"
    assert outputs.get("error_category") == expected_category
    assert outputs.get("error_type") == "GroqLLMError"
    # Verify no raw sensitive error strings in telemetry
    assert response_text not in outputs.get("error_category")


# ── Test 8: Error Normalization in Vision Clients (VLMProviderError) ───────────
def test_gemini_vision_client_error_propagation():
    """Verifies GeminiVisionClient re-raises VLMProviderError and normalizes error category."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    client = GeminiVisionClient(api_key="ai-test-key")

    mock_genai_client = mock.MagicMock()
    mock_genai_client.models.generate_content.side_effect = RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")

    mock_google = mock.MagicMock()
    mock_genai_mod = mock.MagicMock()
    mock_google.genai = mock_genai_mod
    mock_genai_mod.Client.return_value = mock_genai_client

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        mock.patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai_mod, "google.genai.types": mock.MagicMock()}),
        pytest.raises(VLMProviderError) as exc_info,
    ):
        client.read_fields(b"img", [], ["amt"])

    assert exc_info.value.provider == "Gemini"
    assert exc_info.value.reason == "RATE_LIMIT_EXCEEDED"
    assert exc_info.value.status_code == 429

    outputs = captured_spans["gemini_vision_generate"]["tree"].end.call_args.kwargs["outputs"]
    assert outputs.get("status") == "FAILED"
    assert outputs.get("error_category") == "RATE_LIMIT"
    assert outputs.get("error_type") == "VLMProviderError"


# ── Test 9: Zero Overhead & No Traces When Tracing Disabled ───────────────────
def test_tracing_disabled_no_calls_and_identical_output():
    """Verifies that with LANGSMITH_TRACING=False, no telemetry is initialized or called."""
    settings.LANGSMITH_TRACING = False
    os.environ["LANGSMITH_TRACING"] = "false"

    client = GroqLLMClient(api_key="gsk-test")

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"status": "ok"}'}}],
    }

    with (
        mock.patch("langsmith.trace") as mock_langsmith_trace,
        mock.patch.object(httpx.Client, "post", return_value=mock_response),
    ):
        res = client.generate_json(prompt="hello")

    assert res == {"status": "ok"}
    mock_langsmith_trace.assert_not_called()


# ── Test 10: Telemetry Transport Failure Resilience ───────────────────────────
def test_telemetry_transport_failure_resilience():
    """Verifies that an internal LangSmith exception never prevents client execution or changes results."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    client = GroqLLMClient(api_key="gsk-test")

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"resilient": true}'}}],
    }

    # Simulate LangSmith throwing a connection error upon trace activation
    with (
        mock.patch("langsmith.trace", side_effect=RuntimeError("LangSmith network endpoint unreachable")),
        mock.patch.object(httpx.Client, "post", return_value=mock_response),
    ):
        result = client.generate_json(prompt="Test prompt")

    assert result == {"resilient": True}


# ── Test 11: End-to-End Sensitive Data Minimization in Model Spans ────────────
def test_sensitive_canary_scrubbing_across_model_telemetry():
    """Verifies that adversarial canary text never appears in captured model span telemetry."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    client = GroqLLMClient(api_key="gsk-test")

    mock_response = mock.MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": '{"decision": "none"}'}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
    }

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        mock.patch.object(httpx.Client, "post", return_value=mock_response),
    ):
        client.generate_json(
            prompt=f"Please process claim for {CANARY_SENSITIVE_TEXT}",
            system_prompt=f"System directive with secret canary: {CANARY_SENSITIVE_TEXT}",
        )

    span = captured_spans["groq_llm_generate"]
    kwargs = span["kwargs"]
    outputs = span["tree"].end.call_args.kwargs["outputs"]

    # Assert inputs are empty dict {}
    assert kwargs.get("inputs") == {}

    # Inspect all serializable span data
    for source in (kwargs, outputs):
        serialized = str(source)
        assert "000-12-3456" not in serialized
        assert "John Doe" not in serialized
        assert "HIV" not in serialized
        assert "9999.00" not in serialized
        assert "99213" not in serialized

