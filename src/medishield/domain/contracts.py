from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, model_validator

from medishield.db.enums import (
    AgentVerdict,
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
    IndeterminateReason,
)
from medishield.domain.provenance import (
    BBox,
    ClinicalAssertionStatus,
    EvidenceSource,
    EvidenceVerificationStatus,
    FieldProvenance,
    ProvenanceType,
    ProvenanceValue,
)

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
    document_id: str | None = Field(default=None, description="Document the signal originated from")
    document_name: str | None = Field(default=None, description="File name of the flagged document")


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
    dob: ProvenanceValue | None = None
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
    forensic_status: str | None = Field(
        default=None,
        description="Forensic analysis state: FORENSICS_COMPLETED, ANOMALY_DETECTED, IMAGE_RETRIEVAL_FAILED, NO_SUPPORTED_IMAGE_AVAILABLE",
    )

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
    currency: str = Field(default="USD", description="ISO-4217 Currency Code (e.g. USD, INR, EUR)")
    currency_symbol: str = Field(default="$", description="Currency symbol (e.g. $, ₹, €)")

    @model_validator(mode="before")
    @classmethod
    def _backwards_compat_mapping(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "rev_code" not in data:
                data["rev_code"] = ProvenanceValue(value="UNKNOWN", confidence=0.0)
            elif not isinstance(data["rev_code"], (ProvenanceValue, dict)):
                data["rev_code"] = ProvenanceValue(value=str(data["rev_code"]), confidence=1.0)

            if "hcpcs" not in data and "cpt_code" in data:
                data["hcpcs"] = data["cpt_code"]
            if "hcpcs" in data and not isinstance(data["hcpcs"], (ProvenanceValue, dict)) and data["hcpcs"] is not None:
                data["hcpcs"] = ProvenanceValue(value=str(data["hcpcs"]), confidence=1.0)

            if "description" not in data and "line_description" in data:
                data["description"] = data["line_description"]
            if "description" in data and not isinstance(data["description"], (ProvenanceValue, dict)) and data["description"] is not None:
                data["description"] = ProvenanceValue(value=str(data["description"]), confidence=1.0)

            if "units" not in data:
                if "quantity" in data:
                    data["units"] = data["quantity"]
                else:
                    data["units"] = 1.0
            if "units" in data and not isinstance(data["units"], (ProvenanceValue, dict)) and data["units"] is not None:
                data["units"] = ProvenanceValue(value=float(data["units"]), confidence=1.0)

            if "amount" not in data:
                if "billed_amount" in data:
                    data["amount"] = data["billed_amount"]
                elif "total_charge" in data:
                    data["amount"] = data["total_charge"]
                elif "unit_charge" in data:
                    data["amount"] = data["unit_charge"]
            if "amount" in data and not isinstance(data["amount"], (ProvenanceValue, dict)) and data["amount"] is not None:
                data["amount"] = ProvenanceValue(value=float(data["amount"]), confidence=1.0)

            if "service_date" not in data:
                data["service_date"] = ProvenanceValue(value="UNKNOWN", confidence=0.0)
            elif not isinstance(data["service_date"], (ProvenanceValue, dict)):
                data["service_date"] = ProvenanceValue(value=str(data["service_date"]), confidence=1.0)

            if "currency" not in data:
                data["currency"] = "USD"
            if "currency_symbol" not in data:
                data["currency_symbol"] = "$"
        return data


ServiceLineItem = ServiceLine


class VisualEvidenceStatus(str, Enum):
    """Categorization of VLM visual readings and cross-check verification status."""
    VLM_PROPOSED = "VLM_PROPOSED"          # Proposed by VLM visually, pending independent corroboration
    OCR_CORROBORATED = "OCR_CORROBORATED"  # Confirmed by targeted local OCR reread or existing OCR span
    CONFLICTING = "CONFLICTING"            # Conflicts with existing OCR reading or physical evidence
    UNRESOLVED = "UNRESOLVED"              # Uncorroborated visual candidate, preserved for human review


class VisualEvidenceCandidate(BaseModel):
    """Typed visual-evidence contract distinguishing VLM visual readings from OCR-span evidence (Spec 05 § 5, Spec 06 § 2)."""
    case_id: str
    document_id: str
    page_number: int = Field(default=1, ge=1)
    bbox: BBox | None = Field(default=None, description="Normalized visual bounding region [x0, y0, x1, y1]")
    field_name: str = Field(..., description="Field identifier (e.g. cpt_code, line_amount, service_line)")
    proposed_value: Any
    exact_text: str | None = None
    model_id: str = Field(default="vlm", description="Model/provider identifier")
    model_version: str | None = None
    prompt_version: str = Field(default="v1.0-visual-crosscheck", description="Extraction prompt version")
    status: VisualEvidenceStatus = Field(default=VisualEvidenceStatus.VLM_PROPOSED)
    verification_method: str | None = None
    corroborating_span_id: str | None = None
    uncertainty_flag: bool = False
    document_hash: str | None = None


class ClaimsAgentOutput(BaseModel):
    """Extracted claim header, diagnostic coding, procedure lines, and arithmetic validation."""
    agent_name: str = "claims_agent"
    verdict: AgentVerdict
    confidence: float = Field(..., ge=0.0, le=1.0)

    # Header Information
    claim_amount: ProvenanceValue
    currency: str = Field(default="USD", description="Detected claim currency code (e.g. USD, INR, EUR)")
    currency_symbol: str = Field(default="$", description="Detected claim currency symbol (e.g. $, ₹, €)")
    service_date: ProvenanceValue
    provider_name: ProvenanceValue | None = None
    provider_npi: ProvenanceValue

    # Medical Coding
    icd10_codes: list[ProvenanceValue] = Field(default_factory=list)
    cpt_codes: list[ProvenanceValue] = Field(default_factory=list)
    service_lines: list[ServiceLine] = Field(default_factory=list)
    visual_candidates: list[VisualEvidenceCandidate] = Field(default_factory=list)

    # Mathematical & Schema Validation
    schema_valid: bool
    validation_errors: list[str] = Field(default_factory=list)
    line_items_sum_valid: bool
    calculated_lines_sum: float = Field(..., ge=0.0)
    reason_codes: list[IndeterminateReason] = Field(default_factory=list)

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
    payer_product: str | None = None
    jurisdiction: str | None = None


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
    reason_codes: list[IndeterminateReason] = Field(default_factory=list)

    reasoning_summary: str
    latency_ms: int = Field(..., ge=0)


# ── 6. Fraud Detection Agent Contract ────────────────────────────────────────

class FraudFeature(BaseModel):
    """Binary fraud indicator with its raw supporting measurement (ADR-005)."""
    feature_name: str
    raw_value: float
    detected: bool = False
    description: str


class RiskLevel(str, Enum):
    """Fraud review state; MEDIUM/HIGH remain readable for historical records."""
    LOW = "LOW"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FraudAgentOutput(BaseModel):
    """Deterministic binary fraud-review signal and anomaly breakdown (ADR-005)."""
    agent_name: str = "fraud_agent"
    verdict: AgentVerdict
    confidence: float = Field(..., ge=0.0, le=1.0)

    fraud_score: float = Field(..., ge=0.0, le=1.0)
    fraud_review_required: bool | None = None
    risk_level: RiskLevel
    features: list[FraudFeature] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)

    duplicate_claim_detected: bool = False
    duplicate_claim_id: str | None = None
    velocity_anomaly_detected: bool = False

    latency_ms: int = Field(..., ge=0)


