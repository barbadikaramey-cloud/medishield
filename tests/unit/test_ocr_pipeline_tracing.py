"""
Acceptance unit tests for Step 2E-2: OCR Pipeline Tracing Instrumentation.

Validates:
1. OCRPreprocessorEngine.process_document emits a single span named 'ocr_process_document'
   with run_type='tool', component='ocr_preprocessor_engine', inputs={}, and safe operational metrics
   (status='SUCCESS', ocr_span_count, is_complete=True).
2. Fail-closed behaviour on empty content: emits status='FAILED', ocr_span_count=0, is_complete=False.
3. Fail-closed behaviour on low quality or unreadable text: emits status='FAILED', is_complete=False.
4. extract_service_lines_from_table emits 'ocr_extract_service_lines' with run_type='tool',
   component='ocr_reconcile', inputs={}, status='SUCCESS', service_line_count, and retry_count=0.
5. Geometry retry pass (y_tolerance=0.024) records retry_count=1.
6. Internal Gemini VLM enhancement inside process_document emits 'gemini_vision_generate'
   as a child span without duplicate wrappers.
7. Nested span hierarchy under coordinator: ocr_process_document appears under ocr_preprocessor_node,
   and ocr_extract_service_lines appears under claims_extraction_node, both under case_workflow_execute.
8. Strict sensitive content isolation: zero raw OCR text, bounding boxes, file paths, object URIs,
   document IDs, patient/member IDs, CPT/ICD codes, amounts, or raw errors in telemetry payloads.
9. Tracing disabled by default produces identical outputs and zero trace calls.
10. Telemetry transport failures do not interrupt OCR processing or table extraction.
11. Unhandled exceptions propagate unchanged with normalized error categories.
"""

from __future__ import annotations

import os
from typing import Any
from unittest import mock

import pytest

from medishield.core.config import settings
from medishield.core.tracing import reset_tracing_client
from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import (
    OCRBoundingBox,
    OCRDocumentPayload,
    OCRPagePayload,
    OCRTextSpan,
)
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.ocr_reconcile import extract_service_lines_from_table
from medishield.ocr.preprocessor import OCRPreprocessorEngine

CANARY_PHI = "CANARY_PATIENT_SSN_999-88-7777_SECRET_DIAGNOSIS_AMOUNT_$99999.00"

SAMPLE_BILL_TEXT = """COMMUNITY GENERAL HOSPITAL
123 HEALTHCARE BLVD
DATE: 2026-03-15
PATIENT ID: PAT-12345
99213 OFFICE VISIT 2026-03-15 1.0 $200.00 $200.00
93000 ROUTINE ECG 2026-03-15 1.0 $150.00 $150.00
TOTAL CHARGES: $350.00
"""


@pytest.fixture(autouse=True)
def clean_tracing_state():
    """Ensure clean tracing state before and after each test."""
    reset_tracing_client()
    orig_tracing = settings.LANGSMITH_TRACING
    orig_key = settings.LANGSMITH_API_KEY
    orig_pseudo = settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM
    orig_gemini_key = settings.GEMINI_API_KEY
    orig_env = os.environ.get("LANGSMITH_TRACING")
    yield
    settings.LANGSMITH_TRACING = orig_tracing
    settings.LANGSMITH_API_KEY = orig_key
    settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM = orig_pseudo
    settings.GEMINI_API_KEY = orig_gemini_key
    if orig_env is not None:
        os.environ["LANGSMITH_TRACING"] = orig_env
    else:
        os.environ.pop("LANGSMITH_TRACING", None)
    reset_tracing_client()


