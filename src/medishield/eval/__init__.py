"""Evaluation & Synthetic Benchmarking Framework (FEAT-013, Spec 12)."""

from medishield.eval.contracts import (
    CasePrediction,
    EvaluationMetrics,
    GroundTruthCase,
    GroundTruthDocument,
    GroundTruthPolicyAdjudication,
)
from medishield.eval.generator import (
    CATEGORY_METADATA,
    RelationalSyntheticGenerator,
)
from medishield.eval.harness import BenchmarkHarness
from medishield.eval.metrics import (
    compute_classification_accuracy,
    compute_decision_correctness,
    compute_extraction_completeness,
    compute_operational_kpis,
    compute_retrieval_recall_at_k,
    evaluate_benchmark,
)

__all__ = [
    "CATEGORY_METADATA",
    "BenchmarkHarness",
    "CasePrediction",
    "EvaluationMetrics",
    "GroundTruthCase",
    "GroundTruthDocument",
    "GroundTruthPolicyAdjudication",
    "RelationalSyntheticGenerator",
    "compute_classification_accuracy",
    "compute_decision_correctness",
    "compute_extraction_completeness",
    "compute_operational_kpis",
    "compute_retrieval_recall_at_k",
    "evaluate_benchmark",
]