# ── 6B. Clinical Alignment Specialist Contract (ADR-015) ──────────────────────

class ServiceModality(str, Enum):
    """Clinical modality of billed service line."""
    SURGICAL_PROCEDURE = "SURGICAL_PROCEDURE"
    MEDICAL_VISIT_OR_EVAL = "MEDICAL_VISIT_OR_EVAL"
    DIAGNOSTIC_IMAGING = "DIAGNOSTIC_IMAGING"
    LABORATORY_TEST = "LABORATORY_TEST"
    PHARMACEUTICAL = "PHARMACEUTICAL"
    ROOM_AND_BOARD = "ROOM_AND_BOARD"
    OTHER_SERVICE = "OTHER_SERVICE"


class PerformanceEvidenceStatus(str, Enum):
    """Direct clinical documentation that service was physically performed."""
    ORDERED_ONLY = "ORDERED_ONLY"
    DIRECTLY_DOCUMENTED_PERFORMANCE = "DIRECTLY_DOCUMENTED_PERFORMANCE"
    RESULTED = "RESULTED"
    NOT_DOCUMENTED = "NOT_DOCUMENTED"
    EXPLICITLY_CONTRADICTED = "EXPLICITLY_CONTRADICTED"
    CONFIRMED_NON_PERFORMANCE = "CONFIRMED_NON_PERFORMANCE"


class ClinicalIndicationStatus(str, Enum):
    """Documented clinical indication or diagnosis justifying service."""
    EXPLICITLY_INDICATED = "EXPLICITLY_INDICATED"
    POTENTIALLY_RELATED = "POTENTIALLY_RELATED"
    NO_DOCUMENTED_INDICATION = "NO_DOCUMENTED_INDICATION"
    CLINICALLY_CONTRAINDICATED = "CLINICALLY_CONTRAINDICATED"


class MedicalNecessityStatus(str, Enum):
    """Evaluation against authoritative guideline (CMS LCD/NCD or Payer Clinical Policy)."""
    MET = "MET"
    NOT_MET = "NOT_MET"
    CRITERIA_INCOMPLETE = "CRITERIA_INCOMPLETE"
    GUIDELINE_UNAVAILABLE = "GUIDELINE_UNAVAILABLE"


class PriorAuthRequirementStatus(str, Enum):
    """Whether prior authorization is mandated by governing medical policy."""
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    REQUIREMENT_UNKNOWN = "REQUIREMENT_UNKNOWN"


class PriorAuthVerificationStatus(str, Enum):
    """Verification outcome of prior authorization when required."""
    NOT_APPLICABLE = "NOT_APPLICABLE"
    VERIFIED_APPROVED = "VERIFIED_APPROVED"
    VERIFIED_DENIED = "VERIFIED_DENIED"
    MISSING_OR_UNVERIFIED = "MISSING_OR_UNVERIFIED"
    AUTH_SERVICE_UNAVAILABLE = "AUTH_SERVICE_UNAVAILABLE"


class RepeatServiceStatus(str, Enum):
    """Evaluation of repeated identical codes on the same date of service."""
    NOT_A_REPEATED_SERVICE = "NOT_A_REPEATED_SERVICE"
    REPEATED_AND_JUSTIFIED = "REPEATED_AND_JUSTIFIED"
    REPEATED_AND_UNJUSTIFIED = "REPEATED_AND_UNJUSTIFIED"
    UNASSESSED = "UNASSESSED"


class CriterionEvaluationOutcome(str, Enum):
    """Evaluation outcome for an individual mandatory clinical criterion."""
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    UNASSESSED_EVIDENCE_UNAVAILABLE = "UNASSESSED_EVIDENCE_UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CONTRADICTORY = "CONTRADICTORY"


class MandatoryGuidelineCriterion(BaseModel):
    """Granular clinical criterion within an authoritative coverage guideline."""
    criterion_id: str = Field(..., description="Unique criterion identifier (e.g., CRIT-01-CONSERVATIVE-THERAPY)")
    description: str = Field(..., description="Plain-text requirement")
    requirement_reference_id: str | None = None
    requirement_source_reference: str | None = None
    requirement_source_chunk_id: str | None = None
    requirement_source_verified: bool = False
    is_mandatory: bool = Field(default=True, description="Whether criterion is strictly required for coverage")
    evaluation_outcome: CriterionEvaluationOutcome
    supporting_spans: list[FieldProvenance] = Field(
        default_factory=list,
        description="Patient evidence only; guideline text must not be stored here",
    )
    evidence_interpretation: str | None = None
    evidence_relevance_verified: bool = False
    rationale: str | None = Field(default=None, description="Clinical rationale explaining outcome")

    @property
    def has_verified_relevant_patient_evidence(self) -> bool:
        return bool(
            self.evidence_relevance_verified
            and self.evidence_interpretation
            and any(
                span.document_id
                and span.page_number > 0
                and span.text_content.strip()
                and span.evidence_source == EvidenceSource.OCR
                and span.verification_status == EvidenceVerificationStatus.VERIFIED
                and span.evidence_quality_score > 0
                and span.provenance_type in (ProvenanceType.SPAN, ProvenanceType.PAGE)
                and span.assertion_status in (
                    ClinicalAssertionStatus.AFFIRMED,
                    ClinicalAssertionStatus.NEGATED,
                    ClinicalAssertionStatus.CONTRADICTORY,
                )
                and span.normalized_interpretation == self.evidence_interpretation
                for span in self.supporting_spans
            )
        )

    @property
    def has_verified_patient_evidence(self) -> bool:
        return self.has_verified_relevant_patient_evidence and any(
            span.assertion_status == ClinicalAssertionStatus.AFFIRMED
            for span in self.supporting_spans
        )


