"""
Acceptance tests for Step 2A: CaseWorkflowCoordinator Root Trace Instrumentation.

Validates:
1. Root trace creation: CaseWorkflowCoordinator.execute produces a root trace when tracing is enabled.
2. Safe operational summary: Root trace contains only approved operational summaries (status, decision, document_count, duration_ms).
3. Tracing-disabled preservation: Execution with tracing disabled preserves exact existing behavior, decision, and audit ledger.
4. Error recording & propagation: Workflow exceptions are recorded as normalized error categories and original exceptions are re-raised.
5. Observability fault-tolerance: Telemetry failures (mock LangSmith crashes) do not alter business decisions or disrupt execution.
6. Zero sensitive data leakage: Synthetic sensitive canary values in inputs or exception messages never appear in trace payloads.
7. Single root span creation: Exactly one root span is created for a single coordinator execution in Step 2A.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any
from unittest import mock

import pytest

from medishield.agents.claims import ClaimsAgent
from medishield.agents.classifier import DocumentClassifierAgent
from medishield.agents.kyc import InMemoryMemberRepository, KYCAgent, MemberRecord
from medishield.agents.policy import (
    InMemoryPolicyEnrollmentRepository,
    PolicyChunkRecord,
    PolicyContextAgent,
    PolicyCorpusRepository,
    PolicyCoverageAgent,
    PolicyEnrollmentRecord,
)
from medishield.core.config import settings
from medishield.core.tracing import reset_tracing_client
from medishield.db.enums import BusinessDecision, DocumentType, WorkflowState
from medishield.domain.fraud import DeterministicFraudEngine
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.preprocessor import DefaultOCREngine, OCRPreprocessorEngine
from medishield.workflows.coordinator import CaseWorkflowCoordinator
from medishield.workflows.state import DocumentReference, create_initial_case_state

SAMPLE_ID_TEXT = """STATE DRIVER LICENSE
DL NUMBER: D9817264
MEMBER ID: MEM-12345
NAME: Alice Smith
DOB: 1985-05-20
EXP DATE: 2030-01-01
"""

SAMPLE_CLAIM_TEXT = """HEALTH INSURANCE CLAIM FORM
PROVIDER: Dr. Sarah Jenkins Hospital
NPI: 1234567890
DATE OF SERVICE: 2026-03-15
DIAGNOSIS: J02.9
PROCEDURE: 99213
1. 99213 Office Visit 2026-03-15 1.0 $200.00 $200.00
2. 93000 Routine ECG 2026-03-15 1.0 $150.00 $150.00
TOTAL CHARGE: $350.00
"""

SAMPLE_DISCHARGE_SUMMARY_TEXT = """HOSPITAL CLINICAL VISIT NOTE / DISCHARGE SUMMARY
PATIENT: Alice Smith
MEMBER ID: MEM-12345
DATE OF SERVICE: 2026-03-15
ATTENDING PHYSICIAN: Dr. Sarah Jenkins, MD