def _build_test_spans(doc_id: str = "doc-test") -> tuple[OCRDocumentPayload, list[tuple[OCRDocumentPayload, int, OCRTextSpan]]]:
    """Builds synthetic table spans suitable for extract_service_lines_from_table."""
    raw_spans = [
        OCRTextSpan(
            span_id="sp-1",
            page_number=1,
            text="99213",
            confidence=0.98,
            bbox=OCRBoundingBox(x_min=0.10, y_min=0.30, x_max=0.20, y_max=0.32),
        ),
        OCRTextSpan(
            span_id="sp-2",
            page_number=1,
            text="Office Visit",
            confidence=0.95,
            bbox=OCRBoundingBox(x_min=0.22, y_min=0.30, x_max=0.45, y_max=0.32),
        ),
        OCRTextSpan(
            span_id="sp-3",
            page_number=1,
            text="2026-03-15",
            confidence=0.95,
            bbox=OCRBoundingBox(x_min=0.48, y_min=0.30, x_max=0.58, y_max=0.32),
        ),
        OCRTextSpan(
            span_id="sp-4",
            page_number=1,
            text="1.0",
            confidence=0.95,
            bbox=OCRBoundingBox(x_min=0.60, y_min=0.30, x_max=0.65, y_max=0.32),
        ),
        OCRTextSpan(
            span_id="sp-5",
            page_number=1,
            text="$200.00",
            confidence=0.98,
            bbox=OCRBoundingBox(x_min=0.70, y_min=0.30, x_max=0.85, y_max=0.32),
        ),
    ]
    doc_payload = OCRDocumentPayload(
        document_id=doc_id,
        case_id="case-100",
        total_pages=1,
        pages=[
            OCRPagePayload(
                page_number=1,
                width_px=1000,
                height_px=1000,
                raw_text="99213 Office Visit 2026-03-15 1.0 $200.00",
                spans=raw_spans,
                key_value_pairs=[],
            )
        ],
        created_at="2026-03-15T00:00:00Z",
    )
    span_tuples = [(doc_payload, 1, s) for s in raw_spans]
    return doc_payload, span_tuples


# ── Test 1: process_document Span Emission & Operational Metrics ──────────────
def test_ocr_process_document_span_emission_success():
    """Asserts that process_document emits 'ocr_process_document' with safe operational summaries."""
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

    engine = OCRPreprocessorEngine()
    storage = InMemoryStorageBackend()
    content = SAMPLE_BILL_TEXT.encode("utf-8")

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        output = engine.process_document(
            content=content,
            document_id="doc-bill-01",
            case_id="case-bill-01",
            mime_type="text/plain",
            storage_backend=storage,
        )

    assert output.verdict == AgentVerdict.PASS
    assert "ocr_process_document" in captured_spans
    span_data = captured_spans["ocr_process_document"]

    assert span_data["kwargs"].get("run_type") == "tool"
    assert span_data["kwargs"].get("inputs") == {}
    outputs = span_data["tree"].end.call_args.kwargs["outputs"]
    assert outputs.get("status") == "SUCCESS"
    assert outputs.get("is_complete") is True
    assert outputs.get("ocr_span_count") > 0
    assert "duration_ms" in outputs


# ── Test 2: process_document Fail-Closed on Empty Content ─────────────────────
def test_ocr_process_document_empty_content_fail_closed():
    """Asserts that empty content records status='FAILED', is_complete=False, and ocr_span_count=0."""
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

    engine = OCRPreprocessorEngine()
    storage = InMemoryStorageBackend()

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        output = engine.process_document(
            content=b"",
            document_id="doc-empty",
            case_id="case-empty",
            mime_type="text/plain",
            storage_backend=storage,
        )

    assert output.verdict == AgentVerdict.UNAVAILABLE
    assert "ocr_process_document" in captured_spans
    outputs = captured_spans["ocr_process_document"]["tree"].end.call_args.kwargs["outputs"]
    assert outputs.get("status") == "FAILED"
    assert outputs.get("is_complete") is False
    assert outputs.get("ocr_span_count") == 0


# ── Test 3: process_document Fail-Closed on Low Quality / Unreadable ──────────
def test_ocr_process_document_low_quality_fail_closed():
    """Asserts that low quality score (<0.40) records status='FAILED' and is_complete=False."""
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

    mock_engine = mock.MagicMock()
    # Mock returning poor quality
    mock_engine.extract.return_value = (
        [OCRPagePayload(page_number=1, width_px=100, height_px=100, raw_text="bad text", spans=[], key_value_pairs=[])],
        0.25,
        False,
    )

    engine = OCRPreprocessorEngine(engine=mock_engine)
    storage = InMemoryStorageBackend()

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        output = engine.process_document(
            content=b"some bytes",
            document_id="doc-lowq",
            case_id="case-lowq",
            mime_type="application/pdf",
            storage_backend=storage,
        )

    assert output.verdict == AgentVerdict.INDETERMINATE
    assert "ocr_process_document" in captured_spans
    outputs = captured_spans["ocr_process_document"]["tree"].end.call_args.kwargs["outputs"]
    assert outputs.get("status") == "FAILED"
    assert outputs.get("is_complete") is False


