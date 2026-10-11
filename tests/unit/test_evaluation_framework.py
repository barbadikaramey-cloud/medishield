"""Unit tests for the Evaluation & Synthetic Benchmarking Framework (FEAT-013, Spec 12)."""

import tempfile
from pathlib import Path

import pytest

from medishield.db.enums import (
    BusinessDecision,
    DocumentType,
    EscalationSubReason,
)
from medishield.eval import (
    CATEGORY_METADATA,
    BenchmarkHarness,
    CasePrediction,
    RelationalSyntheticGenerator,
    compute_classification_accuracy,
    compute_decision_correctness,
    compute_extraction_completeness,
    compute_retrieval_recall_at_k,
)

# ── 1. Relational Seed Generation & Repeatability Tests ───────────────────────

def test_relational_seed_repeatability() -> None:
    """Verifies that identical base seed produces byte-identical test cases."""
    gen1 = RelationalSyntheticGenerator(base_seed=12345)
    gen2 = RelationalSyntheticGenerator(base_seed=12345)

    case1 = gen1.generate_case("CAT-01", 1)
    case2 = gen2.generate_case("CAT-01", 1)

    assert case1.case_id == case2.case_id
    assert case1.seed == case2.seed
    assert len(case1.documents) == len(case2.documents)
    assert case1.documents[0].expected_fields == case2.documents[0].expected_fields
    assert case1.documents[0].text_content == case2.documents[0].text_content


def test_relational_cross_entity_consistency() -> None:
    """Verifies zero relational drift across member, claim, and document fields (Spec 12 § 2)."""
    gen = RelationalSyntheticGenerator(base_seed=42)
    case = gen.generate_case("CAT-01", 1)

    claim_doc = next(d for d in case.documents if d.expected_doc_type == DocumentType.CLAIM_FORM.value)
    id_doc = next(d for d in case.documents if d.expected_doc_type == DocumentType.ID_DOCUMENT.value)

    # Member ID in claim form matches ID in identity document
    assert claim_doc.expected_fields["member_id"] == id_doc.expected_fields["member_id"]
    # Patient name appears in claim text
    assert claim_doc.expected_fields["patient_name"] in claim_doc.text_content
    # Total charge in expected fields appears in claim form text
    total_amt = claim_doc.expected_fields["total_amount"]
    assert f"${total_amt:.2f}" in claim_doc.text_content


# ── 2. Adversarial Category Coverage Tests (CAT-01 to CAT-11) ─────────────────

@pytest.mark.parametrize("cat_id", [f"CAT-{i:02d}" for i in range(1, 12)])
def test_all_11_adversarial_categories_generated(cat_id: str) -> None:
    """Verifies that all 11 adversarial stress categories are correctly generated (Spec 12 § 3)."""
    gen = RelationalSyntheticGenerator(base_seed=999)
    case = gen.generate_case(cat_id, 1)

    meta = CATEGORY_METADATA[cat_id]
    assert case.category == cat_id
    assert case.category_name == meta["name"]
    assert case.expected_business_decision == meta["decision"]
    assert case.expected_workflow_state == meta["state"]
    assert case.expected_sub_reason == meta["sub_reason"]
    assert case.expected_fraud_risk == meta["fraud_risk"]
    assert len(case.documents) >= 1


def test_prompt_injection_category_flagging() -> None:
    """Verifies CAT-09 synthesizes prompt injection payload with proper security expectation."""
    gen = RelationalSyntheticGenerator(base_seed=500)
    case = gen.generate_case("CAT-09", 1)

    assert case.expected_business_decision == BusinessDecision.ESCALATE.value
    assert case.expected_sub_reason == EscalationSubReason.SECURITY_REVIEW.value
    doc = case.documents[0]
    assert doc.prompt_injection_flag is True
    assert "SYSTEM OVERRIDE" in doc.text_content


def test_tampering_category_flagging() -> None:
    """Verifies CAT-10 synthesizes digital tampering indicator with fraud escalation expectation."""
    gen = RelationalSyntheticGenerator(base_seed=500)
    case = gen.generate_case("CAT-10", 1)

    assert case.expected_business_decision == BusinessDecision.ESCALATE.value
    assert case.expected_sub_reason == EscalationSubReason.FRAUD_REVIEW.value
    doc = case.documents[0]
    assert doc.tamper_flag is True


# ── 3. Dataset Generation and Split Strategy Tests ───────────────────────────

def test_generate_tier_1_dataset() -> None:
    """Verifies Tier 1 assignment core minimum dataset contains 20 cases (Spec 12 § 1)."""
    gen = RelationalSyntheticGenerator(base_seed=42)
    tier1_cases = gen.generate_tier_1_dataset()

    assert len(tier1_cases) == 20
    for c in tier1_cases:
        assert c.tier == "TIER_1"
        assert c.split == "DEV"


def test_generate_tier_2_dataset_with_60_40_split() -> None:
    """Verifies Tier 2 benchmark enforces 60% DEV / 40% TEST split across 100 cases (Spec 12 § 6)."""
    gen = RelationalSyntheticGenerator(base_seed=42)
    tier2_cases = gen.generate_tier_2_dataset(total_cases=100)

    assert len(tier2_cases) == 100
    dev_cases = [c for c in tier2_cases if c.split == "DEV"]
    test_cases = [c for c in tier2_cases if c.split == "TEST"]

    assert len(dev_cases) == 60  # Exactly 60%
    assert len(test_cases) == 40  # Exactly 40%


