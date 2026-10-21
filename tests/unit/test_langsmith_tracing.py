"""
Acceptance unit and integration tests for MediShield PHI-Minimized LangSmith Observability.

Validates:
1. End-to-End Serialized Network Payload: Captures the actual byte stream sent over the wire
   and verifies that synthetic sensitive values (SSN, name, DOB, DOS, claim amount, CPT/ICD,
   raw prompts, completions, exceptions, secrets) never appear anywhere in the serialized payload.
2. Value Validation & Strict Enums: Proves that fields like rule_id_matched, model, provider,
   status, verdict, and numeric ranges reject arbitrary sensitive strings and type violations.
3. Pseudonym Omission by Default: Verifies that persistent case pseudonyms are omitted by default,
   relying exclusively on ephemeral root trace UUIDs for hierarchical trace correlation.
4. Tracing Disabled Isolation: Verifies zero client instantiation and zero HTTP calls when disabled.
5. Observability Fault-Tolerance: Confirms that telemetry transport crashes never disrupt business logic
   or suppress application exceptions.
"""

from __future__ import annotations

import os
import time
from typing import Any
from unittest import mock

import pytest
import requests

from medishield.core.config import settings
from medishield.core.tracing import (
    SafeSpan,
    generate_case_pseudonym,
    get_langsmith_client,
    normalize_error_category,
    reset_tracing_client,
    sanitize_trace_payload,
    scrub_phi_text,
    trace_span,
    validate_telemetry_field,
)


@pytest.fixture(autouse=True)
def clean_tracing_state():
    """Ensure clean tracing state before and after each test."""
    reset_tracing_client()
    original_tracing = settings.LANGSMITH_TRACING
    original_key = settings.LANGSMITH_API_KEY
    original_pseudo = settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM
    orig_env_tracing = os.environ.get("LANGSMITH_TRACING")
    yield
    settings.LANGSMITH_TRACING = original_tracing
    settings.LANGSMITH_API_KEY = original_key
    settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM = original_pseudo
    if orig_env_tracing is not None:
        os.environ["LANGSMITH_TRACING"] = orig_env_tracing
    else:
        os.environ.pop("LANGSMITH_TRACING", None)
    reset_tracing_client()


# ── Test 1: Tracing Disabled Zero Overhead ───────────────────────────────────
def test_tracing_disabled_no_client_and_no_submission():
    """When LANGSMITH_TRACING is false, trace_span no-ops with zero client overhead."""
    settings.LANGSMITH_TRACING = False

    client = get_langsmith_client()
    assert client is None

    executed = False
    with trace_span(
        name="test_disabled_node",
        run_type="chain",
        case_id="CASE-001",
        component="test_component",
    ) as span:
        assert isinstance(span, SafeSpan)
        span.set_summary(verdict="PASS", confidence=0.99)
        executed = True

    assert executed is True
    summary = span.get_summary()
    assert summary["verdict"] == "PASS"
    assert summary["status"] == "SUCCESS"
    # By default, pseudonymous_case_id must be omitted
    assert "pseudonymous_case_id" not in summary


# ── Test 2: Case Pseudonymization & Default Omission ─────────────────────────
def test_case_pseudonym_omitted_by_default_and_keyed_hmac_when_enabled():
    """
    Validates:
    1. By default (LANGSMITH_INCLUDE_CASE_PSEUDONYM=False), case pseudonyms are omitted
       so trace correlation relies exclusively on ephemeral root trace UUIDs.
    2. When explicitly enabled, keyed HMAC-SHA256 is stable and never leaks raw case ID.
    """
    case_id = "CASE-C006-PT_39451"
    secret_key = "test-secret-key-for-unit-tests"

    # Default: omitted from payload
    settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM = False
    payload = {"case_id": case_id, "document_count": 3}
    sanitized = sanitize_trace_payload(payload)
    assert "case_id" not in sanitized
    assert "pseudonymous_case_id" not in sanitized

    # Explicitly enabled: deterministic keyed HMAC generated
    settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM = True
    pseudo_1 = generate_case_pseudonym(case_id, secret_key=secret_key)
    pseudo_2 = generate_case_pseudonym(case_id, secret_key=secret_key)
    assert pseudo_1 == pseudo_2
    assert len(pseudo_1) == 16
    assert case_id not in pseudo_1
    assert secret_key not in pseudo_1

    sanitized_enabled = sanitize_trace_payload(payload)
    assert "case_id" not in sanitized_enabled
    assert sanitized_enabled.get("pseudonymous_case_id") is not None