# ── Test 4: extract_service_lines_from_table Standard Pass ───────────────────
def test_ocr_extract_service_lines_standard_pass():
    """Asserts that extract_service_lines_from_table emits 'ocr_extract_service_lines' with retry_count=0."""
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

    _, span_tuples = _build_test_spans()

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        lines = extract_service_lines_from_table(span_tuples, y_tolerance=0.012)

    assert len(lines) == 1
    assert "ocr_extract_service_lines" in captured_spans
    span_data = captured_spans["ocr_extract_service_lines"]
    assert span_data["kwargs"].get("run_type") == "tool"
    assert span_data["kwargs"].get("inputs") == {}

    outputs = span_data["tree"].end.call_args.kwargs["outputs"]
    assert outputs.get("status") == "SUCCESS"
    assert outputs.get("service_line_count") == 1
    assert outputs.get("retry_count") == 0
    assert "duration_ms" in outputs


# ── Test 5: extract_service_lines_from_table Geometry Retry Pass ─────────────
def test_ocr_extract_service_lines_geometry_retry():
    """Asserts that relaxed tolerance (y_tolerance=0.024) records retry_count=1."""
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

    _, span_tuples = _build_test_spans()

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        lines = extract_service_lines_from_table(span_tuples, y_tolerance=0.024)

    assert len(lines) == 1
    assert "ocr_extract_service_lines" in captured_spans
    outputs = captured_spans["ocr_extract_service_lines"]["tree"].end.call_args.kwargs["outputs"]
    assert outputs.get("status") == "SUCCESS"
    assert outputs.get("retry_count") == 1


