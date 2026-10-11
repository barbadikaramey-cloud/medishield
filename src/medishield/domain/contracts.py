from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
)
from medishield.domain.provenance import BBox, ProvenanceValue

# ── 1. OCR Preprocessor Schemas ──────────────────────────────────────────────

OCRBoundingBox = BBox


class OCRTextSpan(BaseModel):
    """Discrete text token span with spatial coordinates and unique identifier."""
    span_id: str = Field(..., description="Unique span ID within the document (e.g. 'span-p1-0042')")
    page_number: int = Field(..., ge=1, description="1-indexed page number")
    text: str = Field(..., description="Extracted raw text token string")
    confidence: float = Field(..., ge=0.0, le=1.0, description="OCR engine confidence score")
    bbox: OCRBoundingBox


class OCRPagePayload(BaseModel):
    """Container for page-level OCR spans and concatenated raw text."""
    page_number: int = Field(..., ge=1)
    width_px: int = Field(default=1700, ge=1)
    height_px: int = Field(default=2200, ge=1)
    raw_text: str = Field(..., description="Concatenated raw text for the page")
    spans: list[OCRTextSpan] = Field(default_factory=list)
    key_value_pairs: list[dict[str, Any]] = Field(default_factory=list)


class OCRDocumentPayload(BaseModel):
    """Full OCR representation of a document stored in S3 /ocr/ (never in LangGraph state)."""
    document_id: str
    case_id: str | None = None
    ocr_engine: str = "Tesseract-5.3/Surya"
    total_pages: int = Field(..., ge=1)
    pages: list[OCRPagePayload] = Field(default_factory=list)
    created_at: str | None = None
    vlm_extraction: dict[str, Any] = Field(default_factory=dict)
    key_value_pairs: list[dict[str, Any]] = Field(default_factory=list)


class OCRPreprocessorOutput(BaseModel):
    """Lightweight output returned to LangGraph by the OCR Preprocessor node."""
    document_id: str
    ocr_engine: str = "Tesseract-5.3/Surya"
    total_pages: int
    image_quality_score: float = Field(..., ge=0.0, le=1.0)
    verdict: AgentVerdict = AgentVerdict.PASS
    is_searchable_pdf: bool = False
    payload_storage_uri: str = Field(..., description="S3 object key pointing to full OCR JSON")
    latency_ms: int = Field(..., ge=0)


# ── 2. Classifier Agent Contract ─────────────────────────────────────────────

class ClassifierAgentOutput(BaseModel):
    """Document classification decision and document routing metadata."""
    document_id: str
    doc_type: DocumentType
    confidence: float = Field(..., ge=0.0, le=1.0)
    verdict: AgentVerdict
    routing_tags: list[str] = Field(default_factory=list)
    visual_characteristics: dict[str, Any] = Field(default_factory=dict)
    model_version: str = "v1.0.0"
    latency_ms: int = Field(..., ge=0)


# ── 3. KYC Agent Contract ────────────────────────────────────────────────────

class TamperSignal(BaseModel):
    """Digital image or physical alteration signal discovered during KYC."""
    signal_type: str = Field(..., description="ELA_ANOMALY, FONT_INCONSISTENCY, CLONE_STAMP")
    severity: str = Field(..., description="LOW, MEDIUM, HIGH")
    description: str
    confidence: float = Field(..., ge=0.0, le=1.0)


class KYCAgentOutput(BaseModel):
    """Identity verification, member enrollment matching, and tamper assessment."""
    agent_name: str = "kyc_agent"
    verdict: AgentVerdict
    confidence: float = Field(..., ge=0.0, le=1.0)

    # Validated Identity Fields
    member_id: ProvenanceValue | None = None
    first_name: ProvenanceValue | None = None
    last_name: ProvenanceValue | None = None
    date_of_birth: ProvenanceValue | None = None
    government_id_number: ProvenanceValue | None = None

    # Verification Checks
    member_db_matched: bool = False
    dob_matched: bool = False
    id_expired: bool = False
    id_expiration_date: date | None = None
    policy_active_on_service_date: bool = False

    # Tamper & Integrity
    tamper_detected: bool = False
    tamper_score: float = Field(default=0.0, ge=0.0, le=1.0)
    tamper_signals: list[TamperSignal] = Field(default_factory=list)

    flags: list[str] = Field(default_factory=list)
    latency_ms: int = Field(..., ge=0)


# ── 4. Claims Agent Contract ─────────────────────────────────────────────────

