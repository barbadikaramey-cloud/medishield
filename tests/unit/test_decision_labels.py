import pytest

from medishield.eval.decision_labels import (
    compute_decision_label_metrics,
    resolve_adjudication_label,
)


def test_fraud_metadata_does_not_create_adjudication_label() -> None:
    docs = [
        {"fraud_label": False, "fraud_reason": None},
        {"fraud_label": True, "fraud_reason": "duplicate"},
    ]
    assert resolve_adjudication_label(docs) is None


def test_explicit_case_level_adjudication_label_is_resolved() -> None:
    docs = [
        {"fraud_label": True, "adjudication_label": "escalate"},
        {"fraud_label": False},
    ]
    assert resolve_adjudication_label(docs) == "ESCALATE"


def test_conflicting_adjudication_labels_are_rejected() -> None:
    with pytest.raises(ValueError, match="Conflicting adjudication labels"):
        resolve_adjudication_label([
            {"adjudication_label": "APPROVE"},
            {"adjudication_label": "REJECT"},
        ])


def test_invalid_adjudication_label_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid adjudication label"):
        resolve_adjudication_label([{"adjudication_label": "REVIEW"}])


def test_unlabelled_cases_are_excluded_from_quality_metrics() -> None:
    metrics = compute_decision_label_metrics([
        {"reviewed_adjudication_label": None, "final_decision": "APPROVE"},
        {"reviewed_adjudication_label": "REJECT", "final_decision": "REJECT"},
        {"reviewed_adjudication_label": "APPROVE", "final_decision": "REJECT"},
    ])
    assert metrics["labelled_case_count"] == 2
    assert metrics["unlabelled_case_count"] == 1
    assert metrics["decision_accuracy"] == 0.5
    assert metrics["false_approve_count"] == 0
    assert metrics["false_deny_count"] == 1


def test_no_reviewed_labels_returns_none_for_rates() -> None:
    metrics = compute_decision_label_metrics([
        {"reviewed_adjudication_label": None, "expected_decision": "APPROVE", "final_decision": "ESCALATE"},
    ])
    assert metrics["labelled_case_count"] == 0
    assert metrics["decision_accuracy"] is None
    assert metrics["false_approve_rate"] is None
    assert metrics["false_deny_rate"] is None
