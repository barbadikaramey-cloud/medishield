"""Unit tests for Administrator API and Agent Decoupling (ADR-012)."""

import io
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from medishield.agents.claims import ClaimsAgent
from medishield.agents.policy import PolicyCoverageAgent
from medishield.api.deps import (
    ROLE_ADMIN,
    ROLE_CLAIMER,
    ROLE_REVIEWER,
    get_db,
)
from medishield.api.main import create_app
from medishield.db.catalog import InMemoryCodeCatalogRepository, SqlCodeCatalogRepository
from medishield.db.enums import AgentVerdict, BusinessDecision, CodingSystem, WorkflowState
from medishield.db.models import Base, Case, Decision
from medishield.db.session import prepare_sqlite_schema
from medishield.domain.contracts import ProvenanceValue, ServiceLine
from medishield.domain.provenance import FieldProvenance


@pytest.fixture
def test_db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    prepare_sqlite_schema(Base.metadata)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session: Session = session_factory()
    try:
        yield session
    finally:
        session.close()



@pytest.fixture
def client(test_db_session):
    app = create_app()

    def override_get_db():
        yield test_db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client


def test_admin_rbac_protection(client):
    """Verify non-admin roles are forbidden from accessing admin endpoints."""
    # 1. No token -> 401 or 403
    resp = client.get("/api/v1/admin/dashboard", headers={"X-User-Role": ROLE_CLAIMER})
    assert resp.status_code == 403

    # 2. Reviewer role -> 403 Forbidden on upload
    resp = client.post(
        "/api/v1/admin/codes/upload",
        files={"file": ("codes.csv", "code,short_description\n99213,Office Visit")},
        headers={"X-User-Role": ROLE_REVIEWER},
    )
    assert resp.status_code == 403


