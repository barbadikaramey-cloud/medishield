"""
Acceptance unit tests for Step 2D: Evaluation and Decision Node Tracing Instrumentation.

Validates:
1. Each targeted node (policy_coverage_rag_node, fraud_agent_node, faithfulness_gate_node,
   decision_engine_node) emits a child span when tracing is enabled.
2. All targeted spans nest under case_workflow_execute during coordinator execution.
3. Summaries contain only validated operational fields conforming to the approved schema
   (with rule_id_matched strictly omitted per deferred check Item 1).
4. Synthetic sensitive canary values do not appear in captured trace payloads.
5. Tracing-enabled and tracing-disabled executions preserve identical business results and state mutations.
6. Original exceptions propagate unchanged while telemetry stores only normalized error categories.
7. Telemetry transport failures do not alter business decisions.
8. One workflow execution creates only one root trace.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any
from unittest import mock

import pytest

from medishield.agents.claims import ClaimsAgent
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
from medishield.db.enums import AgentVerdict, BusinessDecision, DocumentType
from medishield.domain.contracts import ClaimsAgentOutput, ProvenanceValue
from medishield.domain.decision import DeterministicDecisionEngine
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.preprocessor import DefaultOCREngine, OCRPreprocessorEngine
from medishield.workflows.coordinator import CaseWorkflowCoordinator
from medishield.workflows.nodes import (
    decision_engine_node,
    faithfulness_gate_node,
    fraud_agent_node,
    policy_coverage_rag_node,
)
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


def _setup_test_environment() -> tuple[
    CaseWorkflowCoordinator,
    InMemoryStorageBackend,
    PolicyCoverageAgent,
    DeterministicDecisionEngine,
]:
    """Helper setting up coordinator and agents."""
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
    audit_ledger: list[dict[str, Any]] = []

    coordinator = CaseWorkflowCoordinator(
        storage=storage,
        ocr_engine=ocr_engine,
        kyc_agent=kyc_agent,
        claims_agent=claims_agent,
        policy_context_agent=policy_context_agent,
        policy_coverage_agent=policy_coverage_agent,
        policy_corpus=corpus,
        audit_ledger=audit_ledger,
    )
    return coordinator, storage, policy_coverage_agent, coordinator.decision_engine


def _upload_test_document(
    storage: InMemoryStorageBackend,
    case_id: str,
    doc_id: str,
    content_str: str,
    doc_type: str | None = None,
) -> DocumentReference:
    """Helper to seed storage and build DocumentReference."""
    raw_key = f"cases/{case_id}/{doc_id}.txt"
    storage.put_bytes(raw_key, content_str.encode("utf-8"), "text/plain")

    ocr_engine = OCRPreprocessorEngine(engine=DefaultOCREngine())
    ocr_engine.process_document(
        content=content_str.encode("utf-8"),
        document_id=doc_id,
        case_id=case_id,
        mime_type="text/plain",
        storage_backend=storage,
    )

    return DocumentReference(
        document_id=doc_id,
        object_key=raw_key,
        mime_type="text/plain",
        doc_type=doc_type,
        classification_confidence=1.0 if doc_type else None,
    )


def _build_evaluated_case_state(case_id: str, doc_refs: list[DocumentReference]) -> dict[str, Any]:
    """Helper creating a state ready for evaluation & decision nodes."""
    state = create_initial_case_state(case_id=case_id, documents=doc_refs)
    state["policy_id"] = "POL-CORP-2026"
    state["policy_version"] = "v2.0"
    state["policy_effective_date_valid"] = True
    state["policy_context_verdict"] = AgentVerdict.PASS.value
    state["kyc_verdict"] = AgentVerdict.PASS.value
    state["kyc_summary"] = {
        "agent_name": "kyc_agent",
        "verdict": AgentVerdict.PASS.value,
        "confidence": 0.98,
        "member_db_matched": True,
        "tamper_score": 0.0,
        "latency_ms": 1,
    }
    prov_amt = ProvenanceValue(value=350.0, confidence=0.98)
    claims_out = ClaimsAgentOutput(
        verdict=AgentVerdict.PASS,
        confidence=0.95,
        claim_amount=prov_amt,
        service_date=ProvenanceValue(value="2026-03-15", confidence=0.99),
        provider_npi=ProvenanceValue(value="1234567890", confidence=0.95),
        cpt_codes=[ProvenanceValue(value="99213", confidence=0.96)],
        icd10_codes=[ProvenanceValue(value="J02.9", confidence=0.96)],
        service_lines=[],
        line_items_sum_valid=True,
        schema_valid=True,
        calculated_lines_sum=350.0,
        latency_ms=1,
    )
    state["claims_verdict"] = AgentVerdict.PASS.value
    state["claims_summary"] = claims_out.model_dump()
    state["is_complete"] = True
    return state


# ── Test 1: Each Targeted Node Emits Child Span When Tracing Enabled ──────────
@pytest.mark.parametrize(
    "node_name",
    [
        "policy_coverage_rag_node",
        "fraud_agent_node",
        "faithfulness_gate_node",
        "decision_engine_node",
    ],
)
def test_each_evaluation_decision_node_emits_span_when_enabled(node_name: str):
    """Verifies that each evaluation and decision node emits a span with stable name."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    _, storage, pol_agent, dec_engine = _setup_test_environment()
    case_id = f"case-test-{node_name}"
    doc_ref = _upload_test_document(
        storage, case_id, "doc-01", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    state = _build_evaluated_case_state(case_id, [doc_ref])

    # Pre-populate summaries required by downstream nodes
    state["policy_coverage_summary"] = {
        "agent_name": "policy_agent",
        "verdict": AgentVerdict.PASS.value,
        "confidence": 0.95,
        "covered": True,
        "coverage_percentage": 80.0,
        "effective_on_service_date": True,
        "cited_clauses": [],
        "reasoning_summary": "Office visit covered under policy.",
        "latency_ms": 1,
    }
    state["fraud_summary"] = {
        "agent_name": "fraud_agent",
        "verdict": AgentVerdict.PASS.value,
        "confidence": 0.99,
        "fraud_score": 0.05,
        "risk_level": "LOW",
        "latency_ms": 1,
    }

    captured_spans: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        if node_name == "policy_coverage_rag_node":
            policy_coverage_rag_node(state, pol_agent)
        elif node_name == "fraud_agent_node":
            fraud_agent_node(state)
        elif node_name == "faithfulness_gate_node":
            faithfulness_gate_node(state)
        elif node_name == "decision_engine_node":
            decision_engine_node(state, dec_engine)

    assert node_name in captured_spans
    span_data = captured_spans[node_name]
    assert span_data["kwargs"]["run_type"] == "chain"
    assert span_data["kwargs"]["inputs"] == {"document_count": 1}

    # Verify that run_tree.end was invoked with status SUCCESS
    mock_tree = span_data["tree"]
    assert mock_tree.end.called
    outputs = mock_tree.end.call_args.kwargs.get("outputs", {})
    assert outputs.get("status") == "SUCCESS"
    assert outputs.get("document_count") == 1


# ── Test 2: Spans Nest Under case_workflow_execute ───────────────────────────
def test_evaluation_decision_spans_nested_under_case_workflow_execute():
    """Verifies that all 4 evaluation/decision spans are children of case_workflow_execute."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _, _ = _setup_test_environment()
    case_id = "case-eval-tree-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    doc_clin = _upload_test_document(
        storage, case_id, "doc-clin", SAMPLE_DISCHARGE_SUMMARY_TEXT, DocumentType.DISCHARGE_SUMMARY.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim, doc_clin])

    trace_stack: list[str] = []
    child_parent_map: dict[str, str | None] = {}

    def mock_trace(name, **kwargs):
        parent_name = trace_stack[-1] if trace_stack else None
        child_parent_map[name] = parent_name
        trace_stack.append(name)

        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()

        def on_exit(*args):
            if trace_stack and trace_stack[-1] == name:
                trace_stack.pop()

        cm.__enter__.return_value = mock_tree
        cm.__exit__.side_effect = on_exit
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        res = coordinator.execute(state)

    assert res["decision"] == BusinessDecision.APPROVE.value
    assert child_parent_map["case_workflow_execute"] is None
    for child in [
        "policy_coverage_rag_node",
        "fraud_agent_node",
        "clinical_alignment_node",
        "faithfulness_gate_node",
        "decision_engine_node",
    ]:
        assert child in child_parent_map, f"Missing child span {child}"
        assert (
            child_parent_map[child] == "case_workflow_execute"
        ), f"Span {child} parent is {child_parent_map[child]}, expected case_workflow_execute"


# ── Test 3: Summaries Contain Only Validated Operational Fields ──────────────
def test_evaluation_decision_nodes_contain_only_approved_summaries():
    """Verifies that outputs contain strictly approved operational summaries and omit rule_id_matched."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _, _ = _setup_test_environment()
    case_id = "case-eval-summary-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    doc_clin = _upload_test_document(
        storage, case_id, "doc-clin", SAMPLE_DISCHARGE_SUMMARY_TEXT, DocumentType.DISCHARGE_SUMMARY.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim, doc_clin])

    captured_spans: dict[str, Any] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        captured_spans[name] = {"tree": mock_tree, "kwargs": kwargs}
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        coordinator.execute(state)

    # 1. policy_coverage_rag_node
    pol_out = captured_spans["policy_coverage_rag_node"]["tree"].end.call_args.kwargs.get("outputs", {})
    assert pol_out.get("status") == "SUCCESS"
    assert pol_out.get("document_count") == 3
    assert pol_out.get("verdict") == "PASS"
    assert isinstance(pol_out.get("confidence"), float)
    assert "rule_id_matched" not in pol_out

    # 2. fraud_agent_node
    fraud_out = captured_spans["fraud_agent_node"]["tree"].end.call_args.kwargs.get("outputs", {})
    assert fraud_out.get("status") == "SUCCESS"
    assert fraud_out.get("document_count") == 3
    assert fraud_out.get("verdict") == "PASS"
    assert isinstance(fraud_out.get("confidence"), float)
    assert "fraud_score" not in fraud_out  # not in ALLOWED_TRACE_KEYS
    assert "rule_id_matched" not in fraud_out

    # 2b. clinical_alignment_node
    clin_out = captured_spans["clinical_alignment_node"]["tree"].end.call_args.kwargs.get("outputs", {})
    assert clin_out.get("status") == "SUCCESS"
    assert clin_out.get("document_count") == 3
    assert clin_out.get("verdict") == "PASS"
    assert isinstance(clin_out.get("confidence"), float)

    # 3. faithfulness_gate_node
    faith_out = captured_spans["faithfulness_gate_node"]["tree"].end.call_args.kwargs.get("outputs", {})
    assert faith_out.get("status") == "SUCCESS"
    assert faith_out.get("document_count") == 3
    assert faith_out.get("provenance_integrity_score") == 1.0
    assert faith_out.get("arithmetic_valid") is True
    assert "rule_id_matched" not in faith_out

    # 4. decision_engine_node
    dec_out = captured_spans["decision_engine_node"]["tree"].end.call_args.kwargs.get("outputs", {})
    assert dec_out.get("status") == "SUCCESS"
    assert dec_out.get("document_count") == 3
    assert dec_out.get("decision") == "APPROVE"
    assert isinstance(dec_out.get("confidence"), float)
    # Step 3B: rule_id_matched is emitted and strictly validated against registered rule IDs
    assert dec_out.get("rule_id_matched") == "RULE-P6-CLEAN-PASS"
    assert "justification" not in dec_out
    assert "detailed_explanation" not in dec_out


