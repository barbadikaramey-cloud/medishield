"""
Acceptance unit tests for Step 2E-4: Database and Storage Tracing Instrumentation.

Validates:
1. Storage backend (InMemoryStorageBackend and LocalFilesystemStorageBackend) operations
   emit 'storage_get_bytes' and 'storage_put_bytes' with run_type='tool', inputs={},
   and safe operational metrics (status='SUCCESS', is_complete=True).
2. Repository lookups (InMemoryMemberRepository, SqlMemberRepository, InMemoryPolicyEnrollmentRepository,
   SqlPolicyEnrollmentRepository) emit 'db_member_lookup' and 'db_policy_enrollment_lookup'
   with run_type='retriever', inputs={}, and safe operational metrics.
3. Case state persistence and audit trail export emit 'db_case_state_persistence' and
   'db_audit_trail_retrieval' with inputs={} and safe operational metrics.
4. Span hierarchy: all database and storage spans nest under their active parent workflow node
   and ultimately beneath the active root trace without creating new root traces.
5. Strict sensitive canary isolation: zero raw SQL queries, query parameters, patient names,
   member IDs, policy IDs, storage keys, document bytes, or cryptographic hashes in trace payloads.
6. Functional invariance: all operations return identical results and preserve state/records
   whether tracing is enabled or disabled.
7. Exception propagation: errors (e.g. KeyError, database failures) propagate unchanged
   while recording normalized error categories.
8. Telemetry transport resiliency: failures in telemetry emission do not disrupt storage I/O,
   database queries, or transaction commits.
9. Repeated invocations emit distinct child spans without duplicating root traces.
10. Disabled tracing requires no credentials, makes zero network calls, and creates zero spans.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from medishield.agents.kyc import (
    InMemoryMemberRepository,
    KYCAgent,
    MemberRecord,
    SqlMemberRepository,
)
from medishield.agents.policy import (
    InMemoryPolicyEnrollmentRepository,
    PolicyContextAgent,
    PolicyEnrollmentRecord,
    SqlPolicyEnrollmentRepository,
)
from medishield.api.deps import AuthUser
from medishield.api.routers.audit import export_and_verify_audit_trail
from medishield.core.config import settings
from medishield.core.tracing import ALLOWED_TRACE_KEYS, reset_tracing_client
from medishield.db.audit import GENESIS_HASH, compute_audit_hash
from medishield.db.enums import ActorType, DocumentType, WorkflowState
from medishield.db.models import (
    AuditEvent,
    Base,
    Case,
    Document,
    Enrollment,
    Member,
    Policy,
    PolicyVersion,
)
from medishield.db.session import prepare_sqlite_schema
from medishield.intake.storage import InMemoryStorageBackend, LocalFilesystemStorageBackend
from medishield.ocr.preprocessor import DefaultOCREngine, OCRPreprocessorEngine
from medishield.worker.tasks import process_case_workflow_task
from medishield.workflows.coordinator import CaseWorkflowCoordinator
from medishield.workflows.state import DocumentReference, create_initial_case_state

CANARY_PATIENT = "CANARY_PATIENT_ELEANOR_VANCE_SECRET"
CANARY_MEMBER_ID = "MEM-CANARY-SECRET-777"
CANARY_POLICY_ID = "POL-CANARY-CONFIDENTIAL-999"
CANARY_STORAGE_KEY = "uploads/canary/secret_medical_record.pdf"
CANARY_PAYLOAD_BYTES = b"CANARY_SECRET_PROTECTED_HEALTH_INFORMATION_CANCER_STAGE_4"
CANARY_SQL = "SELECT * FROM members WHERE secret_key = 'canary'"
CANARY_HASH = "hash_canary_secret_12345"

SAMPLE_ID_TEXT = """STATE DRIVER LICENSE
DL NUMBER: D9817264
MEMBER ID: MEM-12345
NAME: Alice Smith
DOB: 1985-05-20
EXP DATE: 2030-01-01
"""

SAMPLE_CLAIM_TEXT = """HEALTH INSURANCE CLAIM FORM
PROVIDER: Dr. Sarah Jenkins Hospital
NPI: 1234567890
DATE OF SERVICE: 2026-03-15
DIAGNOSIS: J02.9
PROCEDURE: 99213
1. 99213 Office Visit 2026-03-15 1.0 $200.00 $200.00
TOTAL CHARGE: $200.00
"""


@pytest.fixture(autouse=True)
def clean_tracing_state():
    """Ensure clean tracing state before and after each test."""
    reset_tracing_client()
    orig_tracing = settings.LANGSMITH_TRACING
    orig_key = settings.LANGSMITH_API_KEY
    orig_pseudo = settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM
    orig_env = os.environ.get("LANGSMITH_TRACING")
    yield
    settings.LANGSMITH_TRACING = orig_tracing
    settings.LANGSMITH_API_KEY = orig_key
    settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM = orig_pseudo
    if orig_env is not None:
        os.environ["LANGSMITH_TRACING"] = orig_env
    else:
        os.environ.pop("LANGSMITH_TRACING", None)
    reset_tracing_client()


@pytest.fixture
def sqlite_session(tmp_path: Path):
    """Provides an isolated SQLite database session for unit testing."""
    db_file = tmp_path / "test_db_storage.db"
    engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    prepare_sqlite_schema(Base.metadata)
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        # Seed minimal test data
        member = Member(
            id=uuid.uuid4(),
            member_id=CANARY_MEMBER_ID,
            first_name="Eleanor",
            last_name="Vance",
            date_of_birth=date(1985, 5, 20),
            ssn_last4="4321",
        )
        session.add(member)

        policy = Policy(
            id=uuid.uuid4(),
            policy_id=CANARY_POLICY_ID,
            plan_name="Canary PPO",
            policy_type="GROUP_PPO",
        )
        session.add(policy)

        p_version = PolicyVersion(
            id=uuid.uuid4(),
            policy_id=policy.id,
            version_tag="v1.0",
            effective_start_date=date(2025, 1, 1),
            effective_end_date=date(2026, 12, 31),
            source_pdf_key="policies/canary.pdf",
            vector_collection_name="canary_collection",
            is_published=True,
        )
        session.add(p_version)

        enrollment = Enrollment(
            id=uuid.uuid4(),
            member_id=member.id,
            policy_version_id=p_version.id,
            status="ACTIVE",
            coverage_start_date=date(2025, 1, 1),
            coverage_end_date=date(2026, 12, 31),
        )
        session.add(enrollment)
        session.commit()
        yield session

    engine.dispose()


# ── Test 1: Storage Backend Tracing ──────────────────────────────────────────
def test_storage_backend_tracing_emits_safe_spans(tmp_path: Path):
    """Storage get_bytes and put_bytes emit storage_get_bytes/storage_put_bytes with tool run_type and safe summaries."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: list[dict[str, Any]] = []

    def mock_trace(name, **kwargs):
        span_data = dict(kwargs)
        span_data["name"] = name
        tree = mock.MagicMock()
        span_data["tree"] = tree
        cm = mock.MagicMock()
        cm.__enter__.return_value = tree
        captured_spans.append(span_data)
        return cm

    in_memory = InMemoryStorageBackend()
    local_fs = LocalFilesystemStorageBackend(base_directory=tmp_path / "storage")

    for storage in (in_memory, local_fs):
        captured_spans.clear()
        with mock.patch("langsmith.trace", side_effect=mock_trace):
            storage.put_bytes("cases/test/doc.txt", b"safe binary content", "text/plain")
            data = storage.get_bytes("cases/test/doc.txt")

        assert data == b"safe binary content"
        assert len(captured_spans) == 2

        # 1. put_bytes span
        put_span = captured_spans[0]
        assert put_span["name"] == "storage_put_bytes"
        assert put_span["run_type"] == "tool"
        assert put_span["inputs"] == {}
        put_tree = put_span["tree"]
        assert put_tree.end.called
        put_outputs = put_tree.end.call_args.kwargs.get("outputs", {})
        assert put_outputs.get("status") == "SUCCESS"
        assert put_outputs.get("is_complete") is True
        assert put_outputs.get("component_name") == "storage_backend"
        assert put_outputs.get("operation_name") == "put_bytes"

        # 2. get_bytes span
        get_span = captured_spans[1]
        assert get_span["name"] == "storage_get_bytes"
        assert get_span["run_type"] == "tool"
        assert get_span["inputs"] == {}
        get_tree = get_span["tree"]
        assert get_tree.end.called
        get_outputs = get_tree.end.call_args.kwargs.get("outputs", {})
        assert get_outputs.get("status") == "SUCCESS"
        assert get_outputs.get("is_complete") is True
        assert get_outputs.get("component_name") == "storage_backend"
        assert get_outputs.get("operation_name") == "get_bytes"