class GuidelineCitation(BaseModel):
    """Authoritative guideline metadata supporting a necessity evaluation."""
    guideline_id: str = Field(..., description="Unique identifier (e.g., CMS-LCD-L34212)")
    guideline_version: str = Field(..., description="Version tag (e.g., 2026.1)")
    guideline_title: str | None = Field(default=None, description="Official title of the clinical guideline")
    jurisdiction: str | None = Field(default=None, description="Verified target jurisdiction or payer region")
    payer_product: str | None = Field(default=None, description="Verified applicable payer/program")
    source_reference: str | None = Field(default=None, description="Authoritative guideline source reference")
    source_chunk_id: str | None = Field(default=None, description="Exact authoritative source chunk identifier")
    source_verified: bool = False
    applicable_to_claim: bool = False
    effective_date: date = Field(..., description="Effective date of the guideline version")
    expiration_date: date | None = Field(default=None, description="Expiration date; None if active")
    criteria_evaluations: list[MandatoryGuidelineCriterion] = Field(default_factory=list)

    @property
    def all_mandatory_criteria_satisfied(self) -> bool:
        """Returns True only when the guideline and each mandatory patient fact are verified."""
        mandatory = [c for c in self.criteria_evaluations if c.is_mandatory]
        if (
            not mandatory
            or not self.source_verified
            or not self.source_reference
            or not self.source_chunk_id
            or not self.payer_product
            or not self.jurisdiction
            or not self.applicable_to_claim
        ):
            return False
        return all(
            criterion.requirement_reference_id
            and criterion.requirement_source_reference
            and criterion.requirement_source_chunk_id
            and criterion.requirement_source_verified
            and (
                criterion.evaluation_outcome == CriterionEvaluationOutcome.NOT_APPLICABLE
                or (
                    criterion.evaluation_outcome == CriterionEvaluationOutcome.SATISFIED
                    and criterion.has_verified_patient_evidence
                )
            )
            for criterion in mandatory
        )


