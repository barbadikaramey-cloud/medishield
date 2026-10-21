"""Resolution of independently reviewed benchmark adjudication labels."""

from collections.abc import Iterable, Mapping

from medishield.db.enums import BusinessDecision


def resolve_adjudication_label(documents: Iterable[Mapping[str, object]]) -> str | None:
    """Return one explicit case adjudication label; never infer it from fraud metadata."""
    labels = {
        str(document["adjudication_label"]).strip().upper()
        for document in documents
        if document.get("adjudication_label") is not None
        and str(document["adjudication_label"]).strip()
    }
    if not labels:
        return None
    if len(labels) > 1:
        raise ValueError(f"Conflicting adjudication labels in case metadata: {sorted(labels)}")
    label = labels.pop()
    if label not in {decision.value for decision in BusinessDecision}:
        raise ValueError(f"Invalid adjudication label: {label}")
    return label


def compute_decision_label_metrics(records: Iterable[Mapping[str, object]]) -> dict[str, int | float | None]:
    """Compute decision quality only on independently labelled case records."""
    records = list(records)
    labelled = [
        record
        for record in records
        if record.get("reviewed_adjudication_label") in {decision.value for decision in BusinessDecision}
    ]
    predictions = [record for record in labelled if record.get("final_decision") in {d.value for d in BusinessDecision}]
    labelled_approvals = sum(record["reviewed_adjudication_label"] == BusinessDecision.APPROVE.value for record in predictions)
    correct = sum(record["reviewed_adjudication_label"] == record["final_decision"] for record in predictions)
    approved_of_expected_approvals = sum(
        record["reviewed_adjudication_label"] == BusinessDecision.APPROVE.value
        and record["final_decision"] == BusinessDecision.APPROVE.value
        for record in predictions
    )
    false_approvals = sum(
        record["reviewed_adjudication_label"] != BusinessDecision.APPROVE.value
        and record["final_decision"] == BusinessDecision.APPROVE.value
        for record in predictions
    )
    false_denials = sum(
        record["reviewed_adjudication_label"] == BusinessDecision.APPROVE.value
        and record["final_decision"] == BusinessDecision.REJECT.value
        for record in predictions
    )
    labeled_count = len(predictions)
    approval_rate = approved_of_expected_approvals / labelled_approvals if labelled_approvals else None
    return {
        "labelled_case_count": labeled_count,
        "unlabelled_case_count": sum(
            record.get("reviewed_adjudication_label") not in {decision.value for decision in BusinessDecision}
            for record in records
        ),
        "correct_decision_count": correct,
        "decision_accuracy": correct / labeled_count if labeled_count else None,
        "expected_approve_count": labelled_approvals,
        "approved_of_expected_approve": approved_of_expected_approvals,
        "approval_recall": approval_rate,
        "false_approve_count": false_approvals,
        "false_approve_rate": false_approvals / labeled_count if labeled_count else None,
        "false_deny_count": false_denials,
        "false_deny_rate": false_denials / labelled_approvals if labelled_approvals else None,
    }
