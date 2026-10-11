"""Unit tests for the Deterministic Fraud Detection Engine (FEAT-004, Spec 10, ADR-005)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    KYCAgentOutput,
    RiskLevel,
)
from medishield.domain.fraud import (
    FEATURE_WEIGHTS,
    FraudDetectionEngine,
    FraudEvaluationContext,
    calculate_duplicate_claim_feature,
    calculate_image_tamper_feature,
    calculate_line_total_arithmetic_drift_feature,
    calculate_member_frequency_30d_feature,
    calculate_provider_billing_outlier_feature,
    calculate_rapid_velocity_feature,
    calculate_service_date_inconsistency_feature,
    evaluate_claim_fraud,
)
from medishield.domain.provenance import ProvenanceValue


def build_mock_claims_output(
    total_amount: float = 500.0,
    line_sum: float = 500.0,
    service_date: date | None = None,
    member_id: str = "MEM-12345",
    provider_npi: str = "1992837465",
) -> ClaimsAgentOutput:
    """Helper to build a valid ClaimsAgentOutput fixture."""
    svc_date = service_date or (datetime.now(UTC).date() - timedelta(days=10))
    return ClaimsAgentOutput(
        agent_name="claims_agent",
        verdict=AgentVerdict.PASS,
        confidence=0.98,
        claim_amount=ProvenanceValue(value=Decimal(str(total_amount)), confidence=0.99),
        service_date=ProvenanceValue(value=svc_date, confidence=0.99),
        provider_npi=ProvenanceValue(value=provider_npi, confidence=0.99),
        schema_valid=True,
        line_items_sum_valid=(total_amount == line_sum),
        calculated_lines_sum=line_sum,
        latency_ms=150,
    )


def build_mock_kyc_output(tamper_score: float = 0.0) -> KYCAgentOutput:
    """Helper to build a KYCAgentOutput fixture."""
    return KYCAgentOutput(
        agent_name="kyc_agent",
        verdict=AgentVerdict.PASS if tamper_score < 0.5 else AgentVerdict.FAIL,
        confidence=0.95,
        tamper_detected=(tamper_score > 0.0),
        tamper_score=tamper_score,
        latency_ms=100,
    )


# ── 1. Weight Validation Tests ───────────────────────────────────────────────

def test_feature_weights_sum_to_one():
    """Confirms all 7 feature weights strictly sum to 1.00 (Spec 10 § 2.1)."""
    assert len(FEATURE_WEIGHTS) == 7
    total_weight = sum(FEATURE_WEIGHTS.values())
    assert abs(total_weight - 1.00) < 1e-9


# ── 2. Feature-by-Feature Calculation Tests ───────────────────────────────────

def test_duplicate_claim_feature():
    """F-01: duplicate_claim_key logic."""
    unique_feat = calculate_duplicate_claim_feature(None)
    assert unique_feat.normalized_score == 0.0
    assert unique_feat.raw_value == 0.0
    assert unique_feat.weight == 0.30

    dup_feat = calculate_duplicate_claim_feature("CLM-HIST-9901")
    assert dup_feat.normalized_score == 1.0
    assert dup_feat.raw_value == 1.0
    assert "CLM-HIST-9901" in dup_feat.description


def test_image_tamper_feature():
    """F-02: image_tamper_anomaly logic."""
    clean_feat = calculate_image_tamper_feature(0.0)
    assert clean_feat.normalized_score == 0.0

    tamper_feat = calculate_image_tamper_feature(0.75)
    assert tamper_feat.normalized_score == 0.75

    clamped_high = calculate_image_tamper_feature(1.4)
    assert clamped_high.normalized_score == 1.0

    clamped_low = calculate_image_tamper_feature(-0.5)
    assert clamped_low.normalized_score == 0.0


def test_rapid_velocity_feature():
    """F-03: rapid_resubmission_velocity logic."""
    # N=1 (current submission only) -> 0.0
    assert calculate_rapid_velocity_feature(1).normalized_score == 0.0
    assert calculate_rapid_velocity_feature(0).normalized_score == 0.0

    # N=2 -> (2 - 1) / 4 = 0.25
    assert calculate_rapid_velocity_feature(2).normalized_score == 0.25

    # N=3 -> (3 - 1) / 4 = 0.50
    assert calculate_rapid_velocity_feature(3).normalized_score == 0.50

    # N=5 -> (5 - 1) / 4 = 1.0
    assert calculate_rapid_velocity_feature(5).normalized_score == 1.0

    # N=10 -> clamped to 1.0
    assert calculate_rapid_velocity_feature(10).normalized_score == 1.0


def test_provider_billing_outlier_feature():
    """F-04: provider_billing_outlier Z-score logic."""
    # Within normal variation (Z <= 2.0)
    # mean=100, std=20, amount=140 -> Z = 2.0 -> score = 0.0
    feat_normal = calculate_provider_billing_outlier_feature(
        claim_amount=140.0, mean_charge=100.0, std_charge=20.0
    )
    assert feat_normal.raw_value == 2.0
    assert feat_normal.normalized_score == 0.0

    # Moderate outlier: amount=170 -> Z = 3.5 -> (3.5 - 2.0)/3.0 = 0.5
    feat_moderate = calculate_provider_billing_outlier_feature(
        claim_amount=170.0, mean_charge=100.0, std_charge=20.0
    )
    assert feat_moderate.raw_value == 3.5
    assert feat_moderate.normalized_score == 0.5

    # Extreme outlier: amount=220 -> Z = 6.0 -> (6.0 - 2.0)/3.0 = 1.33 -> clamped to 1.0
    feat_extreme = calculate_provider_billing_outlier_feature(
        claim_amount=220.0, mean_charge=100.0, std_charge=20.0
    )
    assert feat_extreme.raw_value == 6.0
    assert feat_extreme.normalized_score == 1.0

    # Missing benchmark statistics -> safe 0.0
    feat_missing = calculate_provider_billing_outlier_feature(
        claim_amount=500.0, mean_charge=None, std_charge=None
    )
    assert feat_missing.normalized_score == 0.0
    assert feat_missing.raw_value == 0.0

    # Zero standard deviation -> safe 0.0
    feat_zero_std = calculate_provider_billing_outlier_feature(
        claim_amount=500.0, mean_charge=100.0, std_charge=0.0
    )
    assert feat_zero_std.normalized_score == 0.0


def test_service_date_inconsistency_feature():
    """F-05: service_date_inconsistency logic."""
    today = date(2026, 10, 1)

    # Valid chronological date: 15 days ago -> 0.0
    valid_date = date(2026, 9, 16)
    feat_valid = calculate_service_date_inconsistency_feature(valid_date, today)
    assert feat_valid.normalized_score == 0.0

    # Future date: 5 days in future -> 1.0
    future_date = date(2026, 10, 6)
    feat_future = calculate_service_date_inconsistency_feature(future_date, today)
    assert feat_future.normalized_score == 1.0
    assert "Future service date" in feat_future.description

    # Stale date: 400 days in the past -> 0.5
    stale_date = date(2025, 8, 27)
    feat_stale = calculate_service_date_inconsistency_feature(stale_date, today)
    assert feat_stale.normalized_score == 0.5
    assert "Stale service date" in feat_stale.description

    # Missing date -> 0.0
    feat_none = calculate_service_date_inconsistency_feature(None, today)
    assert feat_none.normalized_score == 0.0


def test_line_total_arithmetic_drift_feature():
    """F-06: line_total_arithmetic_drift percentage drift logic."""
    # Zero drift: total 500, lines sum 500 -> 0.0
    feat_exact = calculate_line_total_arithmetic_drift_feature(500.0, 500.0)
    assert feat_exact.normalized_score == 0.0

    # 5% drift: total 500, lines sum 475 -> delta = 25/500 = 0.05 -> 0.05 / 0.10 = 0.50
    feat_drift = calculate_line_total_arithmetic_drift_feature(500.0, 475.0)
    assert feat_drift.raw_value == 0.05
    assert feat_drift.normalized_score == 0.50

    # 15% drift: total 500, lines sum 425 -> delta = 75/500 = 0.15 -> clamped to 1.0
    feat_high_drift = calculate_line_total_arithmetic_drift_feature(500.0, 425.0)
    assert feat_high_drift.raw_value == 0.15
    assert feat_high_drift.normalized_score == 1.0

    # Zero total amount -> safe 0.0
    feat_zero_total = calculate_line_total_arithmetic_drift_feature(0.0, 0.0)
    assert feat_zero_total.normalized_score == 0.0


def test_member_frequency_30d_feature():
    """F-07: member_claim_frequency_30d baseline exceeding logic."""
    # N <= 3 -> 0.0
    assert calculate_member_frequency_30d_feature(1).normalized_score == 0.0
    assert calculate_member_frequency_30d_feature(3).normalized_score == 0.0

    # N=4 -> (4 - 3) / 5 = 0.20
    assert calculate_member_frequency_30d_feature(4).normalized_score == 0.20

    # N=5 -> (5 - 3) / 5 = 0.40
    assert calculate_member_frequency_30d_feature(5).normalized_score == 0.40

    # N=8 -> (8 - 3) / 5 = 1.0
    assert calculate_member_frequency_30d_feature(8).normalized_score == 1.0

    # N=15 -> clamped to 1.0
    assert calculate_member_frequency_30d_feature(15).normalized_score == 1.0


# ── 3. Engine End-to-End & Scenario Tests ─────────────────────────────────────

def test_clean_claim_scores_low_risk():
    """Clean claim with no anomalies scores 0.00 and passes automated check."""
    claims_out = build_mock_claims_output(total_amount=250.0, line_sum=250.0)
    kyc_out = build_mock_kyc_output(tamper_score=0.0)
    ctx = FraudEvaluationContext(
        duplicate_claim_match_id=None,
        resubmission_count_48h=1,
        member_claim_count_30d=1,
        cpt_benchmark_mean=240.0,
        cpt_benchmark_std=30.0,
    )

    result = evaluate_claim_fraud(claims_out, kyc_out, ctx)

    assert result.fraud_score == 0.0
    assert result.risk_level == RiskLevel.LOW
    assert result.verdict == AgentVerdict.PASS
    assert result.duplicate_claim_detected is False
    assert result.duplicate_claim_id is None
    assert result.velocity_anomaly_detected is False
    assert len(result.anomalies) == 0
    assert len(result.features) == 7


def test_duplicate_claim_alone_triggers_medium_risk():
    """Duplicate claim collision (weight 0.30) elevates risk to MEDIUM (INDETERMINATE)."""
    claims_out = build_mock_claims_output()
    kyc_out = build_mock_kyc_output(tamper_score=0.0)
    ctx = FraudEvaluationContext(duplicate_claim_match_id="CLM-PRIOR-4482")

    result = evaluate_claim_fraud(claims_out, kyc_out, ctx)

    assert result.fraud_score == 0.30
    assert result.risk_level == RiskLevel.MEDIUM
    assert result.verdict == AgentVerdict.INDETERMINATE
    assert result.duplicate_claim_detected is True
    assert result.duplicate_claim_id == "CLM-PRIOR-4482"
    assert len(result.anomalies) == 1
    assert "CLM-PRIOR-4482" in result.anomalies[0]


def test_severe_compounding_anomalies_trigger_high_risk():
    """
    Multiple anomalies compounding above 0.70 threshold:
    - duplicate match (0.30 * 1.0 = 0.30)
    - tamper score 0.8 (0.20 * 0.8 = 0.16)
    - provider outlier 1.0 (0.15 * 1.0 = 0.15)
    - future service date (0.10 * 1.0 = 0.10)
    Total = 0.30 + 0.16 + 0.15 + 0.10 = 0.71 -> HIGH risk (FAIL).
    """
    future_date = datetime.now(UTC).date() + timedelta(days=7)
    claims_out = build_mock_claims_output(
        total_amount=1000.0,
        line_sum=1000.0,
        service_date=future_date,
    )
    kyc_out = build_mock_kyc_output(tamper_score=0.8)
    ctx = FraudEvaluationContext(
        duplicate_claim_match_id="CLM-DUP-9911",
        cpt_benchmark_mean=200.0,
        cpt_benchmark_std=50.0,  # Z = (1000 - 200)/50 = 16.0 -> normalized outlier 1.0
        submission_date=datetime.now(UTC).date(),
    )

    result = evaluate_claim_fraud(claims_out, kyc_out, ctx)

    assert result.fraud_score == 0.71
    assert result.risk_level == RiskLevel.HIGH
    assert result.verdict == AgentVerdict.FAIL
    assert result.duplicate_claim_detected is True
    assert len(result.anomalies) >= 4


def test_threshold_boundaries():
    """Confirms exact behavior at threshold boundaries 0.30 and 0.70."""
    # 0.2999 is LOW
    assert FraudDetectionEngine.evaluate(
        build_mock_claims_output(),
        build_mock_kyc_output(tamper_score=0.0),
    ).risk_level == RiskLevel.LOW

    # Boundary test: score == 0.3000 -> MEDIUM
    claims_out = build_mock_claims_output()
    ctx_dup = FraudEvaluationContext(duplicate_claim_match_id="CLM-01")
    res_med = FraudDetectionEngine.evaluate(claims_out, context=ctx_dup)
    assert res_med.fraud_score == 0.30
    assert res_med.risk_level == RiskLevel.MEDIUM
    assert res_med.verdict == AgentVerdict.INDETERMINATE

    # Boundary test: score >= 0.7000 -> HIGH
    # F01: 0.30, F02: 0.20 (tamper=1.0), F03: 0.15 (N=5), F06: 0.05 (drift 10%)
    # Total = 0.30 + 0.20 + 0.15 + 0.05 = 0.70
    claims_drift = build_mock_claims_output(total_amount=100.0, line_sum=85.0)
    ctx_70 = FraudEvaluationContext(
        duplicate_claim_match_id="CLM-01",
        resubmission_count_48h=5,
    )
    kyc_tamper = build_mock_kyc_output(tamper_score=1.0)
    res_70 = FraudDetectionEngine.evaluate(claims_drift, kyc_tamper, ctx_70)
    assert res_70.fraud_score == 0.70
    assert res_70.risk_level == RiskLevel.HIGH
    assert res_70.verdict == AgentVerdict.FAIL


def test_velocity_flag_activation():
    """Verifies velocity_anomaly_detected activates on 48h resubmissions or 30d frequency."""
    claims_out = build_mock_claims_output()

    # 48h resubmission only
    ctx_48h = FraudEvaluationContext(resubmission_count_48h=3, member_claim_count_30d=1)
    res_48h = evaluate_claim_fraud(claims_out, context=ctx_48h)
    assert res_48h.velocity_anomaly_detected is True

    # 30d frequency only
    ctx_30d = FraudEvaluationContext(resubmission_count_48h=1, member_claim_count_30d=5)
    res_30d = evaluate_claim_fraud(claims_out, context=ctx_30d)
    assert res_30d.velocity_anomaly_detected is True

    # Neither
    ctx_clean = FraudEvaluationContext(resubmission_count_48h=1, member_claim_count_30d=1)
    res_clean = evaluate_claim_fraud(claims_out, context=ctx_clean)
    assert res_clean.velocity_anomaly_detected is False