class LineClinicalEvidence(BaseModel):
    """Granular per-claim-line clinical alignment finding."""
    line_number: int = Field(..., ge=1)
    service_code: str = Field(..., description="CPT, HCPCS, or Revenue Code")
    modality: ServiceModality
    billed_units: float = Field(..., ge=0.0)
    billed_amount: float = Field(..., ge=0.0)
    service_date: date | None = None

    # Granular Evidence Semantics (No fail-open defaults)
    performance_status: PerformanceEvidenceStatus
    indication_status: ClinicalIndicationStatus
    necessity_status: MedicalNecessityStatus
    prior_auth_requirement: PriorAuthRequirementStatus = Field(
        ...,
        description="Explicit, evidence-backed determination whether prior auth is required (never defaulted)",
    )
    prior_auth_verification: PriorAuthVerificationStatus = Field(
        ...,
        description="Verification outcome of prior auth (never defaulted)",
    )
    repeat_service_status: RepeatServiceStatus = Field(
        ...,
        description="Explicit determination of repeat service status (never defaulted)",
    )
    determination_outcome: Literal["SUPPORTED", "UNCERTAIN", "NOT_ESTABLISHED", "NO_RESPONSE"] | None = Field(
        default=None,
        description="Raw ADR-032 clinical determination outcome for this line",
    )

    # Line-Level Evaluation Outcome
    line_verdict: AgentVerdict  # PASS, FAIL, INDETERMINATE, CONFLICT, UNAVAILABLE

    # Provenance and Traceability
    primary_icd10_codes: list[str] = Field(default_factory=list)
    secondary_icd10_codes: list[str] = Field(default_factory=list)
    guideline_citation: GuidelineCitation | None = None
    performance_evidence: list[FieldProvenance] = Field(default_factory=list)
    indication_evidence: list[FieldProvenance] = Field(default_factory=list)
    supporting_spans: list[FieldProvenance] = Field(
        default_factory=list,
        description="Bounding box, document ID, and page provenance of clinical evidence",
    )
    clinical_finding_summary: str = Field(
        ..., description="Brief, factual clinical summary explaining status"
    )
    discrepancy_flag: bool = False
    discrepancy_description: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reason_codes(self) -> list[IndeterminateReason]:
        """Structured causes for an INDETERMINATE line; derived from evidence statuses, never stored."""
        if self.line_verdict != AgentVerdict.INDETERMINATE:
            return []
        codes: list[IndeterminateReason] = []
        if self.performance_status in (
            PerformanceEvidenceStatus.NOT_DOCUMENTED,
            PerformanceEvidenceStatus.ORDERED_ONLY,
        ):
            codes.append(IndeterminateReason.PERFORMANCE_NOT_DOCUMENTED)
        if self.indication_status == ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION:
            codes.append(IndeterminateReason.NO_DOCUMENTED_INDICATION)
        elif self.indication_status == ClinicalIndicationStatus.POTENTIALLY_RELATED:
            codes.append(IndeterminateReason.DIAGNOSIS_VALIDATION_UNAVAILABLE)
        if self.necessity_status == MedicalNecessityStatus.GUIDELINE_UNAVAILABLE:
            codes.append(IndeterminateReason.GUIDELINE_UNAVAILABLE)
        elif self.necessity_status == MedicalNecessityStatus.CRITERIA_INCOMPLETE:
            codes.append(
                IndeterminateReason.CLINICAL_DETERMINATION_UNCERTAIN
                if self.determination_outcome == "UNCERTAIN"
                else IndeterminateReason.GUIDELINE_CRITERIA_UNSATISFIED
            )
        if self.prior_auth_requirement == PriorAuthRequirementStatus.REQUIREMENT_UNKNOWN:
            codes.append(IndeterminateReason.PRIOR_AUTH_REQUIREMENT_UNKNOWN)
        if self.prior_auth_verification in (
            PriorAuthVerificationStatus.MISSING_OR_UNVERIFIED,
            PriorAuthVerificationStatus.AUTH_SERVICE_UNAVAILABLE,
        ):
            codes.append(IndeterminateReason.PRIOR_AUTH_MISSING)
        if self.repeat_service_status in (
            RepeatServiceStatus.UNASSESSED,
            RepeatServiceStatus.REPEATED_AND_UNJUSTIFIED,
        ):
            codes.append(IndeterminateReason.REPEAT_SERVICE_UNASSESSED)
        if not self.supporting_spans:
            codes.append(IndeterminateReason.MISSING_EVIDENCE_SPANS)
        if self.guideline_citation is None:
            codes.append(IndeterminateReason.GUIDELINE_UNAVAILABLE)
        elif not self.guideline_citation.all_mandatory_criteria_satisfied:
            codes.append(IndeterminateReason.GUIDELINE_CRITERIA_UNSATISFIED)
        return list(dict.fromkeys(codes)) or [IndeterminateReason.OTHER]

    @property
    def evidence_sufficient(self) -> bool:
        if self.line_verdict in (AgentVerdict.FAIL, AgentVerdict.CONFLICT):
            candidate_evidence = [
                *self.supporting_spans,
                *self.performance_evidence,
                *self.indication_evidence,
                *(
                    span
                    for criterion in (self.guideline_citation.criteria_evaluations if self.guideline_citation else [])
                    for span in criterion.supporting_spans
                ),
            ]
            return any(
                item.evidence_source == EvidenceSource.OCR
                and item.verification_status == EvidenceVerificationStatus.VERIFIED
                and item.evidence_relevance_verified
                and item.text_content.strip()
                and item.assertion_status in (
                    ClinicalAssertionStatus.AFFIRMED,
                    ClinicalAssertionStatus.NEGATED,
                    ClinicalAssertionStatus.CONTRADICTORY,
                )
                for item in candidate_evidence
            )

        guideline = self.guideline_citation
        if (
            guideline is None
            or not guideline.source_verified
            or not guideline.source_reference
            or not guideline.source_chunk_id
            or not guideline.payer_product
            or not guideline.jurisdiction
            or not guideline.applicable_to_claim
        ):
            return False

        def has_verified_reference(items: list[FieldProvenance]) -> bool:
            return any(
                item.evidence_source == EvidenceSource.OCR
                and item.verification_status == EvidenceVerificationStatus.VERIFIED
                and item.evidence_relevance_verified
                and item.text_content.strip()
                and item.assertion_status != ClinicalAssertionStatus.NOT_ASSESSED
                for item in items
            )

        for criterion in guideline.criteria_evaluations:
            if not criterion.is_mandatory:
                continue
            if (
                criterion.evaluation_outcome == CriterionEvaluationOutcome.UNASSESSED_EVIDENCE_UNAVAILABLE
                or not criterion.requirement_source_verified
                or not criterion.requirement_source_reference
                or not criterion.requirement_source_chunk_id
            ):
                return False
            if (
                criterion.evaluation_outcome != CriterionEvaluationOutcome.NOT_APPLICABLE
                and not criterion.has_verified_relevant_patient_evidence
            ):
                return False
        if self.line_verdict == AgentVerdict.PASS:
            return has_verified_reference(self.performance_evidence) and has_verified_reference(self.indication_evidence)
        return True

    @model_validator(mode="after")
    def validate_line_verdict_consistency(self) -> "LineClinicalEvidence":
        """Enforces that line verdicts strictly follow evidence semantics without fail-open shortcuts."""
        # 1. Performance contradiction immediately forces CONFLICT
        if (
            self.performance_status in (
                PerformanceEvidenceStatus.EXPLICITLY_CONTRADICTED,
                PerformanceEvidenceStatus.CONFIRMED_NON_PERFORMANCE,
            )
            and self.line_verdict != AgentVerdict.CONFLICT
        ):
            raise ValueError("Contradicted performance must produce AgentVerdict.CONFLICT")

        # 2. Performance unmentioned or ordered-only cannot receive PASS
        if (
            self.performance_status in (
                PerformanceEvidenceStatus.NOT_DOCUMENTED,
                PerformanceEvidenceStatus.ORDERED_ONLY,
            )
            and self.line_verdict == AgentVerdict.PASS
        ):
            raise ValueError(
                f"Performance status '{self.performance_status.value}' is insufficient for AgentVerdict.PASS"
            )

        # 3. Indication status requirements for PASS
        if (
            self.indication_status != ClinicalIndicationStatus.EXPLICITLY_INDICATED
            and self.line_verdict == AgentVerdict.PASS
        ):
            raise ValueError(
                f"Indication status '{self.indication_status.value}' cannot produce AgentVerdict.PASS"
            )

        # 4. Medical necessity requirements for PASS
        if (
            self.necessity_status != MedicalNecessityStatus.MET
            and self.line_verdict == AgentVerdict.PASS
        ):
            raise ValueError(
                f"Necessity status '{self.necessity_status.value}' cannot produce AgentVerdict.PASS"
            )

        # 5. Guideline citation criteria, version, applicability, and effective dates for PASS
        if self.line_verdict == AgentVerdict.PASS:
            if self.guideline_citation is None:
                raise ValueError("Line verdict PASS requires a valid GuidelineCitation")
            if not self.guideline_citation.guideline_id or not self.guideline_citation.guideline_version:
                raise ValueError("GuidelineCitation requires non-empty guideline_id and guideline_version")
            if self.service_date:
                if self.service_date < self.guideline_citation.effective_date:
                    raise ValueError(
                        f"Claim service date ({self.service_date}) precedes guideline effective date ({self.guideline_citation.effective_date})"
                    )
                if self.guideline_citation.expiration_date and self.service_date > self.guideline_citation.expiration_date:
                    raise ValueError(
                        f"Claim service date ({self.service_date}) postdates guideline expiration date ({self.guideline_citation.expiration_date})"
                    )
            if not self.guideline_citation.all_mandatory_criteria_satisfied:
                raise ValueError("Line verdict PASS requires all mandatory criteria in GuidelineCitation to be SATISFIED")
            for evidence_name, evidence in (
                ("performance", self.performance_evidence),
                ("indication", self.indication_evidence),
            ):
                if not any(
                    item.evidence_source == EvidenceSource.OCR
                    and item.verification_status == EvidenceVerificationStatus.VERIFIED
                    and item.evidence_relevance_verified
                    and item.assertion_status == ClinicalAssertionStatus.AFFIRMED
                    and item.text_content.strip()
                    and item.normalized_interpretation
                    for item in evidence
                ):
                    raise ValueError(f"Line verdict PASS requires verified, relevant {evidence_name} evidence")
            if not self.evidence_sufficient:
                raise ValueError("Line verdict PASS requires complete evidence sufficient for assessment")

        if (
            self.guideline_citation is not None
            and any(
                criterion.evaluation_outcome == CriterionEvaluationOutcome.CONTRADICTORY
                for criterion in self.guideline_citation.criteria_evaluations
                if criterion.is_mandatory
            )
            and self.line_verdict != AgentVerdict.CONFLICT
        ):
            raise ValueError("Contradictory mandatory criterion evidence must produce AgentVerdict.CONFLICT")

        # 6. Prior authorization requirements for PASS
        if self.prior_auth_requirement == PriorAuthRequirementStatus.REQUIRED:
            if (
                self.prior_auth_verification != PriorAuthVerificationStatus.VERIFIED_APPROVED
                and self.line_verdict == AgentVerdict.PASS
            ):
                raise ValueError(
                    f"Prior authorization REQUIRED with verification status '{self.prior_auth_verification.value}' cannot receive AgentVerdict.PASS"
                )
            if (
                self.prior_auth_verification == PriorAuthVerificationStatus.VERIFIED_DENIED
                and self.line_verdict != AgentVerdict.FAIL
            ):
                raise ValueError("Prior authorization VERIFIED_DENIED must produce AgentVerdict.FAIL")
        elif self.prior_auth_requirement == PriorAuthRequirementStatus.REQUIREMENT_UNKNOWN:
            if self.line_verdict == AgentVerdict.PASS:
                raise ValueError("Line verdict PASS prohibited when prior authorization requirement is REQUIREMENT_UNKNOWN")
        elif self.prior_auth_requirement == PriorAuthRequirementStatus.NOT_REQUIRED:
            if self.prior_auth_verification not in (
                PriorAuthVerificationStatus.NOT_APPLICABLE,
                PriorAuthVerificationStatus.VERIFIED_APPROVED,
            ):
                raise ValueError(
                    f"Prior authorization NOT_REQUIRED cannot have verification status '{self.prior_auth_verification.value}'"
                )
            if self.line_verdict == AgentVerdict.PASS and self.guideline_citation is None:
                raise ValueError("Line verdict PASS for prior auth NOT_REQUIRED requires evidence-backed GuidelineCitation")

        if (
            self.prior_auth_verification in (
                PriorAuthVerificationStatus.MISSING_OR_UNVERIFIED,
                PriorAuthVerificationStatus.AUTH_SERVICE_UNAVAILABLE,
            )
            and self.line_verdict == AgentVerdict.PASS
        ):
            raise ValueError(
                f"Prior authorization verification status '{self.prior_auth_verification.value}' cannot produce AgentVerdict.PASS"
            )

        # 7. Repeat service evaluation gate
        if (
            self.repeat_service_status not in (
                RepeatServiceStatus.NOT_A_REPEATED_SERVICE,
                RepeatServiceStatus.REPEATED_AND_JUSTIFIED,
            )
            and self.line_verdict == AgentVerdict.PASS
        ):
            raise ValueError(
                f"Repeat service status '{self.repeat_service_status.value}' cannot receive AgentVerdict.PASS"
            )

        # 8. Provenance span requirement for PASS
        if self.line_verdict == AgentVerdict.PASS and not self.supporting_spans:
            raise ValueError("Line verdict PASS requires non-empty supporting_spans provenance")

        return self


