"""Unit tests for LangGraph Case Workflow Coordinator and Synchronization Barriers (FEAT-010, Spec 04)."""

from datetime import date, datetime
from typing import Any

from medishield.agents.claims import ClaimsAgent
from medishield.agents.classifier import DocumentClassifierAgent
from medishield.agents.kyc import (
    InMemoryMemberRepository,
    KYCAgent,
    MemberRecord,
)
from medishield.agents.policy import (
    InMemoryPolicyEnrollmentRepository,
    PolicyChunkRecord,
    PolicyContextAgent,
    PolicyCorpusRepository,
    PolicyCoverageAgent,
    PolicyEnrollmentRecord,
)
from medishield.db.audit import verify_audit_chain
from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.domain.contracts import FraudAgentOutput, RiskLevel
from medishield.domain.fraud import DeterministicFraudEngine
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.preprocessor import DefaultOCREngine, OCRPreprocessorEngine
from medishield.workflows.coordinator import CaseWorkflowCoordinator
from medishield.workflows.state import DocumentReference, create_initial_case_state


def _setup_test_environment() -> tuple[CaseWorkflowCoordinator, InMemoryStorageBackend, list[dict[str, Any]]]:
    """Helper setting up configured coordinator, storage, and repositories."""
    storage = InMemoryStorageBackend()
    ocr_engine = OCRPreprocessorEngine(engine=DefaultOCREngine())

    # 1. Member Repository
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

    # 2. Claims Agent
    claims_agent = ClaimsAgent()

    # 3. Policy Enrollment Repository
    enrollment_repo = InMemoryPolicyEnrollmentRepository([
        PolicyEnrollmentRecord(
            member_id="MEM-12345",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=date(2026, 12, 31),
        ),
        PolicyEnrollmentRecord(
            member_id="MEM-12345",
            policy_id="POL-CORP-2025",
            version_tag="v1.0",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2025, 1, 1),
            effective_end_date=date(2025, 12, 31),
        ),
    ])
    policy_context_agent = PolicyContextAgent(enrollment_repo=enrollment_repo)

    # 4. Policy Corpus Repository
    corpus = PolicyCorpusRepository()
    # Outpatient benefit
    corpus.add_chunk(
        PolicyChunkRecord(
            chunk_id="chk-001",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            section_number="SEC-3.1",
            section_title="Outpatient Evaluation and Management",
            chunk_text=(
                "Outpatient office visits and routine diagnostic evaluations are covered benefits. "
                "The insurer pays 80% of allowed charges after the annual deductible of $500.00 is satisfied. "
                "Co-payment of 20% applies to in-network professional services."
            ),
            page_number=12,
            is_exclusion=False,
            requires_prior_auth=False,
            covered_percentage=80.0,
            deductible_limit=500.0,
            copay_percentage=20.0,
            matching_cpt_codes=["99213", "99214", "93000"],
        )
    )
    # Inpatient with Prior Auth
    corpus.add_chunk(
        PolicyChunkRecord(
            chunk_id="chk-002",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            section_number="SEC-5.4",
            section_title="Inpatient Surgical and Major Procedures",
            chunk_text=(
                "Major elective surgical procedures including laparoscopic cholecystectomy require prior authorization "
                "submitted at least 48 hours in advance. Covered at 90% after deductible when prior authorization is approved."
            ),
            page_number=25,
            is_exclusion=False,
            requires_prior_auth=True,
            covered_percentage=90.0,
            deductible_limit=500.0,
            copay_percentage=10.0,
            matching_cpt_codes=["47562"],
        )
    )
    # Cosmetic Exclusion
    corpus.add_chunk(
        PolicyChunkRecord(
            chunk_id="chk-003",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            section_number="SEC-9.2",
            section_title="Cosmetic and Elective Resurfacing Exclusion",
            chunk_text=(
                "Cosmetic surgery, dermabrasion, and elective non-reconstructive procedures are strictly excluded "
                "from policy coverage. No benefit is payable for services billed under CPT 15780."
            ),
            page_number=44,
            is_exclusion=True,
            requires_prior_auth=False,
            covered_percentage=0.0,
            matching_cpt_codes=["15780"],
        )
    )
    policy_coverage_agent = PolicyCoverageAgent(corpus_repo=corpus)

    # 5. Fraud & Classifier Engines
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
    ref = DocumentReference(
        document_id=doc_id,
        object_key=key,
        mime_type="text/plain",
        checksum="dummy-hash",
        split_source_page_indices=[1],
    )
    if doc_type:
        ref["doc_type"] = doc_type
    return ref


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