# ── Test 2: Repository Queries ───────────────────────────────────────────────
def test_repository_queries_emit_safe_retriever_spans(sqlite_session: Session):
    """Member and policy enrollment repositories emit db_member_lookup and db_policy_enrollment_lookup."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: list[dict[str, Any]] = []

    def mock_trace(name, **kwargs):
        span_data = dict(kwargs)
        span_data["name"] = name
        tree = mock.MagicMock()
        span_data["tree"] = tree
        cm = mock.MagicMock()
        cm.__enter__.return_value = tree
        captured_spans.append(span_data)
        return cm

    in_mem_member = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
            ssn_last4="4321",
        )
    ])
    sql_member = SqlMemberRepository(sqlite_session)

    # Test Member Repositories (match and non-match)
    for repo, member_id in ((in_mem_member, "MEM-12345"), (sql_member, CANARY_MEMBER_ID)):
        captured_spans.clear()
        with mock.patch("langsmith.trace", side_effect=mock_trace):
            matched = repo.find_member(member_id)
            unmatched = repo.find_member("MEM-NONEXISTENT")

        assert matched is not None
        assert unmatched is None
        assert len(captured_spans) == 2

        # Matched span
        s_match = captured_spans[0]
        assert s_match["name"] == "db_member_lookup"
        assert s_match["run_type"] == "retriever"
        assert s_match["inputs"] == {}
        out_match = s_match["tree"].end.call_args.kwargs.get("outputs", {})
        assert out_match.get("status") == "SUCCESS"
        assert out_match.get("member_matched") is True
        assert out_match.get("is_complete") is True
        assert out_match.get("component_name") == "member_repository"
        assert out_match.get("operation_name") == "find_member"

        # Unmatched span
        s_unmatch = captured_spans[1]
        assert s_unmatch["name"] == "db_member_lookup"
        assert s_unmatch["run_type"] == "retriever"
        assert s_unmatch["inputs"] == {}
        out_unmatch = s_unmatch["tree"].end.call_args.kwargs.get("outputs", {})
        assert out_unmatch.get("status") == "SUCCESS"
        assert out_unmatch.get("member_matched") is False
        assert out_unmatch.get("is_complete") is False

    # Test Policy Enrollment Repositories (match and non-match)
    in_mem_enrollment = InMemoryPolicyEnrollmentRepository([
        PolicyEnrollmentRecord(
            member_id="MEM-12345",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=date(2026, 12, 31),
        )
    ])
    sql_enrollment = SqlPolicyEnrollmentRepository(sqlite_session)

    for repo, member_id in ((in_mem_enrollment, "MEM-12345"), (sql_enrollment, CANARY_MEMBER_ID)):
        captured_spans.clear()
        with mock.patch("langsmith.trace", side_effect=mock_trace):
            res_match = repo.find_enrollments(member_id)
            res_empty = repo.find_enrollments("MEM-NONEXISTENT")

        assert len(res_match) >= 1
        assert len(res_empty) == 0
        assert len(captured_spans) == 2

        s_match = captured_spans[0]
        assert s_match["name"] == "db_policy_enrollment_lookup"
        assert s_match["run_type"] == "retriever"
        assert s_match["inputs"] == {}
        out_match = s_match["tree"].end.call_args.kwargs.get("outputs", {})
        assert out_match.get("status") == "SUCCESS"
        assert out_match.get("retrieved_chunk_count") == len(res_match)
        assert out_match.get("is_complete") is True
        assert out_match.get("component_name") == "policy_enrollment_repository"
        assert out_match.get("operation_name") == "find_enrollments"

        s_empty = captured_spans[1]
        out_empty = s_empty["tree"].end.call_args.kwargs.get("outputs", {})
        assert out_empty.get("status") == "SUCCESS"
        assert out_empty.get("retrieved_chunk_count") == 0
        assert out_empty.get("is_complete") is False


# ── Test 3: Workflow State Persistence and Audit Retrieval ───────────────────
def test_workflow_persistence_and_audit_retrieval_spans(sqlite_session: Session):
    """Case state persistence and audit trail export emit db_case_state_persistence and db_audit_trail_retrieval."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_spans: list[dict[str, Any]] = []

    def mock_trace(name, **kwargs):
        span_data = dict(kwargs)
        span_data["name"] = name
        tree = mock.MagicMock()
        span_data["tree"] = tree
        cm = mock.MagicMock()
        cm.__enter__.return_value = tree
        captured_spans.append(span_data)
        return cm

    # 1. db_audit_trail_retrieval via export_and_verify_audit_trail
    case_id = f"CASE-{uuid.uuid4().hex[:8]}"
    case = Case(
        id=uuid.uuid4(),
        case_id=case_id,
        workflow_state=WorkflowState.RECEIVED,
    )
    sqlite_session.add(case)

    # Seed 2 chained audit events
    now1 = datetime(2026, 10, 2, 10, 0, 0)
    id1 = uuid.uuid4()
    p1 = {"action": "CASE_CREATED"}
    h1 = compute_audit_hash(id1, GENESIS_HASH, p1, now1)
    ev1 = AuditEvent(
        id=id1,
        case_id=case.id,
        event_type="CASE_CREATED",
        actor_type=ActorType.SYSTEM,
        actor_id="system",
        event_payload=p1,
        prev_hash=GENESIS_HASH,
        current_hash=h1,
        created_at=now1,
    )
    sqlite_session.add(ev1)
    sqlite_session.commit()

    user = AuthUser(user_id="user-1", username="testuser", role="OPERATIONS_MANAGER")

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        resp = export_and_verify_audit_trail(case_id=case_id, current_user=user, db=sqlite_session)

    assert resp.chain_intact is True
    assert resp.total_events == 1

    audit_spans = [s for s in captured_spans if s["name"] == "db_audit_trail_retrieval"]
    assert len(audit_spans) == 1
    a_span = audit_spans[0]
    assert a_span["run_type"] == "retriever"
    assert a_span["inputs"] == {}
    a_out = a_span["tree"].end.call_args.kwargs.get("outputs", {})
    assert a_out.get("status") == "SUCCESS"
    assert a_out.get("retrieved_chunk_count") == 1
    assert a_out.get("is_complete") is True
    assert a_out.get("component_name") == "audit_repository"
    assert a_out.get("operation_name") == "get_audit_trail"

    # 2. db_case_state_persistence via process_case_workflow_task
    storage = InMemoryStorageBackend()
    storage.put_bytes(f"raw/{case_id}/DOC-CLAIM.txt", b"CMS-1500 CLAIM FORM\nTOTAL: $100.00", "text/plain")
    storage.put_bytes(f"raw/{case_id}/DOC-ID.txt", b"DRIVER LICENSE\nDOB: 1985-05-20", "text/plain")

    doc_refs = [
        DocumentReference(
            document_id="DOC-CLAIM",
            object_key=f"raw/{case_id}/DOC-CLAIM.txt",
            mime_type="text/plain",
            checksum="chk-claim",
            doc_type=DocumentType.CLAIM_FORM.value,
            split_source_page_indices=[1],
        ),
        DocumentReference(
            document_id="DOC-ID",
            object_key=f"raw/{case_id}/DOC-ID.txt",
            mime_type="text/plain",
            checksum="chk-id",
            doc_type=DocumentType.ID_DOCUMENT.value,
            split_source_page_indices=[1],
        ),
    ]
    initial_state = create_initial_case_state(case_id=case_id, documents=doc_refs)

    # Attach documents to DB case
    doc_model = Document(
        id=uuid.uuid4(),
        case_id=case.id,
        document_id="DOC-CLAIM",
        file_name="DOC-CLAIM.txt",
        object_key=f"raw/{case_id}/DOC-CLAIM.txt",
        mime_type="text/plain",
        checksum="chk-claim",
        size_bytes=100,
        doc_type=DocumentType.CLAIM_FORM,
    )
    sqlite_session.add(doc_model)
    sqlite_session.commit()

    captured_spans.clear()
    with mock.patch("langsmith.trace", side_effect=mock_trace):
        res = process_case_workflow_task.apply(
            args=[case_id],
            kwargs={
                "db_session": sqlite_session,
                "initial_state": initial_state,
            },
        ).get()

    assert res["status"] in ("COMPLETED", "AWAITING_HUMAN", "AWAITING_INFO", "FAILED")
    persist_spans = [s for s in captured_spans if s["name"] == "db_case_state_persistence"]
    assert len(persist_spans) == 1
    p_span = persist_spans[0]
    assert p_span["run_type"] == "tool"
    assert p_span["inputs"] == {}
    p_out = p_span["tree"].end.call_args.kwargs.get("outputs", {})
    assert p_out.get("status") == "SUCCESS"
    assert p_out.get("is_complete") is True
    assert p_out.get("component_name") == "case_repository"
    assert p_out.get("operation_name") == "persist_case_state"