CLINICAL IMPRESSION & FINDINGS:
Patient presented for outpatient evaluation and management office visit (99213).
Low complexity medical decision making confirmed. Physical exam and vital signs recorded.
Diagnostic 12-lead electrocardiogram (93000 Routine ECG) performed and interpreted. Normal sinus rhythm noted without acute ST elevation.
Treatment plan discussed, prescription provided. Patient discharged home in stable condition.
"""


def _setup_test_environment() -> tuple[CaseWorkflowCoordinator, InMemoryStorageBackend, list[dict[str, Any]]]:
    """Helper setting up configured coordinator, storage, and repositories."""
    storage = InMemoryStorageBackend()
    ocr_engine = OCRPreprocessorEngine(engine=DefaultOCREngine())

    member_repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
            ssn_last4="4321",
        )
    ])
    kyc_agent = KYCAgent(member_repo=member_repo)
    claims_agent = ClaimsAgent()

    enrollment_repo = InMemoryPolicyEnrollmentRepository([
        PolicyEnrollmentRecord(
            member_id="MEM-12345",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=date(2026, 12, 31),
        ),
    ])
    policy_context_agent = PolicyContextAgent(enrollment_repo=enrollment_repo)

    corpus = PolicyCorpusRepository()
    corpus.add_chunk(
        PolicyChunkRecord(
            chunk_id="chk-001",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            section_number="SEC-3.1",
            section_title="Outpatient Evaluation and Management",
            chunk_text="Outpatient office visits are covered benefits.",
            page_number=12,
            is_exclusion=False,
            requires_prior_auth=False,
            covered_percentage=80.0,
            deductible_limit=500.0,
            copay_percentage=20.0,
            matching_cpt_codes=["99213", "99214", "93000"],
        )
    )
    policy_coverage_agent = PolicyCoverageAgent(corpus_repo=corpus)

    fraud_engine = DeterministicFraudEngine
    classifier_agent = DocumentClassifierAgent()
    audit_ledger: list[dict[str, Any]] = []

    coordinator = CaseWorkflowCoordinator(
        storage=storage,
        ocr_engine=ocr_engine,
        classifier_agent=classifier_agent,
        kyc_agent=kyc_agent,
        claims_agent=claims_agent,
        policy_context_agent=policy_context_agent,
        policy_coverage_agent=policy_coverage_agent,
        fraud_engine=fraud_engine,
        policy_corpus=corpus,
        audit_ledger=audit_ledger,
    )

    return coordinator, storage, audit_ledger


def _upload_test_document(
    storage: InMemoryStorageBackend,
    case_id: str,
    doc_id: str,
    text_content: str,
    doc_type: str | None = None,
) -> DocumentReference:
    """Helper storing raw text bytes in storage and returning a DocumentReference."""
    key = f"uploads/{case_id}/{doc_id}.txt"
    storage.put_bytes(key, text_content.encode("utf-8"), "text/plain")
    return DocumentReference(
        document_id=doc_id,
        object_key=key,
        mime_type="text/plain",
        checksum="dummy-hash",
        split_source_page_indices=[1],
        file_name=f"{doc_id}.txt",
        doc_type=doc_type,
    )


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


# ── Test 1: Coordinator Produces Root Trace When Enabled ────────────────────
def test_coordinator_produces_root_trace_when_enabled():
    """Validates that CaseWorkflowCoordinator.execute produces a root span when tracing is enabled."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-trace-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage,
        case_id,
        "doc-claim",
        SAMPLE_CLAIM_TEXT,
        DocumentType.CLAIM_FORM.value,
    )
    doc_clin = _upload_test_document(
        storage,
        case_id,
        "doc-clin",
        SAMPLE_DISCHARGE_SUMMARY_TEXT,
        DocumentType.DISCHARGE_SUMMARY.value,
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim, doc_clin])

    captured_traces = []

    def mock_trace(name, **kwargs):
        captured_traces.append({"name": name, "kwargs": kwargs})
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        result = coordinator.execute(state)

    assert result["decision"] == BusinessDecision.APPROVE.value
    assert len(captured_traces) >= 1
    root = captured_traces[0]
    assert root["name"] == "case_workflow_execute"
    assert root["kwargs"]["run_type"] == "chain"