def test_dataset_disk_serialization_roundtrip() -> None:
    """Verifies saving and loading dataset manifests from disk."""
    gen = RelationalSyntheticGenerator(base_seed=42)
    cases = gen.generate_tier_1_dataset()[:3]

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        gen.save_dataset_to_disk(cases, tmp_path)

        assert (tmp_path / "cases_manifest.json").exists()
        loaded_cases = RelationalSyntheticGenerator.load_dataset_from_disk(tmp_path)

        assert len(loaded_cases) == 3
        assert loaded_cases[0].case_id == cases[0].case_id
        assert loaded_cases[0].expected_business_decision == cases[0].expected_business_decision


# ── 4. Metrics Calculation Engine Tests ───────────────────────────────────────

def test_metrics_classification_accuracy() -> None:
    """Verifies classification accuracy formula matches Spec 12 § 5.1."""
    gen = RelationalSyntheticGenerator(base_seed=42)
    cases = gen.generate_tier_1_dataset()[:2]  # 2 cases

    # Predictions where all document classifications are correct
    preds_perfect = [
        CasePrediction(
            case_id=c.case_id,
            predicted_doc_types={d.document_id: d.expected_doc_type for d in c.documents},
        )
        for c in cases
    ]
    acc_perfect = compute_classification_accuracy(cases, preds_perfect)
    assert acc_perfect == 1.0

    # Predictions where one document is wrong
    preds_partial = [
        CasePrediction(
            case_id=cases[0].case_id,
            predicted_doc_types={cases[0].documents[0].document_id: "WRONG_TYPE"},
        ),
        CasePrediction(
            case_id=cases[1].case_id,
            predicted_doc_types={d.document_id: d.expected_doc_type for d in cases[1].documents},
        ),
    ]
    acc_partial = compute_classification_accuracy(cases, preds_partial)
    assert 0.0 < acc_partial < 1.0


def test_metrics_extraction_completeness() -> None:
    """Verifies extraction completeness evaluates required fields matching ground truth."""
    gen = RelationalSyntheticGenerator(base_seed=42)
    case = gen.generate_case("CAT-01", 1)

    preds = [
        CasePrediction(
            case_id=case.case_id,
            extracted_fields={d.document_id: dict(d.expected_fields) for d in case.documents},
        )
    ]
    completeness = compute_extraction_completeness([case], preds)
    assert completeness == 1.0


def test_metrics_retrieval_recall_at_k() -> None:
    """Verifies recall@5 computation for retrieved policy chunks."""
    gen = RelationalSyntheticGenerator(base_seed=42)
    case = gen.generate_case("CAT-01", 1)

    assert case.expected_policy_adjudication is not None
    target_clause = case.expected_policy_adjudication.expected_clause_ids[0]
    preds = [
        CasePrediction(
            case_id=case.case_id,
            retrieved_clause_ids=["noise-1", "noise-2", target_clause, "noise-3"],
        )
    ]
    recall = compute_retrieval_recall_at_k([case], preds, k=5)
    assert recall == 1.0

    preds_miss = [
        CasePrediction(
            case_id=case.case_id,
            retrieved_clause_ids=["noise-1", "noise-2", "noise-3", "noise-4", "noise-5", target_clause],
        )
    ]
    recall_miss = compute_retrieval_recall_at_k([case], preds_miss, k=5)
    assert recall_miss == 0.0


def test_metrics_decision_correctness_and_breakdown() -> None:
    """Verifies business decision correctness and per-category accuracy breakdown."""
    gen = RelationalSyntheticGenerator(base_seed=42)
    case1 = gen.generate_case("CAT-01", 1)
    case2 = gen.generate_case("CAT-02", 2)

    preds = [
        CasePrediction(
            case_id=case1.case_id,
            actual_decision=case1.expected_business_decision,
            actual_sub_reason=case1.expected_sub_reason,
        ),
        CasePrediction(
            case_id=case2.case_id,
            actual_decision=case2.expected_business_decision,
            actual_sub_reason=case2.expected_sub_reason,
        ),
    ]

    accuracy, breakdown = compute_decision_correctness([case1, case2], preds)
    assert accuracy == 1.0
    assert "CAT-01" in breakdown
    assert breakdown["CAT-01"]["accuracy"] == 1.0
    assert "CAT-02" in breakdown
    assert breakdown["CAT-02"]["accuracy"] == 1.0


def test_weighted_rubric_and_pass_gates() -> None:
    """Verifies composite scoring formula and >= 70% overall / >= 60% decision gates (Spec 12 § 5.1)."""
    gen = RelationalSyntheticGenerator(base_seed=42)
    cases = gen.generate_tier_1_dataset()

    harness = BenchmarkHarness(ui_pass_rate=1.0, code_quality_pass_rate=1.0)
    metrics, _ = harness.run_benchmark(cases)

    # In simulated benchmark matching canonical rules:
    assert metrics.passed_decision_gate is True
    assert metrics.passed_overall_gate is True
    assert metrics.decision_correctness >= 0.60
    assert metrics.composite_score >= 0.70

    # Operational KPIs
    assert metrics.straight_through_processing_rate >= 0.65
    assert metrics.false_approval_rate <= 0.01
    assert metrics.false_denial_rate <= 0.02


def test_benchmark_markdown_report_formatting() -> None:
    """Verifies markdown report generator produces complete structured tables."""
    gen = RelationalSyntheticGenerator(base_seed=42)
    cases = gen.generate_tier_1_dataset()[:5]

    harness = BenchmarkHarness()
    metrics, _ = harness.run_benchmark(cases)
    report = harness.generate_markdown_report(metrics, title="MediShield Unit Test Benchmark")

    assert "# MediShield Unit Test Benchmark" in report
    assert "Assignment Weighted Acceptance Rubric" in report
    assert "Enterprise Operational KPIs" in report
    assert "Adversarial Category Breakdown" in report
    assert "Straight-Through Processing" in report
    assert "OVERALL WEIGHTED COMPOSITE" in report

