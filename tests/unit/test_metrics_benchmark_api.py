"""Unit and contract tests for the Benchmark Evaluation & Operational Telemetry APIs (Spec 12)."""

import pytest
from fastapi.testclient import TestClient

from medishield.api.deps import ROLE_ADMIN, ROLE_MANAGER, ROLE_REVIEWER
from medishield.api.main import app
from medishield.benchmarks.evaluator import summarize_fraud_metadata_labels
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
    labels = data["fraud_label_summary"]
    assert labels["source"] == "dataset/metadata.json"
    assert labels["total_documents"] == 155
    assert labels["labelled_documents"] == 155
    assert labels["positive_documents"] == 6
    assert labels["negative_documents"] == 149
    assert labels["unlabelled_documents"] == 0
    assert len(labels["positive_by_reason"]) == 6


def test_fraud_metadata_label_summary_counts_only_explicit_boolean_labels() -> None:
    summary = summarize_fraud_metadata_labels(
        [
            {"fraud_label": True, "fraud_reason": "duplicate_claim"},
            {"fraud_label": True, "fraud_reason": None},
            {"fraud_label": False},
            {"fraud_label": "true"},
            {},
        ]
    )

    assert summary["total_documents"] == 5
    assert summary["labelled_documents"] == 3
    assert summary["positive_documents"] == 2
    assert summary["negative_documents"] == 1
    assert summary["unlabelled_documents"] == 2
    assert summary["positive_by_reason"] == [
        {"reason": "duplicate_claim", "count": 1},
        {"reason": "unspecified", "count": 1},
    ]


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


def test_calibration_metrics_endpoint() -> None:
    """Verifies that GET /api/v1/metrics/calibration returns Brier, ECE, and reliability curves (ADR-017)."""
    client = TestClient(app)
    response = client.get(
        "/api/v1/metrics/calibration",
        headers={"X-User-Role": ROLE_REVIEWER, "X-User-Id": "rev-01"},
    )
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()

    assert data["total_samples"] > 0
    assert "brier_score" in data
    assert "expected_calibration_error" in data
    assert "maximum_calibration_error" in data
    assert "calibration_method" in data
    assert data["evaluation_source"] == "generated_synthetic_simulation"
    assert "generated expected_business_decision" in data["evaluation_label"]
    assert data["evaluation_population"] == "Generated synthetic cases: 50 fit cases and 50 disjoint evaluation cases."
    assert data["total_samples"] == 50
    assert len(data["bins"]) >= 3
    assert "selective_risk_coverage" in data
    assert len(data["selective_risk_coverage"]) > 0
    assert "interpretation" in data

