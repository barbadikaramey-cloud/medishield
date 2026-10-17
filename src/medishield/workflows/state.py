"""LangGraph State Models and Document References (Spec 04 § 1, Spec 02 § 1)."""

import uuid
from datetime import UTC, datetime
from typing import Any, TypedDict

from medishield.db.enums import WorkflowState


class DocumentReference(TypedDict, total=False):
    """Document reference maintained in LangGraph state (Spec 04 § 1.1)."""

    document_id: str
    file_name: str
    object_key: str
    mime_type: str
    checksum: str
    ocr_payload_uri: str
    doc_type: str
    classification_confidence: float
    routing_tags: list[str]
    split_source_page_indices: list[int]
    text_content: str


class CaseState(TypedDict, total=False):
    """
    Authoritative LangGraph State Model (Spec 04 § 1.1).
    Contains structural references, pointers, confidence scores, and verdicts.
    Heavy binary files and raw OCR text JSON remain in S3 / object storage.
    """

    # Core Case Identifiers
    case_id: str
    workflow_version: str
    execution_id: str
    trace_id: str
    created_at: str

    # Workflow Progression
    workflow_state: str
    completed_nodes: list[str]
    errors: list[dict[str, Any]]

    # 1:N Document Inventory
    documents: list[DocumentReference]
    is_complete: bool
    missing_required_documents: list[str]

    # Specialist Execution References & Outcomes
    kyc_execution_id: str | None
    kyc_verdict: str | None
    kyc_confidence: float | None
    kyc_summary: dict[str, Any] | None

    claims_execution_id: str | None
    claims_verdict: str | None
    claims_confidence: float | None
    claims_summary: dict[str, Any] | None

    policy_context_id: str | None
    policy_context_verdict: str | None
    candidate_policies: list[dict[str, Any]] | None
    policy_id: str | None
    policy_version: str | None
    policy_effective_date_valid: bool | None

    missing_documents: list[str] | None

    policy_coverage_execution_id: str | None
    policy_coverage_verdict: str | None
    policy_coverage_summary: dict[str, Any] | None

    fraud_execution_id: str | None
    fraud_verdict: str | None
    fraud_score: float | None
    fraud_risk_level: str | None
    fraud_summary: dict[str, Any] | None

    # Clinical Alignment Specialist
    clinical_alignment_execution_id: str | None
    clinical_alignment_verdict: str | None
    clinical_alignment_confidence: float | None
    clinical_alignment_summary: dict[str, Any] | None

    # Faithfulness & Integrity Gate
    faithfulness_gate_passed: bool
    provenance_integrity_score: float
    unsupported_claims_count: int
    consistency_report: dict[str, Any] | None

    # Final Business Decision Output
    decision: str | None
    escalation_sub_reason: str | None
    decision_confidence: float
    justification: str
    detailed_explanation: str | None
    rule_id_matched: str
    is_auto_decided: bool
    decision_summary: str | None
    decisive_factors: str | None
    blocking_condition_count: int | None

    # Dynamic Repositories & Duplicate Tracking
    code_catalog: Any | None
    claim_history_repo: Any | None
    duplicate_claim_match_id: str | None
    duplicate_claim_detected: bool | None


def create_initial_case_state(
    case_id: str,
    documents: list[DocumentReference] | None = None,
    trace_id: str | None = None,
    execution_id: str | None = None,
    workflow_version: str = "v1.0.0",
) -> CaseState:
    """Initializes a new CaseState with default pointers and RECEIVED state."""
    now_iso = datetime.now(UTC).isoformat()
    return CaseState(
        case_id=case_id,
        workflow_version=workflow_version,
        execution_id=execution_id or f"exec-{uuid.uuid4().hex[:12]}",
        trace_id=trace_id or f"trace-{uuid.uuid4().hex[:16]}",
        created_at=now_iso,
        workflow_state=WorkflowState.RECEIVED.value,
        completed_nodes=[],
        errors=[],
        documents=documents or [],
        is_complete=False,
        missing_required_documents=[],
        kyc_execution_id=None,
        kyc_verdict=None,
        kyc_confidence=None,
        kyc_summary=None,
        claims_execution_id=None,
        claims_verdict=None,
        claims_confidence=None,
        claims_summary=None,
        policy_context_id=None,
        policy_context_verdict=None,
        candidate_policies=None,
        policy_id=None,
        policy_version=None,
        policy_effective_date_valid=None,
        missing_documents=None,
        policy_coverage_execution_id=None,
        policy_coverage_verdict=None,
        policy_coverage_summary=None,
        fraud_execution_id=None,
        fraud_verdict=None,
        fraud_score=None,
        fraud_risk_level=None,
        fraud_summary=None,
        faithfulness_gate_passed=False,
        provenance_integrity_score=0.0,
        unsupported_claims_count=0,
        decision=None,
        escalation_sub_reason=None,
        decision_confidence=0.0,
        justification="",
        rule_id_matched="",
        is_auto_decided=False,
    )