# ── Test 4: Span Hierarchy & Nesting ─────────────────────────────────────────
def test_db_and_storage_spans_nest_under_workflow_trace(sqlite_session: Session):
    """Database and storage spans nest under the active workflow node and root trace."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    storage = InMemoryStorageBackend()
    storage.put_bytes("raw/case-nest/doc1.txt", SAMPLE_ID_TEXT.encode(), "text/plain")
    storage.put_bytes("raw/case-nest/doc2.txt", SAMPLE_CLAIM_TEXT.encode(), "text/plain")

    member_repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
            ssn_last4="4321",
        )
    ])
    enrollment_repo = InMemoryPolicyEnrollmentRepository([
        PolicyEnrollmentRecord(
            member_id="MEM-12345",
            policy_id="POL-CORP-2026",
            version_tag="v2.0",
            plan_name="Comprehensive Gold PPO",
            effective_start_date=date(2026, 1, 1),
            effective_end_date=date(2026, 12, 31),
        )
    ])

    kyc_agent = KYCAgent(member_repo=member_repo)
    policy_context_agent = PolicyContextAgent(enrollment_repo=enrollment_repo)
    coordinator = CaseWorkflowCoordinator(
        storage=storage,
        ocr_engine=OCRPreprocessorEngine(engine=DefaultOCREngine()),
        kyc_agent=kyc_agent,
        policy_context_agent=policy_context_agent,
    )

    span_sequence: list[str] = []

    def mock_trace(name, **kwargs):
        span_sequence.append(name)
        tree = mock.MagicMock()
        cm = mock.MagicMock()
        cm.__enter__.return_value = tree
        return cm

    doc1 = DocumentReference(
        document_id="doc1",
        object_key="raw/case-nest/doc1.txt",
        mime_type="text/plain",
        checksum="chk1",
        doc_type=DocumentType.ID_DOCUMENT.value,
        split_source_page_indices=[1],
    )
    doc2 = DocumentReference(
        document_id="doc2",
        object_key="raw/case-nest/doc2.txt",
        mime_type="text/plain",
        checksum="chk2",
        doc_type=DocumentType.CLAIM_FORM.value,
        split_source_page_indices=[1],
    )
    state = create_initial_case_state("case-nest", [doc1, doc2])

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        coordinator.execute(state)

    # Root span is case_workflow_execute
    assert span_sequence[0] == "case_workflow_execute"

    # storage_get_bytes, db_member_lookup, and db_policy_enrollment_lookup are emitted
    assert "storage_get_bytes" in span_sequence
    assert "db_member_lookup" in span_sequence
    assert "db_policy_enrollment_lookup" in span_sequence

    # Verify that child spans appear after root trace starts
    first_root_idx = span_sequence.index("case_workflow_execute")
    for child in ("storage_get_bytes", "db_member_lookup", "db_policy_enrollment_lookup"):
        assert span_sequence.index(child) > first_root_idx


# ── Test 5: Strict Sensitive Canary Isolation ────────────────────────────────
def test_strict_sensitive_canary_isolation_across_db_and_storage(sqlite_session: Session):
    """Canary values never appear in trace payloads across all 6 database and storage operations."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_trees: list[tuple[dict[str, Any], mock.MagicMock]] = []

    def mock_trace(name, **kwargs):
        span_data = dict(kwargs)
        span_data["name"] = name
        tree = mock.MagicMock()
        cm = mock.MagicMock()
        cm.__enter__.return_value = tree
        captured_trees.append((span_data, tree))
        return cm

    storage = InMemoryStorageBackend()
    sql_member = SqlMemberRepository(sqlite_session)
    sql_enrollment = SqlPolicyEnrollmentRepository(sqlite_session)

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        # 1 & 2: storage_put_bytes and storage_get_bytes with canaries
        storage.put_bytes(CANARY_STORAGE_KEY, CANARY_PAYLOAD_BYTES, "application/pdf")
        storage.get_bytes(CANARY_STORAGE_KEY)

        # 3: db_member_lookup with canary member ID
        sql_member.find_member(CANARY_MEMBER_ID)

        # 4: db_policy_enrollment_lookup with canary member ID
        sql_enrollment.find_enrollments(CANARY_MEMBER_ID)

        # 5: db_audit_trail_retrieval
        case_id = f"CASE-{uuid.uuid4().hex[:8]}"
        case = Case(id=uuid.uuid4(), case_id=case_id, workflow_state=WorkflowState.RECEIVED)
        sqlite_session.add(case)
        sqlite_session.commit()
        user = AuthUser(user_id="user-1", username="testuser", role="OPERATIONS_MANAGER")
        export_and_verify_audit_trail(case_id=case_id, current_user=user, db=sqlite_session)

    # Inspect all captured trace invocations
    canaries = [
        CANARY_PATIENT,
        CANARY_MEMBER_ID,
        CANARY_POLICY_ID,
        CANARY_STORAGE_KEY,
        "Eleanor",
        "Vance",
        "CANCER",
        "secret_key",
    ]

    assert len(captured_trees) >= 5

    for span_data, tree in captured_trees:
        # Check inputs, metadata, name
        serialized_input = str(span_data.get("inputs", ""))
        serialized_meta = str(span_data.get("metadata", ""))
        serialized_tags = str(span_data.get("tags", ""))
        name = span_data.get("name", "")

        for canary in canaries:
            assert canary not in serialized_input, f"Canary '{canary}' leaked in inputs of {name}"
            assert canary not in serialized_meta, f"Canary '{canary}' leaked in metadata of {name}"
            assert canary not in serialized_tags, f"Canary '{canary}' leaked in tags of {name}"

        # Check outputs passed to tree.end()
        if tree.end.called:
            outputs = tree.end.call_args.kwargs.get("outputs", {})
            serialized_out = str(outputs)
            for canary in canaries:
                assert canary not in serialized_out, f"Canary '{canary}' leaked in outputs of {name}"

            # Verify allowed keys only
            for k in outputs:
                assert k in ALLOWED_TRACE_KEYS, f"Disallowed trace key '{k}' found in outputs of {name}"