# ── Test 3: Telemetry Value & Range Validation ───────────────────────────────
def test_telemetry_value_and_range_validation():
    """
    Proves that fields like rule_id_matched, model, provider, status, verdict,
    and numeric ranges reject arbitrary sensitive strings and type violations.
    """
    # 1. Status enum validation
    assert validate_telemetry_field("status", "SUCCESS")[0] is True
    assert validate_telemetry_field("status", "FAILED")[0] is True
    assert validate_telemetry_field("status", "ARBITRARY_STATUS_WITH_PHI")[0] is False

    # 2. Verdict & Decision enums
    assert validate_telemetry_field("verdict", "PASS")[0] is True
    assert validate_telemetry_field("verdict", "INVALID_VERDICT")[0] is False
    assert validate_telemetry_field("decision", "APPROVE")[0] is True
    assert validate_telemetry_field("decision", "UNKNOWN_DECISION")[0] is False

    # 3. Provider enum
    assert validate_telemetry_field("provider", "groq")[0] is True
    assert validate_telemetry_field("provider", "google")[0] is True
    assert validate_telemetry_field("provider", "malicious_untrusted_provider")[0] is False

    # 4. Model validation (cannot contain arbitrary narrative or spaces)
    assert validate_telemetry_field("model", "openai/gpt-oss-120b")[0] is True
    assert validate_telemetry_field("model", "gemini-3.8-flash")[0] is True
    assert validate_telemetry_field("model", "Patient John Doe DOB 01/01/1980")[0] is False

    # 5. Rule ID validation (must match strict prefix and format)
    assert validate_telemetry_field("rule_id_matched", "RULE-P7-BORDERLINE-REVIEW")[0] is True
    assert validate_telemetry_field("rule_id_matched", "RULE-P1-EXCLUSION")[0] is True
    assert validate_telemetry_field("rule_id_matched", "RULE-PATIENT-ALICE-SMITH-SSN-999-00-1111")[0] is False
    assert validate_telemetry_field("rule_id_matched", "arbitrary text")[0] is False

    # 6. Numeric ranges
    assert validate_telemetry_field("duration_ms", 150.25)[0] is True
    assert validate_telemetry_field("duration_ms", -10.0)[0] is False
    assert validate_telemetry_field("duration_ms", 999_999_999.0)[0] is False

    assert validate_telemetry_field("retry_count", 2)[0] is True
    assert validate_telemetry_field("retry_count", -1)[0] is False
    assert validate_telemetry_field("retry_count", 100)[0] is False  # Exceeds max 50

    assert validate_telemetry_field("confidence", 0.95)[0] is True
    assert validate_telemetry_field("confidence", 1.5)[0] is False

    # 7. Strict booleans (reject non-bools)
    assert validate_telemetry_field("arithmetic_valid", True)[0] is True
    assert validate_telemetry_field("arithmetic_valid", "true")[0] is False
    assert validate_telemetry_field("arithmetic_valid", 1)[0] is False

    # 8. Token usage schema
    assert validate_telemetry_field(
        "token_usage", {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    )[0] is True
    assert validate_telemetry_field("token_usage", {"prompt_tokens": -5})[0] is False
    assert validate_telemetry_field("token_usage", "arbitrary")[0] is False


# ── Test 4: End-to-End Wire Payload Privacy Enforcement ──────────────────────
def test_end_to_end_network_payload_privacy_enforcement():
    """
    Integration test capturing the actual serialized byte stream sent over the wire
    to LangSmith endpoints. Inserts synthetic sensitive values into nested inputs,
    outputs, metadata, and exceptions across both custom trace_span and automatic
    @traceable calls. Verifies that zero prohibited values are transmitted.
    """
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-unit-langsmith-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_bodies: list[bytes] = []

    def fake_send(self: Any, request: Any, **kwargs: Any) -> requests.Response:
        if request.body:
            body = request.body
            if isinstance(body, bytes):
                captured_bodies.append(body)
            elif hasattr(body, "getvalue"):
                captured_bodies.append(body.getvalue())
            elif hasattr(body, "read"):
                pos = body.tell() if hasattr(body, "tell") else 0
                content = body.read()
                if hasattr(body, "seek"):
                    body.seek(pos)
                captured_bodies.append(content if isinstance(content, bytes) else str(content).encode("utf-8"))
            elif isinstance(body, str):
                captured_bodies.append(body.encode("utf-8"))
            else:
                captured_bodies.append(str(body).encode("utf-8"))
        resp = requests.Response()
        resp.status_code = 200
        resp._content = b"{}"
        return resp

    # Synthetic sensitive fixture data
    sensitive_ssn = "999-00-1111"
    sensitive_member_id = "MED-GLD-9999999"
    sensitive_name = "Alice Montgomery-Smith"
    sensitive_dob = "1985-04-12"
    sensitive_dos = "10/14/2025"
    sensitive_cpt = "70553"
    sensitive_amount_num = 25432.89
    sensitive_amount_str = "$25,432.89"
    sensitive_prompt = "Generate prior auth for Alice Montgomery-Smith with CPT 70553"
    sensitive_completion = "Approved claim 25432.89 for Alice Montgomery-Smith"
    sensitive_secret = "gsk_1234567890abcdef1234567890"
    sensitive_err_msg = f"Database query failed for {sensitive_name} SSN {sensitive_ssn} on {sensitive_dos}"

    with mock.patch.object(requests.Session, "send", fake_send):
        client = get_langsmith_client()
        assert client is not None

        # Path A: Custom span with nested synthetic sensitive values
        try:
            with trace_span(
                name="claims_validation_node",
                run_type="chain",
                component="ClaimsAgent",
                inputs={
                    "patient_name": sensitive_name,
                    "ssn": sensitive_ssn,
                    "dob": sensitive_dob,
                    "cpt": sensitive_cpt,
                    "amount": sensitive_amount_num,
                    "documents": [{"file": "sensitive_claim.png"}],
                },
                metadata={
                    "secret_key": sensitive_secret,
                    "date_of_service": sensitive_dos,
                    "member_id": sensitive_member_id,
                },
            ) as span:
                span.set_summary(status="SUCCESS", verdict="PASS", rule_id_matched="RULE-P1-EXCLUSION")
                raise RuntimeError(sensitive_err_msg)
        except RuntimeError:
            pass

        # Path B: Automatic tracer with synthetic prompts and completions
        from langsmith import traceable

        @traceable(name="auto_model_call")
        def call_auto(prompt: str):
            return {
                "verdict": "PASS",
                "choices": [{"message": sensitive_completion}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
            }

        call_auto(sensitive_prompt)
        time.sleep(0.3)  # Allow background queue to flush

    # Inspect all raw serialized bytes transmitted over the network
    full_wire_bytes = b"".join(captured_bodies)
    full_wire_str = full_wire_bytes.decode("utf-8", errors="ignore")

    assert len(full_wire_bytes) > 0, "No serialized network payloads were captured"

    prohibited_test_values = [
        sensitive_ssn,
        sensitive_member_id,
        sensitive_name,
        sensitive_dob,
        sensitive_dos,
        sensitive_cpt,
        str(sensitive_amount_num),
        sensitive_amount_str,
        sensitive_prompt,
        sensitive_completion,
        sensitive_secret,
    ]

    for prohibited in prohibited_test_values:
        assert prohibited not in full_wire_str, (
            f"Prohibited sensitive value '{prohibited}' leaked into serialized wire payload!"
        )

    # Verify that safe operational telemetry is preserved in the wire payload
    assert "claims_validation_node" in full_wire_str
    assert "auto_model_call" in full_wire_str
    assert "document_count" in full_wire_str
    assert "RULE-P1-EXCLUSION" in full_wire_str


# ── Test 5: PHI & Secret Regex Scrubbing ──────────────────────────────────────
def test_scrub_phi_text_and_secrets():
    """Validates regex masking of SSNs, member IDs, dates, currency, and API keys."""
    raw_text = (
        "Patient John Doe SSN 123-45-6789 (or 987654321), member MED-GLD-5446912, "
        "DOS 09/25/2025 with billed amount $21,735.00. Using Groq key gsk_abcdef1234567890abcdef."
    )
    scrubbed = scrub_phi_text(raw_text)

    assert "123-45-6789" not in scrubbed
    assert "987654321" not in scrubbed
    assert "MED-GLD-5446912" not in scrubbed
    assert "09/25/2025" not in scrubbed
    assert "$21,735.00" not in scrubbed
    assert "gsk_abcdef1234567890abcdef" not in scrubbed
    assert "[REDACTED]" in scrubbed


# ── Test 6: Safe Error Normalization & App Exception Preservation ────────────
def test_error_normalization_and_application_exception_preservation():
    """
    Asserts:
    1. Exceptions are classified into normalized non-sensitive categories.
    2. trace_span captures the normalized error without leaking raw exception parameters.
    3. The original application exception is re-raised without modification.
    """
    settings.LANGSMITH_TRACING = False

    # 1. Category normalization tests
    assert normalize_error_category(ValueError("Validation failed for CPT 36415")) == "VALIDATION_ERROR"
    assert normalize_error_category(TimeoutError("HTTP request timed out after 30s")) == "TIMEOUT"
    assert normalize_error_category(RuntimeError("HTTP 429 Rate limit exceeded")) == "RATE_LIMIT"
    assert normalize_error_category(PermissionError("401 Unauthorized access")) == "AUTH_ERROR"
    assert normalize_error_category(Exception("503 Service Unavailable")) == "PROVIDER_ERROR"

    # 2. Re-raising validation
    span_ref = None
    with (
        pytest.raises(ValueError, match="Domain rule violated"),
        trace_span(name="failing_operation", component="DomainValidator") as span,
    ):
        span_ref = span
        span.set_summary(document_count=3)
        raise ValueError("Domain rule violated")

    summary = span_ref.get_summary()
    assert summary["status"] == "FAILED"
    assert summary["error_category"] == "VALIDATION_ERROR"
    assert summary["error_type"] == "ValueError"
    assert summary["duration_ms"] >= 0.0


# ── Test 7: Observability Fault Isolation ─────────────────────────────────────
def test_observability_failure_does_not_break_business_logic():
    """
    If LangSmith's client raises an unexpected internal exception (e.g. network failure),
    the business logic must complete successfully and return its results.
    """
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"

    result = None
    with (
        mock.patch(
            "langsmith.trace",
            side_effect=RuntimeError("LangSmith API connection dropped"),
        ),
        trace_span(name="fault_tolerant_span", case_id="CASE-123") as span,
    ):
        span.set_summary(verdict="PASS")
        result = 42

    assert result == 42


# ── Test 8: Nested Span Hierarchy & Ephemeral Trace Correlation ──────────────
def test_nested_span_hierarchy_and_single_trace_tree():
    """
    When tracing is active, root and child spans are recorded in a single hierarchy,
    relying on ephemeral trace UUIDs rather than persistent case identifiers.
    """
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"

    mock_run_tree = mock.MagicMock()
    mock_run_tree.id = "mock-root-uuid"
    mock_run_tree.parent_run_id = None

    mock_child_tree = mock.MagicMock()
    mock_child_tree.id = "mock-child-uuid"
    mock_child_tree.parent_run_id = "mock-root-uuid"

    created_spans = []

    def mock_trace(name, **kwargs):
        created_spans.append({"name": name, "kwargs": kwargs})
        cm = mock.MagicMock()
        cm.__enter__.return_value = (
            mock_run_tree if len(created_spans) == 1 else mock_child_tree
        )
        return cm

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        trace_span(
            name="WorkflowCoordinator.execute",
            run_type="chain",
            case_id="CASE-C006-PT_39451",
            component="WorkflowCoordinator",
        ) as root_span,
    ):
        root_span.set_summary(status="SUCCESS")

        with trace_span(
            name="kyc_agent_node",
            run_type="chain",
            case_id="CASE-C006-PT_39451",
            component="KYCAgent",
        ) as child_span:
            child_span.set_summary(verdict="PASS", confidence=0.95)
            child_span.record_retry(1)

    assert len(created_spans) == 2
    root_call = created_spans[0]
    child_call = created_spans[1]

    # Verify names are operational, not case IDs
    assert root_call["name"] == "WorkflowCoordinator.execute"
    assert child_call["name"] == "kyc_agent_node"

    # Verify raw case ID is completely omitted by default
    assert "CASE-C006-PT_39451" not in str(root_call)
    assert "CASE-C006-PT_39451" not in str(child_call)
    assert "pseudonymous_case_id" not in root_call["kwargs"]["inputs"]


# ── Test 9: Agent-Only LangSmith Trace Filtering (LANGSMITH_AGENTS_ONLY) ──────
def test_langsmith_agents_only_filters_tools_and_allows_agents():
    """Asserts that when LANGSMITH_AGENTS_ONLY=True, only agent/workflow coordinator spans hit LangSmith."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-unit-key"
    settings.LANGSMITH_AGENTS_ONLY = True
    os.environ["LANGSMITH_TRACING"] = "true"

    created_spans: list[str] = []

    def mock_trace(name: str, **kwargs: Any) -> Any:
        created_spans.append(name)
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        # 1. Non-agent tool span should be filtered out
        with trace_span(name="storage_put_bytes", run_type="tool", component="storage_backend") as s1:
            s1.set_summary(status="SUCCESS")

        # 2. Non-agent OCR tool span should be filtered out
        with trace_span(name="ocr_process_document", run_type="tool", component="ocr_preprocessor_engine") as s2:
            s2.set_summary(status="SUCCESS")

        # 3. Non-agent DB lookup should be filtered out
        with trace_span(name="db_member_lookup", run_type="retriever", component="member_repository") as s3:
            s3.set_summary(status="SUCCESS")

        # 4. Agent nodes should be captured
        with trace_span(name="kyc_agent_node", run_type="chain", component="kyc_agent_node") as s4:
            s4.set_summary(verdict="PASS")

        with trace_span(name="claims_extraction_node", run_type="chain", component="claims_extraction_node") as s5:
            s5.set_summary(status="SUCCESS")

        with trace_span(name="fraud_agent_node", run_type="chain", component="fraud_agent_node") as s6:
            s6.set_summary(risk_level="LOW")

        with trace_span(name="CaseWorkflowCoordinator.execute", run_type="chain", component="CaseWorkflowCoordinator") as s7:
            s7.set_summary(decision="APPROVE")

    assert created_spans == [
        "kyc_agent_node",
        "claims_extraction_node",
        "fraud_agent_node",
        "CaseWorkflowCoordinator.execute",
    ]


# ── Test 10: Prohibition of Image and Raw Byte Payloads ─────────────────────
def test_langsmith_prohibits_image_and_byte_payloads():
    """Asserts that image bytes, base64 data URLs, and image keys are strictly rejected from LangSmith spans."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-unit-key"
    settings.LANGSMITH_AGENTS_ONLY = False
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_inputs: list[dict[str, Any]] = []

    def mock_trace(name: str, **kwargs: Any) -> Any:
        captured_inputs.append(kwargs.get("inputs", {}))
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        trace_span(
            name="kyc_agent_node",
            component="KYCAgent",
            inputs={
                "image": b"\x89PNG\r\n\x1a\nrawimagedata",
                "image_data": "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
                "raw_bytes": b"secretbinarybytes",
                "document_count": 2,
            },
        ) as span,
    ):
        span.set_summary(
            image_base64="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
            raw_bytes=b"\xff\xd8\xff\xe0",
            verdict="PASS",
        )
        summary = span.get_summary()

    # Verify input payload was sanitized
    assert len(captured_inputs) == 1
    inputs = captured_inputs[0]
    assert "image" not in inputs
    assert "image_data" not in inputs
    assert "raw_bytes" not in inputs
    assert inputs.get("document_count") == 2

    # Verify summary output was sanitized
    assert "image_base64" not in summary
    assert "raw_bytes" not in summary
    assert summary.get("verdict") == "PASS"
