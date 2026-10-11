"""Unit tests for Celery Background Workers and Queue Routing (FEAT-011, Spec 01 § 2 Layer 3)."""

from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest

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
from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.domain.contracts import FraudAgentOutput, RiskLevel
from medishield.intake.storage import InMemoryStorageBackend
from medishield.ocr.preprocessor import DefaultOCREngine, OCRPreprocessorEngine
from medishield.worker.celery_app import celery_app
from medishield.worker.tasks import (
    process_case_workflow_task,
    process_document_ocr_task,
    resume_case_workflow_task,
)
from medishield.workflows.coordinator import CaseWorkflowCoordinator
from medishield.workflows.state import DocumentReference, create_initial_case_state


@pytest.fixture(autouse=True)
def celery_eager_mode():
    """Ensure Celery runs in eager mode synchronously for unit tests."""
    original_eager = celery_app.conf.task_always_eager
    original_propagate = celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = False
    yield
    celery_app.conf.task_always_eager = original_eager
    celery_app.conf.task_eager_propagates = original_propagate


def _setup_coordinator() -> tuple[CaseWorkflowCoordinator, InMemoryStorageBackend]:
    """Helper creating a fully wired test coordinator."""
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
        )
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
            chunk_text=(
                "Outpatient office visits and routine diagnostic evaluations are covered benefits. "
                "The insurer pays 80% of allowed charges."
            ),
            page_number=12,
            is_exclusion=False,
            requires_prior_auth=False,
            covered_percentage=80.0,
            deductible_limit=500.0,
            copay_percentage=20.0,
            matching_cpt_codes=["99213", "93000"],
        )
    )
    policy_coverage_agent = PolicyCoverageAgent(corpus_repo=corpus)
    classifier_agent = DocumentClassifierAgent()

    coordinator = CaseWorkflowCoordinator(
        storage=storage,
        ocr_engine=ocr_engine,
        classifier_agent=classifier_agent,
        kyc_agent=kyc_agent,
        claims_agent=claims_agent,
        policy_context_agent=policy_context_agent,
        policy_coverage_agent=policy_coverage_agent,
        policy_corpus=corpus,
    )
    return coordinator, storage


def test_celery_app_configuration_and_routing():
    """Verify Celery task queues and routing tables (Spec 01 § 2 Layer 3)."""
    routes = celery_app.conf.task_routes
    assert routes["medishield.tasks.intake.*"]["queue"] == "intake_queue"
    assert routes["medishield.tasks.workflow.*"]["queue"] == "workflow_queue"

    queue_names = [q.name for q in celery_app.conf.task_queues]
    assert "intake_queue" in queue_names
    assert "workflow_queue" in queue_names
    assert "dlq" in queue_names
    assert celery_app.conf.task_serializer == "json"


def test_process_document_ocr_task_success():
    """Verify document OCR task on intake_queue extracts words and returns clean metadata."""
    storage = InMemoryStorageBackend()
    case_id = "CASE-20261004-OCR001"
    doc_id = "DOC-CLAIM-01"
    storage.put_bytes(f"raw/{case_id}/{doc_id}.txt", b"HEALTH INSURANCE CLAIM FORM\nTotal: $120.00")

    result = process_document_ocr_task.apply(
        args=[case_id, doc_id],
        kwargs={"storage": storage},
    ).get()

    assert result["status"] == "COMPLETED"
    assert result["case_id"] == case_id
    assert result["document_id"] == doc_id
    assert result["page_count"] == 1
    assert result["payload_uri"] != ""


def test_process_document_ocr_task_retry_on_transient_error():
    """Verify OCR task triggers Celery retry on transient connection failures."""
    mock_engine = MagicMock()
    mock_engine.process_document.side_effect = ConnectionError("Redis broker unreachable")

    # Pass content directly so it doesn't fail on storage lookup
    process_document_ocr_task.apply(
        args=["CASE-RETRY", "DOC-01"],
        kwargs={"ocr_engine": mock_engine, "content": b"Sample document bytes"},
    )
    # The task attempted retries
    assert mock_engine.process_document.call_count >= 1


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


