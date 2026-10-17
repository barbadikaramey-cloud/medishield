"""Pydantic schemas for Administrator operations (ADR-012)."""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from medishield.db.enums import CodingSystem


class MedicalCodeItem(BaseModel):
    """Output representation of a medical code entity."""
    id: str | None = None
    code: str
    coding_system: CodingSystem
    short_description: str
    long_description: str | None = None
    category: str | None = None
    standard_charge: Decimal | None = None
    requires_prior_auth: bool = False
    effective_from: date | None = None
    effective_to: date | None = None
    is_active: bool = True
    created_at: datetime | None = None


class MedicalCodeCreateRequest(BaseModel):
    """Payload to create or update an individual medical code."""
    code: str = Field(..., min_length=1, max_length=32)
    coding_system: CodingSystem = Field(default=CodingSystem.CPT)
    short_description: str = Field(..., min_length=2, max_length=255)
    long_description: str | None = None
    category: str | None = None
    standard_charge: Decimal | None = None
    requires_prior_auth: bool = False
    effective_from: date | None = None
    effective_to: date | None = None
    is_active: bool = True


class CodeUploadSummary(BaseModel):
    """Summary of batch medical code ingestion from CSV."""
    records_processed: int
    records_inserted: int
    coding_systems_updated: list[str]
    errors_count: int
    error_sample: list[str] = []
    message: str


class CodeListResponse(BaseModel):
    """Paginated list of medical codes."""
    total: int
    limit: int
    offset: int
    items: list[MedicalCodeItem]


class AdminDashboardMetrics(BaseModel):
    """Executive system health, throughput, and terminology metrics."""
    total_cases: int
    workflow_distribution: dict[str, int]
    decision_distribution: dict[str, int]
    straight_through_processing_rate: float
    escalation_reason_distribution: dict[str, int]
    total_medical_codes: int
    active_codes_by_system: dict[str, int]
    active_policies_count: int
    system_status: str = "HEALTHY"

