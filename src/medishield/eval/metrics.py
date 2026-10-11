"""Evaluation metrics calculation and scoring rubric engine (Spec 12 § 5)."""

import statistics

from medishield.eval.contracts import (
    CasePrediction,
    EvaluationMetrics,
    GroundTruthCase,
)


def compute_classification_accuracy(
    ground_truth_cases: list[GroundTruthCase],
    predictions: list[CasePrediction],
) -> float:
    """
    Computes Document Classification Accuracy (Spec 12 § 5.1).
    Accuracy = Correct Document Types / Total Evaluated Documents
    """
    pred_map = {p.case_id: p for p in predictions}
    total_docs = 0
    correct_docs = 0

    for gt in ground_truth_cases:
        pred = pred_map.get(gt.case_id)
        if not pred:
            total_docs += len(gt.documents)
            continue

        for doc in gt.documents:
            total_docs += 1
            predicted_type = pred.predicted_doc_types.get(doc.document_id)
            if predicted_type and predicted_type == doc.expected_doc_type:
                correct_docs += 1

    return (correct_docs / total_docs) if total_docs > 0 else 1.0


def compute_extraction_completeness(
    ground_truth_cases: list[GroundTruthCase],
    predictions: list[CasePrediction],
) -> float:
    """
    Computes Information Extraction Completeness (Spec 12 § 5.1).
    Completeness = Extracted Required Fields Matching Ground Truth / Total Ground Truth Fields
    """
    pred_map = {p.case_id: p for p in predictions}
    total_fields = 0
    matched_fields = 0

    for gt in ground_truth_cases:
        pred = pred_map.get(gt.case_id)
        if not pred:
            total_fields += sum(len(d.expected_fields) for d in gt.documents)
            continue

        for doc in gt.documents:
            for field_name, expected_val in doc.expected_fields.items():
                total_fields += 1
                doc_extractions = pred.extracted_fields.get(doc.document_id, {})
                actual_val = doc_extractions.get(field_name)

                # Tolerance-aware comparison
                if actual_val is not None:
                    if isinstance(expected_val, float) and isinstance(actual_val, (int, float)):
                        if abs(expected_val - actual_val) < 0.01:
                            matched_fields += 1
                    elif isinstance(expected_val, list) and isinstance(actual_val, list):
                        if {str(x) for x in expected_val} == {str(x) for x in actual_val}:
                            matched_fields += 1
                    elif str(actual_val).strip().lower() == str(expected_val).strip().lower():
                        matched_fields += 1

    return (matched_fields / total_fields) if total_fields > 0 else 1.0


def compute_retrieval_recall_at_k(
    ground_truth_cases: list[GroundTruthCase],
    predictions: list[CasePrediction],
    k: int = 5,
) -> float:
    """
    Computes Policy Retrieval Recall@K (Spec 12 § 5.1).
    Recall@K = Ground Truth Clauses Retrieved in Top K / Total Ground Truth Clauses
    """
    pred_map = {p.case_id: p for p in predictions}
    total_clauses = 0
    retrieved_clauses = 0

    for gt in ground_truth_cases:
        if not gt.expected_policy_adjudication or not gt.expected_policy_adjudication.expected_clause_ids:
            continue

        expected = set(gt.expected_policy_adjudication.expected_clause_ids)
        total_clauses += len(expected)

        pred = pred_map.get(gt.case_id)
        if pred:
            top_k_retrieved = set(pred.retrieved_clause_ids[:k])
            retrieved_clauses += len(expected.intersection(top_k_retrieved))

    return (retrieved_clauses / total_clauses) if total_clauses > 0 else 1.0


def compute_decision_correctness(
    ground_truth_cases: list[GroundTruthCase],
    predictions: list[CasePrediction],
) -> tuple[float, dict[str, dict[str, float]]]:
    """
    Computes Business Decision Correctness (Spec 12 § 5.1).
    Accuracy = Correct Business Decisions / Total Cases Evaluated
    Also computes granular breakdown per evaluation category.
    """
    pred_map = {p.case_id: p for p in predictions}
    total_cases = len(ground_truth_cases)
    correct_cases = 0

    category_stats: dict[str, dict[str, int]] = {}

    for gt in ground_truth_cases:
        cat = gt.category
        if cat not in category_stats:
            category_stats[cat] = {"total": 0, "correct": 0}
        category_stats[cat]["total"] += 1

        pred = pred_map.get(gt.case_id)
        if pred and pred.actual_decision:
            is_decision_match = (pred.actual_decision == gt.expected_business_decision)
            if is_decision_match:
                if gt.expected_sub_reason and pred.actual_sub_reason:
                    if pred.actual_sub_reason == gt.expected_sub_reason:
                        correct_cases += 1
                        category_stats[cat]["correct"] += 1
                else:
                    correct_cases += 1
                    category_stats[cat]["correct"] += 1

    overall_accuracy = (correct_cases / total_cases) if total_cases > 0 else 0.0

    breakdown: dict[str, dict[str, float]] = {}
    for cat, stats in category_stats.items():
        breakdown[cat] = {
            "total_cases": float(stats["total"]),
            "correct_cases": float(stats["correct"]),
            "accuracy": (stats["correct"] / stats["total"]) if stats["total"] > 0 else 0.0,
        }

    return overall_accuracy, breakdown


