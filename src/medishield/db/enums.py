from enum import Enum


class WorkflowState(str, Enum):
    """Operational lifecycle states of a case in the processing pipeline (ADR-001)."""
    RECEIVED = "RECEIVED"
    SCANNING = "SCANNING"
    SPLIT = "SPLIT"
    CLASSIFYING = "CLASSIFYING"
    ANALYZING = "ANALYZING"
    AWAITING_INFO = "AWAITING_INFO"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BusinessDecision(str, Enum):
    """Binding adjudication decisions produced exclusively by the Decision Engine (ADR-001)."""
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


class EscalationSubReason(str, Enum):
    """Standardized taxonomy explaining why a case escalated to human review or info request."""
    MISSING_INFORMATION = "MISSING_INFORMATION"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    FRAUD_REVIEW = "FRAUD_REVIEW"
    SECURITY_REVIEW = "SECURITY_REVIEW"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"
    AMBIGUITY = "AMBIGUITY"
    RECOMMEND_DENIAL = "RECOMMEND_DENIAL"


class AgentVerdict(str, Enum):
    """Evidence and assessment output emitted by individual specialist agents (ADR-001)."""
    PASS = "PASS"
    FAIL = "FAIL"
    INDETERMINATE = "INDETERMINATE"
    CONFLICT = "CONFLICT"
    UNAVAILABLE = "UNAVAILABLE"


class IndeterminateReason(str, Enum):
    """Structured cause of an INDETERMINATE agent outcome; informational, never alters decision precedence."""
    MISSING_INPUT = "MISSING_INPUT"
    INCOMPLETE_CLAIM_DATA = "INCOMPLETE_CLAIM_DATA"
    UNVERIFIED_DIAGNOSIS_CODE = "UNVERIFIED_DIAGNOSIS_CODE"
    UNVERIFIED_PROCEDURE_CODE = "UNVERIFIED_PROCEDURE_CODE"
    UNRESOLVED_VISUAL_EVIDENCE = "UNRESOLVED_VISUAL_EVIDENCE"
    MISSING_PROVENANCE = "MISSING_PROVENANCE"
    NO_POLICY_CLAUSE = "NO_POLICY_CLAUSE"
    UNVERIFIED_POLICY_CITATION = "UNVERIFIED_POLICY_CITATION"
    PRIOR_AUTH_MISSING = "PRIOR_AUTH_MISSING"
    PRIOR_AUTH_REQUIREMENT_UNKNOWN = "PRIOR_AUTH_REQUIREMENT_UNKNOWN"
    PERFORMANCE_NOT_DOCUMENTED = "PERFORMANCE_NOT_DOCUMENTED"
    NO_DOCUMENTED_INDICATION = "NO_DOCUMENTED_INDICATION"
    DIAGNOSIS_VALIDATION_UNAVAILABLE = "DIAGNOSIS_VALIDATION_UNAVAILABLE"
    GUIDELINE_UNAVAILABLE = "GUIDELINE_UNAVAILABLE"
    GUIDELINE_CRITERIA_UNSATISFIED = "GUIDELINE_CRITERIA_UNSATISFIED"
    CLINICAL_DETERMINATION_UNCERTAIN = "CLINICAL_DETERMINATION_UNCERTAIN"
    REPEAT_SERVICE_UNASSESSED = "REPEAT_SERVICE_UNASSESSED"
    MISSING_EVIDENCE_SPANS = "MISSING_EVIDENCE_SPANS"
    OTHER = "OTHER"


class DocumentType(str, Enum):
    """Authoritative document classification types recognized by MediShield intake."""
    CLAIM_FORM = "CLAIM_FORM"
    ID_DOCUMENT = "ID_DOCUMENT"
    DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY"
    PRESCRIPTION = "PRESCRIPTION"
    POLICY_AMENDMENT = "POLICY_AMENDMENT"
    UNKNOWN = "UNKNOWN"


class ActorType(str, Enum):
    """Actor identity category performing an action or emitting a decision."""
    SYSTEM = "SYSTEM"
    HUMAN = "HUMAN"


class CodingSystem(str, Enum):
    """Authoritative medical coding terminology systems (CPT, HCPCS, ICD-10, Revenue)."""
    CPT = "CPT"
    HCPCS = "HCPCS"
    ICD10_CM = "ICD10_CM"
    REV_CODE = "REV_CODE"
    NDC = "NDC"


class UserRole(str, Enum):
    """Authoritative role identities for multi-tenant access control (ADR-012)."""
    CLAIMER = "CLAIMER"
    REVIEWER = "OPERATIONS_REVIEWER"
    MANAGER = "OPERATIONS_MANAGER"
    ADMIN = "ADMINISTRATOR"

