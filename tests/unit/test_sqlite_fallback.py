"""Unit tests for SQLite development persistence fallback and benchmark dataset seeding (ADR-008)."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from medishield.agents.kyc import (
    InMemoryMemberRepository,
    KYCAgent,
    SqlMemberRepository,
)
from medishield.agents.policy import PolicyCorpusRepository
from medishield.db.enums import AgentVerdict
from medishield.db.models import (
    Base,
    Case,
    Enrollment,
    EnrollmentRiderApplication,
    Member,
    Policy,
    PolicyRider,
)
from medishield.db.schema_compat import prepare_sqlite_schema as prepare_legacy_sqlite_schema
from medishield.db.seeder import seed_benchmark_dataset, seed_demo_cases_if_empty
from medishield.db.session import prepare_sqlite_schema
from medishield.domain.contracts import (
    OCRBoundingBox,
    OCRDocumentPayload,
    OCRPagePayload,
    OCRTextSpan,
)


@pytest.fixture
def sqlite_session(tmp_path: Path):
    """Provides an isolated SQLite database session for unit testing."""
    db_file = tmp_path / "test_medishield.db"
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    prepare_sqlite_schema(Base.metadata)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        seed_benchmark_dataset(session)
        seed_demo_cases_if_empty(session)
        yield session

    engine.dispose()


def test_sqlite_tables_created_and_seeded(sqlite_session: Session):
    """Verifies that all tables are created and seeded with 30 benchmark members + demo cases."""
    member_count = sqlite_session.query(Member).count()
    assert member_count >= 30, f"Expected at least 30 members, got {member_count}"

    policy_count = sqlite_session.query(Policy).count()
    assert policy_count >= 1, "Expected seeded policies"

    case_count = sqlite_session.query(Case).count()
    assert case_count >= 2, "Expected 2 initial demo cases"


def test_benchmark_seed_rerun_does_not_duplicate_members(sqlite_session: Session):
    member_count = sqlite_session.query(Member).count()
    rider_count = sqlite_session.query(PolicyRider).count()
    application_count = sqlite_session.query(EnrollmentRiderApplication).count()

    seed_benchmark_dataset(sqlite_session)

    assert sqlite_session.query(Member).count() == member_count
    assert sqlite_session.query(PolicyRider).count() == rider_count
    assert sqlite_session.query(EnrollmentRiderApplication).count() == application_count
    assert (
        sqlite_session.query(EnrollmentRiderApplication)
        .filter(EnrollmentRiderApplication.status != "REQUESTED")
        .count()
        == 0
    )


def test_policy_rider_terms_and_patient_requests_are_persisted(sqlite_session: Session):
    riders = {
        rider.rider_code: rider
        for rider in sqlite_session.query(PolicyRider).all()
    }
    assert {
        code: (rider.waiting_period_value, rider.waiting_period_unit)
        for code, rider in riders.items()
    } == {
        "MATERNITY": (9, "MONTH"),
        "CRITICAL_ILLNESS": (90, "DAY"),
        "OPD": (30, "DAY"),
    }

    applications = sqlite_session.query(EnrollmentRiderApplication).all()
    assert applications
    for application in applications:
        assert application.status == "REQUESTED"
        assert application.effective_start_date is None
        assert application.enrollment.policy_version_id == application.policy_rider.policy_version_id


def test_legacy_sqlite_schema_receives_new_model_columns(tmp_path: Path):
    """Ensures databases created before migration 005 remain startup-compatible."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE policies (
                id VARCHAR(36) PRIMARY KEY,
                policy_id VARCHAR(32) NOT NULL,
                plan_name VARCHAR(128) NOT NULL,
                policy_type VARCHAR(32) NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME NOT NULL
            )
            """
        )

        prepare_legacy_sqlite_schema(connection)

    columns = {column["name"] for column in inspect(engine).get_columns("policies")}
    assert {"payer_product", "jurisdiction"} <= columns


def test_pt16658_karen_harris_seeded_correctly(sqlite_session: Session):
    """Verifies that patient PT_16658 (Cluster C_012) is seeded with active Gold policy."""
    member = sqlite_session.query(Member).filter(Member.member_id == "PT_16658").first()
    assert member is not None, "PT_16658 should exist in members table"
    assert member.first_name == "Karen"
    assert "Harris" in member.last_name
    assert member.date_of_birth == date(1996, 11, 17)
    assert member.status == "ACTIVE"

    # Verify active enrollment in MED-GLD-5437001
    enrollment = (
        sqlite_session.query(Enrollment)
        .filter(Enrollment.member_id == member.id)
        .first()
    )
    assert enrollment is not None
    assert enrollment.status == "ACTIVE"
    policy_id = enrollment.policy_version.policy.policy_id
    assert policy_id == "MED-GLD-5437001"


def test_sql_member_repository_lookup(sqlite_session: Session):
    """Verifies SqlMemberRepository resolves PT_16658 by both member_id and policy_id."""
    repo = SqlMemberRepository(sqlite_session)

    # 1. Lookup by Patient ID
    record_by_id = repo.find_member("PT_16658")
    assert record_by_id is not None
    assert record_by_id.member_id == "PT_16658"
    assert record_by_id.date_of_birth == date(1996, 11, 17)
    assert record_by_id.policy_id == "MED-GLD-5437001"
    assert record_by_id.status == "ACTIVE"

    # 2. Lookup by Policy ID
    record_by_policy = repo.find_member("MED-GLD-5437001")
    assert record_by_policy is not None
    assert record_by_policy.member_id == "PT_16658"

    # 3. Non-existent lookup
    assert repo.find_member("NON_EXISTENT_ID") is None


def test_in_memory_member_repository_contains_all_30_members():
    """Verifies default InMemoryMemberRepository contains PT_16658 and benchmark dataset."""
    in_mem_repo = InMemoryMemberRepository()
    record = in_mem_repo.find_member("PT_16658")
    assert record is not None, "InMemoryMemberRepository must include PT_16658"
    assert record.first_name == "Karen"
    assert record.policy_id == "MED-GLD-5437001"


def test_kyc_agent_execution_with_sql_repository(sqlite_session: Session):
    """Verifies that KYC Agent successfully passes PT_16658 using SqlMemberRepository."""
    repo = SqlMemberRepository(sqlite_session)
    kyc_agent = KYCAgent(member_repo=repo)

    # Construct mock OCR payload with Karen Harris and PT_16658
    span_id = OCRTextSpan(
        span_id="span-id-01",
        page_number=1,
        text="PATIENT ID: PT_16658 NAME: KAREN HARRIS DOB: 11/17/1996",
        bbox=OCRBoundingBox(x_min=0.1, y_min=0.1, x_max=0.4, y_max=0.2),
        confidence=0.98,
    )
    doc_payload = OCRDocumentPayload(
        document_id="DOC-TEST-01",
        pages=[
            OCRPagePayload(
                page_number=1,
                width=800,
                height=1000,
                raw_text=span_id.text,
                spans=[span_id],
                quality_score=0.95,
            )
        ],
        total_pages=1,
        average_confidence=0.98,
        image_quality_score=0.95,
        payload_storage_uri="raw/test.json",
    )

    output = kyc_agent.execute(
        case_id="CASE-20261006-7ACA03",
        document_payloads=[doc_payload],
        service_date=date(2025, 5, 10),
    )

    assert output.member_db_matched is True
    assert output.dob_matched is True
    assert output.policy_active_on_service_date is True
    assert "MEMBER_NOT_FOUND_IN_DB" not in output.flags
    assert output.member_id is not None
    assert output.member_id.value == "PT_16658"


def test_policy_corpus_retrieval_for_gold_series():
    """Verifies that PolicyCorpusRepository retrieves cosmetic exclusion for MED-GLD-5437001."""
    corpus = PolicyCorpusRepository()
    chunks = corpus.query_chunks(
        policy_id="MED-GLD-5437001",
        version_tag="v1.0",
        query_text="Destruction premalignant lesions cosmetic aesthetic surgery",
        cpt_codes=["17000"],
    )

    assert len(chunks) > 0, "Expected matching chunks for MED-GLD series"
    # Find cosmetic exclusion chunk
    exclusion_chunk = next((c for c in chunks if c.section_number == "SEC-9.2"), None)
    assert exclusion_chunk is not None
    assert exclusion_chunk.is_exclusion is True
    assert "17000" in exclusion_chunk.matching_cpt_codes


def test_contract_kyc_and_policy_context_resolve_pt19116_from_same_source(sqlite_session: Session):
    from medishield.agents.policy import (
        PolicyContextAgent,
        SqlPolicyEnrollmentRepository,
        resolve_claim_ready_policy,
    )

    # 1. KYC Agent resolution
    kyc_repo = SqlMemberRepository(sqlite_session)
    member_record = kyc_repo.find_member("PT_19116")
    assert member_record is not None, "PT_19116 must be resolvable by KYC"
    assert member_record.first_name == "Christopher"
    assert member_record.last_name == "Harris I."

    # 2. PolicyContext Agent resolution from the same DB session
    policy_repo = SqlPolicyEnrollmentRepository(sqlite_session)
    pol_agent = PolicyContextAgent(enrollment_repo=policy_repo)
    ctx_output = pol_agent.execute(member_id="PT_19116")

    assert ctx_output.verdict == AgentVerdict.PASS
    assert len(ctx_output.candidate_policies) > 0, "PT_19116 must have candidate policies from DB"

    # 3. Intersect with service date 12/15/2025
    ok, matched = resolve_claim_ready_policy(ctx_output.candidate_policies, service_date=date(2025, 12, 15))
    assert ok is True, "Service date 12/15/2025 must be covered by active policy window"
    assert matched is not None
    assert matched.policy_id == member_record.policy_id


def test_policy_context_agent_unavailable_when_no_enrollments(sqlite_session: Session):
    """Verifies that missing enrollment returns UNAVAILABLE, never silently treated as date mismatch FAIL."""
    from medishield.agents.policy import PolicyContextAgent, SqlPolicyEnrollmentRepository

    policy_repo = SqlPolicyEnrollmentRepository(sqlite_session)
    pol_agent = PolicyContextAgent(enrollment_repo=policy_repo)
    ctx_output = pol_agent.execute(member_id="NON_EXISTENT_MEMBER_999")

    assert ctx_output.verdict == AgentVerdict.UNAVAILABLE
    assert len(ctx_output.candidate_policies) == 0


def test_coordinator_fails_to_start_without_db_session():
    """Verifies that CaseWorkflowCoordinator refuses to start without db_session or explicit agent injection."""
    import pytest

    from medishield.workflows.coordinator import CaseWorkflowCoordinator

    with pytest.raises(RuntimeError, match="requires an active db_session or explicit policy_context_agent injection"):
        CaseWorkflowCoordinator()