def compute_operational_kpis(
    ground_truth_cases: list[GroundTruthCase],
    predictions: list[CasePrediction],
) -> dict[str, float]:
    """
    Computes Enterprise Operational KPIs (Spec 12 § 5.2):
    1. Straight-Through Processing (STP) Rate on clean standard claims.
    2. False Approval Rate (FAR).
    3. False Denial Rate (FDR).
    4. p50 and p95 latency.
    5. Rework Rate.
    """
    pred_map = {p.case_id: p for p in predictions}

    clean_total = 0
    clean_auto_approved = 0

    non_approve_gt_total = 0
    false_approvals = 0

    approve_gt_total = 0
    false_denials = 0

    latencies: list[float] = []
    rework_count = 0

    for gt in ground_truth_cases:
        pred = pred_map.get(gt.case_id)
        if not pred:
            continue

        if pred.processing_time_ms > 0:
            latencies.append(pred.processing_time_ms)

        if pred.execution_count > 1:
            rework_count += 1

        if gt.category in ["CAT-01", "CAT-03"]:
            clean_total += 1
            if pred.actual_decision == "APPROVE":
                clean_auto_approved += 1

        if gt.expected_business_decision != "APPROVE":
            non_approve_gt_total += 1
            if pred.actual_decision == "APPROVE":
                false_approvals += 1

        if gt.expected_business_decision == "APPROVE":
            approve_gt_total += 1
            if pred.actual_decision == "REJECT":
                false_denials += 1

    stp_rate = (clean_auto_approved / clean_total) if clean_total > 0 else 1.0
    far_rate = (false_approvals / non_approve_gt_total) if non_approve_gt_total > 0 else 0.0
    fdr_rate = (false_denials / approve_gt_total) if approve_gt_total > 0 else 0.0

    p50_latency = statistics.median(latencies) if latencies else 0.0
    p95_latency = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else (max(latencies) if latencies else 0.0)
    rework_rate = (rework_count / len(predictions)) if predictions else 0.0

    return {
        "straight_through_processing_rate": stp_rate,
        "false_approval_rate": far_rate,
        "false_denial_rate": fdr_rate,
        "p50_latency_ms": p50_latency,
        "p95_latency_ms": p95_latency,
        "rework_rate": rework_rate,
    }


def evaluate_benchmark(
    ground_truth_cases: list[GroundTruthCase],
    predictions: list[CasePrediction],
    ui_functionality_pass_rate: float = 1.0,
    code_quality_pass_rate: float = 1.0,
) -> EvaluationMetrics:
    """
    Evaluates complete benchmark dataset against Ground Truth contracts and computes
    weighted composite score and pass gates (Spec 12 § 5.1).
    """
    total_cases = len(ground_truth_cases)
    cls_acc = compute_classification_accuracy(ground_truth_cases, predictions)
    ext_comp = compute_extraction_completeness(ground_truth_cases, predictions)
    ret_rec = compute_retrieval_recall_at_k(ground_truth_cases, predictions, k=5)
    dec_corr, category_breakdown = compute_decision_correctness(ground_truth_cases, predictions)

    # Spec 12 § 5.1 Weighted Composite Formula:
    # 20% Classification + 20% Extraction + 15% Retrieval + 25% Decision + 10% UI + 10% Code Quality
    composite_score = (
        (0.20 * cls_acc)
        + (0.20 * ext_comp)
        + (0.15 * ret_rec)
        + (0.25 * dec_corr)
        + (0.10 * ui_functionality_pass_rate)
        + (0.10 * code_quality_pass_rate)
    )

    passed_decision_gate = dec_corr >= 0.60
    passed_overall_gate = composite_score >= 0.70

    kpis = compute_operational_kpis(ground_truth_cases, predictions)

    return EvaluationMetrics(
        total_cases=total_cases,
        classification_accuracy=cls_acc,
        extraction_completeness=ext_comp,
        policy_retrieval_recall_at_5=ret_rec,
        decision_correctness=dec_corr,
        ui_functionality_pass_rate=ui_functionality_pass_rate,
        code_quality_pass_rate=code_quality_pass_rate,
        composite_score=composite_score,
        passed_decision_gate=passed_decision_gate,
        passed_overall_gate=passed_overall_gate,
        straight_through_processing_rate=kpis["straight_through_processing_rate"],
        false_approval_rate=kpis["false_approval_rate"],
        false_denial_rate=kpis["false_denial_rate"],
        p50_latency_ms=kpis["p50_latency_ms"],
        p95_latency_ms=kpis["p95_latency_ms"],
        rework_rate=kpis["rework_rate"],
        category_breakdown=category_breakdown,
    )

