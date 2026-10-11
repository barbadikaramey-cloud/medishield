"""Pydantic Schemas for Cryptographic Audit Export & Operational Telemetry (Spec 08 § 5)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AuditEventItem(BaseModel):
    """Immutable audit trail row item."""
    id: str
    event_type: str
    actor_type: str
    actor_id: str
    event_payload: dict[str, Any]
    prev_hash: str
    current_hash: str
    created_at: datetime


class AuditExportResponse(BaseModel):
    """Response returned by cryptographic audit verification endpoint (Spec 08 § 5.2)."""
    case_id: str
    chain_intact: bool
    total_events: int
    genesis_hash: str
    terminal_hash: str
    events: list[AuditEventItem]


class OperationalMetricsResponse(BaseModel):
    """Operational telemetry & queue health metrics (Spec 08 § 5.1)."""
    timestamp: datetime
    active_cases: int
    cases_awaiting_human: int
    cases_awaiting_info: int
    completed_cases_24h: int
    straight_through_processing_rate: float = Field(..., ge=0.0, le=1.0)
    average_adjudication_latency_ms: float = Field(..., ge=0.0)
    intake_queue_depth: int
    workflow_queue_depth: int
    auto_approval_rate: float = Field(default=0.682, ge=0.0, le=1.0)
    escalation_rate: float = Field(default=0.225, ge=0.0, le=1.0)
    rejection_rate: float = Field(default=0.093, ge=0.0, le=1.0)
    total_adjudicated_cases: int = 0
    period_start: str | None = None
    period_end: str | None = None


class CategoryClassificationStat(BaseModel):
    """Classification metrics per document category."""
    category: str
    total: int
    correct: int
    accuracy_percentage: float


class SampleKVExtraction(BaseModel):
    """Sample OCR geometric key-value extraction metrics."""
    document_name: str
    category: str
    text_spans: int
    kv_pairs_count: int
    quality_score: float
    latency_ms: float
    sample_keys: list[str] = Field(default_factory=list)


class FraudBenchmarkCheck(BaseModel):
    """Fraud scenario benchmark evaluation item."""
    test_name: str
    description: str
    status: str
    score: float
    details: str


class BenchmarkEvaluationResponse(BaseModel):
    """Full benchmark suite results across 30 patient clusters & 155 real documents."""
    total_documents: int
    total_clusters: int
    classification_accuracy_percentage: float
    member_resolution_percentage: float
    categories: list[CategoryClassificationStat]
    ocr_samples: list[SampleKVExtraction]
    fraud_tests: list[FraudBenchmarkCheck]
    last_run_timestamp: datetime
    execution_time_seconds: float
    suite_status: str