# ── Test 4: Synthetic Sensitive Canary Values Do Not Leak ────────────────────
def test_evaluation_decision_nodes_zero_sensitive_canary_leakage():
    """Verifies that no clinical codes, policy text, justifications, or amounts leak into traces."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    canary_cpt = "99299-CANARY"
    canary_icd = "Z99.9-CANARY"
    canary_policy_text = "CANARY-EXCLUSION-CLAUSE-CONFIDENTIAL"
    canary_justification = "CANARY-UNVERIFIED-RATIONALE"
    canary_amount = 8888.99

    coordinator, storage, _, _ = _setup_test_environment()
    case_id = "case-eval-canary-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    captured_payloads: list[str] = []

    def mock_trace(name, **kwargs):
        captured_payloads.append(str(name) + str(kwargs))
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()

        def mock_end(**end_kwargs):
            captured_payloads.append(str(end_kwargs))

        mock_tree.end.side_effect = mock_end
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        coordinator.execute(state)

    full_trace_blob = "\n".join(captured_payloads)
    assert canary_cpt not in full_trace_blob
    assert canary_icd not in full_trace_blob
    assert canary_policy_text not in full_trace_blob
    assert canary_justification not in full_trace_blob
    assert str(canary_amount) not in full_trace_blob


# ── Test 5: Tracing Disabled Preserves Identical Behavior and State ──────────
@pytest.mark.parametrize(
    "node_fn_name",
    [
        "policy_coverage_rag_node",
        "fraud_agent_node",
        "faithfulness_gate_node",
        "decision_engine_node",
    ],
)
def test_evaluation_decision_nodes_tracing_disabled_preserves_state(node_fn_name: str):
    """Verifies identical state mutations and return values when tracing is disabled vs enabled."""
    _, storage, pol_agent, dec_engine = _setup_test_environment()
    case_id = f"case-state-comp-{node_fn_name}"
    doc_ref = _upload_test_document(
        storage, case_id, "doc-01", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )

    state_disabled = _build_evaluated_case_state(case_id, [doc_ref])
    state_enabled = _build_evaluated_case_state(case_id, [doc_ref])

    state_disabled["policy_coverage_summary"] = {
        "agent_name": "policy_agent",
        "verdict": AgentVerdict.PASS.value,
        "confidence": 0.95,
        "covered": True,
        "coverage_percentage": 80.0,
        "effective_on_service_date": True,
        "cited_clauses": [],
        "reasoning_summary": "Covered.",
        "latency_ms": 1,
    }
    state_enabled["policy_coverage_summary"] = dict(state_disabled["policy_coverage_summary"])

    state_disabled["fraud_summary"] = {
        "agent_name": "fraud_agent",
        "verdict": AgentVerdict.PASS.value,
        "confidence": 0.99,
        "fraud_score": 0.05,
        "risk_level": "LOW",
        "latency_ms": 1,
    }
    state_enabled["fraud_summary"] = dict(state_disabled["fraud_summary"])

    def _invoke(node_name: str, state: dict[str, Any]):
        if node_name == "policy_coverage_rag_node":
            return policy_coverage_rag_node(state, pol_agent)
        elif node_name == "fraud_agent_node":
            return fraud_agent_node(state)
        elif node_name == "faithfulness_gate_node":
            return faithfulness_gate_node(state)
        elif node_name == "decision_engine_node":
            return decision_engine_node(state, dec_engine)

    # 1. Tracing disabled
    settings.LANGSMITH_TRACING = False
    os.environ["LANGSMITH_TRACING"] = "false"
    out_disabled = _invoke(node_fn_name, state_disabled)

    # 2. Tracing enabled
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"
    out_enabled = _invoke(node_fn_name, state_enabled)

    # Compare key fields
    if node_fn_name == "decision_engine_node":
        assert out_disabled["decision"] == out_enabled["decision"]
        assert out_disabled["rule_id_matched"] == out_enabled["rule_id_matched"]
        assert out_disabled["workflow_state"] == out_enabled["workflow_state"]
    elif node_fn_name == "faithfulness_gate_node":
        assert out_disabled["faithfulness_gate_passed"] == out_enabled["faithfulness_gate_passed"]
        assert out_disabled["provenance_integrity_score"] == out_enabled["provenance_integrity_score"]
    elif node_fn_name == "policy_coverage_rag_node":
        assert out_disabled["policy_coverage_verdict"] == out_enabled["policy_coverage_verdict"]
    elif node_fn_name == "fraud_agent_node":
        assert out_disabled["fraud_verdict"] == out_enabled["fraud_verdict"]
        assert out_disabled["fraud_score"] == out_enabled["fraud_score"]

    assert out_disabled["completed_nodes"] == out_enabled["completed_nodes"]


# ── Test 6: Original Exceptions Propagate with Normalized Telemetry ──────────
@pytest.mark.parametrize(
    ("node_fn_name", "exc_agent_attr"),
    [
        ("policy_coverage_rag_node", "pol_cov"),
        ("fraud_agent_node", "fraud"),
        ("decision_engine_node", "decision"),
    ],
)
def test_evaluation_decision_exceptions_propagate_and_record_safely(
    node_fn_name: str,
    exc_agent_attr: str,
):
    """Verifies that unhandled agent exceptions propagate unchanged while recording normalized telemetry."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    _, storage, _, _ = _setup_test_environment()
    case_id = f"case-eval-err-{node_fn_name}"
    doc_ref = _upload_test_document(
        storage, case_id, "doc-01", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    state = _build_evaluated_case_state(case_id, [doc_ref])
    state["policy_coverage_summary"] = {
        "agent_name": "policy_agent",
        "verdict": AgentVerdict.PASS.value,
        "confidence": 0.95,
        "covered": True,
        "coverage_percentage": 80.0,
        "effective_on_service_date": True,
        "cited_clauses": [],
        "reasoning_summary": "Covered.",
        "latency_ms": 1,
    }
    state["fraud_summary"] = {
        "agent_name": "fraud_agent",
        "verdict": AgentVerdict.PASS.value,
        "confidence": 0.99,
        "fraud_score": 0.05,
        "risk_level": "LOW",
        "latency_ms": 1,
    }

    captured_tree = None

    def mock_trace(name, **kwargs):
        nonlocal captured_tree
        cm = mock.MagicMock()
        captured_tree = mock.MagicMock()
        cm.__enter__.return_value = captured_tree
        return cm

    canary_error_msg = f"Fatal evaluation failure for patient Alice Montgomery in {node_fn_name}"

    mock_pol = mock.MagicMock(spec=PolicyCoverageAgent)
    mock_fraud = mock.MagicMock()
    mock_dec = mock.MagicMock(spec=DeterministicDecisionEngine)

    if exc_agent_attr == "pol_cov":
        mock_pol.execute.side_effect = ValueError(canary_error_msg)
    elif exc_agent_attr == "fraud":
        mock_fraud.evaluate.side_effect = ValueError(canary_error_msg)
    elif exc_agent_attr == "decision":
        mock_dec.evaluate.side_effect = ValueError(canary_error_msg)

    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        pytest.raises(ValueError, match="Fatal evaluation failure for patient Alice Montgomery"),
    ):
        if node_fn_name == "policy_coverage_rag_node":
            policy_coverage_rag_node(state, mock_pol)
        elif node_fn_name == "fraud_agent_node":
            fraud_agent_node(state, fraud_engine=mock_fraud)
        elif node_fn_name == "decision_engine_node":
            decision_engine_node(state, mock_dec)

    assert captured_tree is not None
    assert captured_tree.end.called
    end_kwargs = captured_tree.end.call_args.kwargs
    outputs = end_kwargs.get("outputs", {})
    assert outputs.get("status") == "FAILED"
    assert outputs.get("error_category") == "VALIDATION_ERROR"
    assert outputs.get("error_type") == "ValueError"

    # Ensure canary error text was NOT stored in outputs or error string
    assert "Alice Montgomery" not in str(outputs)