class ServiceLine(BaseModel):
    """Individual billing service line."""
    line_number: int = Field(..., ge=1)
    rev_code: ProvenanceValue = Field(default_factory=lambda: ProvenanceValue(value="UNKNOWN", confidence=0.0))
    description: ProvenanceValue | None = None
    hcpcs: ProvenanceValue | None = None
    service_date: ProvenanceValue
    units: ProvenanceValue
    amount: ProvenanceValue

    @model_validator(mode="before")
    @classmethod
    def _backwards_compat_mapping(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "rev_code" not in data:
                data["rev_code"] = ProvenanceValue(value="UNKNOWN", confidence=0.0)
            if "hcpcs" not in data and "cpt_code" in data:
                data["hcpcs"] = data["cpt_code"]
            if "units" not in data and "quantity" in data:
                data["units"] = data["quantity"]
            if "amount" not in data:
                if "total_charge" in data:
                    data["amount"] = data["total_charge"]
                elif "unit_charge" in data:
                    data["amount"] = data["unit_charge"]
        return data


ServiceLineItem = ServiceLine


class ClaimsAgentOutput(BaseModel):
    """Extracted claim header, diagnostic coding, procedure lines, and arithmetic validation."""
    agent_name: str = "claims_agent"
    verdict: AgentVerdict
    confidence: float = Field(..., ge=0.0, le=1.0)

    # Header Information
    claim_amount: ProvenanceValue
    service_date: ProvenanceValue
    provider_name: ProvenanceValue | None = None
    provider_npi: ProvenanceValue

    # Medical Coding
    icd10_codes: list[ProvenanceValue] = Field(default_factory=list)
    cpt_codes: list[ProvenanceValue] = Field(default_factory=list)
    service_lines: list[ServiceLine] = Field(default_factory=list)

    # Mathematical & Schema Validation
    schema_valid: bool
    validation_errors: list[str] = Field(default_factory=list)
    line_items_sum_valid: bool
    calculated_lines_sum: float = Field(..., ge=0.0)

    latency_ms: int = Field(..., ge=0)


# ── 5. Policy Agent Contracts (2-Stage) ──────────────────────────────────────

class CandidatePolicy(BaseModel):
    """Candidate policy matched to member prior to service-date resolution."""
    policy_id: str
    policy_version: str
    effective_start_date: date
    effective_end_date: date | None = None
    plan_name: str
    active_riders: list[str] = Field(default_factory=list)


class PolicyContextOutput(BaseModel):
    """Stage A: Pre-claim policy resolution from member enrollment records."""
    member_enrollment_id: str
    candidate_policies: list[CandidatePolicy] = Field(default_factory=list)
    verdict: AgentVerdict


class PolicyClauseCitation(BaseModel):
    """Direct verifiable citation of an authoritative policy chunk."""
    policy_id: str
    policy_version: str
    section_number: str
    clause_title: str
    verbatim_text: str
    page_number: int
    vector_score: float = Field(..., ge=0.0, le=1.0)
    verified_in_corpus: bool = Field(
        ...,
        description="True if verbatim text achieves >=0.95 similarity against PostgreSQL chunk",
    )


class PolicyCoverageOutput(BaseModel):
    """Stage B: Clause-level coverage determination and benefits adjudication."""
    agent_name: str = "policy_agent"
    verdict: AgentVerdict
    confidence: float = Field(..., ge=0.0, le=1.0)

    covered: bool
    coverage_percentage: float = Field(..., ge=0.0, le=100.0)
    effective_on_service_date: bool = True
    policy_deductible_limit: float | None = None
    policy_copay_percentage: float | None = None

    cited_clauses: list[PolicyClauseCitation] = Field(default_factory=list)
    identified_exclusions: list[str] = Field(default_factory=list)
    exclusion_clause_verified: bool = False
    applicable_service_codes: list[str] = Field(default_factory=list)
    prior_authorization_required: bool = False
    prior_authorization_present: bool = False

    reasoning_summary: str
    latency_ms: int = Field(..., ge=0)


# ── 6. Fraud Detection Agent Contract ────────────────────────────────────────

class FraudFeature(BaseModel):
    """Individual explainable feature score within the fraud scoring suite (ADR-005)."""
    feature_name: str
    raw_value: float
    normalized_score: float = Field(..., ge=0.0, le=1.0)
    weight: float = Field(..., ge=0.0, le=1.0)
    description: str


class RiskLevel(str, Enum):
    """Calibrated risk tier based on composite fraud score."""
    LOW = "LOW"        # score < 0.30
    MEDIUM = "MEDIUM"  # 0.30 <= score < 0.70
    HIGH = "HIGH"      # score >= 0.70


class FraudAgentOutput(BaseModel):
    """Deterministic, explainable fraud score and anomaly breakdown (ADR-005)."""
    agent_name: str = "fraud_agent"
    verdict: AgentVerdict
    confidence: float = Field(..., ge=0.0, le=1.0)

    fraud_score: float = Field(..., ge=0.0, le=1.0)
    risk_level: RiskLevel
    features: list[FraudFeature] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)

    duplicate_claim_detected: bool = False
    duplicate_claim_id: str | None = None
    velocity_anomaly_detected: bool = False

    latency_ms: int = Field(..., ge=0)


# ── 7. Decision Engine & Orchestrator Contract ───────────────────────────────

class ConsistencyCheckReport(BaseModel):
    """Outcome of cross-agent factual consistency validation."""
    is_consistent: bool
    identity_consistent: bool
    dates_consistent: bool
    amounts_consistent: bool
    conflicts_detected: list[str] = Field(default_factory=list)
    verdict_override: AgentVerdict | None = None


class OrchestratorOutput(BaseModel):
    """Authoritative, legally binding adjudication emitted by the Decision Engine."""
    case_id: str
    execution_id: str
    decision: BusinessDecision
    escalation_sub_reason: EscalationSubReason | None = None
    decision_confidence: float = Field(..., ge=0.0, le=1.0)
    rule_id_matched: str = Field(..., description="Matched priority rule, e.g. 'RULE-P6-CLEAN-PASS'")
    justification: str
    recommendation_tag: str | None = Field(
        default=None,
        description="Non-binding reviewer recommendation; never a top-level decision.",
    )

    agent_summaries: dict[str, Any] = Field(
        ...,
        description="Snapshot of KYC, Claims, Policy, and Fraud outputs",
    )
    is_auto_decided: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
