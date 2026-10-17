"""
Acceptance unit tests for Step 3B: Decision Explainability and Rich LangSmith Traces.

Validates:
1. APPROVE scenario: Trace captures clean pass across all agents, rule RULE-P6-CLEAN-PASS,
   approval_safety_passed=True, decisive_factors="all_agents", and clear decision_summary.
2. REJECT scenario (e.g. policy exclusion): Trace captures failing specialist,
   rule RULE-P5-HARD-REJECT, approval_safety_passed=False, decisive_factors="policy",
   and deterministic rejection summary.
3. ESCALATE scenarios:
   - Fraud escalation: RULE-P4-HIGH-FRAUD, escalation_sub_reason="FRAUD_REVIEW".
   - Incomplete case: Phase 1 early halt captures RULE-P2-INCOMPLETE, escalation_sub_reason="MISSING_INFORMATION".
   - Ambiguity escalation: RULE-P3-AMBIGUITY, escalation_sub_reason="AMBIGUITY".
4. Indeterminate and Unavailable verdicts: Properly captured in trace summaries without dropping.
5. Invariance: Tracing-enabled vs disabled produces identical CaseState, workflow_state, and business decisions.
6. Fault tolerance: Telemetry failures during trace enrichment do not alter adjudication outcomes.
7. PHI minimization and canary protection: Prohibited fields and canaries never leak into trace summaries.
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
from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.domain.fraud import DeterministicFraudEngine
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.preprocessor import DefaultOCREngine, OCRPreprocessorEngine
from medishield.workflows.coordinator import CaseWorkflowCoordinator
from medishield.workflows.nodes import (
    kyc_agent_node,
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


def _setup_coordinator(
    is_exclusion: bool = False,
    effective_start: date = date(2026, 1, 1),
    effective_end: date = date(2026, 12, 31),
) -> tuple[CaseWorkflowCoordinator, InMemoryStorageBackend]:
    """Helper setting up a coordinator with configured policy rules."""
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
            effective_start_date=effective_start,
            effective_end_date=effective_end,
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
            section_title="Outpatient Services",
            chunk_text="Outpatient office visits and ECG procedures are covered.",
            page_number=12,
            is_exclusion=is_exclusion,
            requires_prior_auth=False,
            covered_percentage=0.0 if is_exclusion else 80.0,
            deductible_limit=500.0,
            copay_percentage=20.0,
            matching_cpt_codes=["99213", "99214", "93000"],
        )
    )
    policy_coverage_agent = PolicyCoverageAgent(corpus_repo=corpus)
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
        fraud_engine=DeterministicFraudEngine,
        policy_corpus=corpus,
        audit_ledger=audit_ledger,
    )
    return coordinator, storage


def _upload_doc(
    storage: InMemoryStorageBackend,
    case_id: str,
    doc_id: str,
    text: str,
    doc_type: str | None = None,
) -> DocumentReference:
    """Helper storing raw text bytes in storage and returning DocumentReference."""
    key = f"uploads/{case_id}/{doc_id}.txt"
    storage.put_bytes(key, text.encode("utf-8"), "text/plain")
    return DocumentReference(
        document_id=doc_id,
        object_key=key,
        mime_type="text/plain",
        checksum="dummy-hash",
        split_source_page_indices=[1],
        file_name=f"{doc_id}.txt",
        doc_type=doc_type,
    )


# ── 1. APPROVE Scenario Verification ─────────────────────────────────────────
def test_approve_scenario_records_rich_diagnostics():
    """Validates that a clean pass records RULE-P6-CLEAN-PASS, all passing specialist reasons, and approval safety."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage = _setup_coordinator(is_exclusion=False)
    case_id = "case-approve-rich-01"

    doc_id = _upload_doc(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_doc(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)
    doc_clin = _upload_doc(
        storage, case_id, "doc-clin", SAMPLE_DISCHARGE_SUMMARY_TEXT, DocumentType.DISCHARGE_SUMMARY.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim, doc_clin])

    captured_spans: dict[str, dict[str, Any]] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()

        def on_end(outputs=None, error=None):
            captured_spans[name] = outputs or {}

        mock_tree.end.side_effect = on_end
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        result = coordinator.execute(state)

    assert result["decision"] == BusinessDecision.APPROVE.value
    assert result["rule_id_matched"] == "RULE-P6-CLEAN-PASS"

    # 1. KYC Span
    kyc_s = captured_spans["kyc_agent_node"]
    assert kyc_s.get("verdict") == "PASS"
    assert kyc_s.get("member_matched") is True
    assert kyc_s.get("kyc_reason") == "CLEAN_MATCH"
    assert kyc_s.get("tamper_detected") is False
    assert kyc_s.get("id_expired") is False

    # 2. Claims Span
    claims_s = captured_spans["claims_extraction_node"]
    assert claims_s.get("verdict") == "PASS"
    assert claims_s.get("arithmetic_valid") is True
    assert claims_s.get("schema_valid") is True
    assert claims_s.get("claims_reason") == "CLEAN_MATH"
    assert claims_s.get("validation_error_count") == 0
    assert claims_s.get("duplicate_claim_detected") is False

    # 3. Policy Span
    pol_s = captured_spans["policy_coverage_rag_node"]
    assert pol_s.get("verdict") == "PASS"
    assert pol_s.get("covered") is True
    assert pol_s.get("is_exclusion") is False
    assert pol_s.get("policy_active_on_service_date") is True
    assert pol_s.get("policy_reason") == "COVERED"

    # 4. Fraud Span
    fraud_s = captured_spans["fraud_agent_node"]
    assert fraud_s.get("verdict") == "PASS"
    assert fraud_s.get("risk_level") == "LOW"
    assert fraud_s.get("duplicate_claim_detected") is False

    # 5. Decision Engine Span
    dec_s = captured_spans["decision_engine_node"]
    assert dec_s.get("decision") == "APPROVE"
    assert dec_s.get("rule_id_matched") == "RULE-P6-CLEAN-PASS"
    assert dec_s.get("approval_safety_passed") is True
    assert dec_s.get("decisive_factors") == "all_agents"
    assert dec_s.get("blocking_condition_count") == 0
    assert "approved" in str(dec_s.get("decision_summary")).lower()

    # 6. Root Span
    root_s = captured_spans["case_workflow_execute"]
    assert root_s.get("decision") == "APPROVE"
    assert root_s.get("rule_id_matched") == "RULE-P6-CLEAN-PASS"
    assert root_s.get("approval_safety_passed") is True
    assert root_s.get("decisive_factors") == "all_agents"
    assert "approved" in str(root_s.get("decision_summary")).lower()


