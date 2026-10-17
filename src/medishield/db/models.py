import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from medishield.db.enums import (
    ActorType,
    AgentVerdict,
    BusinessDecision,
    CodingSystem,
    DocumentType,
    EscalationSubReason,
    WorkflowState,
)
from medishield.db.session import Base


class Case(Base):

    """Case Management Aggregate: Primary operational entity for claims processing."""
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    case_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    workflow_state: Mapped[WorkflowState] = mapped_column(
        SQLEnum(WorkflowState, name="workflow_state_enum", native_enum=True),
        nullable=False,
        default=WorkflowState.RECEIVED,
        server_default=text("'RECEIVED'"),
    )
    workflow_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1.0.0", server_default=text("'v1.0.0'")
    )
    current_decision: Mapped[BusinessDecision | None] = mapped_column(
        SQLEnum(BusinessDecision, name="business_decision_enum", native_enum=True),
        nullable=True,
    )
    current_sub_reason: Mapped[EscalationSubReason | None] = mapped_column(
        SQLEnum(EscalationSubReason, name="escalation_sub_reason_enum", native_enum=True),
        nullable=True,
    )
    decision_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    is_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    missing_documents: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.utcnow,
    )

    # Relationships
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="case", cascade="all, delete-orphan"
    )
    claim: Mapped[Optional["Claim"]] = relationship(
        "Claim", back_populates="case", uselist=False, cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        "AgentRun", back_populates="case", cascade="all, delete-orphan"
    )
    decisions: Mapped[list["Decision"]] = relationship(
        "Decision", back_populates="case", cascade="all, delete-orphan"
    )
    human_reviews: Mapped[list["HumanReview"]] = relationship(
        "HumanReview", back_populates="case", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list["AuditEvent"]] = relationship(
        "AuditEvent", back_populates="case", cascade="all, delete-orphan"
    )
    review_lock: Mapped[Optional["ReviewLock"]] = relationship(
        "ReviewLock", back_populates="case", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_cases_state", "workflow_state"),
        Index("idx_cases_created", text("created_at DESC")),
    )


class Document(Base):
    """Submitted physical documents attached to a case."""
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    document_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    doc_type: Mapped[DocumentType] = mapped_column(
        SQLEnum(DocumentType, name="document_type_enum", native_enum=True),
        nullable=False,
        default=DocumentType.UNKNOWN,
        server_default=text("'UNKNOWN'"),
    )
    classification_confidence: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 3), nullable=True
    )
    ocr_payload_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    # Relationships
    case: Mapped["Case"] = relationship("Case", back_populates="documents")
    pages: Mapped[list["DocumentPage"]] = relationship(
        "DocumentPage", back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_documents_case", "case_id"),)


class DocumentPage(Base):
    """Rasterized page images rendered for UI dual-pane inspection."""
    __tablename__ = "document_pages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    width_px: Mapped[int] = mapped_column(Integer, nullable=False)
    height_px: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    document: Mapped["Document"] = relationship("Document", back_populates="pages")

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", name="uq_document_pages_page"),
    )


class Member(Base):
    """Read-only synthetic member master data."""
    __tablename__ = "members"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    member_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    last_name: Mapped[str] = mapped_column(String(64), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    ssn_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE", server_default=text("'ACTIVE'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    enrollments: Mapped[list["Enrollment"]] = relationship(
        "Enrollment", back_populates="member"
    )


class Policy(Base):
    """Authoritative master policy definition."""
    __tablename__ = "policies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    policy_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    plan_name: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    versions: Mapped[list["PolicyVersion"]] = relationship(
        "PolicyVersion", back_populates="policy"
    )


class PolicyVersion(Base):
    """Specific effective-dated version of a policy."""
    __tablename__ = "policy_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policies.id", ondelete="RESTRICT"), nullable=False
    )
    version_tag: Mapped[str] = mapped_column(String(16), nullable=False)
    effective_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    effective_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_pdf_key: Mapped[str] = mapped_column(Text, nullable=False)
    vector_collection_name: Mapped[str] = mapped_column(String(64), nullable=False)
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    published_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    policy: Mapped["Policy"] = relationship("Policy", back_populates="versions")
    enrollments: Mapped[list["Enrollment"]] = relationship(
        "Enrollment", back_populates="policy_version"
    )
    chunks: Mapped[list["PolicyChunk"]] = relationship(
        "PolicyChunk", back_populates="policy_version", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("policy_id", "version_tag", name="uq_policy_version_tag"),
    )


class Enrollment(Base):
    """Member enrollment in an effective policy version."""
    __tablename__ = "enrollments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("members.id", ondelete="RESTRICT"), nullable=False
    )
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    coverage_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    coverage_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ACTIVE", server_default=text("'ACTIVE'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    member: Mapped["Member"] = relationship("Member", back_populates="enrollments")
    policy_version: Mapped["PolicyVersion"] = relationship(
        "PolicyVersion", back_populates="enrollments"
    )