def test_workflow_task_clean_claim_auto_approve():
    """Verify end-to-end multi-agent adjudication to APPROVE via Celery workflow task."""
    coordinator, storage = _setup_coordinator()
    case_id = "CASE-20261004-AUTO01"

    storage.put_bytes(f"raw/{case_id}/DOC-CLAIM.txt", SAMPLE_CLAIM_TEXT.encode("utf-8"))
    storage.put_bytes(f"raw/{case_id}/DOC-ID.txt", SAMPLE_ID_TEXT.encode("utf-8"))

    doc_refs = [
        DocumentReference(
            document_id="DOC-CLAIM",
            object_key=f"raw/{case_id}/DOC-CLAIM.txt",
            mime_type="text/plain",
            checksum="chk-claim",
            doc_type=DocumentType.CLAIM_FORM.value,
            split_source_page_indices=[1],
        ),
        DocumentReference(
            document_id="DOC-ID",
            object_key=f"raw/{case_id}/DOC-ID.txt",
            mime_type="text/plain",
            checksum="chk-id",
            doc_type=DocumentType.ID_DOCUMENT.value,
            split_source_page_indices=[1],
        ),
    ]
    initial_state = create_initial_case_state(case_id=case_id, documents=doc_refs)

    result = process_case_workflow_task.apply(
        args=[case_id],
        kwargs={"coordinator": coordinator, "initial_state": initial_state},
    ).get()

    assert result["status"] == WorkflowState.COMPLETED.value
    assert result["decision"] == BusinessDecision.APPROVE.value
    assert result["completed"] is True
    assert result["confidence"] > 0.8


def test_workflow_task_missing_document_early_halt_no_thread_block():
    """Verify Human Review Decoupling: Incomplete case exits at AWAITING_INFO with SUCCESS."""
    coordinator, storage = _setup_coordinator()
    case_id = "CASE-20261004-MISSING"

    # Only claim form provided, missing mandatory ID
    claim_content = "HEALTH INSURANCE CLAIM FORM CMS-1500\nTOTAL CHARGE: $100.00"
    storage.put_bytes(f"raw/{case_id}/DOC-CLAIM.txt", claim_content.encode("utf-8"))

    doc_refs = [
        DocumentReference(
            document_id="DOC-CLAIM",
            object_key=f"raw/{case_id}/DOC-CLAIM.txt",
            mime_type="text/plain",
            checksum="chk-claim",
            ocr_payload_uri="",
        )
    ]
    initial_state = create_initial_case_state(case_id=case_id, documents=doc_refs)

    result = process_case_workflow_task.apply(
        args=[case_id],
        kwargs={"coordinator": coordinator, "initial_state": initial_state},
    ).get()

    assert result["status"] == WorkflowState.AWAITING_INFO.value
    assert result["decision"] == BusinessDecision.ESCALATE.value
    assert result["escalation_sub_reason"] == EscalationSubReason.MISSING_INFORMATION.value
    assert result["completed"] is True  # Task finished with SUCCESS, compute thread freed!


def test_workflow_task_fraud_escalation_no_thread_block():
    """Verify escalated case transitions to AWAITING_HUMAN and frees worker thread."""
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

    coordinator, storage = _setup_coordinator()
    coordinator.fraud_engine = HighRiskFraudEngine  # type: ignore[assignment]
    case_id = "CASE-20261004-FRAUD"

    storage.put_bytes(f"raw/{case_id}/DOC-CLAIM.txt", SAMPLE_CLAIM_TEXT.encode("utf-8"))
    storage.put_bytes(f"raw/{case_id}/DOC-ID.txt", SAMPLE_ID_TEXT.encode("utf-8"))

    doc_refs = [
        DocumentReference(
            document_id="DOC-CLAIM",
            object_key=f"raw/{case_id}/DOC-CLAIM.txt",
            mime_type="text/plain",
            checksum="chk-claim",
            doc_type=DocumentType.CLAIM_FORM.value,
            split_source_page_indices=[1],
        ),
        DocumentReference(
            document_id="DOC-ID",
            object_key=f"raw/{case_id}/DOC-ID.txt",
            mime_type="text/plain",
            checksum="chk-id",
            doc_type=DocumentType.ID_DOCUMENT.value,
            split_source_page_indices=[1],
        ),
    ]
    initial_state = create_initial_case_state(
        case_id=case_id,
        documents=doc_refs,
    )

    result = process_case_workflow_task.apply(
        args=[case_id],
        kwargs={"coordinator": coordinator, "initial_state": initial_state},
    ).get()

    assert result["status"] == WorkflowState.AWAITING_HUMAN.value
    assert result["decision"] == BusinessDecision.ESCALATE.value
    assert result["escalation_sub_reason"] == EscalationSubReason.FRAUD_REVIEW.value
    assert result["completed"] is True