class FacilityBillingConvention(str, Enum):
    """Payer-approved facility room-and-board billing convention."""
    MIDNIGHT_CENSUS = "MIDNIGHT_CENSUS"
    CALENDAR_DAY_SPAN = "CALENDAR_DAY_SPAN"
    SAME_DAY_OBSERVATION = "SAME_DAY_OBSERVATION"
    FRACTIONAL_HOURLY = "FRACTIONAL_HOURLY"


class BedDayBillingRuleContract(BaseModel):
    """Approved facility billing-rule specification."""
    rule_id: str = Field(..., description="Unique approved rule ID (e.g., BR-ACUTE-INP-01)")
    rule_version: str = Field(..., description="Version tag")
    facility_type: str = Field(..., description="Applicable facility classification (e.g., INPATIENT_ACUTE, SNF)")
    claim_type: str = Field(..., description="Inpatient acute, subacute, or observation")
    effective_date: date
    expiration_date: date | None = None
    is_approved: bool = Field(default=False, description="Must be approved by claims authority")
    convention: FacilityBillingConvention


class BedDayReconciliation(BaseModel):
    """Reconciliation between billed room & board units and documented hospital course."""
    billing_rule: BedDayBillingRuleContract | None = Field(
        default=None,
        description="Explicit approved billing rule; None indicates rule unverified or unavailable",
    )
    claim_facility_type: str | None = Field(
        default=None,
        description="Claim facility classification to verify against billing rule",
    )
    claim_type: str | None = Field(
        default=None,
        description="Claim type to verify against billing rule",
    )

    # Documented Timestamps / Dates (Evidence of Admission and Discharge)
    admission_datetime: datetime | None = None
    discharge_datetime: datetime | None = None
    admission_date: date | None = None
    discharge_date: date | None = None
    timestamps_complete: bool = False

    # Quantitative Units
    calendar_days_stayed: int | None = None
    expected_billable_units_min: float | None = None
    expected_billable_units_max: float | None = None
    billed_bed_days: float = Field(..., ge=0.0)

    # Reconciliation Outcome (No fail-open default)
    is_reconciled: bool = Field(default=False, description="Must be explicitly evaluated; never defaults to True")
    discrepancy_units: float = 0.0
    uncertainty_flag: bool = False
    reconciliation_verdict: AgentVerdict  # PASS, CONFLICT, INDETERMINATE, UNAVAILABLE
    remarks: str = ""

    @model_validator(mode="after")
    def validate_reconciliation_integrity(self) -> "BedDayReconciliation":
        """Ensures that bed-day reconciliation fails closed when evidence is missing, rules unapproved, or ranges ambiguous."""
        # 1. Admission and discharge evidence requirement for PASS:
        has_admission = self.admission_datetime is not None or self.admission_date is not None
        has_discharge = self.discharge_datetime is not None or self.discharge_date is not None
        if (self.is_reconciled or self.reconciliation_verdict == AgentVerdict.PASS) and (
            not has_admission or not has_discharge
        ):
            raise ValueError("Bed-day reconciliation PASS strictly requires documented admission and discharge evidence")

        # 2. Rule presence and approval:
        if self.is_reconciled or self.reconciliation_verdict == AgentVerdict.PASS:
            if self.billing_rule is None:
                raise ValueError("Cannot pass bed-day reconciliation without a governing BedDayBillingRuleContract")
            if not self.billing_rule.is_approved:
                raise ValueError("Cannot pass bed-day reconciliation under an unapproved billing rule")

        # 3. Rule applicability (facility, claim type, and effective dates):
        if self.billing_rule:
            if self.is_reconciled or self.reconciliation_verdict == AgentVerdict.PASS:
                if self.claim_facility_type and self.billing_rule.facility_type != self.claim_facility_type:
                    raise ValueError(
                        f"Billing rule facility '{self.billing_rule.facility_type}' does not match claim facility '{self.claim_facility_type}'"
                    )
                if self.claim_type and self.billing_rule.claim_type != self.claim_type:
                    raise ValueError(
                        f"Billing rule claim_type '{self.billing_rule.claim_type}' does not match claim type '{self.claim_type}'"
                    )

            service_d = self.discharge_date or (self.discharge_datetime.date() if self.discharge_datetime else None)
            if service_d:
                if (
                    service_d < self.billing_rule.effective_date
                    and (self.is_reconciled or self.reconciliation_verdict == AgentVerdict.PASS)
                ):
                    raise ValueError("Billing rule is not yet effective on claim service date")
                if (
                    self.billing_rule.expiration_date
                    and service_d > self.billing_rule.expiration_date
                    and (self.is_reconciled or self.reconciliation_verdict == AgentVerdict.PASS)
                ):
                    raise ValueError("Cannot pass bed-day reconciliation under an expired billing rule")

        # 4. Reconciliation verdict and flag agreement:
        if self.reconciliation_verdict == AgentVerdict.PASS and not self.is_reconciled:
            raise ValueError("reconciliation_verdict=PASS strictly requires is_reconciled=True")
        if self.is_reconciled and self.reconciliation_verdict != AgentVerdict.PASS:
            raise ValueError("is_reconciled=True strictly requires reconciliation_verdict=PASS")

        # 5. Unambiguous calculation and billed-unit match:
        if self.is_reconciled or self.reconciliation_verdict == AgentVerdict.PASS:
            if self.expected_billable_units_min is None or self.expected_billable_units_max is None:
                raise ValueError("Bed-day reconciliation PASS requires computed expected billable unit bounds")
            if self.expected_billable_units_min != self.expected_billable_units_max:
                raise ValueError("Bed-day reconciliation PASS requires an unambiguous calculation (min == max)")
            if self.billed_bed_days != self.expected_billable_units_min:
                raise ValueError(
                    f"Billed bed days ({self.billed_bed_days}) does not match expected units ({self.expected_billable_units_min})"
                )
            if self.uncertainty_flag:
                raise ValueError("uncertainty_flag=True is incompatible with is_reconciled=True or PASS")

        return self


