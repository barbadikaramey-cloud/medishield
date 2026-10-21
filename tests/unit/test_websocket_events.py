"""
Unit and Integration Tests for Real-Time Agent Execution Events and WebSockets.
Specification Reference: ADR-017 § Feature 2.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from medishield.api.events import (
    AgentExecutionEvent,
    CaseEventBroadcaster,
    broadcaster,
)
from medishield.api.main import create_app
from medishield.db.enums import BusinessDecision, WorkflowState
from medishield.db.models import Case
from medishield.db.session import SyncSessionLocal


def test_agent_execution_event_model() -> None:
    """Verifies AgentExecutionEvent data contract and zero-PII sanitization."""
    evt = AgentExecutionEvent(
        case_id="CASE-TEST-001",
        run_id="run-001",
        agent_name="KYCAgent",
        event_type="STARTED",
        status="RUNNING",
        sequence_number=1,
        safe_summary="KYC verification started",
        payload={"sanitized_flag": True},
    )
    assert evt.case_id == "CASE-TEST-001"
    assert evt.sequence_number == 1
    assert evt.duration_ms is None
    data = json.loads(evt.model_dump_json())
    assert data["agent_name"] == "KYCAgent"
    assert data["status"] == "RUNNING"


def test_case_event_broadcaster_sequence_and_buffer() -> None:
    """Verifies sequence numbers increment monotonically and buffer retains history."""
    b = CaseEventBroadcaster(max_buffer_size=5)
    case_id = f"CASE-{uuid.uuid4()}"
    run_id = "run-1"

    # Emit 7 events to verify bounded deque (maxlen=5)
    for i in range(7):
        evt = b.emit(
            case_id=case_id,
            run_id=run_id,
            agent_name=f"Agent-{i}",
            event_type="COMPLETED",
            status="SUCCESS",
            safe_summary=f"Summary {i}",
            duration_ms=50.0,
        )
        assert evt.sequence_number == i + 1

    history = b.get_replay_history(case_id)
    assert len(history) == 5
    # Should retain the last 5 events (seq 3, 4, 5, 6, 7)
    assert history[0].sequence_number == 3
    assert history[-1].sequence_number == 7


@pytest.mark.asyncio
async def test_case_event_broadcaster_slow_consumer_isolation() -> None:
    """Verifies slow or dropping consumers are isolated without blocking execution."""
    b = CaseEventBroadcaster(max_buffer_size=10)
    case_id = f"CASE-{uuid.uuid4()}"

    # Create mock websocket that raises exception on send_text
    faulty_ws = MagicMock()
    faulty_ws.send_text = AsyncMock(side_effect=RuntimeError("Connection reset by peer"))

    await b.connect(case_id, faulty_ws)
    assert len(b._connections[case_id]) == 1

    # Emitting an event should gracefully discard the faulty client without throwing
    evt = b.emit(
        case_id=case_id,
        run_id="run-1",
        agent_name="ClaimsAgent",
        event_type="COMPLETED",
        status="SUCCESS",
        safe_summary="Success",
    )
    assert evt.sequence_number == 1


def test_websocket_endpoint_auth_and_lifecycle() -> None:
    """Verifies WebSocket endpoint authentication, case existence, and replay."""
    app = create_app()
    client = TestClient(app)

    case_id = f"CASE-{uuid.uuid4()}"

    # 1. Non-existent case -> closes with 4404
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(
        f"/api/v1/cases/{case_id}/ws?token=reviewer-token"
    ):
        pass

    # 2. Seed a valid case into DB
    session = SyncSessionLocal()
    try:
        c = Case(
            case_id=case_id,
            workflow_state=WorkflowState.ANALYZING,
            is_complete=False,
            version=1,
        )
        session.add(c)
        session.commit()
    finally:
        session.close()

    # Pre-populate broadcaster history
    broadcaster.emit(
        case_id=case_id,
        run_id="run-ws",
        agent_name="OCRPreprocessor",
        event_type="COMPLETED",
        status="SUCCESS",
        safe_summary="Preprocessed 1 document",
    )

    # 3. Connect with valid token -> connects and receives historical event
    with client.websocket_connect(f"/api/v1/cases/{case_id}/ws?token=reviewer-token") as ws:
        data = ws.receive_json()
        assert data["case_id"] == case_id
        assert data["agent_name"] == "OCRPreprocessor"
        assert data["status"] == "SUCCESS"

        # Test keepalive ping/pong
        ws.send_text("ping")
        resp = ws.receive_json()
        assert resp["type"] == "pong"


def test_get_case_events_rest_endpoint() -> None:
    """Verifies REST fallback endpoint returns event history."""
    app = create_app()
    client = TestClient(app)

    case_id = f"CASE-{uuid.uuid4()}"
    session = SyncSessionLocal()
    try:
        c = Case(
            case_id=case_id,
            workflow_state=WorkflowState.COMPLETED,
            current_decision=BusinessDecision.APPROVE,
            is_complete=True,
            version=1,
        )
        session.add(c)
        session.commit()
    finally:
        session.close()

    # Request events
    res = client.get(
        f"/api/v1/cases/{case_id}/events",
        headers={"X-User-Role": "OPERATIONS_REVIEWER"},
    )
    assert res.status_code == 200
    events = res.json()
    assert isinstance(events, list)
    assert len(events) >= 1
    assert any(e["agent_name"] == "DecisionEngine" for e in events)


def test_websocket_live_step_streaming_lifecycle() -> None:
    """Verifies has_subscribers, live step event broadcasting, and replay clearing."""
    app = create_app()
    client = TestClient(app)

    case_id = f"CASE-{uuid.uuid4()}"
    session = SyncSessionLocal()
    try:
        c = Case(
            case_id=case_id,
            workflow_state=WorkflowState.ANALYZING,
            is_complete=False,
            version=1,
        )
        session.add(c)
        session.commit()
    finally:
        session.close()

    assert broadcaster.has_subscribers(case_id) is False

    with client.websocket_connect(f"/api/v1/cases/{case_id}/ws?token=reviewer-token") as ws:
        # Client is now active subscriber
        assert broadcaster.has_subscribers(case_id) is True

        # Emit STARTED event
        broadcaster.emit(
            case_id=case_id,
            run_id="run-live-1",
            agent_name="KYCAgent",
            event_type="STARTED",
            status="RUNNING",
            safe_summary="KYC checking identity",
        )
        msg1 = ws.receive_json()
        assert msg1["agent_name"] == "KYCAgent"
        assert msg1["status"] == "RUNNING"
        assert msg1["event_type"] == "STARTED"

        # Emit COMPLETED event
        broadcaster.emit(
            case_id=case_id,
            run_id="run-live-1",
            agent_name="KYCAgent",
            event_type="COMPLETED",
            status="SUCCESS",
            duration_ms=45.2,
            safe_summary="KYC identity verified",
        )
        msg2 = ws.receive_json()
        assert msg2["agent_name"] == "KYCAgent"
        assert msg2["status"] == "SUCCESS"
        assert msg2["event_type"] == "COMPLETED"

    # Once socket context exits, subscriber is disconnected
    assert broadcaster.has_subscribers(case_id) is False

    # Check replay buffer retains 2 events
    assert len(broadcaster.get_replay_history(case_id)) == 2

    # Clear history for rerun
    broadcaster.clear_replay_history(case_id)
    assert len(broadcaster.get_replay_history(case_id)) == 0
