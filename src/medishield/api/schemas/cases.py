"""Pydantic Request & Response Schemas for Case Operations (Spec 08 § 2, FEAT-012)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DocumentUploadedInfo(BaseModel):
    """Metadata of an uploaded document attached to a case."""
    document_id: str
    file_name: str
    checksum: str
    size_bytes: int
    mime_type: str = "application/pdf"
    doc_type: str = "UNKNOWN"
    ocr_payload_uri: str | None = None


class CaseCreateResponse(BaseModel):
    """Response returned upon successful case ingestion handshake (Spec 08 § 2.1)."""
    case_id: str
    workflow_state: str
    document_count: int
    documents: list[DocumentUploadedInfo]
    created_at: datetime


class CaseListItem(BaseModel):
    """Summary item in case queue list view (Spec 08 § 2.2)."""
    case_id: str
    workflow_state: str
    current_decision: str | None = None
    current_sub_reason: str | None = None
    decision_confidence: float | None = None
    document_count: int = 0
    is_complete: bool = False
    version: int = 1
    created_at: datetime
    updated_at: datetime


class CaseListResponse(BaseModel):
    """Paginated case list response (Spec 08 § 2.2)."""
    total_count: int
    page: int
    page_size: int
    items: list[CaseListItem]


class CaseDetailResponse(BaseModel):
    """Detailed case record view (Spec 08 § 2.3)."""
    case_id: str
    workflow_state: str
    workflow_version: str = "v1.0.0"
    current_decision: str | None = None
    current_sub_reason: str | None = None
    decision_confidence: float | None = None
    is_complete: bool = False
    missing_documents: list[str] = Field(default_factory=list)
    rule_id_matched: str | None = None
    justification: str | None = None
    detailed_explanation: str | None = None
    agent_findings: dict[str, Any] | None = None
    version: int = 1
    document_count: int = 0
    documents: list[DocumentUploadedInfo] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class SupplementaryDocResponse(BaseModel):
    """Response returned when supplementary documents are added (Spec 08 § 2.4)."""
    case_id: str
    added_documents: list[DocumentUploadedInfo]
    missing_documents: list[str]
    total_document_count: int


class ProcessResumeRequest(BaseModel):
    """Request payload to resume case processing (Spec 08 § 2.5)."""
    resume_reason: str = "SUPPLEMENTARY_DOCUMENTS_ADDED"
    expected_case_version: int = Field(..., ge=1)


class ProcessResumeResponse(BaseModel):
    """Response payload upon resuming processing (Spec 08 § 2.5)."""
    case_id: str
    workflow_state: str
    resume_reason: str
    version: int
    updated_at: datetime


class CaseRerunResponse(BaseModel):
    """Response payload upon re-running case workflow adjudication."""
    case_id: str
    workflow_state: str
    status: str
    decision: str | None = None
    escalation_sub_reason: str | None = None
    version: int
    message: str


class ClaimerDocumentItem(BaseModel):
    """Sanitized document summary for patient view."""
    document_id: str
    file_name: str
    doc_type: str
    size_bytes: int
    created_at: datetime


class ClaimerAdjudicationSummary(BaseModel):
    """Patient-facing financial breakdown and plain-language explanation."""
    decision: str
    covered_amount: float | None = None
    total_billed: float | None = None
    deductible_applied: float | None = None
    copay_percentage: float | None = None
    member_responsibility: float | None = None
    explanation_letter: str | None = None


class ClaimerStatusResponse(BaseModel):
    """Sanitized, patient-facing claim tracking response (Spec 11 § 2, ADR-012)."""
    case_id: str
    workflow_state: str
    is_complete: bool
    status_label: str
    missing_documents: list[str] = Field(default_factory=list)
    adjudication: ClaimerAdjudicationSummary | None = None
    submitted_documents: list[ClaimerDocumentItem] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CaseExplanationResponse(BaseModel):
    """Clinical and policy explanation for claims adjudication (ADR-011)."""
    case_id: str
    decision: str
    rule_id_matched: str
    decision_confidence: float | None = None
    detailed_explanation: str
    model_source: str
    generated_at: datetime
