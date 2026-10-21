"""Unit tests for FastAPI REST API & Control Plane (Spec 08, Spec 11, FEAT-012)."""

import io
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from medishield.api.deps import (
    ROLE_ADMIN,
    ROLE_REVIEWER,
    get_db,
    get_storage,
)
from medishield.api.main import app
from medishield.db.audit import GENESIS_HASH, compute_audit_hash
from medishield.db.enums import (
    ActorType,
    BusinessDecision,
    EscalationSubReason,
    WorkflowState,
)
from medishield.db.models import (
    AuditEvent,
    Case,
    Decision,
    Document,
    HumanReview,
    Policy,
    PolicyVersion,
    ReviewLock,
)
from medishield.intake.storage import InMemoryStorageBackend

# ── In-Memory Database Session for API Testing ───────────────────────────────

class FakeQuery:
    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def filter(self, *expressions: Any) -> "FakeQuery":
        # Simplified filter matching for test attributes
        filtered = self._items
        for expr in expressions:
            if hasattr(expr, "left") and hasattr(expr, "right"):
                col_name = expr.left.name
                val = getattr(expr.right, "value", expr.right)
                filtered = [
                    item for item in filtered
                    if getattr(item, col_name, None) == val
                    or (hasattr(getattr(item, col_name, None), "value") and getattr(item, col_name).value == val)
                ]
        return FakeQuery(filtered)

    def order_by(self, *args: Any) -> "FakeQuery":
        return self

    def offset(self, n: int) -> "FakeQuery":
        return FakeQuery(self._items[n:])

    def limit(self, n: int) -> "FakeQuery":
        return FakeQuery(self._items[:n])

    def count(self) -> int:
        return len(self._items)

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any | None:
        return self._items[0] if self._items else None


class FakeDBSession:
    """Deterministic in-memory database session simulating PostgreSQL system of record."""

    def __init__(self) -> None:
        self.cases: list[Case] = []
        self.documents: list[Document] = []
        self.review_locks: list[ReviewLock] = []
        self.human_reviews: list[HumanReview] = []
        self.decisions: list[Decision] = []
        self.policies: list[Policy] = []
        self.policy_versions: list[PolicyVersion] = []
        self.audit_events: list[AuditEvent] = []

    def query(self, model_class: type) -> FakeQuery:
        if model_class is Case:
            return FakeQuery(self.cases)
        if model_class is Document:
            return FakeQuery(self.documents)
        if model_class is ReviewLock:
            return FakeQuery(self.review_locks)
        if model_class is HumanReview:
            return FakeQuery(self.human_reviews)
        if model_class is Decision:
            return FakeQuery(self.decisions)
        if model_class is Policy:
            return FakeQuery(self.policies)
        if model_class is PolicyVersion:
            return FakeQuery(self.policy_versions)
        if model_class is AuditEvent:
            return FakeQuery(self.audit_events)
        return FakeQuery([])

    def add(self, obj: Any) -> None:
        if isinstance(obj, Case):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            if not hasattr(obj, "documents") or obj.documents is None:
                obj.documents = []
            if not hasattr(obj, "human_reviews") or obj.human_reviews is None:
                obj.human_reviews = []
            if not hasattr(obj, "decisions") or obj.decisions is None:
                obj.decisions = []
            if obj not in self.cases:
                self.cases.append(obj)
        elif isinstance(obj, Document):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            if obj not in self.documents:
                self.documents.append(obj)
            # Link to parent case if present
            for c in self.cases:
                if c.id == obj.case_id and obj not in c.documents:
                    c.documents.append(obj)
        elif isinstance(obj, ReviewLock):
            self.review_locks.append(obj)
        elif isinstance(obj, HumanReview):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.human_reviews.append(obj)
            for c in self.cases:
                if c.id == obj.case_id and obj not in c.human_reviews:
                    c.human_reviews.append(obj)
        elif isinstance(obj, Decision):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.decisions.append(obj)
            for c in self.cases:
                if c.id == obj.case_id and obj not in c.decisions:
                    c.decisions.append(obj)
        elif isinstance(obj, Policy):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.policies.append(obj)
        elif isinstance(obj, PolicyVersion):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.policy_versions.append(obj)
        elif isinstance(obj, AuditEvent):
            if not getattr(obj, "id", None):
                obj.id = uuid.uuid4()
            self.audit_events.append(obj)

    def flush(self) -> None:
        pass

    def commit(self) -> None:
        pass

    def refresh(self, obj: Any) -> None:
        pass

    def delete(self, obj: Any) -> None:
        if isinstance(obj, ReviewLock) and obj in self.review_locks:
            self.review_locks.remove(obj)

    def close(self) -> None:
        pass


