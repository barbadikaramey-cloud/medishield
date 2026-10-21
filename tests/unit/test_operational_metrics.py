"""Operational metrics must be derived from stored data, never from invented defaults."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from medishield.api.deps import ROLE_MANAGER, get_db
from medishield.api.main import create_app
from medishield.api.routers.audit import _percentile
from medishield.db.enums import ActorType, AgentVerdict, BusinessDecision, WorkflowState
from medishield.db.models import AgentRun, Base, Case, Decision
from medishield.db.session import prepare_sqlite_schema

_OLD_FABRICATED = {0.724, 0.682, 0.225, 0.093, 1250.0, 1482}


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    prepare_sqlite_schema(Base.metadata)
    Base.metadata.create_all(engine)
    session: Session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client


def _get(client: TestClient) -> dict:
    res = client.get("/api/v1/metrics/operational", headers={"X-User-Role": ROLE_MANAGER})
    assert res.status_code == 200
    return res.json()


def _add_case(
    db: Session,
    name: str,
    decision: BusinessDecision | None,
    actor: ActorType = ActorType.SYSTEM,
    state: WorkflowState = WorkflowState.COMPLETED,
    latency_ms: int | None = None,
) -> None:
    case = Case(case_id=name, workflow_state=state, current_decision=decision)
    db.add(case)
    db.flush()
    if decision is not None:
        db.add(
            Decision(
                case_id=case.id,
                decision=decision,
                actor_type=actor,
                actor_id="test",
                decision_confidence=Decimal("0.900"),
                rule_id_matched="RULE-TEST",
                justification="test",
                agent_summary_snapshot={},
                case_version_at_decision=1,
                is_active=True,
            )
        )
    if latency_ms is not None:
        now = datetime.now(UTC)
        db.add(
            AgentRun(
                case_id=case.id,
                execution_id=uuid.uuid4(),
                agent_name="kyc_agent",
                verdict=AgentVerdict.PASS,
                confidence=Decimal("0.900"),
                output_json={},
                latency_ms=latency_ms,
                started_at=now - timedelta(milliseconds=latency_ms),
                completed_at=now,
            )
        )
    db.commit()


def test_empty_database_returns_null_not_invented_values(client) -> None:
    data = _get(client)
    assert data["insufficient_data"] is True
    assert data["total_adjudicated_cases"] == 0
    for key in (
        "straight_through_processing_rate",
        "auto_approval_rate",
        "escalation_rate",
        "rejection_rate",
        "average_adjudication_latency_ms",
        "latency_p50_ms",
        "latency_p95_ms",
    ):
        assert data[key] is None, key
    llm = data["llm_runtime"]
    assert llm["scope"] == "process_since_start"
    assert llm["calls_total"] >= 0
    assert llm["provider_attempts_failed"] >= 0
    assert isinstance(llm["provider_failures"], dict)
    assert isinstance(llm["provider_failure_categories"], dict)
    assert isinstance(llm["token_usage_complete"], bool)


def test_rates_are_computed_from_stored_decisions(client, db_session) -> None:
    _add_case(db_session, "C-1", BusinessDecision.APPROVE, latency_ms=100)
    _add_case(db_session, "C-2", BusinessDecision.APPROVE, ActorType.HUMAN, latency_ms=300)
    _add_case(db_session, "C-3", BusinessDecision.ESCALATE, state=WorkflowState.AWAITING_HUMAN, latency_ms=500)
    _add_case(db_session, "C-4", BusinessDecision.REJECT, latency_ms=900)
    _add_case(db_session, "C-5", None, state=WorkflowState.RECEIVED)

    data = _get(client)
    assert data["insufficient_data"] is False
    assert data["total_adjudicated_cases"] == 4
    assert data["auto_approval_rate"] == 0.5
    assert data["escalation_rate"] == 0.25
    assert data["rejection_rate"] == 0.25
    # System-issued APPROVE/REJECT: C-1 and C-4 of 4 adjudicated cases.
    assert data["straight_through_processing_rate"] == 0.5
    assert data["intake_queue_depth"] == 1
    assert data["average_adjudication_latency_ms"] == 450.0
    assert data["latency_p50_ms"] == 300.0
    assert data["latency_p95_ms"] == 900.0
    assert not _OLD_FABRICATED.intersection(
        v for v in data.values() if isinstance(v, (int, float)) and not isinstance(v, bool)
    )


def test_percentile_nearest_rank() -> None:
    assert _percentile([], 95) is None
    assert _percentile([10.0], 50) == 10.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 95) == 4.0