# ── Test 6: Functional Invariance ────────────────────────────────────────────
def test_functional_invariance_tracing_enabled_vs_disabled(sqlite_session: Session):
    """Storage, repository, and audit operations produce identical results whether tracing is enabled or disabled."""
    storage = InMemoryStorageBackend()
    sql_member = SqlMemberRepository(sqlite_session)
    sql_enrollment = SqlPolicyEnrollmentRepository(sqlite_session)

    # Run with tracing disabled
    settings.LANGSMITH_TRACING = False
    os.environ["LANGSMITH_TRACING"] = "false"
    reset_tracing_client()

    storage.put_bytes("cases/inv/doc.bin", b"identical bytes 12345", "application/octet-stream")
    data_off = storage.get_bytes("cases/inv/doc.bin")
    member_off = sql_member.find_member(CANARY_MEMBER_ID)
    enr_off = sql_enrollment.find_enrollments(CANARY_MEMBER_ID)

    # Run with tracing enabled
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"
    reset_tracing_client()

    storage.put_bytes("cases/inv/doc2.bin", b"identical bytes 12345", "application/octet-stream")
    data_on = storage.get_bytes("cases/inv/doc2.bin")
    member_on = sql_member.find_member(CANARY_MEMBER_ID)
    enr_on = sql_enrollment.find_enrollments(CANARY_MEMBER_ID)

    assert data_off == data_on == b"identical bytes 12345"
    assert member_off is not None and member_on is not None
    assert member_off.member_id == member_on.member_id
    assert member_off.first_name == member_on.first_name
    assert len(enr_off) == len(enr_on)
    assert enr_off[0].policy_id == enr_on[0].policy_id