# ── Test Cases ────────────────────────────────────────────────────────────────


def test_clean_claim_auto_approve() -> None:
    """End-to-end execution of a clean claim resulting in Rule P6 auto-approval."""
    coordinator, storage, audit_ledger = _setup_test_environment()
    case_id = "case-clean-01"

    doc_id = _upload_test_document(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_test_document(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)

    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    result = coordinator.execute(state)

    assert result["workflow_state"] == WorkflowState.COMPLETED.value
    assert result["decision"] == BusinessDecision.APPROVE.value
    assert result["rule_id_matched"] == "RULE-P6-CLEAN-PASS"
    assert result["is_auto_decided"] is True
    assert result["decision_confidence"] >= 0.75
    assert result["policy_id"] == "POL-CORP-2026"
    assert result["policy_version"] == "v2.0"
    assert len(audit_ledger) >= 1


def test_case_completeness_missing_id_early_exit() -> None:
    """Missing mandatory ID document triggers early halt at AWAITING_INFO without running specialists."""
    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-incomplete-02"

    # Only claim form provided, missing ID
    doc_claim = _upload_test_document(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)

    state = create_initial_case_state(case_id=case_id, documents=[doc_claim])
    result = coordinator.execute(state)

    assert result["workflow_state"] == WorkflowState.AWAITING_INFO.value
    assert result["decision"] == BusinessDecision.ESCALATE.value
    assert result["escalation_sub_reason"] == EscalationSubReason.MISSING_INFORMATION.value
    assert result["rule_id_matched"] == "RULE-P2-INCOMPLETE"
    assert result["is_complete"] is False
    assert "ID_DOCUMENT" in result.get("missing_required_documents", [])

    # Specialist nodes must NOT have run (compute savings)
    completed = result.get("completed_nodes", [])
    assert "kyc_agent_node" not in completed
    assert "claims_extraction_node" not in completed
    assert "fraud_agent_node" not in completed
    assert "policy_coverage_rag_node" not in completed


def test_policy_exclusion_hard_reject() -> None:
    """Billed cosmetic dermabrasion procedure triggers Rule P5 hard rejection."""
    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-exclusion-03"

    cosmetic_claim_text = """HEALTH INSURANCE CLAIM FORM
PROVIDER: Dr. Sarah Jenkins Hospital
NPI: 1234567890
DATE OF SERVICE: 2026-03-15
DIAGNOSIS: L81.8
PROCEDURE: 15780
1. 15780 Dermabrasion Face 2026-03-15 1.0 $1200.00 $1200.00
TOTAL CHARGE: $1200.00
"""
    doc_id = _upload_test_document(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_test_document(storage, case_id, "doc-claim", cosmetic_claim_text, DocumentType.CLAIM_FORM.value)

    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    result = coordinator.execute(state)

    assert result["workflow_state"] == WorkflowState.AWAITING_HUMAN.value
    assert result["decision"] == BusinessDecision.ESCALATE.value
    assert result["rule_id_matched"] == "RULE-P5-HARD-REJECT"
    assert "recommended denial" in result["justification"].lower() or "requires human confirmation" in result["justification"].lower()


def test_prior_authorization_missing_escalate() -> None:
    """Mandatory surgical procedure without prior authorization escalates to human review (Rule P3)."""
    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-preauth-04"

    surgery_claim_text = """HEALTH INSURANCE CLAIM FORM
PROVIDER: Dr. Sarah Jenkins Hospital
NPI: 1234567890
DATE OF SERVICE: 2026-03-15
DIAGNOSIS: K80.20
PROCEDURE: 47562
1. 47562 Laparoscopic Cholecystectomy 2026-03-15 1.0 $4500.00 $4500.00
TOTAL CHARGE: $4500.00
"""
    doc_id = _upload_test_document(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_test_document(storage, case_id, "doc-claim", surgery_claim_text, DocumentType.CLAIM_FORM.value)

    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    result = coordinator.execute(state)

    assert result["workflow_state"] == WorkflowState.AWAITING_HUMAN.value
    assert result["decision"] == BusinessDecision.ESCALATE.value
    assert result["rule_id_matched"] == "RULE-P3-AMBIGUITY"
    assert result["escalation_sub_reason"] == EscalationSubReason.AMBIGUITY.value


def test_join_claim_ready_policy_service_date_matching() -> None:
    """Verifies that JoinClaimReady barrier selects 2025 policy when service date is in 2025."""
    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-barrier-05"

    claim_2025_text = """HEALTH INSURANCE CLAIM FORM
PROVIDER: Dr. Sarah Jenkins Hospital
NPI: 1234567890
DATE OF SERVICE: 2025-08-10
1. 99213 Office Visit 2025-08-10 1.0 $200.00 $200.00
TOTAL CHARGE: $200.00
"""
    doc_id = _upload_test_document(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_test_document(storage, case_id, "doc-claim", claim_2025_text, DocumentType.CLAIM_FORM.value)

    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    result = coordinator.execute(state)

    assert result["policy_id"] == "POL-CORP-2025"
    assert result["policy_version"] == "v1.0"
    assert result["policy_effective_date_valid"] is True


def test_join_claim_ready_policy_out_of_bounds_fail() -> None:
    """Service date outside all enrolled policy windows causes rejection."""
    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-barrier-out-06"

    claim_future_text = """HEALTH INSURANCE CLAIM FORM
PROVIDER: Dr. Sarah Jenkins Hospital
NPI: 1234567890
DATE OF SERVICE: 2029-01-01
1. 99213 Office Visit 2029-01-01 1.0 $200.00 $200.00
TOTAL CHARGE: $200.00
"""
    doc_id = _upload_test_document(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_test_document(storage, case_id, "doc-claim", claim_future_text, DocumentType.CLAIM_FORM.value)

    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    result = coordinator.execute(state)

    assert result["policy_effective_date_valid"] is False
    assert result["decision"] == BusinessDecision.ESCALATE.value


def test_tamper_detected_security_escalate() -> None:
    """Tamper signals detected in KYC identity document triggers Rule P1 Security review."""
    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-tamper-07"

    tampered_id_text = """STATE DRIVER LICENSE
DL NUMBER: D9817264
MEMBER ID: MEM-12345
NAME: Alice Smith
DOB: 1985-05-20
EXP DATE: 2030-01-01
Security Notice: clone_stamp alteration detected
"""
    doc_id = _upload_test_document(storage, case_id, "doc-id", tampered_id_text, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_test_document(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)

    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    result = coordinator.execute(state)

    assert result["workflow_state"] == WorkflowState.AWAITING_HUMAN.value
    assert result["decision"] == BusinessDecision.ESCALATE.value
    assert result["escalation_sub_reason"] == EscalationSubReason.SECURITY_REVIEW.value
    assert result["rule_id_matched"] == "RULE-P1-SECURITY"


def test_audit_persistence_ledger_integrity() -> None:
    """Verifies that audit ledger maintains HMAC hash chain integrity across graph runs."""
    coordinator, storage, audit_ledger = _setup_test_environment()
    case_id = "case-audit-08"

    doc_id = _upload_test_document(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_test_document(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)

    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    coordinator.execute(state)

    assert len(audit_ledger) >= 1
    last_event = audit_ledger[-1]
    assert last_event["case_id"] == case_id
    assert len(last_event["current_hash"]) == 64

    class MockAuditEvent:
        def __init__(self, d: dict[str, Any]) -> None:
            self.id = d["event_id"]
            self.prev_hash = d["prev_hash"]
            self.current_hash = d["current_hash"]
            self.event_payload = d["event_payload"]
            self.created_at = d.get("created_at_dt") or datetime.fromisoformat(d["created_at"])

    events = [MockAuditEvent(e) for e in audit_ledger]
    assert verify_audit_chain(events) is True


def test_human_reviewer_override_resumption() -> None:
    """Operator overrides escalated case from AWAITING_HUMAN to COMPLETED (ADR-005)."""
    coordinator, storage, audit_ledger = _setup_test_environment()
    case_id = "case-override-09"

    # Incomplete case escalated
    doc_claim = _upload_test_document(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)
    state = create_initial_case_state(case_id=case_id, documents=[doc_claim])
    escalated_state = coordinator.execute(state)

    assert escalated_state["workflow_state"] == WorkflowState.AWAITING_INFO.value

    # Reviewer supplies manual override
    resumed_state = coordinator.resume_with_human_override(
        state=escalated_state,
        override_decision=BusinessDecision.APPROVE,
        reviewer_id="reviewer-lead-42",
        reason="Approved pursuant to executive medical waiver.",
    )

    assert resumed_state["workflow_state"] == WorkflowState.COMPLETED.value
    assert resumed_state["decision"] == BusinessDecision.APPROVE.value
    assert "reviewer-lead-42" in resumed_state["justification"]
    assert resumed_state["is_auto_decided"] is False
    assert len(audit_ledger) >= 2


def test_high_fraud_score_escalate() -> None:
    """High fraud composite score (>= 0.70) triggers Rule P4 Fraud Review escalation."""
    class HighRiskFraudEngine:
        @classmethod
        def evaluate(cls, claims_output: Any, kyc_output: Any = None, context: Any = None) -> FraudAgentOutput:
            return FraudAgentOutput(
                agent_name="fraud_agent",
                verdict=AgentVerdict.FAIL,
                confidence=1.0,
                fraud_score=0.88,
                risk_level=RiskLevel.HIGH,
                features=[],
                anomalies=["Severe duplicate and velocity anomalies"],
                latency_ms=1,
            )

    coordinator, storage, _ = _setup_test_environment()
    coordinator.fraud_engine = HighRiskFraudEngine
    case_id = "case-fraud-10"

    doc_id = _upload_test_document(storage, case_id, "doc-id", SAMPLE_ID_TEXT, DocumentType.ID_DOCUMENT.value)
    doc_claim = _upload_test_document(storage, case_id, "doc-claim", SAMPLE_CLAIM_TEXT, DocumentType.CLAIM_FORM.value)

    state = create_initial_case_state(case_id=case_id, documents=[doc_id, doc_claim])
    result = coordinator.execute(state)

    assert result["workflow_state"] == WorkflowState.AWAITING_HUMAN.value
    assert result["decision"] == BusinessDecision.ESCALATE.value
    assert result["escalation_sub_reason"] == EscalationSubReason.FRAUD_REVIEW.value
    assert result["rule_id_matched"] == "RULE-P4-HIGH-FRAUD"


def test_workflow_reclassifies_unknown_documents_by_filename() -> None:
    """Verifies that intake documents initialized as UNKNOWN are correctly classified by filename in Node 3."""
    coordinator, storage, _ = _setup_test_environment()
    case_id = "case-unknown-reclassify-11"

    # Upload documents as UNKNOWN but with meaningful filenames
    doc_claim = _upload_test_document(
        storage, case_id, "doc-01", SAMPLE_CLAIM_TEXT, DocumentType.UNKNOWN.value
    )
    doc_claim["file_name"] = "claim_PT_15075.png"

    doc_id = _upload_test_document(
        storage, case_id, "doc-02", SAMPLE_ID_TEXT, DocumentType.UNKNOWN.value
    )
    doc_id["file_name"] = "id_PT_15075.png"

    state = create_initial_case_state(case_id=case_id, documents=[doc_claim, doc_id])
    result = coordinator.execute(state)

    # Documents should have been reclassified out of UNKNOWN
    reclassified_types = {d["doc_type"] for d in result["documents"]}
    assert DocumentType.CLAIM_FORM.value in reclassified_types
    assert DocumentType.ID_DOCUMENT.value in reclassified_types
    assert result["is_complete"] is True
    assert result["workflow_state"] != WorkflowState.AWAITING_INFO.value