# ── Test 2: Root Trace Contains Only Approved Operational Summaries ─────────
def test_coordinator_root_trace_contains_only_approved_summaries():
    """Asserts that root trace captures strictly approved operational summaries (status, decision, document_count)."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-trace-02"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage,
        case_id,
        "doc-claim",
        SAMPLE_CLAIM_TEXT,
        DocumentType.CLAIM_FORM.value,
    )
    doc_clin = _upload_test_document(
        storage,
        case_id,
        "doc-clin",
        SAMPLE_DISCHARGE_SUMMARY_TEXT,
        DocumentType.DISCHARGE_SUMMARY.value,
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim, doc_clin])

    captured_spans = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        coordinator.execute(state)

    assert "case_workflow_execute" in captured_spans
    root_data = captured_spans["case_workflow_execute"]
    captured_kwargs = root_data["kwargs"]
    captured_tree = root_data["tree"]

    # Verify inputs contains only safe operational count
    assert captured_kwargs["inputs"] == {"document_count": 3}
    # Verify no raw case ID or patient names in inputs or tags
    assert case_id not in str(captured_kwargs["inputs"])
    assert "Alice" not in str(captured_kwargs)

    # Verify outputs passed to run_tree.end
    end_calls = captured_tree.end.call_args_list
    assert len(end_calls) >= 1
    outputs = end_calls[0].kwargs.get("outputs") or {}

    assert outputs.get("status") == "SUCCESS"
    assert outputs.get("decision") == "APPROVE"
    assert outputs.get("document_count") == 3
    assert "duration_ms" in outputs

    # Verify prohibited fields are absent
    for prohibited in ("cpt", "amount", "patient_name", "member_id", "service_date", "case_id"):
        assert prohibited not in outputs


# ── Test 3: Tracing Disabled Preserves Existing Behavior ─────────────────────
def test_coordinator_tracing_disabled_preserves_behavior():
    """Verifies that with tracing disabled, coordinator execution is 100% identical and zero telemetry calls occur."""
    settings.LANGSMITH_TRACING = False

    coordinator, storage, audit_ledger = _setup_test_environment()
    case_id = "case-disabled-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage,
        case_id,
        "doc-claim",
        SAMPLE_CLAIM_TEXT,
        DocumentType.CLAIM_FORM.value,
    )
    doc_clin = _upload_test_document(
        storage,
        case_id,
        "doc-clin",
        SAMPLE_DISCHARGE_SUMMARY_TEXT,
        DocumentType.DISCHARGE_SUMMARY.value,
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim, doc_clin])

    with mock.patch("langsmith.trace") as mock_trace:
        result = coordinator.execute(state)

    assert mock_trace.call_count == 0
    assert result["workflow_state"] == WorkflowState.COMPLETED.value
    assert result["decision"] == BusinessDecision.APPROVE.value
    assert result["rule_id_matched"] == "RULE-P6-CLEAN-PASS"
    assert len(audit_ledger) >= 1


# ── Test 4: Workflow Errors Are Recorded Safely & Original Exception Propagated ─
def test_coordinator_workflow_error_recording_and_propagation():
    """Asserts that when a node fails, the exception is propagated without alteration and recorded safely."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-error-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage,
        case_id,
        "doc-claim",
        SAMPLE_CLAIM_TEXT,
        DocumentType.CLAIM_FORM.value,
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    captured_spans = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    canary_error_message = (
        "Database failure while querying patient Alice Montgomery-Smith SSN 999-00-1111"
    )

    # Force KYC agent to throw a database error with canary text
    coordinator.kyc_agent.execute = mock.MagicMock(
        side_effect=RuntimeError(canary_error_message)
    )

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        pytest.raises(RuntimeError, match="Database failure while querying patient"),
    ):
        coordinator.execute(state)

    # Verify error was captured safely without canary leak on root trace
    assert "case_workflow_execute" in captured_spans
    captured_tree = captured_spans["case_workflow_execute"]["tree"]
    end_kwargs = captured_tree.end.call_args.kwargs
    outputs = end_kwargs.get("outputs") or {}
    error_str = end_kwargs.get("error")

    assert outputs.get("status") == "FAILED"
    assert outputs.get("error_category") == "DB_ERROR"
    assert outputs.get("error_type") == "RuntimeError"

    # Ensure canary values in exception message were NOT included in the error field
    assert "999-00-1111" not in str(error_str)
    assert "Alice Montgomery-Smith" not in str(error_str)
    assert error_str == "DB_ERROR: RuntimeError"