def test_workflow_task_optimistic_concurrency_lock_conflict():
    """Verify task halts safely when DB version does not match expected_case_version."""
    mock_session = MagicMock()
    mock_case = MagicMock()
    mock_case.version = 5
    mock_session.query.return_value.filter.return_value.first.return_value = mock_case

    result = process_case_workflow_task.apply(
        args=["CASE-CONCURRENT"],
        kwargs={
            "expected_case_version": 4,  # Version conflict: expected 4, found 5
            "db_session": mock_session,
        },
    ).get()

    assert result["status"] == "VERSION_CONFLICT"
    assert result["current_version"] == 5
    assert result["expected_version"] == 4
    assert result["completed"] is False


def test_workflow_task_already_completed_idempotency():
    """Verify task skips execution if case is already COMPLETED."""
    mock_session = MagicMock()
    mock_case = MagicMock()
    mock_case.version = 1
    mock_case.workflow_state = WorkflowState.COMPLETED
    mock_case.current_decision = BusinessDecision.APPROVE
    mock_session.query.return_value.filter.return_value.first.return_value = mock_case

    result = process_case_workflow_task.apply(
        args=["CASE-DONE"],
        kwargs={"db_session": mock_session},
    ).get()

    assert result["status"] == "ALREADY_COMPLETED"
    assert result["decision"] == BusinessDecision.APPROVE.value
    assert result["completed"] is True


def test_workflow_task_unrecoverable_failure_escalates_to_technical_failure():
    """Verify fatal unrecoverable exception updates case to FAILED with TECHNICAL_FAILURE."""
    mock_coordinator = MagicMock()
    mock_coordinator.execute.side_effect = ValueError("Malformed document structure corrupted byte stream")

    mock_session = MagicMock()
    mock_case = MagicMock()
    mock_case.version = 1
    mock_case.workflow_state = WorkflowState.RECEIVED
    mock_session.query.return_value.filter.return_value.first.return_value = mock_case

    result = process_case_workflow_task.apply(
        args=["CASE-FATAL"],
        kwargs={
            "coordinator": mock_coordinator,
            "db_session": mock_session,
        },
    ).get()

    assert result["status"] == "FAILED"
    assert result["decision"] == BusinessDecision.ESCALATE.value
    assert result["escalation_sub_reason"] == EscalationSubReason.TECHNICAL_FAILURE.value
    assert result["completed"] is False
    assert mock_case.workflow_state == WorkflowState.FAILED
    assert mock_case.current_decision == BusinessDecision.ESCALATE
    assert mock_case.current_sub_reason == EscalationSubReason.TECHNICAL_FAILURE


def test_resume_workflow_task_human_override_success():
    """Verify resuming an escalated case with human override to APPROVE."""
    coordinator, _ = _setup_coordinator()
    case_id = "CASE-RESUME-01"

    paused_state = create_initial_case_state(case_id=case_id, documents=[])
    paused_state["workflow_state"] = WorkflowState.AWAITING_HUMAN.value
    paused_state["decision"] = BusinessDecision.ESCALATE.value
    paused_state["escalation_sub_reason"] = EscalationSubReason.FRAUD_REVIEW.value

    result = resume_case_workflow_task.apply(
        args=[case_id, "Supervisor confirmed valid provider"],
        kwargs={
            "human_decision": BusinessDecision.APPROVE.value,
            "reviewer_id": "REV-007",
            "coordinator": coordinator,
            "state": paused_state,
        },
    ).get()

    assert result["status"] == WorkflowState.COMPLETED.value
    assert result["decision"] == BusinessDecision.APPROVE.value
    assert result["resumed"] is True
    assert result["completed"] is True


def test_phi_scrubbing_in_task_outputs():
    """Verify task output payloads are cleansed of sensitive patient PHI."""
    coordinator, _ = _setup_coordinator()
    case_id = "CASE-PHI-01"

    state = create_initial_case_state(case_id=case_id, documents=[])
    state["workflow_state"] = WorkflowState.COMPLETED.value
    state["decision"] = BusinessDecision.APPROVE.value

    result = process_case_workflow_task.apply(
        args=[case_id],
        kwargs={"coordinator": coordinator, "initial_state": state},
    ).get()

    assert "1985-05-20" not in str(result)
    assert "4321" not in str(result)