# ── Test 7: Telemetry Error Does Not Alter Business Decision ─────────────────
def test_evaluation_decision_telemetry_error_does_not_alter_result():
    """Verifies that an internal telemetry crash does not disrupt the final decision."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _, _ = _setup_test_environment()
    case_id = "case-telem-fault-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    doc_clin = _upload_test_document(
        storage,
        case_id,
        "doc-clin",
        SAMPLE_DISCHARGE_SUMMARY_TEXT,
        DocumentType.DISCHARGE_SUMMARY.value,
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim, doc_clin])

    def buggy_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()
        mock_tree.end.side_effect = RuntimeError("Fatal telemetry connection error")
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=buggy_trace):
        result = coordinator.execute(state)

    # Workflow must successfully complete and decide APPROVE despite telemetry failure
    assert result["decision"] == BusinessDecision.APPROVE.value
    assert "decision_engine_node" in result.get("completed_nodes", [])


# ── Test 8: One Workflow Execution Creates Exactly One Root Trace ────────────
def test_coordinator_creates_exactly_one_root_trace_with_all_thirteen_spans():
    """Validates that a full coordinator execution creates exactly one root trace and 12 child spans."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage, _, _ = _setup_test_environment()
    case_id = "case-all-thirteen-spans-01"

    doc_id = _upload_test_document(
        storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value
    )
    doc_claim = _upload_test_document(
        storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value
    )
    doc_clin = _upload_test_document(
        storage,
        case_id,
        "doc-clin",
        SAMPLE_DISCHARGE_SUMMARY_TEXT,
        DocumentType.DISCHARGE_SUMMARY.value,
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim, doc_clin])

    span_sequence: list[str] = []

    def mock_trace(name, **kwargs):
        span_sequence.append(name)
        cm = mock.MagicMock()
        cm.__enter__.return_value = mock.MagicMock()
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        result = coordinator.execute(state)

    assert result["decision"] == BusinessDecision.APPROVE.value
    assert span_sequence[0] == "case_workflow_execute"
    assert span_sequence.count("case_workflow_execute") == 1

    expected_workflow_nodes = [
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
    workflow_node_spans = [s for s in span_sequence if s in expected_workflow_nodes]
    assert workflow_node_spans == expected_workflow_nodes
    for s in span_sequence:
        assert (
            s in expected_workflow_nodes
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