class ClinicalAlignmentOutput(BaseModel):
    """Composite output emitted by Stage B Clinical Alignment Specialist."""
    agent_name: str = "clinical_alignment_specialist"
    verdict: AgentVerdict  # PASS, FAIL, INDETERMINATE, CONFLICT, UNAVAILABLE
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Calibrated confidence derived from Spec 06 formula; None if uncalibrated",
    )

    # Line Item Alignments
    expected_claim_line_numbers: list[int] = Field(..., description="Unique line identifiers from claim form")
    total_lines_assessed: int = Field(..., ge=0, description="Total service lines evaluated by clinical engine")
    line_assessments: list[LineClinicalEvidence] = Field(default_factory=list)
    bed_day_reconciliation: BedDayReconciliation | None = None

    # Quantitative Breakdown
    lines_fully_supported_count: int = 0
    lines_indeterminate_count: int = 0
    lines_contradicted_count: int = 0
    lines_failed_necessity_count: int = 0
    lines_unavailable_count: int = 0
    unsupported_billed_amount: float = Field(default=0.0, ge=0.0)

    # Document Coverage Context
    clinical_documents_present: list[str] = Field(default_factory=list)
    low_ocr_quality_pages: list[int] = Field(default_factory=list)
    schema_valid: bool = False
    ocr_quality_sufficient: bool = False
    evidence_sufficient: bool = False
    assessment_sufficient: bool = False

    # Summary and Discrepancies
    clinical_summary: str
    blocking_discrepancies: list[str] = Field(default_factory=list)
    latency_ms: int = Field(..., ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def reason_codes(self) -> list[IndeterminateReason]:
        """Union of line-level reason codes; empty unless the overall verdict is INDETERMINATE."""
        if self.verdict != AgentVerdict.INDETERMINATE:
            return []
        codes: list[IndeterminateReason] = []
        if not self.clinical_documents_present:
            codes.append(IndeterminateReason.MISSING_INPUT)
        for line in self.line_assessments:
            codes.extend(line.reason_codes)
        return list(dict.fromkeys(codes)) or [IndeterminateReason.OTHER]

    @model_validator(mode="after")
    def validate_composite_invariants(self) -> "ClinicalAlignmentOutput":
        """Enforces programmatic structural invariants on ClinicalAlignmentOutput."""
        # 1. Expected line identifier uniqueness:
        if len(self.expected_claim_line_numbers) != len(set(self.expected_claim_line_numbers)):
            raise ValueError(f"expected_claim_line_numbers contains duplicate identifiers: {self.expected_claim_line_numbers}")

        # 2. Assessed line identifier uniqueness:
        actual_line_numbers = [l.line_number for l in self.line_assessments]
        if len(actual_line_numbers) != len(set(actual_line_numbers)):
            raise ValueError(f"line_assessments contains duplicate line identifiers: {actual_line_numbers}")

        # 3. Exact identifier set equality:
        if set(actual_line_numbers) != set(self.expected_claim_line_numbers):
            raise ValueError(
                f"Line assessment identifier mismatch: assessed lines {sorted(set(actual_line_numbers))} "
                f"do not match expected claim lines {sorted(set(self.expected_claim_line_numbers))}"
            )

        # 4. Line count agreement:
        if self.total_lines_assessed != len(self.line_assessments):
            raise ValueError("total_lines_assessed must equal len(line_assessments)")

        # 5. Aggregate count agreement:
        calc_pass = sum(1 for l in self.line_assessments if l.line_verdict == AgentVerdict.PASS)
        calc_ind = sum(1 for l in self.line_assessments if l.line_verdict == AgentVerdict.INDETERMINATE)
        calc_con = sum(1 for l in self.line_assessments if l.line_verdict == AgentVerdict.CONFLICT)
        calc_fail = sum(1 for l in self.line_assessments if l.line_verdict == AgentVerdict.FAIL)
        calc_unav = sum(1 for l in self.line_assessments if l.line_verdict == AgentVerdict.UNAVAILABLE)

        if (
            self.lines_fully_supported_count != calc_pass
            or self.lines_indeterminate_count != calc_ind
            or self.lines_contradicted_count != calc_con
            or self.lines_failed_necessity_count != calc_fail
            or self.lines_unavailable_count != calc_unav
        ):
            raise ValueError("Aggregate line counts do not reconcile with line_assessments outcomes")

        # 6. Sufficiency vs. Verdict Invariant:
        if not self.assessment_sufficient and self.verdict == AgentVerdict.PASS:
            raise ValueError("assessment_sufficient=False is strictly incompatible with composite AgentVerdict.PASS")

        # 7. Composite PASS requires unanimous line PASS:
        if self.verdict == AgentVerdict.PASS and any(l.line_verdict != AgentVerdict.PASS for l in self.line_assessments):
            raise ValueError("Composite verdict cannot be PASS when any line verdict is not PASS")

        # 8. Room-and-board reconciliation barrier:
        has_room_and_board = any(l.modality == ServiceModality.ROOM_AND_BOARD for l in self.line_assessments)
        if has_room_and_board:
            if self.bed_day_reconciliation is None and self.verdict == AgentVerdict.PASS:
                raise ValueError("Claim contains room-and-board charges; bed_day_reconciliation is required for PASS")
            if (
                self.bed_day_reconciliation is not None
                and self.bed_day_reconciliation.reconciliation_verdict != AgentVerdict.PASS
                and self.verdict == AgentVerdict.PASS
            ):
                raise ValueError("Composite verdict cannot be PASS when bed_day_reconciliation did not PASS")

        return self


def build_clinical_alignment_output(
    expected_line_numbers: list[int],
    line_assessments: list[LineClinicalEvidence],
    bed_day_reconciliation: BedDayReconciliation | None = None,
    clinical_documents_present: list[str] | None = None,
    low_ocr_quality_pages: list[int] | None = None,
    latency_ms: int = 0,
) -> ClinicalAlignmentOutput:
    """
    Authoritative factory constructing ClinicalAlignmentOutput with fail-closed error propagation.
    Ensures that validation errors produce a structured INDETERMINATE result routed to human review.
    """
    blocking_discrepancies: list[str] = []
    input_errors: list[str] = []

    if low_ocr_quality_pages is None:
        low_ocr_quality_pages = []
    if clinical_documents_present is None:
        clinical_documents_present = []
    elif isinstance(clinical_documents_present, bool):
        clinical_documents_present = ["DOC-CLIN-01"] if clinical_documents_present else []

    # 1. Deduplicate and Validate Expected Line Numbers
    unique_expected = sorted(set(expected_line_numbers))
    if len(expected_line_numbers) != len(unique_expected):
        err = f"DUPLICATE_EXPECTED_LINE_NUMBERS_DETECTED: input={expected_line_numbers}"
        input_errors.append(err)
        blocking_discrepancies.append(err)

    # 2. Line Identifier Coverage Verification
    actual_line_numbers = [l.line_number for l in line_assessments]
    unique_actual = set(actual_line_numbers)
    expected_set = set(unique_expected)

    has_duplicate_assessed = len(actual_line_numbers) != len(unique_actual)
    if has_duplicate_assessed:
        err = f"DUPLICATE_ASSESSED_LINE_NUMBERS: {actual_line_numbers}"
        input_errors.append(err)
        blocking_discrepancies.append(err)

    missing_lines = expected_set - unique_actual
    unexpected_lines = unique_actual - expected_set

    if missing_lines:
        err = f"MISSING_CLAIM_LINES_IN_ASSESSMENT: {sorted(missing_lines)}"
        input_errors.append(err)
        blocking_discrepancies.append(err)
    if unexpected_lines:
        err = f"UNEXPECTED_CLAIM_LINES_IN_ASSESSMENT: {sorted(unexpected_lines)}"
        input_errors.append(err)
        blocking_discrepancies.append(err)

    # 3. Create Structured Fail-Closed Stubs for Missing Lines to Satisfy Contract Invariant
    sanitized_assessments = list(line_assessments)
    if missing_lines or unexpected_lines or has_duplicate_assessed:
        seen_ids = set()
        deduped_actual = []
        for l in sanitized_assessments:
            if l.line_number in expected_set and l.line_number not in seen_ids:
                seen_ids.add(l.line_number)
                deduped_actual.append(l)

        for missing_id in sorted(missing_lines):
            stub_line = LineClinicalEvidence(
                line_number=missing_id,
                service_code="UNKNOWN",
                modality=ServiceModality.OTHER_SERVICE,
                billed_units=0.0,
                billed_amount=0.0,
                performance_status=PerformanceEvidenceStatus.NOT_DOCUMENTED,
                indication_status=ClinicalIndicationStatus.NO_DOCUMENTED_INDICATION,
                necessity_status=MedicalNecessityStatus.GUIDELINE_UNAVAILABLE,
                prior_auth_requirement=PriorAuthRequirementStatus.REQUIREMENT_UNKNOWN,
                prior_auth_verification=PriorAuthVerificationStatus.MISSING_OR_UNVERIFIED,
                repeat_service_status=RepeatServiceStatus.UNASSESSED,
                line_verdict=AgentVerdict.INDETERMINATE,
                clinical_finding_summary=f"Line {missing_id} missing from extraction payload.",
                discrepancy_flag=True,
                discrepancy_description="Omitted from specialist extraction.",
            )
            deduped_actual.append(stub_line)

        sanitized_assessments = sorted(deduped_actual, key=lambda x: x.line_number)

    # 4. Low Document Quality Check
    if bool(low_ocr_quality_pages):
        err = f"LOW_OCR_QUALITY_ON_PAGES: {low_ocr_quality_pages}"
        input_errors.append(err)
        blocking_discrepancies.append(err)

    # 5. Aggregate Quantitative Counts
    calc_pass = sum(1 for l in sanitized_assessments if l.line_verdict == AgentVerdict.PASS)
    calc_ind = sum(1 for l in sanitized_assessments if l.line_verdict == AgentVerdict.INDETERMINATE)
    calc_con = sum(1 for l in sanitized_assessments if l.line_verdict == AgentVerdict.CONFLICT)
    calc_fail = sum(1 for l in sanitized_assessments if l.line_verdict == AgentVerdict.FAIL)
    calc_unav = sum(1 for l in sanitized_assessments if l.line_verdict == AgentVerdict.UNAVAILABLE)
    unsupported_amt = sum(
        l.billed_amount for l in sanitized_assessments if l.line_verdict != AgentVerdict.PASS
    )

    # 6. Bed-Day Barrier for Facility Claims
    has_room_and_board = any(l.modality == ServiceModality.ROOM_AND_BOARD for l in sanitized_assessments)
    bed_day_ok = True
    bed_day_conflict = False
    bed_day_fail = False
    bed_day_unav = False

    if has_room_and_board:
        if bed_day_reconciliation is None:
            bed_day_ok = False
            blocking_discrepancies.append("BED_DAY_RECONCILIATION_NOT_PASS: status=MISSING")
        elif bed_day_reconciliation.reconciliation_verdict != AgentVerdict.PASS:
            bed_day_ok = False
            reconciliation_status = bed_day_reconciliation.reconciliation_verdict.value
            blocking_discrepancies.append(f"BED_DAY_RECONCILIATION_NOT_PASS: status={reconciliation_status}")
            if bed_day_reconciliation.reconciliation_verdict == AgentVerdict.CONFLICT:
                bed_day_conflict = True
            elif bed_day_reconciliation.reconciliation_verdict == AgentVerdict.FAIL:
                bed_day_fail = True
            elif bed_day_reconciliation.reconciliation_verdict == AgentVerdict.UNAVAILABLE:
                bed_day_unav = True

    # 7. Composite Verdict Derivation (Fail-Closed)
    had_input_error = bool(input_errors)
    ocr_acceptable = len(low_ocr_quality_pages) == 0
    schema_valid = not had_input_error
    evidence_sufficient = bool(sanitized_assessments) and all(
        line.evidence_sufficient for line in sanitized_assessments
    )
    assessment_sufficient = schema_valid and ocr_acceptable and evidence_sufficient

    if not assessment_sufficient:
        verdict = AgentVerdict.INDETERMINATE
    elif calc_unav > 0 or bed_day_unav:
        verdict = AgentVerdict.UNAVAILABLE
    elif calc_con > 0 or bed_day_conflict:
        verdict = AgentVerdict.CONFLICT
    elif calc_fail > 0 or bed_day_fail:
        verdict = AgentVerdict.FAIL
    elif calc_ind > 0 or not bed_day_ok:
        verdict = AgentVerdict.INDETERMINATE
    elif calc_pass == len(sanitized_assessments) and bed_day_ok:
        verdict = AgentVerdict.PASS
    else:
        verdict = AgentVerdict.INDETERMINATE

    # Calibrated confidence: exact formula specified by canonical Spec 06 § 5
    # Decision Confidence = 0.40 * Provenance Quality + 0.35 * Validation Completeness + 0.25 * Model Confidence
    calibrated_confidence: float | None = None
    if verdict == AgentVerdict.PASS and assessment_sufficient:
        all_spans = [
            span
            for line in sanitized_assessments
            for evidence in (
                line.performance_evidence,
                line.indication_evidence,
                *(
                    criterion.supporting_spans
                    for criterion in (
                        line.guideline_citation.criteria_evaluations
                        if line.guideline_citation is not None
                        else []
                    )
                ),
            )
            for span in evidence
        ]
        if all_spans and all(span.model_confidence is not None for span in all_spans):
            prov_quality = sum(s.evidence_quality_score for s in all_spans) / len(all_spans)
            val_completeness = 1.0 if schema_valid and ocr_acceptable and evidence_sufficient else 0.0
            model_conf = min(float(s.model_confidence) for s in all_spans if s.model_confidence is not None)
            calibrated_confidence = round(
                0.40 * prov_quality + 0.35 * val_completeness + 0.25 * model_conf, 3
            )

    # Collect line-level discrepancies and clinical findings
    for l in sanitized_assessments:
        desc = l.discrepancy_description or (
            l.clinical_finding_summary if l.line_verdict != AgentVerdict.PASS else None
        )
        if desc and desc not in blocking_discrepancies:
            blocking_discrepancies.append(desc)

    summary_parts = [(
        f"Clinical alignment {verdict.value}: {calc_pass}/{len(sanitized_assessments)} lines fully supported, "
        f"{calc_ind} indeterminate, {calc_con} conflicted, {calc_fail} failed."
    )]
    unique_findings = list(dict.fromkeys(blocking_discrepancies))
    if unique_findings:
        summarized_findings = unique_findings[:3]
        extra_count = len(unique_findings) - len(summarized_findings)
        suffix = f" (+{extra_count} additional findings)" if extra_count else ""
        summary_parts.append(f"Review: {'; '.join(summarized_findings)}{suffix}")
    clinical_summary_text = " ".join(summary_parts)

    return ClinicalAlignmentOutput(
        agent_name="clinical_alignment_specialist",
        verdict=verdict,
        confidence=calibrated_confidence,
        expected_claim_line_numbers=unique_expected,
        total_lines_assessed=len(sanitized_assessments),
        line_assessments=sanitized_assessments,
        bed_day_reconciliation=bed_day_reconciliation,
        lines_fully_supported_count=calc_pass,
        lines_indeterminate_count=calc_ind,
        lines_contradicted_count=calc_con,
        lines_failed_necessity_count=calc_fail,
        lines_unavailable_count=calc_unav,
        unsupported_billed_amount=unsupported_amt,
        clinical_documents_present=clinical_documents_present,
        low_ocr_quality_pages=low_ocr_quality_pages,
        schema_valid=schema_valid,
        ocr_quality_sufficient=ocr_acceptable,
        evidence_sufficient=evidence_sufficient,
        assessment_sufficient=assessment_sufficient,
        clinical_summary=clinical_summary_text,
        blocking_discrepancies=blocking_discrepancies,
        latency_ms=latency_ms,
    )


# ── 7. Decision Engine & Orchestrator Contract ───────────────────────────────

class ConsistencyCheckReport(BaseModel):
    """Outcome of cross-agent factual consistency validation."""
    is_consistent: bool
    identity_consistent: bool
    dates_consistent: bool
    amounts_consistent: bool
    conflicts_detected: list[str] = Field(default_factory=list)
    verdict_override: AgentVerdict | None = None
    doctor_signature_verified: bool = True
    patient_signature_verified: bool = True
    officer_signature_verified: bool = True
    npi_verified: bool = True
    prior_authorization_verified: bool = True
    procedure_codes_verified: bool = True
    unvalidated_charges_total: float = 0.0
    specific_escalation_reasons: list[str] = Field(default_factory=list)
    date_reconciliation_summary: str | None = None


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
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
