"""Case completeness evaluation engine and intake category matrix (Spec 03 § 4)."""

from typing import Any, Final

from pydantic import BaseModel, Field

from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)

# Minimum classifier confidence required for a document to count toward completeness
CLASSIFICATION_CONFIDENCE_THRESHOLD: Final[float] = 0.70

# ── Canonical Required Documents Matrix (Spec 03 § 4.1) ───────────────────────
REQUIRED_DOCUMENTS_MATRIX: Final[dict[str, dict[str, Any]]] = {
    "STANDARD_REIMBURSEMENT_CLAIM": {
        "required": [DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT],
        "supporting": [DocumentType.DISCHARGE_SUMMARY, DocumentType.PRESCRIPTION],
        "min_required_count": 2,
    },
    "HOSPITAL_CASHLESS_PREAUTH": {
        "required": [DocumentType.CLAIM_FORM, DocumentType.ID_DOCUMENT, DocumentType.DISCHARGE_SUMMARY],
        "supporting": [DocumentType.PRESCRIPTION],
        "min_required_count": 3,
    },
    "POLICY_AMENDMENT_REQUEST": {
        "required": [DocumentType.POLICY_AMENDMENT, DocumentType.ID_DOCUMENT],
        "supporting": [],
        "min_required_count": 2,
    },
}

DEFAULT_CLAIM_TYPE: Final[str] = "STANDARD_REIMBURSEMENT_CLAIM"


class ClassifiedDocumentItem(BaseModel):
    """Minimal document metadata required to assess case completeness."""

    document_id: str
    doc_type: DocumentType
    classification_confidence: float = Field(..., ge=0.0, le=1.0)


class CaseCompletenessResult(BaseModel):
    """Authoritative outcome of case completeness verification (Spec 03 § 4.2)."""

    is_complete: bool
    claim_type: str
    present_types: list[DocumentType] = Field(default_factory=list)
    missing_types: list[DocumentType] = Field(default_factory=list)
    verdict: AgentVerdict
    recommended_state: WorkflowState
    recommended_decision: BusinessDecision
    recommended_sub_reason: EscalationSubReason | None = None
    remarks: str


def evaluate_case_completeness(
    case_documents: list[ClassifiedDocumentItem],
    claim_type: str = DEFAULT_CLAIM_TYPE,
) -> CaseCompletenessResult:
    """
    Evaluates whether the documents attached to a case satisfy mandatory intake requirements:
    - Filters for documents whose classification confidence meets the >= 0.70 threshold.
    - Cross-references present document types against REQUIRED_DOCUMENTS_MATRIX.
    - If all mandatory documents are present, emits AgentVerdict.PASS -> WorkflowState.ANALYZING.
    - If mandatory documents are missing, emits AgentVerdict.INDETERMINATE, pauses in AWAITING_INFO,
      and recommends BusinessDecision.ESCALATE(MISSING_INFORMATION) per Rule P2.
    """
    matrix_rule = REQUIRED_DOCUMENTS_MATRIX.get(
        claim_type,
        REQUIRED_DOCUMENTS_MATRIX[DEFAULT_CLAIM_TYPE],
    )
    required_types: list[DocumentType] = matrix_rule["required"]

    # Only documents with high enough classification confidence count
    qualified_docs = [
        doc for doc in case_documents
        if doc.classification_confidence >= CLASSIFICATION_CONFIDENCE_THRESHOLD
    ]
    present_types = {doc.doc_type for doc in qualified_docs}

    missing_required = [req for req in required_types if req not in present_types]

    if not missing_required:
        return CaseCompletenessResult(
            is_complete=True,
            claim_type=claim_type,
            present_types=sorted(present_types, key=lambda x: x.value),
            missing_types=[],
            verdict=AgentVerdict.PASS,
            recommended_state=WorkflowState.ANALYZING,
            recommended_decision=BusinessDecision.APPROVE,
            recommended_sub_reason=None,
            remarks=(
                f"All {len(required_types)} mandatory document types for '{claim_type}' "
                f"present with classification confidence >= {CLASSIFICATION_CONFIDENCE_THRESHOLD:.2f}."
            ),
        )

    return CaseCompletenessResult(
        is_complete=False,
        claim_type=claim_type,
        present_types=sorted(present_types, key=lambda x: x.value),
        missing_types=missing_required,
        verdict=AgentVerdict.INDETERMINATE,
        recommended_state=WorkflowState.AWAITING_INFO,
        recommended_decision=BusinessDecision.ESCALATE,
        recommended_sub_reason=EscalationSubReason.MISSING_INFORMATION,
        remarks=(
            f"Case Incompleteness (P2): Missing mandatory document(s) for '{claim_type}': "
            f"{', '.join(d.value for d in missing_required)}. "
            "Case paused in AWAITING_INFO for supplemental upload."
        ),
    )
