"""Unit tests for Case Explanation API (ADR-011)."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from medishield.api.deps import get_db
from medishield.api.main import create_app
from medishield.db.enums import BusinessDecision, WorkflowState
from medishield.db.models import Base, Case, Decision
from medishield.db.session import prepare_sqlite_schema


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
    app.dependency_overrides[get_db] = lambda: test_db_session
    with TestClient(app) as tc:
        yield tc


def test_explain_case_not_found(client):
    response = client.post("/api/v1/cases/NON_EXISTENT_CASE/explain")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_explain_case_no_decision(client, test_db_session):
    case_id = "CASE-TEST-NODEC"
    c = Case(
        id=uuid.uuid4(),
        case_id=case_id,
        workflow_state=WorkflowState.SCANNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    test_db_session.add(c)
    test_db_session.commit()

    response = client.post(f"/api/v1/cases/{case_id}/explain")
    assert response.status_code == 400
    assert "adjudication" in response.json()["detail"].lower()


def test_explain_case_success(client, test_db_session):
    case_uuid = uuid.uuid4()
    case_id = "CASE-TEST-EXPLAIN"
    c = Case(
        id=case_uuid,
        case_id=case_id,
        workflow_state=WorkflowState.COMPLETED,
        current_decision=BusinessDecision.APPROVE,
        decision_confidence=0.98,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    d = Decision(
        id=uuid.uuid4(),
        case_id=case_uuid,
        decision=BusinessDecision.APPROVE,
        actor_type="SYSTEM",
        actor_id="system-decision-engine",
        decision_confidence=0.98,
        rule_id_matched="RULE-P6-CLEAN-PASS",
        justification="Clean pass criteria met.",
        agent_summary_snapshot={
            "kyc": {"verdict": "PASS", "confidence": 0.95, "member_matched": True},
            "claims": {"verdict": "PASS", "confidence": 0.88, "claim_amount": 1250.0},
            "policy": {"verdict": "PASS", "confidence": 0.95, "policy_id": "MED-GLD-123"},
            "fraud": {"verdict": "PASS", "fraud_score": 0.05, "risk_level": "LOW"},
        },
        case_version_at_decision=1,
        is_active=True,
        created_at=datetime.now(UTC),
    )
    test_db_session.add(c)
    test_db_session.add(d)
    test_db_session.commit()

    response = client.post(f"/api/v1/cases/{case_id}/explain")
    assert response.status_code == 200
    data = response.json()
    assert data["case_id"] == case_id
    assert data["decision"] == "APPROVE"
    assert data["rule_id_matched"] == "RULE-P6-CLEAN-PASS"
    assert "detailed_explanation" in data
    assert "Executive" in data["detailed_explanation"] and "Summary" in data["detailed_explanation"]
    assert "Member Identity" in data["detailed_explanation"]
    assert "model_source" in data