@pytest.fixture
def fake_db() -> FakeDBSession:
    return FakeDBSession()


@pytest.fixture
def fake_storage() -> InMemoryStorageBackend:
    return InMemoryStorageBackend()


@pytest.fixture(autouse=True)
def mock_celery_tasks(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Mock Celery task.delay calls to avoid connecting to live broker in unit tests."""
    from medishield.worker import tasks
    mock_delay = MagicMock()
    monkeypatch.setattr(tasks.process_case_workflow_task, "delay", mock_delay)
    monkeypatch.setattr(tasks.resume_case_workflow_task, "delay", mock_delay)
    return mock_delay


@pytest.fixture
def client(fake_db: FakeDBSession, fake_storage: InMemoryStorageBackend) -> TestClient:
    """TestClient wired with in-memory DB and storage overrides."""
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_storage] = lambda: fake_storage
    yield TestClient(app)
    app.dependency_overrides.clear()


# ── Tests ────────────────────────────────────────────────────────────────────

def test_health_check_probes(client: TestClient) -> None:
    """Verify liveness and readiness health endpoints."""
    res1 = client.get("/health")
    assert res1.status_code == 200
    assert res1.json()["status"] == "healthy"

    res2 = client.get("/api/v1/health")
    assert res2.status_code == 200
    assert res2.json()["service"] == "medishield-api"


def test_multipart_case_intake_success(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify sub-200ms intake handshake creates case, stores files, and returns 202 Accepted."""
    pdf_content = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    png_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"

    files = [
        ("files", ("claim_form.pdf", io.BytesIO(pdf_content), "application/pdf")),
        ("files", ("receipt.png", io.BytesIO(png_content), "image/png")),
    ]

    response = client.post(
        "/api/v1/cases",
        files=files,
        data={"claim_type": "STANDARD_REIMBURSEMENT_CLAIM"},
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "rev-01"},
    )

    assert response.status_code == 202
    data = response.json()
    assert "CASE-" in data["case_id"]
    assert data["workflow_state"] == "RECEIVED"
    assert data["document_count"] == 2
    assert len(data["documents"]) == 2
    assert len(fake_db.cases) == 1
    assert len(fake_db.documents) == 2


def test_intake_rejects_unauthorized_spoofed_file(client: TestClient) -> None:
    """Verify MIME magic byte validation blocks unauthorized binary payload (Spec 03 § 3.2)."""
    malicious_bytes = b"MZ\x90\x00\x03\x00\x00\x00"  # Windows Executable signature
    files = [
        ("files", ("trojan.exe", io.BytesIO(malicious_bytes), "application/octet-stream")),
    ]

    response = client.post(
        "/api/v1/cases",
        files=files,
        headers={"X-User-Role": ROLE_REVIEWER},
    )

    assert response.status_code in (400, 415)


def test_intake_rejects_empty_file_list(client: TestClient) -> None:
    """Verify rejection when no files are submitted."""
    response = client.post(
        "/api/v1/cases",
        files=[],
        headers={"X-User-Role": ROLE_REVIEWER},
    )
    assert response.status_code == 422  # FastAPI validation error for missing required files field


