"""Unit and contract tests for the Benchmark Evaluation & Operational Telemetry APIs (Spec 12)."""

import pytest
from fastapi.testclient import TestClient

from medishield.api.deps import ROLE_ADMIN, ROLE_MANAGER, ROLE_REVIEWER
from medishield.api.main import app
from medishield.core.config import settings


def test_get_benchmark_metrics_endpoint() -> None:
    """Verifies that GET /api/v1/metrics/benchmark returns complete 30-cluster evaluation metrics."""
    client = TestClient(app)
    response = client.get(
        "/api/v1/metrics/benchmark",
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "rev-01"},
    )
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()

    assert data["total_documents"] >= 150
    assert data["total_clusters"] == 30
    assert data["classification_accuracy_percentage"] >= 95.0
    assert data["member_resolution_percentage"] >= 95.0
    assert data["suite_status"] == "PASS"
    assert len(data["categories"]) >= 5
    assert len(data["fraud_tests"]) >= 2
    assert "last_run_timestamp" in data
    assert "execution_time_seconds" in data


def test_trigger_benchmark_evaluation_run() -> None:
    """Verifies that POST /api/v1/metrics/benchmark/run re-executes evaluation and returns updated metrics."""
    client = TestClient(app)
    response = client.post(
        "/api/v1/metrics/benchmark/run",
        headers={"X-User-Role": ROLE_MANAGER, "X-User-Id": "mgr-01"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["suite_status"] == "PASS"
    assert data["classification_accuracy_percentage"] == 100.0


def test_benchmark_metrics_rbac(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies RBAC enforcement on the benchmark metrics endpoint."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    client = TestClient(app)

    # 1. Missing role header defaults or raises 401/403
    res_no_role = client.get("/api/v1/metrics/benchmark")
    assert res_no_role.status_code in (401, 403)

    # 2. Invalid role is denied
    res_bad_role = client.get(
        "/api/v1/metrics/benchmark",
        headers={"X-User-Role": "UNAUTHORIZED_GUEST"},
    )
    assert res_bad_role.status_code == 403

    # 3. Authorized roles are admitted
    for role in (ROLE_REVIEWER, ROLE_MANAGER, ROLE_ADMIN):
        res = client.get("/api/v1/metrics/benchmark", headers={"X-User-Role": role})
        assert res.status_code == 200


def test_operational_metrics_enriched_fields() -> None:
    """Verifies GET /api/v1/metrics/operational returns all expected telemetry fields for dashboards."""
    client = TestClient(app)
    response = client.get(
        "/api/v1/metrics/operational",
        headers={"X-User-Role": ROLE_MANAGER, "X-User-Id": "mgr-01"},
    )
    assert response.status_code == 200
    data = response.json()

    assert "straight_through_processing_rate" in data
    assert "auto_approval_rate" in data
    assert "escalation_rate" in data
    assert "rejection_rate" in data
    assert "total_adjudicated_cases" in data
    assert data["total_adjudicated_cases"] >= 0
    assert 0.0 <= data["auto_approval_rate"] <= 1.0

