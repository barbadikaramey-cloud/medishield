# MediShield Multi-Agent Document Intake System
## Canonical Agent Interface Contracts

**Status:** Authoritative Engineering Contract  
**Version:** 1.0.0 (Canonical Baseline)  
**Governing Principle:** All agents communicate via typed, versioned Pydantic schemas. Every material fact includes verifiable provenance.

---

## 1. Provenance Base Model

Every extracted factual field in the MediShield system inherits from `FieldProvenance`, ensuring strict auditability and verifiable links back to OCR text spans.

```python
from typing import List, Optional, Dict, Any
from enum import Enum
from pydantic import BaseModel, Field
from datetime import date, datetime

class ProvenanceType(str, Enum):
    SPAN = "SPAN"                  # Grounded in a specific OCR text span
    PAGE = "PAGE"                  # Fallback: grounded at page level (quality penalty)
    DERIVED = "DERIVED"            # Computed or database-resolved fact
    INFERRED = "INFERRED"          # Interpreted by LLM reasoning

class FieldProvenance(BaseModel):
    document_id: str = Field(..., description="Referenced document identifier")
    page_number: int = Field(..., ge=1, description="1-indexed physical page")
    span_id: Optional[str] = Field(None, description="Exact OCR span ID, e.g., 'span-p1-042'")
    text_content: str = Field(..., description="Exact raw text snippet extracted from document")
    bounding_box: Optional[List[float]] = Field(None, description="[x_min, y_min, x_max, y_max] normalized coordinates")
    provenance_type: ProvenanceType = ProvenanceType.SPAN
    evidence_quality_score: float = Field(..., ge=0.0, le=1.0, description="1.0 for span-match, 0.5 for page fallback")

class ProvenanceValue(BaseModel):
    value: Any
    confidence: float = Field(..., ge=0.0, le=1.0)
    provenance: Optional[FieldProvenance] = None
```

---

## 2. Classifier Agent Contract

The Classifier Agent identifies document categories and assigns routing metadata.

```python
class DocumentType(str, Enum):
    CLAIM_FORM = "CLAIM_FORM"
    ID_DOCUMENT = "ID_DOCUMENT"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    PRESCRIPTION = "PRESCRIPTION"
    POLICY_AMENDMENT = "POLICY_AMENDMENT"
    UNKNOWN = "UNKNOWN"

class ClassifierAgentOutput(BaseModel):
    document_id: str
    doc_type: DocumentType
    confidence: float = Field(..., ge=0.0, le=1.0)
    verdict: str = Field(
        ...,
        description=(
            "AgentVerdict: PASS (high-confidence classification, doc_type != UNKNOWN), "
            "INDETERMINATE (low confidence or doc_type == UNKNOWN), "
            "UNAVAILABLE (model or feature extraction failure)"
        )
    )
    routing_tags: List[str] = Field(default_factory=list)
    visual_characteristics: Dict[str, Any] = Field(default_factory=dict)
    model_version: str
    latency_ms: int
```

---

## 3. OCR / Preprocessor Contract

The OCR Preprocessor produces normalized text, geometric coordinates, and character spans.

```python
class OCRBoundingBox(BaseModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float

class OCRTextSpan(BaseModel):
    span_id: str
    page_number: int
    text: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    bbox: OCRBoundingBox

class OCRPreprocessorOutput(BaseModel):
    document_id: str
    ocr_engine: str
    total_pages: int
    raw_text: str
    spans: List[OCRTextSpan]
    image_quality_score: float = Field(..., ge=0.0, le=1.0)
    verdict: str = Field(
        ...,
        description=(
            "AgentVerdict: PASS (adequate image quality and OCR confidence), "
            "INDETERMINATE (poor image quality or low-confidence spans), "
            "UNAVAILABLE (OCR engine failure or unreadable input)"
        )
    )
    is_searchable_pdf: bool
    payload_storage_uri: str
```

---

## 4. KYC Agent Contract

The KYC Agent validates member identity, date of birth, policy active dates, and checks for physical/digital image tampering.

```python
class TamperSignal(BaseModel):
    signal_type: str = Field(..., description="ELA_ANOMALY, FONT_INCONSISTENCY, CLONE_STAMP")
    severity: str = Field(..., description="LOW, MEDIUM, HIGH")
    description: str
    confidence: float = Field(..., ge=0.0, le=1.0)

class KYCAgentOutput(BaseModel):
    agent_name: str = "kyc_agent"
    verdict: str = Field(..., description="AgentVerdict: PASS, FAIL, INDETERMINATE, CONFLICT, UNAVAILABLE")
    confidence: float = Field(..., ge=0.0, le=1.0)
    
    # Validated Identity Fields
    member_id: Optional[ProvenanceValue] = None
    first_name: Optional[ProvenanceValue] = None
    last_name: Optional[ProvenanceValue] = None
    date_of_birth: Optional[ProvenanceValue] = None
    government_id_number: Optional[ProvenanceValue] = None
    
    # Verification Checks
    member_db_matched: bool = False
    dob_matched: bool = False
    id_expired: bool = False
    id_expiration_date: Optional[date] = None
    policy_active_on_service_date: bool = False
    
    # Tamper & Integrity
    tamper_detected: bool = False
    tamper_score: float = Field(default=0.0, ge=0.0, le=1.0)
    tamper_signals: List[TamperSignal] = Field(default_factory=list)
    
    flags: List[str] = Field(default_factory=list)
    latency_ms: int
```

---

## 5. Claims Agent Contract

The Claims Agent extracts diagnosis codes, procedure codes, line items, and verifies mathematical consistency.

