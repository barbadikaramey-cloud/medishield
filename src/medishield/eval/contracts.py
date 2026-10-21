"""Evaluation and synthetic benchmarking data contracts (Spec 12 § 4)."""

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


class GroundTruthDocument(BaseModel):
    """Ground truth descriptor for a single evaluated document."""

    document_id: str
    file_name: str
    expected_doc_type: str
    expected_fields: dict[str, Any] = Field(default_factory=dict)
    text_content: str = ""
    is_corrupted: bool = False
    tamper_flag: bool = False
    prompt_injection_flag: bool = False


class GroundTruthPolicyAdjudication(BaseModel):
    """Ground truth expectations for policy RAG retrieval and coverage."""

    policy_id: str
    covered: bool
    expected_clause_ids: list[str] = Field(default_factory=list)


class GroundTruthCase(BaseModel):
    """Canonical ground truth manifest for a test case (Spec 12 § 4)."""

    case_id: str
    category: str
    category_name: str
    seed: int
    claim_type: str = "STANDARD_REIMBURSEMENT_CLAIM"
    documents: list[GroundTruthDocument] = Field(default_factory=list)
    expected_policy_adjudication: GroundTruthPolicyAdjudication | None = None
    expected_fraud_risk: str = "LOW"
    expected_business_decision: str
    expected_sub_reason: str | None = None
    expected_workflow_state: str
    tier: str = "TIER_2"  # TIER_1 or TIER_2
    split: str = "DEV"    # DEV or TEST


@dataclass
class CasePrediction:
    """Execution output from evaluating a case against MediShield adjudication."""

    case_id: str
    actual_decision: str | None = None
    actual_sub_reason: str | None = None
    actual_workflow_state: str | None = None
    predicted_doc_types: dict[str, str] = field(default_factory=dict)
    extracted_fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    retrieved_clause_ids: list[str] = field(default_factory=list)
    processing_time_ms: float = 0.0
    execution_count: int = 1
    decision_confidence: float = 0.90
    calibrated_confidence: float | None = None
    error_message: str | None = None


@dataclass
class EvaluationMetrics:
    """Calculated benchmark metrics and weighted assignment acceptance rubric (Spec 12 § 5)."""

    total_cases: int
    classification_accuracy: float      # Weight: 20% (Target >= 85.0%)
    extraction_completeness: float      # Weight: 20% (Target >= 80.0%)
    policy_retrieval_recall_at_5: float # Weight: 15% (Target >= 75.0%)
    decision_correctness: float         # Weight: 25% (Gate >= 60.0%)
    ui_functionality_pass_rate: float   # Weight: 10% (Target 100%)
    code_quality_pass_rate: float       # Weight: 10% (Target 100%)
    composite_score: float              # Weighted total (Gate >= 70.0%)
    passed_decision_gate: bool          # Decision Correctness >= 60.0%
    passed_overall_gate: bool           # Composite >= 70.0%

    # Enterprise Operational KPIs (Spec 12 § 5.2)
    straight_through_processing_rate: float = 0.0  # Target >= 65% on clean
    false_approval_rate: float = 0.0               # Target <= 1.0%
    false_denial_rate: float = 0.0                 # Target <= 2.0%
    p50_latency_ms: float = 0.0                    # Target <= 4500ms
    p95_latency_ms: float = 0.0                    # Target <= 12000ms
    rework_rate: float = 0.0                       # Reworked cases / Total

    # Per-category metrics breakdown
    category_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)

