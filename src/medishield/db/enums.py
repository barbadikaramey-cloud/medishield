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