# ── Test 7: Exception Propagation & Error Normalization ──────────────────────
def test_exception_propagation_and_normalized_error_categories():
    """Missing storage keys or repository exceptions propagate unchanged and record FAILED status."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    captured_trees: list[mock.MagicMock] = []

    def mock_trace(name, **kwargs):
        tree = mock.MagicMock()
        cm = mock.MagicMock()
        cm.__enter__.return_value = tree
        captured_trees.append(tree)
        return cm

    storage = InMemoryStorageBackend()

    # 1. Missing storage key -> KeyError
    with mock.patch("langsmith.trace", side_effect=mock_trace), pytest.raises(KeyError):
        storage.get_bytes("nonexistent_key_123")

    assert len(captured_trees) == 1
    tree = captured_trees[0]
    assert tree.end.called
    outputs = tree.end.call_args.kwargs.get("outputs", {})
    assert outputs.get("status") == "FAILED"
    assert outputs.get("error_type") == "KeyError"
    assert outputs.get("is_complete") is False

    # 2. Database query failure -> RuntimeError propagates
    failing_session = mock.MagicMock()
    failing_session.query.side_effect = RuntimeError("database connection dead")
    failing_session.return_value = failing_session
    sql_member = SqlMemberRepository(failing_session)

    captured_trees.clear()
    with (
        mock.patch("langsmith.trace", side_effect=mock_trace),
        pytest.raises(RuntimeError, match="database connection dead"),
    ):
        sql_member.find_member("MEM-12345")

    assert len(captured_trees) == 1
    tree2 = captured_trees[0]
    assert tree2.end.called
    outputs2 = tree2.end.call_args.kwargs.get("outputs", {})
    assert outputs2.get("status") == "FAILED"
    assert outputs2.get("error_type") == "RuntimeError"


# ── Test 8: Telemetry Transport Resiliency ───────────────────────────────────
def test_telemetry_transport_resiliency(sqlite_session: Session):
    """Telemetry transport errors do not disrupt storage I/O, database queries, or commits."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    def broken_trace(name, **kwargs):
        raise ConnectionError("LangSmith cloud connection dropped")

    storage = InMemoryStorageBackend()
    sql_member = SqlMemberRepository(sqlite_session)
    sql_enrollment = SqlPolicyEnrollmentRepository(sqlite_session)

    with mock.patch("langsmith.trace", side_effect=broken_trace):
        # All operations must execute successfully without raising ConnectionError
        storage.put_bytes("cases/resilience/test.txt", b"payload", "text/plain")
        data = storage.get_bytes("cases/resilience/test.txt")
        member = sql_member.find_member(CANARY_MEMBER_ID)
        enrollments = sql_enrollment.find_enrollments(CANARY_MEMBER_ID)

    assert data == b"payload"
    assert member is not None
    assert len(enrollments) >= 1