# ── Test 5: Observability Failure Does Not Alter Business Result ─────────────
def test_coordinator_tracing_failure_does_not_break_business_decision():
    """Observability failures (mock LangSmith internal errors) must not disrupt claim execution or alter decision."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, audit_ledger = _setup_test_environment()
    case_id = "case-trace-fault-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage,
        case_id,
        "doc-claim",
        SAMPLE_CLAIM_TEXT,
        DocumentType.CLAIM_FORM.value,
    )
    doc_clin = _upload_test_document(
        storage,
        case_id,
        "doc-clin",
        SAMPLE_DISCHARGE_SUMMARY_TEXT,
        DocumentType.DISCHARGE_SUMMARY.value,
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim, doc_clin])

    with mock.patch(
        "langsmith.trace", side_effect=RuntimeError("LangSmith network unreachable")
    ):
        result = coordinator.execute(state)

    assert result["workflow_state"] == WorkflowState.COMPLETED.value
    assert result["decision"] == BusinessDecision.APPROVE.value
    assert result["rule_id_matched"] == "RULE-P6-CLEAN-PASS"
    assert len(audit_ledger) >= 1


# ── Test 6: Zero Sensitive Canary Leakage in Inputs, Outputs, Metadata ────────
def test_coordinator_trace_zero_canary_leakage():
    """Verifies that synthetic canary values in state, inputs, or exception messages never leak into telemetry."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-canary-01"

    canary_patient_name = "Canary John Doe"
    canary_ssn = "111-22-3333"
    canary_amount = 77777.77

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage,
        case_id,
        "doc-claim",
        SAMPLE_CLAIM_TEXT,
        DocumentType.CLAIM_FORM.value,
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    # Inject canary fields into state
    state["canary_patient_name"] = canary_patient_name
    state["canary_ssn"] = canary_ssn
    state["canary_amount"] = canary_amount

    captured_tree = None
    captured_kwargs = None

    def mock_trace(name, **kwargs):
        nonlocal captured_kwargs, captured_tree
        captured_kwargs = kwargs
        cm = mock.MagicMock()
        captured_tree = mock.MagicMock()
        cm.__enter__.return_value = captured_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        coordinator.execute(state)

    trace_dump = str(captured_kwargs) + str(captured_tree.end.call_args)
    assert canary_patient_name not in trace_dump
    assert canary_ssn not in trace_dump
    assert str(canary_amount) not in trace_dump


# ── Test 7: Exactly One Root Span Created in Step 2A ─────────────────────────
def test_coordinator_creates_single_root_span():
    """Validates that exactly one root trace is created for a single coordinator execution in Step 2A."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-single-span-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage,
        case_id,
        "doc-claim",
        SAMPLE_CLAIM_TEXT,
        DocumentType.CLAIM_FORM.value,
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    span_names = []

    def mock_trace(name, **kwargs):
        span_names.append(name)
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        coordinator.execute(state)

    # In Step 2D/2E, the coordinator root span, intake child spans, specialist child spans,
    # evaluation/decision child spans, and child model client spans are emitted under one root trace.
    assert span_names[0] == "case_workflow_execute"
    assert span_names.count("case_workflow_execute") == 1

    expected_node_spans = [
        "case_workflow_execute",
        "ocr_preprocessor_node",
        "split_group_node",
        "classifier_node",
        "case_completeness_node",
        "kyc_agent_node",
        "claims_extraction_node",
        "policy_context_node",
        "policy_coverage_rag_node",
        "fraud_agent_node",
        "clinical_alignment_node",
        "faithfulness_gate_node",
        "decision_engine_node",
    ]
    workflow_node_spans = [s for s in span_names if s in expected_node_spans]
    assert workflow_node_spans == expected_node_spans
    for s in span_names:
        assert (
            s in expected_node_spans
            or s.endswith("_generate")
            or s in (
                "ocr_process_document",
                "ocr_extract_service_lines",
                "policy_corpus_query",
                "storage_get_bytes",
                "storage_put_bytes",
                "db_member_lookup",
                "db_policy_enrollment_lookup",
                "db_case_state_persistence",
                "db_audit_trail_retrieval",
            )
        )