class PolicyChunk(Base):
    """Pre-processed and indexed text chunks from a policy version."""
    __tablename__ = "policy_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("policy_versions.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    section_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    vector_id: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    stable_chunk_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    policy_version: Mapped["PolicyVersion"] = relationship(
        "PolicyVersion", back_populates="chunks"
    )


class Claim(Base):
    """Extracted claim aggregate derived from submitted claim form."""
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    claim_number: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    member_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_npi: Mapped[str] = mapped_column(String(10), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    icd10_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    cpt_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    schema_valid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    validation_errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    case: Mapped["Case"] = relationship("Case", back_populates="claim")
    lines: Mapped[list["ClaimLine"]] = relationship(
        "ClaimLine", back_populates="claim", cascade="all, delete-orphan"
    )


class ClaimLine(Base):
    """Granular service line items on a claim."""
    __tablename__ = "claim_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    cpt_code: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(6, 2), nullable=False, default=Decimal("1.0"), server_default=text("1.0")
    )
    unit_charge: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    total_charge: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    provenance_span_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    claim: Mapped["Claim"] = relationship("Claim", back_populates="lines")

    __table_args__ = (
        UniqueConstraint("claim_id", "line_number", name="uq_claim_lines_line_number"),
    )


class AgentRun(Base):
    """Telemetry and execution trace of an individual specialist agent."""
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[AgentVerdict] = mapped_column(
        SQLEnum(AgentVerdict, name="agent_verdict_enum", native_enum=True),
        nullable=False,
    )
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    output_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    case: Mapped["Case"] = relationship("Case", back_populates="agent_runs")

    __table_args__ = (Index("idx_agent_runs_case", "case_id"),)


class Decision(Base):
    """Adjudication decision history emitted by the Decision Engine or Human Reviewer."""
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[BusinessDecision] = mapped_column(
        SQLEnum(BusinessDecision, name="business_decision_enum", native_enum=True),
        nullable=False,
    )
    escalation_sub_reason: Mapped[EscalationSubReason | None] = mapped_column(
        SQLEnum(EscalationSubReason, name="escalation_sub_reason_enum", native_enum=True),
        nullable=True,
    )
    actor_type: Mapped[ActorType] = mapped_column(
        SQLEnum(ActorType, name="actor_type_enum", native_enum=True),
        nullable=False,
    )
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    rule_id_matched: Mapped[str] = mapped_column(String(64), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    agent_summary_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    case_version_at_decision: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    case: Mapped["Case"] = relationship("Case", back_populates="decisions")

    __table_args__ = (
        Index("idx_decisions_case_active", "case_id", "is_active"),
    )


class ReviewLock(Base):
    """Concurrency lock preventing multiple human reviewers from working the same case."""
    __tablename__ = "review_locks"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    case: Mapped["Case"] = relationship("Case", back_populates="review_lock")


class HumanReview(Base):
    """Audit log of human decisions, overrides, and information requests."""
    __tablename__ = "human_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # OVERRIDE, CONFIRM, REQUEST_INFO
    new_decision: Mapped[BusinessDecision | None] = mapped_column(
        SQLEnum(BusinessDecision, name="business_decision_enum", native_enum=True),
        nullable=True,
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    remarks: Mapped[str] = mapped_column(Text, nullable=False)
    expected_case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    case: Mapped["Case"] = relationship("Case", back_populates="human_reviews")


class AuditEvent(Base):
    """Tamper-evident, cryptographically hash-chained audit log."""
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        SQLEnum(ActorType, name="actor_type_enum", native_enum=True),
        nullable=False,
    )
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    case: Mapped["Case"] = relationship("Case", back_populates="audit_events")

    __table_args__ = (
        Index("idx_audit_case", "case_id", text("created_at ASC")),
    )


class IdempotencyKey(Base):
    """HTTP Idempotency key tracking store."""
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(32), nullable=False)
    response_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MedicalCode(Base):
    """Authoritative clinical code and terminology catalog entry (CPT, HCPCS, ICD-10, Revenue)."""
    __tablename__ = "medical_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("uuid_generate_v4()"),
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    coding_system: Mapped[CodingSystem] = mapped_column(
        SQLEnum(CodingSystem, name="coding_system_enum", native_enum=True),
        nullable=False,
        index=True,
    )
    short_description: Mapped[str] = mapped_column(String(255), nullable=False)
    long_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    standard_charge: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    requires_prior_auth: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint("coding_system", "code", name="uq_medical_codes_system_code"),
        Index("idx_medical_codes_active_system", "is_active", "coding_system"),
    )