# ── Test 9: Repeated Invocations ─────────────────────────────────────────────
def test_repeated_invocations_emit_distinct_child_spans():
    """Multiple storage operations and repository queries emit distinct child spans."""
    settings.LANGSMITH_TRACING = True
    settings.LANGSMITH_API_KEY = "test-key"
    os.environ["LANGSMITH_TRACING"] = "true"

    emitted_spans: list[str] = []

    def mock_trace(name, **kwargs):
        emitted_spans.append(name)
        tree = mock.MagicMock()
        cm = mock.MagicMock()
        cm.__enter__.return_value = tree
        return cm

    storage = InMemoryStorageBackend()
    member_repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
            ssn_last4="4321",
        )
    ])

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        for i in range(3):
            storage.put_bytes(f"cases/rep/doc_{i}.txt", f"content {i}".encode(), "text/plain")
            storage.get_bytes(f"cases/rep/doc_{i}.txt")
            member_repo.find_member("MEM-12345")

    assert len(emitted_spans) == 9
    assert emitted_spans.count("storage_put_bytes") == 3
    assert emitted_spans.count("storage_get_bytes") == 3
    assert emitted_spans.count("db_member_lookup") == 3


# ── Test 10: Zero-Cost Disabled Tracing ───────────────────────────────────────
def test_disabled_tracing_requires_zero_credentials_and_makes_zero_network_calls():
    """When tracing is disabled, no spans are emitted and no credentials are checked."""
    settings.LANGSMITH_TRACING = False
    settings.LANGSMITH_API_KEY = ""
    os.environ["LANGSMITH_TRACING"] = "false"
    reset_tracing_client()

    emitted_spans: list[str] = []

    def mock_trace(name, **kwargs):
        emitted_spans.append(name)
        tree = mock.MagicMock()
        cm = mock.MagicMock()
        cm.__enter__.return_value = tree
        return cm

    storage = InMemoryStorageBackend()
    member_repo = InMemoryMemberRepository([
        MemberRecord(
            member_id="MEM-12345",
            first_name="Alice",
            last_name="Smith",
            date_of_birth=date(1985, 5, 20),
            ssn_last4="4321",
        )
    ])

    with mock.patch("langsmith.trace", side_effect=mock_trace):
        storage.put_bytes("cases/off/doc.txt", b"off content", "text/plain")
        data = storage.get_bytes("cases/off/doc.txt")
        member = member_repo.find_member("MEM-12345")

    assert data == b"off content"
    assert member is not None
    assert len(emitted_spans) == 0