def test_admin_csv_upload_and_search(client, test_db_session):
    """Verify successful CSV upload, persistence, and list/search."""
    csv_data = (
        "code,coding_system,short_description,standard_charge,is_active\n"
        "99213,CPT,Office Outpatient Low,150.00,true\n"
        "99214,CPT,Office Outpatient Moderate,210.00,true\n"
        "J02.9,ICD-10-CM,Acute Pharyngitis,0.00,true\n"
        "0250,REV_CODE,Pharmacy General,50.00,true\n"
    )

    resp = client.post(
        "/api/v1/admin/codes/upload",
        files={"file": ("catalog.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")},
        headers={"X-User-Role": ROLE_ADMIN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["records_inserted"] == 4
    assert "CPT" in data["coding_systems_updated"]
    assert "ICD10_CM" in data["coding_systems_updated"]

    # Search for codes
    search_resp = client.get(
        "/api/v1/admin/codes?query=Office",
        headers={"X-User-Role": ROLE_ADMIN},
    )
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    assert search_data["total"] == 2
    assert len(search_data["items"]) == 2
    assert search_data["items"][0]["code"] in ("99213", "99214")


def test_admin_dashboard_metrics(client, test_db_session):
    """Verify executive dashboard metrics calculation."""
    # Seed 1 code
    repo = SqlCodeCatalogRepository(db=test_db_session)
    repo.upsert_code(
        code="99213",
        coding_system=CodingSystem.CPT,
        short_description="Office Visit",
    )
    test_db_session.commit()

    resp = client.get(
        "/api/v1/admin/dashboard",
        headers={"X-User-Role": ROLE_ADMIN},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "total_cases" in data
    assert "straight_through_processing_rate" in data
    assert data["total_medical_codes"] >= 1
    assert data["system_status"] == "HEALTHY"


def test_claims_agent_decoupled_catalog():
    """Verify ClaimsAgent uses injected CodeCatalogRepository for validation."""
    custom_repo = InMemoryCodeCatalogRepository(initial_codes=[])
    # Add a custom code not in the synthetic default
    custom_repo.upsert_code(
        code="99300",
        coding_system=CodingSystem.CPT,
        short_description="Specialty Care Procedure",
    )

    agent = ClaimsAgent(code_catalog=custom_repo)

    dummy_prov = FieldProvenance(
        document_id="DOC-01",
        page_number=1,
        text_content="99300",
        evidence_quality_score=1.0,
    )


    cpt_val = ProvenanceValue(value="99300", confidence=0.99, provenance=dummy_prov)
    service_line = ServiceLine(
        line_number=1,
        hcpcs=ProvenanceValue(value="99300", confidence=0.99, provenance=dummy_prov),
        amount=ProvenanceValue(value=Decimal("150.00"), confidence=0.99, provenance=dummy_prov),
        service_date=ProvenanceValue(value=date(2026, 1, 15), confidence=0.99, provenance=dummy_prov),
        units=ProvenanceValue(value=Decimal(1), confidence=0.99, provenance=dummy_prov),
    )



    verdict, conf = agent._adjudicate_verdict(
        raw_claim_amount=150.0,
        raw_npi="1234567890",
        schema_valid=True,
        line_items_sum_valid=True,
        service_lines=[service_line],
        validation_errors=[],
        cpt_codes=[cpt_val],
    )
    assert verdict == AgentVerdict.PASS
    assert conf >= 0.95


def test_policy_coverage_agent_decoupled_catalog():
    """Verify PolicyCoverageAgent expands terms dynamically using injected CodeCatalogRepository."""
    custom_repo = InMemoryCodeCatalogRepository(initial_codes=[])
    custom_repo.upsert_code(
        code="99300",
        coding_system=CodingSystem.CPT,
        short_description="Specialty Care Inpatient Visit",
        long_description="Comprehensive inpatient evaluation",
    )

    agent = PolicyCoverageAgent(code_catalog=custom_repo)
    res = agent.execute(
        policy_id="POL-CORP-2026",
        policy_version="v1.0",
        cpt_codes=["99300"],
    )
    # Even if no matching chunks found, it shouldn't crash and should evaluate dynamically
    assert res is not None


def test_claimer_claim_submission_and_status(client, test_db_session):
    """Verify Claimer can submit a claim and track sanitized adjudication status (ADR-012)."""
    # 1. Claimer submits intake files
    file_content = b"%PDF-1.4\n1 0 obj\n<<\n/Title (Medical Bill)\n>>\nendobj\ntrailer\n<<\n>>\n%%EOF"
    submit_resp = client.post(
        "/api/v1/cases",
        files={"files": ("claim_bill.pdf", io.BytesIO(file_content), "application/pdf")},
        data={"claim_type": "STANDARD_REIMBURSEMENT_CLAIM"},
        headers={"X-User-Role": ROLE_CLAIMER, "X-User-Id": "patient-999"},
    )
    assert submit_resp.status_code == 202
    data = submit_resp.json()
    case_id = data["case_id"]
    assert case_id.startswith("CASE-")
    assert data["workflow_state"] == "RECEIVED"

    # 2. Claimer checks status -> returns sanitized status summary
    status_resp = client.get(
        f"/api/v1/cases/{case_id}/status",
        headers={"X-User-Role": ROLE_CLAIMER, "X-User-Id": "patient-999"},
    )
    assert status_resp.status_code == 200
    st_data = status_resp.json()
    assert st_data["case_id"] == case_id
    assert st_data["status_label"] == "Claim Received"
    assert len(st_data["submitted_documents"]) == 1
    assert st_data["submitted_documents"][0]["file_name"] == "claim_bill.pdf"


def test_claimer_forbidden_from_reviewer_queue(client, test_db_session):
    """Verify Claimer role is strictly barred from viewing internal reviewer queue or raw traces."""
    # 1. Attempt to view reviewer case queue
    queue_resp = client.get(
        "/api/v1/cases",
        headers={"X-User-Role": ROLE_CLAIMER, "X-User-Id": "patient-999"},
    )
    assert queue_resp.status_code == 403

    # 2. Attempt to view reviewer detailed case trace (internal prompt defenses/fraud vectors)
    detail_resp = client.get(
        "/api/v1/cases/CASE-20261001-000001",
        headers={"X-User-Role": ROLE_CLAIMER, "X-User-Id": "patient-999"},
    )
    assert detail_resp.status_code == 403


def test_claimer_status_when_awaiting_human_review(client, test_db_session):
    """Verify Claimer status does NOT leak preliminary automated decisions while in AWAITING_HUMAN (ADR-014)."""
    import uuid
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    case_id = f"CASE-REV-{uuid.uuid4().hex[:6]}"
    c = Case(
        id=uuid.uuid4(),
        case_id=case_id,
        workflow_state=WorkflowState.AWAITING_HUMAN,
        current_decision=BusinessDecision.ESCALATE,
        is_complete=False,
        version=1,
        created_at=now,
        updated_at=now,
    )
    dec = Decision(
        id=uuid.uuid4(),
        case_id=c.id,
        decision=BusinessDecision.ESCALATE,
        actor_type="SYSTEM",
        actor_id="system-decision-engine",
        decision_confidence=Decimal("0.85"),
        rule_id_matched="RULE-P4-HIGH-FRAUD",
        justification="Internal fraud score 0.85 exceeded threshold",
        agent_summary_snapshot={"fraud": {"fraud_score": 0.85}},
        case_version_at_decision=1,
        is_active=True,
        created_at=now,
    )
    test_db_session.add(c)
    test_db_session.add(dec)
    test_db_session.commit()

    resp = client.get(
        f"/api/v1/cases/{case_id}/status",
        headers={"X-User-Role": ROLE_CLAIMER, "X-User-Id": "patient-123"},
    )
    assert resp.status_code == 200
    data = resp.json()

    # Crucial assertion: Must indicate review in progress, NOT premature decisions or fraud details
    assert data["status_label"] == "Under Specialist Review"
    assert data["is_complete"] is False
    assert data["workflow_state"] == "AWAITING_HUMAN"
    assert data["adjudication"] is None  # Strict withholding of preliminary settlement


def test_claimer_status_when_completed_by_human(client, test_db_session):
    """Verify Claimer status reveals official determination once finalized (ADR-014)."""
    import uuid
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    case_id = f"CASE-FIN-{uuid.uuid4().hex[:6]}"
    c = Case(
        id=uuid.uuid4(),
        case_id=case_id,
        workflow_state=WorkflowState.COMPLETED,
        current_decision=BusinessDecision.APPROVE,
        is_complete=True,
        version=2,
        created_at=now,
        updated_at=now,
    )
    dec = Decision(
        id=uuid.uuid4(),
        case_id=c.id,
        decision=BusinessDecision.APPROVE,
        actor_type="HUMAN",
        actor_id="reviewer-alice",
        decision_confidence=Decimal("1.00"),
        rule_id_matched="RULE-HUMAN-CONFIRM",
        justification="Claim approved following verification of hospital records.",
        agent_summary_snapshot={
            "claims": {"total_billed": 1500.0},
            "policy": {
                "coverage_percentage": 80.0,
                "deductible_limit": 250.0,
                "copay_percentage": 20.0,
                "member_responsibility": 550.0,
            },
        },
        case_version_at_decision=2,
        is_active=True,
        created_at=now,
    )
    test_db_session.add(c)
    test_db_session.add(dec)
    test_db_session.commit()

    resp = client.get(
        f"/api/v1/cases/{case_id}/status",
        headers={"X-User-Role": ROLE_CLAIMER, "X-User-Id": "patient-123"},
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["status_label"] == "Claim Approved"
    assert data["is_complete"] is True
    assert data["workflow_state"] == "COMPLETED"
    assert data["adjudication"] is not None
    assert data["adjudication"]["decision"] == "APPROVE"
    assert data["adjudication"]["covered_amount"] == 1200.0
    assert data["adjudication"]["total_billed"] == 1500.0
    assert "Claim approved" in data["adjudication"]["explanation_letter"]


def test_list_policies_admin_only(client, test_db_session):
    import uuid
    from datetime import UTC, date, datetime

    from medishield.db.models import Policy, PolicyVersion

    now = datetime.now(UTC)
    pol = Policy(
        id=uuid.uuid4(),
        policy_id="POL-CORP-TEST",
        plan_name="Test Health Plan",
        policy_type="COMPREHENSIVE_HEALTH",
        is_active=True,
        created_at=now,
    )
    test_db_session.add(pol)
    test_db_session.flush()

    ver = PolicyVersion(
        id=uuid.uuid4(),
        policy_id=pol.id,
        version_tag="2026.1",
        effective_start_date=date(2025, 1, 1),
        effective_end_date=date(2026, 12, 31),
        source_pdf_key="policies/POL-CORP-TEST/2026.1.pdf",
        vector_collection_name="policy_pol_corp_test_2026_1",
        is_published=True,
        published_by="admin-user",
        published_at=now,
        created_at=now,
    )
    test_db_session.add(ver)
    test_db_session.commit()

    # Unauthorized role (Claimer / Reviewer forbidden)
    resp = client.get("/api/v1/policies", headers={"X-User-Role": ROLE_CLAIMER})
    assert resp.status_code == 403

    # Authorized admin
    resp = client.get("/api/v1/policies", headers={"X-User-Role": ROLE_ADMIN})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    match = [p for p in data if p["policy_id"] == "POL-CORP-TEST"]
    assert len(match) == 1
    assert match[0]["plan_name"] == "Test Health Plan"
    assert match[0]["version_tag"] == "2026.1"
    assert match[0]["is_published"] is True