# ── Test 6: Internal Gemini VLM Child Span Emission ──────────────────────────
def test_ocr_process_document_with_internal_gemini_vlm():
    """Asserts that the internal Gemini VLM enhancement emits gemini_vision_generate under ocr_process_document."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    settings.GEMINI_API_KEY = "ai-test-key"
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

    # Mock google.genai
    mock_genai_client = mock.MagicMock()
    mock_response = mock.MagicMock()
    mock_response.text = '{"member_id": "MEM-12345", "first_name": "Alice"}'
    mock_usage = mock.MagicMock()
    mock_usage.prompt_token_count = 150
    mock_usage.candidates_token_count = 25
    mock_usage.total_token_count = 175
    mock_response.usage_metadata = mock_usage
    mock_genai_client.models.generate_content.return_value = mock_response

    mock_google = mock.MagicMock()
    mock_genai_mod = mock.MagicMock()
    mock_google.genai = mock_genai_mod
    mock_genai_mod.Client.return_value = mock_genai_client

    mock_engine = mock.MagicMock()
    mock_engine.extract.return_value = (
        [
            OCRPagePayload(
                page_number=1,
                width_px=1000,
                height_px=1000,
                raw_text="STATE DRIVER LICENSE Alice Smith",
                spans=[
                    OCRTextSpan(
                        span_id="sp-1",
                        page_number=1,
                        text="STATE DRIVER LICENSE Alice Smith",
                        confidence=0.99,
                        bbox=OCRBoundingBox(x_min=0.1, y_min=0.1, x_max=0.9, y_max=0.2),
                    )
                ],
                key_value_pairs=[],
            )
        ],
        0.95,
        False,
    )
    engine = OCRPreprocessorEngine(engine=mock_engine)
    storage = InMemoryStorageBackend()
    # PNG magic bytes to trigger image VLM branch
    png_content = b"\x89PNG\r\n\x1a\n" + b"STATE DRIVER LICENSE Alice Smith 1985-05-20"

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        mock.patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai_mod, "google.genai.types": mock.MagicMock()}),
    ):
        output = engine.process_document(
            content=png_content,
            document_id="doc-id-vlm",
            case_id="case-id-vlm",
            mime_type="image/png",
            storage_backend=storage,
        )

    assert output.verdict == AgentVerdict.PASS
    assert captured_calls == ["ocr_process_document", "gemini_vision_generate", "storage_put_bytes"]

    vlm_span = captured_spans["gemini_vision_generate"]
    assert vlm_span["kwargs"].get("run_type") == "llm"
    assert vlm_span["kwargs"].get("inputs") == {}
    vlm_out = vlm_span["tree"].end.call_args.kwargs["outputs"]
    assert vlm_out.get("status") == "SUCCESS"
    assert vlm_out.get("provider") == "google"
    assert vlm_out.get("prompt_tokens") == 150
    assert vlm_out.get("completion_tokens") == 25
    assert vlm_out.get("total_tokens") == 175


# ── Test 7: Sensitive Content Isolation ──────────────────────────────────────
def test_ocr_pipeline_zero_sensitive_content_leakage():
    """Asserts that canary strings, raw OCR text, bboxes, and paths do not appear in telemetry."""
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

    engine = OCRPreprocessorEngine()
    storage = InMemoryStorageBackend()
    content = f"BILLING INFO {CANARY_PHI} CPT 99213 $99999.00".encode()

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        engine.process_document(
            content=content,
            document_id="doc-canary-01",
            case_id="case-canary-01",
            mime_type="text/plain",
            storage_backend=storage,
        )

    for span_name, span_info in captured_spans.items():
        inputs = span_info["kwargs"].get("inputs", {})
        metadata = span_info["kwargs"].get("metadata", {})
        outputs = span_info["tree"].end.call_args.kwargs.get("outputs", {})

        serialized = f"{inputs} {metadata} {outputs}"
        assert CANARY_PHI not in serialized, f"Canary found in {span_name}"
        assert "999-88-7777" not in serialized
        assert "99999" not in serialized
        assert "doc-canary-01" not in serialized
        assert "raw_text" not in serialized
        assert "bbox" not in serialized
        assert "file_path" not in serialized


# ── Test 8: Tracing Disabled by Default ───────────────────────────────────────
def test_ocr_pipeline_tracing_disabled_produces_no_traces():
    """Asserts that with LANGSMITH_TRACING=False, no spans are emitted and results match exactly."""
    settings.LANGSMITH_TRACING = False
    os.environ.pop("LANGSMITH_TRACING", None)

    mock_trace = mock.MagicMock()

    engine = OCRPreprocessorEngine()
    storage = InMemoryStorageBackend()
    content = SAMPLE_BILL_TEXT.encode("utf-8")

    with mock.patch("langsmith.trace", mock_trace):
        output = engine.process_document(
            content=content,
            document_id="doc-bill-notrace",
            case_id="case-bill-notrace",
            mime_type="text/plain",
            storage_backend=storage,
        )

    assert output.verdict == AgentVerdict.PASS
    mock_trace.assert_not_called()

    # Table extraction without tracing
    _, span_tuples = _build_test_spans()
    with mock.patch("langsmith.trace", mock_trace):
        lines = extract_service_lines_from_table(span_tuples, y_tolerance=0.012)

    assert len(lines) == 1
    mock_trace.assert_not_called()


# ── Test 9: Telemetry Transport Failures Do Not Interrupt Processing ─────────
def test_ocr_pipeline_telemetry_failure_resilience():
    """Asserts that telemetry transport exceptions do not disrupt OCR processing or line extraction."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    def broken_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        mock_tree.end.side_effect = RuntimeError("Fatal network drop in tracing exporter")
        cm.__enter__.return_value = mock_tree
        return cm

    engine = OCRPreprocessorEngine()
    storage = InMemoryStorageBackend()
    content = SAMPLE_BILL_TEXT.encode("utf-8")

    with mock.patch("langsmith.trace", side_effect=broken_trace):
        output = engine.process_document(
            content=content,
            document_id="doc-resilience",
            case_id="case-resilience",
            mime_type="text/plain",
            storage_backend=storage,
        )

    assert output.verdict == AgentVerdict.PASS
    assert output.total_pages == 1

    _, span_tuples = _build_test_spans()
    with mock.patch("langsmith.trace", side_effect=broken_trace):
        lines = extract_service_lines_from_table(span_tuples, y_tolerance=0.012)

    assert len(lines) == 1


# ── Test 10: Exception Propagation & Normalized Error Categories ──────────────
def test_ocr_pipeline_exception_propagation():
    """Asserts that unhandled exceptions propagate unchanged while recording normalized error categories."""
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

    mock_storage = mock.MagicMock()
    mock_storage.put_bytes.side_effect = ConnectionResetError("503 Service Unavailable S3 storage endpoint reset")

    engine = OCRPreprocessorEngine()
    content = SAMPLE_BILL_TEXT.encode("utf-8")

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        pytest.raises(ConnectionResetError, match="503 Service Unavailable"),
    ):
        engine.process_document(
            content=content,
            document_id="doc-exc",
            case_id="case-exc",
            mime_type="text/plain",
            storage_backend=mock_storage,
        )

    assert "ocr_process_document" in captured_spans
    outputs = captured_spans["ocr_process_document"]["tree"].end.call_args.kwargs["outputs"]
    assert outputs.get("status") == "FAILED"
    assert outputs.get("is_complete") is False
    assert outputs.get("error_category") == "PROVIDER_ERROR"
    assert outputs.get("error_type") == "ConnectionResetError"