```python
class ServiceLineItem(BaseModel):
    line_number: int
    cpt_code: ProvenanceValue
    description: ProvenanceValue
    service_date: ProvenanceValue
    quantity: ProvenanceValue
    unit_charge: ProvenanceValue
    total_charge: ProvenanceValue

class ClaimsAgentOutput(BaseModel):
    agent_name: str = "claims_agent"
    verdict: str = Field(..., description="AgentVerdict: PASS, FAIL, INDETERMINATE, CONFLICT, UNAVAILABLE")
    confidence: float = Field(..., ge=0.0, le=1.0)
    
    # Header Information
    claim_amount: ProvenanceValue
    service_date: ProvenanceValue
    provider_name: Optional[ProvenanceValue] = None
    provider_npi: ProvenanceValue
    
    # Medical Coding
    icd10_codes: List[ProvenanceValue] = Field(default_factory=list)
    cpt_codes: List[ProvenanceValue] = Field(default_factory=list)
    service_lines: List[ServiceLineItem] = Field(default_factory=list)
    
    # Mathematical & Schema Validation
    schema_valid: bool
    validation_errors: List[str] = Field(default_factory=list)
    line_items_sum_valid: bool
    calculated_lines_sum: float
    
    latency_ms: int
```

---

## 6. Policy Agent Contract (2-Stage Architecture)

### 6.1 Stage A: Policy Context Resolution

```python
class CandidatePolicy(BaseModel):
    policy_id: str
    policy_version: str
    effective_start_date: date
    effective_end_date: Optional[date] = None
    plan_name: str
    active_riders: List[str] = Field(default_factory=list)

class PolicyContextOutput(BaseModel):
    member_enrollment_id: str
    candidate_policies: List[CandidatePolicy] = Field(
        ...,
        description="All candidate policies for the member. Service-date filtering occurs at JoinClaimReady barrier."
    )
    verdict: str = Field(
        ...,
        description=(
            "AgentVerdict: PASS (at least one candidate enrollment found), "
            "FAIL (member not found or no enrollments exist), "
            "INDETERMINATE (enrollment data ambiguous), "
            "UNAVAILABLE (database lookup failure)"
        )
    )
```

### 6.2 Stage B: Policy Coverage Evaluation

```python
class PolicyClauseCitation(BaseModel):
    policy_id: str
    policy_version: str
    section_number: str
    clause_title: str
    verbatim_text: str
    page_number: int
    vector_score: float
    verified_in_corpus: bool = Field(..., description="Verified against authoritative index")

class PolicyCoverageOutput(BaseModel):
    agent_name: str = "policy_agent"
    verdict: str = Field(..., description="AgentVerdict: PASS, FAIL, INDETERMINATE, UNAVAILABLE")
    confidence: float = Field(..., ge=0.0, le=1.0)
    
    covered: bool
    coverage_percentage: float = Field(..., ge=0.0, le=100.0)
    policy_deductible_limit: Optional[float] = Field(
        None,
        description="Annual deductible limit from policy document. Actual remaining deductible requires claims history (out of scope for RAG)."
    )
    policy_copay_percentage: Optional[float] = Field(
        None,
        description="Copay/coinsurance percentage from policy document terms."
    )
    
    cited_clauses: List[PolicyClauseCitation] = Field(default_factory=list)
    identified_exclusions: List[str] = Field(default_factory=list)
    prior_authorization_required: bool = False
    prior_authorization_present: bool = False
    
    reasoning_summary: str
    latency_ms: int
```

---

## 7. Fraud Detection Agent Contract

The Fraud Detection Agent scores claims against a deterministic feature suite and patient billing history.

```python
class FraudFeature(BaseModel):
    feature_name: str
    raw_value: float
    normalized_score: float = Field(..., ge=0.0, le=1.0)
    weight: float = Field(..., ge=0.0, le=1.0)
    description: str

class RiskLevel(str, Enum):
    LOW = "LOW"          # fraud_score < 0.30
    MEDIUM = "MEDIUM"    # 0.30 <= fraud_score < 0.70
    HIGH = "HIGH"        # fraud_score >= 0.70

class FraudAgentOutput(BaseModel):
    agent_name: str = "fraud_agent"
    verdict: str = Field(..., description="AgentVerdict: PASS, FAIL, INDETERMINATE, UNAVAILABLE")
    confidence: float = Field(..., ge=0.0, le=1.0)
    
    fraud_score: float = Field(..., ge=0.0, le=1.0)
    risk_level: RiskLevel
    features: List[FraudFeature]
    anomalies: List[str] = Field(default_factory=list)
    
    duplicate_claim_detected: bool = False
    duplicate_claim_id: Optional[str] = None
    velocity_anomaly_detected: bool = False
    
    latency_ms: int
```

---

## 8. Orchestrator & Decision Engine Contract

The Decision Engine produces the authoritative, legally binding business determination.

```python
class OrchestratorOutput(BaseModel):
    case_id: str
    execution_id: str
    decision: str = Field(..., description="BusinessDecision: APPROVE, REJECT, ESCALATE")
    escalation_sub_reason: Optional[str] = Field(
        None, 
        description="EscalationSubReason: MISSING_INFORMATION, HUMAN_REVIEW, FRAUD_REVIEW, SECURITY_REVIEW, TECHNICAL_FAILURE, AMBIGUITY"
    )
    decision_confidence: float = Field(..., ge=0.0, le=1.0)
    rule_id_matched: str = Field(..., description="E.g., 'RULE-P6-CLEAN-PASS'")
    justification: str
    
    agent_summaries: Dict[str, Any] = Field(
        ..., 
        description="Consolidated snapshot of KYC, Claims, Policy, and Fraud results"
    )
    is_auto_decided: bool = True
    created_at: datetime
```