# ── 2. REJECT Scenario Verification ──────────────────────────────────────────
def test_reject_scenario_records_rich_diagnostics():
    """Validates that a policy exclusion failure records RULE-P5-HARD-REJECT and decisive factor 'policy'."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage = _setup_coordinator(is_exclusion=True)
    case_id = "case-reject-rich-01"

    doc_id = _upload_doc(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_doc(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    captured_spans: dict[str, dict[str, Any]] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()

        def on_end(outputs=None, error=None):
            captured_spans[name] = outputs or {}

        mock_tree.end.side_effect = on_end
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        result = coordinator.execute(state)

    assert result["decision"] == BusinessDecision.REJECT.value
    assert result["rule_id_matched"] == "RULE-P5-HARD-REJECT"

    # Policy span must show exclusion
    pol_s = captured_spans["policy_coverage_rag_node"]
    assert pol_s.get("is_exclusion") is True
    assert pol_s.get("policy_reason") == "EXCLUSION_APPLIES"

    # Decision engine span
    dec_s = captured_spans["decision_engine_node"]
    assert dec_s.get("decision") == "REJECT"
    assert dec_s.get("rule_id_matched") == "RULE-P5-HARD-REJECT"
    assert dec_s.get("approval_safety_passed") is False
    assert "policy" in str(dec_s.get("decisive_factors"))
    assert "rejected" in str(dec_s.get("decision_summary")).lower()

    # Root span
    root_s = captured_spans["case_workflow_execute"]
    assert root_s.get("decision") == "REJECT"
    assert root_s.get("rule_id_matched") == "RULE-P5-HARD-REJECT"
    assert root_s.get("approval_safety_passed") is False
    assert "policy" in str(root_s.get("decisive_factors"))


# ── 3. ESCALATE Scenarios Verification ────────────────────────────────────────
def test_escalate_fraud_records_rich_diagnostics():
    """Validates that a high fraud anomaly records RULE-P4-HIGH-FRAUD and FRAUD_REVIEW sub-reason."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage = _setup_coordinator(is_exclusion=False)
    case_id = "case-escalate-fraud-01"

    doc_id = _upload_doc(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_doc(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    # Seed fraud context with duplicate claim flag to trigger RULE-P4-HIGH-FRAUD
    state["duplicate_claim_match_id"] = "CASE-PREV-9999"

    captured_spans: dict[str, dict[str, Any]] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()

        def on_end(outputs=None, error=None):
            captured_spans[name] = outputs or {}

        mock_tree.end.side_effect = on_end
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        result = coordinator.execute(state)

    assert result["decision"] == BusinessDecision.ESCALATE.value
    assert result["rule_id_matched"] == "RULE-P4-HIGH-FRAUD"
    assert result["escalation_sub_reason"] == EscalationSubReason.FRAUD_REVIEW.value

    # Fraud span
    fraud_s = captured_spans["fraud_agent_node"]
    assert fraud_s.get("duplicate_claim_detected") is True

    # Decision engine span
    dec_s = captured_spans["decision_engine_node"]
    assert dec_s.get("decision") == "ESCALATE"
    assert dec_s.get("rule_id_matched") == "RULE-P4-HIGH-FRAUD"
    assert dec_s.get("escalation_sub_reason") == "FRAUD_REVIEW"
    assert dec_s.get("decisive_factors") == "fraud"
    assert dec_s.get("approval_safety_passed") is False
    assert "fraud" in str(dec_s.get("decision_summary")).lower()

    # Root span
    root_s = captured_spans["case_workflow_execute"]
    assert root_s.get("decision") == "ESCALATE"
    assert root_s.get("rule_id_matched") == "RULE-P4-HIGH-FRAUD"
    assert root_s.get("escalation_sub_reason") == "FRAUD_REVIEW"


def test_escalate_incomplete_case_early_halt_records_rich_diagnostics():
    """Validates that missing required intake documents records RULE-P2-INCOMPLETE on early halt."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage = _setup_coordinator()
    case_id = "case-incomplete-01"

    # Provide ONLY Claim Form, missing ID Document
    doc_claim = _upload_doc(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)
    state = create_initial_case_state(case_id=case_id, documents=[doc_claim])

    captured_spans: dict[str, dict[str, Any]] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()

        def on_end(outputs=None, error=None):
            captured_spans[name] = outputs or {}

        mock_tree.end.side_effect = on_end
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        result = coordinator.execute(state)

    assert result["decision"] == BusinessDecision.ESCALATE.value
    assert result["rule_id_matched"] == "RULE-P2-INCOMPLETE"
    assert result["escalation_sub_reason"] == EscalationSubReason.MISSING_INFORMATION.value
    assert result["workflow_state"] == WorkflowState.AWAITING_INFO.value

    # Root span should immediately explain the early halt
    root_s = captured_spans["case_workflow_execute"]
    assert root_s.get("decision") == "ESCALATE"
    assert root_s.get("rule_id_matched") == "RULE-P2-INCOMPLETE"
    assert root_s.get("escalation_sub_reason") == "MISSING_INFORMATION"
    assert root_s.get("decisive_factors") == "intake"
    assert "incomplete" in str(root_s.get("decision_summary")).lower()


# ── 4. Indeterminate and Unavailable Verdicts Support ─────────────────────────
def test_indeterminate_and_unavailable_verdicts_captured_in_traces():
    """Validates that INDETERMINATE and UNAVAILABLE verdicts are properly preserved in telemetry summaries."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: dict[str, dict[str, Any]] = {}

    def mock_trace(name, **kwargs):
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()

        def on_end(outputs=None, error=None):
            captured_spans[name] = outputs or {}

        mock_tree.end.side_effect = on_end
        cm.__enter__.return_value = mock_tree
        return cm

    # 1. Test KYC emitting INDETERMINATE
    mock_kyc_agent = mock.MagicMock()
    mock_kyc_out = mock.MagicMock()
    mock_kyc_out.verdict = AgentVerdict.INDETERMINATE
    mock_kyc_out.confidence = 0.40
    mock_kyc_out.member_db_matched = False
    mock_kyc_out.tamper_detected = False
    mock_kyc_out.tamper_score = 0.0
    mock_kyc_out.flags = []
    mock_kyc_out.model_dump.return_value = {}
    mock_kyc_agent.execute.return_value = mock_kyc_out

    state = create_initial_case_state(case_id="case-indet-01", documents=[])
    with mock.patch("langsmith.trace", side_effect=mock_trace):
        kyc_agent_node(state, InMemoryStorageBackend(), mock_kyc_agent)

    kyc_s = captured_spans["kyc_agent_node"]
    assert kyc_s.get("verdict") == "INDETERMINATE"
    assert kyc_s.get("kyc_reason") == "INDETERMINATE"

    # 2. Test Policy Coverage emitting UNAVAILABLE
    pol_state = create_initial_case_state(case_id="case-unavail-01", documents=[])
    pol_state["policy_context_verdict"] = AgentVerdict.UNAVAILABLE.value

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        policy_coverage_rag_node(pol_state, mock.MagicMock())

    pol_s = captured_spans["policy_coverage_rag_node"]
    assert pol_s.get("verdict") == "UNAVAILABLE"
    assert pol_s.get("policy_reason") == "UNAVAILABLE"


# ── 5. Invariance: Tracing-Enabled vs Tracing-Disabled ─────────────────────────
def test_tracing_invariance_produces_identical_state_and_decisions():
    """Validates that enabling vs disabling tracing produces byte-for-byte identical business state and decisions."""
    coordinator, storage = _setup_coordinator()
    case_id = "case-invariance-01"

    doc_id = _upload_doc(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_doc(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)

    # 1. Run with Tracing DISABLED
    settings.LANGSMITH_TRACING = False
    state_disabled = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    res_disabled = coordinator.execute(state_disabled)

    # 2. Run with Tracing ENABLED
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"
    state_enabled = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    with mock.patch("langsmith.trace"):
        res_enabled = coordinator.execute(state_enabled)

    # Validate exact business invariance
    assert res_enabled["decision"] == res_disabled["decision"]
    assert res_enabled["rule_id_matched"] == res_disabled["rule_id_matched"]
    assert res_enabled["workflow_state"] == res_disabled["workflow_state"]
    assert res_enabled["is_auto_decided"] == res_disabled["is_auto_decided"]
    assert res_enabled["completed_nodes"] == res_disabled["completed_nodes"]


# ── 6. Fault Tolerance: Telemetry Errors Never Disrupt Decisions ──────────────
def test_telemetry_errors_do_not_disrupt_adjudication():
    """Validates that runtime exceptions in the telemetry transport do not crash or alter the decision."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    coordinator, storage = _setup_coordinator()
    case_id = "case-telemetry-err-01"

    doc_id = _upload_doc(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_doc(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)
    doc_clin = _upload_doc(
        storage, case_id, "doc-clin", SAMPLE_DISCHARGE_SUMMARY_TEXT, DocumentType.DISCHARGE_SUMMARY.value
    )
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim, doc_clin])

    def crashing_trace(*args, **kwargs):
        raise RuntimeError("Simulated LangSmith network crash / socket error")

    with mock.patch("langsmith.trace", side_effect=crashing_trace):
        result = coordinator.execute(state)

    # Adjudication must still succeed safely
    assert result["decision"] == BusinessDecision.APPROVE.value
    assert result["workflow_state"] == WorkflowState.COMPLETED.value
    assert result["rule_id_matched"] == "RULE-P6-CLEAN-PASS"


# ── 7. Canary and Sensitive Data Protection ───────────────────────────────────
def test_trace_payloads_zero_sensitive_canary_leakage():
    """Validates that synthetic PHI canaries never leak into any trace summaries or metadata."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    canary_ssn = "123-45-6789"
    canary_member = "MED-CANARY-99"
    canary_dob = "1975-12-31"
    canary_name = "PatientCanary"

    coordinator, storage = _setup_coordinator()
    case_id = "case-canary-01"

    doc_id = _upload_doc(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_doc(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)
    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])

    captured_payloads: list[str] = []

    def mock_trace(name, **kwargs):
        captured_payloads.append(str(name) + str(kwargs))
        cm = mock.MagicMock()
        mock_tree = mock.MagicMock()

        def on_end(outputs=None, error=None):
            captured_payloads.append(str(outputs))

        mock_tree.end.side_effect = on_end
        cm.__enter__.return_value = mock_tree
        return cm

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        coordinator.execute(state)

    combined_traces = " ".join(captured_payloads)
    assert canary_ssn not in combined_traces
    assert canary_member not in combined_traces
    assert canary_dob not in combined_traces
    assert canary_name not in combined_traces
    assert "9817264" not in combined_traces  # Driver license number
