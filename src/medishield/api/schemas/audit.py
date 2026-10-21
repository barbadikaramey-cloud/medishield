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


class LLMStageTelemetryResponse(BaseModel):
    calls_total: int
    calls_success: int
    calls_failed: int
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None


class LLMRuntimeTelemetryResponse(BaseModel):
    scope: str
    calls_total: int
    calls_success: int
    calls_failed: int
    provider_attempts: int
    provider_attempts_success: int
    provider_attempts_failed: int
    provider_failures: dict[str, int]
    provider_failure_categories: dict[str, int]
    stages: dict[str, LLMStageTelemetryResponse]
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    responses_with_token_usage: int
    responses_without_token_usage: int
    token_usage_complete: bool
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    provider_latency_p50_ms: float | None = None
    provider_latency_p95_ms: float | None = None


class OperationalMetricsResponse(BaseModel):
    """Operational telemetry & queue health metrics (Spec 08 § 5.1)."""
    timestamp: datetime
    active_cases: int
    cases_awaiting_human: int
    cases_awaiting_info: int
    completed_cases_24h: int
    # Rates and latencies are null when no adjudicated data exists; they are never estimated.
    straight_through_processing_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    average_adjudication_latency_ms: float | None = Field(default=None, ge=0.0)
    latency_p50_ms: float | None = Field(default=None, ge=0.0)
    latency_p95_ms: float | None = Field(default=None, ge=0.0)
    intake_queue_depth: int
    workflow_queue_depth: int
    auto_approval_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    escalation_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    rejection_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    total_adjudicated_cases: int = 0
    insufficient_data: bool = True
    llm_runtime: LLMRuntimeTelemetryResponse
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
    fraud_review_required: bool = False
    score: float = Field(..., description="Binary compatibility indicator (0.0 or 1.0), not fraud severity")
    details: str


class FraudLabelReasonCount(BaseModel):
    """Count of supplied positive document fraud labels by metadata reason."""

    reason: str
    count: int


class FraudMetadataLabelSummary(BaseModel):
    """Metadata label coverage; these labels are not case-level adjudication outcomes."""

    source: str
    total_documents: int
    labelled_documents: int
    positive_documents: int
    negative_documents: int
    unlabelled_documents: int
    positive_by_reason: list[FraudLabelReasonCount] = Field(default_factory=list)


class BenchmarkEvaluationResponse(BaseModel):
    """Benchmark diagnostics and supplied fraud-label coverage for the synthetic dataset."""
    total_documents: int
    total_clusters: int
    classification_accuracy_percentage: float
    member_resolution_percentage: float
    fraud_label_summary: FraudMetadataLabelSummary | None = None
    categories: list[CategoryClassificationStat]
    ocr_samples: list[SampleKVExtraction]
    fraud_tests: list[FraudBenchmarkCheck]
    last_run_timestamp: datetime
    execution_time_seconds: float
    suite_status: str


class CalibrationBinSchema(BaseModel):
    bin_index: int
    lower_bound: float
    upper_bound: float
    mean_predicted_confidence: float
    empirical_accuracy: float
    sample_count: int
    weight: float


class SelectiveCoverageSchema(BaseModel):
    confidence_threshold: float
    coverage: float
    selective_accuracy: float
    accepted_samples: int


class CalibrationMetricsResponse(BaseModel):
    """Reliability and uncertainty diagnostics for generated synthetic decision scenarios."""
    strategy: str
    total_samples: int
    evaluation_source: str
    evaluation_label: str
    evaluation_population: str
    brier_score: float
    expected_calibration_error: float
    maximum_calibration_error: float
    raw_confidence_mean: float
    calibrated_confidence_mean: float | None = None
    calibrated_brier_score: float | None = None
    calibrated_expected_calibration_error: float | None = None
    calibration_method: str = "Platt Scaling"
    bins: list[CalibrationBinSchema] = Field(default_factory=list)
    calibrated_bins: list[CalibrationBinSchema] = Field(default_factory=list)
    selective_risk_coverage: list[SelectiveCoverageSchema] = Field(default_factory=list)
    interpretation: str