def test_list_cases_with_pagination(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify case listing and pagination."""
    now = datetime.now(UTC)
    for i in range(5):
        case = Case(
            case_id=f"CASE-20261004-{i:03d}",
            workflow_state=WorkflowState.COMPLETED if i % 2 == 0 else WorkflowState.AWAITING_HUMAN,
            current_decision=BusinessDecision.APPROVE if i % 2 == 0 else BusinessDecision.ESCALATE,
            version=1,
            created_at=now,
            updated_at=now,
        )
        fake_db.add(case)

    res = client.get("/api/v1/cases?page=1&page_size=10", headers={"X-User-Role": ROLE_REVIEWER})
    assert res.status_code == 200
    data = res.json()
    assert data["total_count"] == 5
    assert len(data["items"]) == 5


def test_get_case_detail_found_and_not_found(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify case detail query returns metadata or 404."""
    now = datetime.now(UTC)
    case = Case(
        case_id="CASE-TARGET-01",
        workflow_state=WorkflowState.AWAITING_HUMAN,
        current_decision=BusinessDecision.ESCALATE,
        current_sub_reason=EscalationSubReason.FRAUD_REVIEW,
        decision_confidence=0.88,
        version=2,
        created_at=now,
        updated_at=now,
    )
    fake_db.add(case)

    # 1. Found
    res = client.get("/api/v1/cases/CASE-TARGET-01", headers={"X-User-Role": ROLE_REVIEWER})
    assert res.status_code == 200
    assert res.json()["case_id"] == "CASE-TARGET-01"
    assert res.json()["current_decision"] == "ESCALATE"

    # 2. Not Found
    res404 = client.get("/api/v1/cases/CASE-NONEXISTENT", headers={"X-User-Role": ROLE_REVIEWER})
    assert res404.status_code == 404


def test_human_review_lock_acquire_and_collision(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify 15-minute review locking and 409 conflict on collision (Spec 08 § 3.1)."""
    now = datetime.now(UTC)
    case = Case(
        case_id="CASE-LOCK-01",
        workflow_state=WorkflowState.AWAITING_HUMAN,
        version=1,
        created_at=now,
        updated_at=now,
    )
    fake_db.add(case)

    # 1. Adjuster 1 acquires lock
    res1 = client.post(
        "/api/v1/cases/CASE-LOCK-01/review/lock",
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "adj-alice"},
    )
    assert res1.status_code == 200
    assert res1.json()["reviewer_id"] == "adj-alice"

    # 2. Adjuster 2 attempts concurrent lock -> 409 Conflict
    res2 = client.post(
        "/api/v1/cases/CASE-LOCK-01/review/lock",
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "adj-bob"},
    )
    assert res2.status_code == 409

    # 3. Adjuster 1 releases lock
    res3 = client.delete(
        "/api/v1/cases/CASE-LOCK-01/review/lock",
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "adj-alice"},
    )
    assert res3.status_code == 204

    # 4. Adjuster 2 can now acquire lock
    res4 = client.post(
        "/api/v1/cases/CASE-LOCK-01/review/lock",
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "adj-bob"},
    )
    assert res4.status_code == 200
    assert res4.json()["reviewer_id"] == "adj-bob"


def test_human_review_override_optimistic_concurrency(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify human override transitions case to COMPLETED and enforces optimistic locking (ADR-005)."""
    now = datetime.now(UTC)
    case = Case(
        case_id="CASE-OVERRIDE-01",
        workflow_state=WorkflowState.AWAITING_HUMAN,
        current_decision=BusinessDecision.ESCALATE,
        version=2,  # Current DB version is 2
        created_at=now,
        updated_at=now,
    )
    fake_db.add(case)

    # 1. Conflict test: Adjuster passes expected_version=1 != 2 -> 409 Conflict
    res_conflict = client.post(
        "/api/v1/cases/CASE-OVERRIDE-01/review/override",
        json={
            "new_decision": "APPROVE",
            "reason_code": "CLINICAL_NECESSITY_VERIFIED",
            "remarks": "Attending physician letter confirms inpatient medical necessity.",
            "expected_case_version": 1,
        },
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "adj-alice"},
    )
    assert res_conflict.status_code == 409

    # 2. Success test: Adjuster passes matching version=2
    res_success = client.post(
        "/api/v1/cases/CASE-OVERRIDE-01/review/override",
        json={
            "new_decision": "APPROVE",
            "reason_code": "CLINICAL_NECESSITY_VERIFIED",
            "remarks": "Attending physician letter confirms inpatient medical necessity.",
            "expected_case_version": 2,
        },
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "adj-alice"},
    )
    assert res_success.status_code == 200
    data = res_success.json()
    assert data["action"] == "OVERRIDE"
    assert data["decision"] == "APPROVE"
    assert data["case_version"] == 3
    assert case.workflow_state == WorkflowState.COMPLETED
    assert case.current_decision == BusinessDecision.APPROVE
    assert case.is_complete is True

    # 3. Verify HumanReview recorded in DB
    assert len(fake_db.human_reviews) == 1
    hr = fake_db.human_reviews[0]
    assert hr.action == "OVERRIDE"
    assert hr.reason_code == "CLINICAL_NECESSITY_VERIFIED"
    assert hr.remarks == "Attending physician letter confirms inpatient medical necessity."
    assert hr.reviewer_id == "adj-alice"

    # 4. Verify authoritative Decision record added with ActorType.HUMAN
    assert len(fake_db.decisions) == 1
    dec = fake_db.decisions[0]
    assert dec.actor_type == ActorType.HUMAN
    assert dec.decision == BusinessDecision.APPROVE
    assert dec.rule_id_matched == "HUMAN_OVERRIDE_CLINICAL_NECESSITY_VERIFIED"
    assert dec.is_active is True

    # 5. Verify chained AuditEvent recorded
    assert any(e.event_type == "HUMAN_OVERRIDE" and e.actor_type == ActorType.HUMAN for e in fake_db.audit_events)

    # 6. Verify get_case_detail exposes latest_human_review with feedback
    res_detail = client.get("/api/v1/cases/CASE-OVERRIDE-01", headers={"X-User-Role": ROLE_REVIEWER})
    assert res_detail.status_code == 200
    detail_data = res_detail.json()
    assert detail_data["latest_human_review"] is not None
    assert detail_data["latest_human_review"]["remarks"] == "Attending physician letter confirms inpatient medical necessity."
    assert detail_data["latest_human_review"]["reason_code"] == "CLINICAL_NECESSITY_VERIFIED"
    assert detail_data["latest_human_review"]["new_decision"] == "APPROVE"

    # 7. Verify reviewer can override when case is ALREADY COMPLETED (always available)
    res_override_completed = client.post(
        "/api/v1/cases/CASE-OVERRIDE-01/review/override",
        json={
            "new_decision": "REJECT",
            "reason_code": "SUSPECTED_FRAUD_VERIFIED",
            "remarks": "Post-adjudication investigation confirmed fabricated receipts.",
            "expected_case_version": 3,
        },
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "supervisor-dan"},
    )
    assert res_override_completed.status_code == 200
    assert case.current_decision == BusinessDecision.REJECT
    assert case.version == 4
    assert len(fake_db.human_reviews) == 2
    assert fake_db.human_reviews[-1].remarks == "Post-adjudication investigation confirmed fabricated receipts."


