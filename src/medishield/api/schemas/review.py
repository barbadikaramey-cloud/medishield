"""Pydantic Schemas for Human Review Exception Console (Spec 08 § 3, FEAT-012)."""

from datetime import datetime

from pydantic import BaseModel, Field

from medishield.db.enums import BusinessDecision


class ReviewLockResponse(BaseModel):
    """Response returned when acquiring or querying review lock (Spec 08 § 3.1)."""
    case_id: str
    reviewer_id: str
    locked_at: datetime
    expires_at: datetime
    lock_active: bool = True


class HumanOverrideRequest(BaseModel):
    """Payload to override AI decision by operations reviewer (Spec 08 § 3.2)."""
    new_decision: BusinessDecision
    reason_code: str = Field(..., min_length=2, max_length=64)
    remarks: str = Field(..., min_length=3)
    expected_case_version: int = Field(..., ge=1)


class HumanReviewResponse(BaseModel):
    """Response returned upon successful human review action (Spec 08 § 3.2-3.4)."""
    case_id: str
    action: str  # OVERRIDE, CONFIRM, REQUEST_INFO
    decision: str | None = None
    reason_code: str
    reviewer_id: str
    case_version: int
    created_at: datetime


class RequestInfoRequest(BaseModel):
    """Payload to pause case and request supplementary documentation (Spec 08 § 3.4)."""
    reason_code: str = "MISSING_DOCUMENTATION"
    remarks: str
    requested_document_types: list[str] = Field(default_factory=list)
    expected_case_version: int = Field(..., ge=1)

