"""Pydantic Schemas for Policy Administration & Publishing Firewall (Spec 08 § 4, ADR-006)."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class PolicyUploadResponse(BaseModel):
    """Response returned upon uploading policy version (Spec 08 § 4.1)."""
    policy_id: str
    version_tag: str
    plan_name: str
    policy_type: str
    effective_start_date: date
    effective_end_date: date | None = None
    is_published: bool = False
    source_pdf_key: str
    created_at: datetime


class PolicyPublishRequest(BaseModel):
    """Payload to publish policy version to production vector collection (Spec 08 § 4.2)."""
    version_tag: str
    change_justification: str = Field(..., min_length=5)


class PolicyPublishResponse(BaseModel):
    """Response returned upon successful policy publication (Spec 08 § 4.2)."""
    policy_id: str
    version_tag: str
    is_published: bool = True
    published_by: str
    published_at: datetime
    vector_collection_name: str


class PolicyListItem(BaseModel):
    """Schema for listing policies and their versions in admin UI."""
    policy_id: str
    plan_name: str
    policy_type: str
    is_active: bool
    version_tag: str
    effective_start_date: date | None = None
    effective_end_date: date | None = None
    is_published: bool
    published_by: str | None = None
    published_at: datetime | None = None
    source_pdf_key: str
    chunk_count: int = 0