def test_human_review_override_succeeds_when_broker_is_unavailable(
    client: TestClient,
    fake_db: FakeDBSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kombu.exceptions import OperationalError

    from medishield.worker import tasks

    now = datetime.now(UTC)
    case = Case(
        case_id="CASE-OVERRIDE-BROKER-01",
        workflow_state=WorkflowState.AWAITING_HUMAN,
        current_decision=BusinessDecision.ESCALATE,
        version=1,
        created_at=now,
        updated_at=now,
    )
    fake_db.add(case)

    def fail_enqueue(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("broker unavailable")

    monkeypatch.setattr(tasks.resume_case_workflow_task, "delay", fail_enqueue)
    response = client.post(
        "/api/v1/cases/CASE-OVERRIDE-BROKER-01/review/override",
        json={
            "new_decision": "APPROVE",
            "reason_code": "CLINICAL_NECESSITY_VERIFIED",
            "remarks": "Additional clinical evidence reviewed by the assigned reviewer.",
            "expected_case_version": 1,
        },
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "adj-alice"},
    )

    assert response.status_code == 200
    assert case.current_decision == BusinessDecision.APPROVE
    assert case.version == 2
    assert len(fake_db.human_reviews) == 1


def test_override_on_approved_and_rejected_cases(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify that cases already in APPROVE or REJECT state can be authoritatively overridden (ADR-005)."""
    now = datetime.now(UTC)

    # 1. Start with an APPROVED case
    case_approved = Case(
        case_id="CASE-APP-01",
        workflow_state=WorkflowState.COMPLETED,
        current_decision=BusinessDecision.APPROVE,
        version=1,
        created_at=now,
        updated_at=now,
    )
    fake_db.add(case_approved)

    # Override APPROVED -> REJECT
    res_reject = client.post(
        "/api/v1/cases/CASE-APP-01/review/override",
        json={
            "new_decision": "REJECT",
            "reason_code": "SUSPECTED_FRAUD_VERIFIED",
            "remarks": "Reviewer verified fabricated billing receipts post-approval.",
            "expected_case_version": 1,
        },
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "adj-bob"},
    )
    assert res_reject.status_code == 200
    assert case_approved.current_decision == BusinessDecision.REJECT
    assert case_approved.workflow_state == WorkflowState.COMPLETED
    assert case_approved.version == 2

    # 2. Start with a REJECTED case
    case_rejected = Case(
        case_id="CASE-REJ-01",
        workflow_state=WorkflowState.COMPLETED,
        current_decision=BusinessDecision.REJECT,
        version=1,
        created_at=now,
        updated_at=now,
    )
    fake_db.add(case_rejected)

    # Override REJECT -> APPROVE
    res_approve = client.post(
        "/api/v1/cases/CASE-REJ-01/review/override",
        json={
            "new_decision": "APPROVE",
            "reason_code": "CLINICAL_APPEAL_HONORED",
            "remarks": "Medical director honored clinical appeal and authorized full benefit.",
            "expected_case_version": 1,
        },
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "adj-alice"},
    )
    assert res_approve.status_code == 200
    assert case_rejected.current_decision == BusinessDecision.APPROVE
    assert case_rejected.workflow_state == WorkflowState.COMPLETED
    assert case_rejected.version == 2


def test_human_review_request_info(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify request-info transitions case to AWAITING_INFO."""
    now = datetime.now(UTC)
    case = Case(
        case_id="CASE-REQ-INFO-01",
        workflow_state=WorkflowState.AWAITING_HUMAN,
        version=1,
        created_at=now,
        updated_at=now,
    )
    fake_db.add(case)

    res = client.post(
        "/api/v1/cases/CASE-REQ-INFO-01/review/request-info",
        json={
            "reason_code": "MISSING_DISCHARGE_SUMMARY",
            "remarks": "Please upload official hospital discharge summary.",
            "requested_document_types": ["DISCHARGE_SUMMARY"],
            "expected_case_version": 1,
        },
        headers={"X-User-Role": ROLE_REVIEWER},
    )
    assert res.status_code == 200
    assert case.workflow_state == WorkflowState.AWAITING_INFO
    assert case.missing_documents == ["DISCHARGE_SUMMARY"]


def test_rbac_policy_publishing_firewall_adr006(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify ADR-006 policy publishing firewall forbids non-admin roles."""
    pol = Policy(policy_id="POL-CORP-2026", plan_name="Gold PPO", policy_type="HEALTH", is_active=True)
    fake_db.add(pol)

    ver = PolicyVersion(
        policy_id=pol.id,
        version_tag="v1.0",
        effective_start_date=datetime.now(UTC).date(),
        source_pdf_key="policies/pol.pdf",
        vector_collection_name="col_v1",
        is_published=False,
    )
    fake_db.add(ver)

    # 1. Reviewer role denied -> 403 Forbidden
    res_reviewer = client.post(
        "/api/v1/policies/POL-CORP-2026/publish",
        json={"version_tag": "v1.0", "change_justification": "Annual refresh"},
        headers={"X-User-Role": ROLE_REVIEWER},
    )
    assert res_reviewer.status_code == 403

    # 2. Administrator reaches the publishing firewall but cannot publish a
    # legacy record that has no retrievable authoritative PDF.
    res_admin = client.post(
        "/api/v1/policies/POL-CORP-2026/publish",
        json={"version_tag": "v1.0", "change_justification": "Annual corporate benefit update"},
        headers={"X-User-Role": ROLE_ADMIN, "X-User-Id": "admin-super"},
    )
    assert res_admin.status_code == 422
    assert ver.is_published is False


def test_cryptographic_audit_export_verification(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify cryptographic audit export recalculates HMAC-SHA256 hashes on the fly (Spec 08 § 5.2)."""
    now = datetime.now(UTC)
    case = Case(case_id="CASE-AUDIT-01", workflow_state=WorkflowState.COMPLETED, version=1, created_at=now, updated_at=now)
    fake_db.add(case)

    # Build 2 chained audit events
    evt1_id = uuid.uuid4()
    p1 = {"event": "intake_received"}
    h1 = compute_audit_hash(evt1_id, GENESIS_HASH, p1, now)
    evt1 = AuditEvent(
        id=evt1_id,
        case_id=case.id,
        event_type="INTAKE_RECEIVED",
        actor_type=ActorType.SYSTEM,
        actor_id="system",
        event_payload=p1,
        prev_hash=GENESIS_HASH,
        current_hash=h1,
        created_at=now,
    )
    fake_db.add(evt1)

    evt2_id = uuid.uuid4()
    p2 = {"decision": "APPROVE"}
    h2 = compute_audit_hash(evt2_id, h1, p2, now)
    evt2 = AuditEvent(
        id=evt2_id,
        case_id=case.id,
        event_type="DECISION_EMITTED",
        actor_type=ActorType.SYSTEM,
        actor_id="decision_engine",
        event_payload=p2,
        prev_hash=h1,
        current_hash=h2,
        created_at=now,
    )
    fake_db.add(evt2)

    res = client.get("/api/v1/audit/cases/CASE-AUDIT-01", headers={"X-User-Role": ROLE_REVIEWER})
    assert res.status_code == 200
    data = res.json()
    assert data["chain_intact"] is True
    assert data["total_events"] == 2
    assert data["terminal_hash"] == h2


def test_operational_metrics_rbac(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify operational metrics require MANAGER or ADMIN role (data checks: test_operational_metrics.py)."""
    # 1. Reviewer is forbidden
    res_rev = client.get("/api/v1/metrics/operational", headers={"X-User-Role": ROLE_REVIEWER})
    assert res_rev.status_code == 403


def test_rerun_case_not_found(client: TestClient, fake_db: FakeDBSession) -> None:
    """Verify 404 is returned when attempting to rerun a non-existent case."""
    res = client.post("/api/v1/cases/CASE-NONEXISTENT/rerun", headers={"X-User-Role": ROLE_REVIEWER})
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_rerun_case_rbac_unauthenticated(
    client: TestClient,
    fake_db: FakeDBSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify unauthorized or invalid roles to rerun endpoint are rejected."""
    # 1. Invalid role is rejected with 403 Forbidden
    res_bad_role = client.post(
        "/api/v1/cases/CASE-123/rerun",
        headers={"X-User-Role": "INVALID_ROLE"},
    )
    assert res_bad_role.status_code == 403

    # 2. In production mode, unauthenticated request without token is rejected with 401 Unauthorized
    from medishield.core.config import settings
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    res_unauth = client.post("/api/v1/cases/CASE-123/rerun")
    assert res_unauth.status_code == 401


def test_rerun_case_success_synchronous(
    client: TestClient,
    fake_db: FakeDBSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify successful case rerun executes workflow and returns CaseRerunResponse."""
    now = datetime.now(UTC)
    case = Case(
        case_id="CASE-RERUN-TEST",
        workflow_state=WorkflowState.COMPLETED,
        current_decision=BusinessDecision.APPROVE,
        version=2,
        created_at=now,
        updated_at=now,
    )
    fake_db.add(case)

    # Mock Redis offline so it runs in-process
    monkeypatch.setattr("medishield.api.routers.cases.check_redis_available", lambda: False)

    mock_run = MagicMock(return_value={
        "status": "COMPLETED",
        "case_id": "CASE-RERUN-TEST",
        "decision": "APPROVE",
        "completed": True,
    })
    monkeypatch.setattr("medishield.worker.tasks.process_case_workflow_task.run", mock_run)

    res = client.post(
        "/api/v1/cases/CASE-RERUN-TEST/rerun",
        headers={"X-User-Role": ROLE_REVIEWER},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["case_id"] == "CASE-RERUN-TEST"
    assert data["status"] == "COMPLETED"
    assert data["workflow_state"] == "COMPLETED"
    assert "rerun executed successfully" in data["message"].lower()
    mock_run.assert_called_once()
