"""Unit tests for the Deterministic Fraud Detection Engine (FEAT-004, Spec 10, ADR-005)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    KYCAgentOutput,
    RiskLevel,
    ServiceLine,
)
from medishield.domain.fraud import (
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
        service_lines=[
            ServiceLine(
                line_number=1,
                hcpcs=ProvenanceValue(value="99213", confidence=0.99),
                amount=ProvenanceValue(value=Decimal(str(line_sum)), confidence=0.99),
                units=ProvenanceValue(value=1.0, confidence=0.99),
                service_date=ProvenanceValue(value=svc_date, confidence=0.99),
            )
        ],
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


# ── 1. Binary Feature Contract Tests ────────────────────────────────────────

def test_each_feature_has_binary_detection_without_weights():
    feature = calculate_duplicate_claim_feature("CLM-01")
    assert feature.detected is True
    assert feature.raw_value == 1.0
    assert "weight" not in feature.model_dump()
    assert "normalized_score" not in feature.model_dump()


# ── 2. Feature-by-Feature Calculation Tests ───────────────────────────────────

def test_duplicate_claim_feature():
    """F-01: duplicate_claim_key logic."""
    unique_feat = calculate_duplicate_claim_feature(None)
    assert unique_feat.detected is False
    assert unique_feat.raw_value == 0.0

    dup_feat = calculate_duplicate_claim_feature("CLM-HIST-9901")
    assert dup_feat.detected is True
    assert dup_feat.raw_value == 1.0
    assert "CLM-HIST-9901" in dup_feat.description


def test_image_tamper_feature():
    """F-02: image_tamper_anomaly logic."""
    clean_feat = calculate_image_tamper_feature(0.0)
    assert clean_feat.detected is False

    tamper_feat = calculate_image_tamper_feature(0.75)
    assert tamper_feat.detected is True
    assert tamper_feat.raw_value == 0.75

    clamped_high = calculate_image_tamper_feature(1.4)
    assert clamped_high.detected is True

    clamped_low = calculate_image_tamper_feature(-0.5)
    assert clamped_low.detected is False


def test_rapid_velocity_feature():
    """F-03: rapid_resubmission_velocity logic."""
    assert calculate_rapid_velocity_feature(1).detected is False
    assert calculate_rapid_velocity_feature(0).detected is False
    assert calculate_rapid_velocity_feature(2).detected is True
    assert calculate_rapid_velocity_feature(5).detected is True


def test_provider_billing_outlier_feature():
    """F-04: provider_billing_outlier Z-score logic."""
    # Within normal variation (Z <= 2.0)
    feat_normal = calculate_provider_billing_outlier_feature(
        claim_amount=140.0, mean_charge=100.0, std_charge=20.0
    )
    assert feat_normal.raw_value == 2.0
    assert feat_normal.detected is False

    # Any value above the statistical threshold is a binary signal.
    feat_moderate = calculate_provider_billing_outlier_feature(
        claim_amount=170.0, mean_charge=100.0, std_charge=20.0
    )
    assert feat_moderate.raw_value == 3.5
    assert feat_moderate.detected is True

    # Extreme outlier: amount=220 -> Z = 6.0 -> (6.0 - 2.0)/3.0 = 1.33 -> clamped to 1.0
    feat_extreme = calculate_provider_billing_outlier_feature(
        claim_amount=220.0, mean_charge=100.0, std_charge=20.0
    )
    assert feat_extreme.raw_value == 6.0
    assert feat_extreme.detected is True

    # Missing benchmark statistics -> safe 0.0
    feat_missing = calculate_provider_billing_outlier_feature(
        claim_amount=500.0, mean_charge=None, std_charge=None
    )
    assert feat_missing.detected is False
    assert feat_missing.raw_value == 0.0

    # Zero standard deviation -> safe 0.0
    feat_zero_std = calculate_provider_billing_outlier_feature(
        claim_amount=500.0, mean_charge=100.0, std_charge=0.0
    )
    assert feat_zero_std.detected is False


def test_service_date_inconsistency_feature():
    """F-05: service_date_inconsistency logic."""
    today = date(2026, 10, 1)

    # Valid chronological date: 15 days ago -> 0.0
    valid_date = date(2026, 9, 16)
    feat_valid = calculate_service_date_inconsistency_feature(valid_date, today)
    assert feat_valid.detected is False

    # Future date: 5 days in future -> 1.0
    future_date = date(2026, 10, 6)
    feat_future = calculate_service_date_inconsistency_feature(future_date, today)
    assert feat_future.detected is True
    assert "Future service date" in feat_future.description

    # Stale date: 400 days in the past -> 0.5
    stale_date = date(2025, 8, 27)
    feat_stale = calculate_service_date_inconsistency_feature(stale_date, today)
    assert feat_stale.detected is True
    assert "Stale service date" in feat_stale.description

    # Missing date -> 0.0
    feat_none = calculate_service_date_inconsistency_feature(None, today)
    assert feat_none.detected is False


def test_line_total_arithmetic_drift_feature():
    """F-06: line_total_arithmetic_drift percentage drift logic."""
    # Zero drift: total 500, lines sum 500 -> 0.0
    feat_exact = calculate_line_total_arithmetic_drift_feature(500.0, 500.0)
    assert feat_exact.detected is False

    # Any verified non-zero drift is a binary signal.
    feat_drift = calculate_line_total_arithmetic_drift_feature(500.0, 475.0)
    assert feat_drift.raw_value == 0.05
    assert feat_drift.detected is True

    # 15% drift: total 500, lines sum 425 -> delta = 75/500 = 0.15 -> clamped to 1.0
    feat_high_drift = calculate_line_total_arithmetic_drift_feature(500.0, 425.0)
    assert feat_high_drift.raw_value == 0.15
    assert feat_high_drift.detected is True

    # Zero total amount -> safe 0.0
    feat_zero_total = calculate_line_total_arithmetic_drift_feature(0.0, 0.0)
    assert feat_zero_total.detected is False

    feat_incomplete = calculate_line_total_arithmetic_drift_feature(
        500.0,
        400.0,
        extraction_complete=False,
    )
    assert feat_incomplete.detected is False
    assert "incomplete" in feat_incomplete.description.lower()


def test_member_frequency_30d_feature():
    """F-07: member_claim_frequency_30d baseline exceeding logic."""
    assert calculate_member_frequency_30d_feature(1).detected is False
    assert calculate_member_frequency_30d_feature(3).detected is False
    assert calculate_member_frequency_30d_feature(4).detected is True
    assert calculate_member_frequency_30d_feature(15).detected is True


# ── 3. Engine End-to-End & Scenario Tests ─────────────────────────────────────

def test_clean_claim_has_no_fraud_review_signal():
    """Clean claim has a zero binary signal and no fraud review requirement."""
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
    assert result.fraud_review_required is False
    assert result.risk_level == RiskLevel.LOW
    assert result.verdict == AgentVerdict.PASS
    assert result.duplicate_claim_detected is False
    assert result.duplicate_claim_id is None
    assert result.velocity_anomaly_detected is False
    assert len(result.anomalies) == 0
    assert len(result.features) == 7


def test_duplicate_claim_alone_triggers_fraud_review():
    """A single detected factor triggers review without summing weights."""
    claims_out = build_mock_claims_output()
    kyc_out = build_mock_kyc_output(tamper_score=0.0)
    ctx = FraudEvaluationContext(duplicate_claim_match_id="CLM-PRIOR-4482")

    result = evaluate_claim_fraud(claims_out, kyc_out, ctx)

    assert result.fraud_score == 1.0
    assert result.fraud_review_required is True
    assert result.risk_level == RiskLevel.REVIEW_REQUIRED
    assert result.verdict == AgentVerdict.INDETERMINATE
    assert result.duplicate_claim_detected is True
    assert result.duplicate_claim_id == "CLM-PRIOR-4482"
    assert len(result.anomalies) == 1
    assert "CLM-PRIOR-4482" in result.anomalies[0]


def test_any_single_factor_triggers_fraud_review():
    """Each independent factor triggers review on its own, regardless of magnitude."""
    ordinary_claim = build_mock_claims_output()
    future_claim = build_mock_claims_output(service_date=datetime.now(UTC).date() + timedelta(days=7))
    drifted_claim = build_mock_claims_output(total_amount=500.0, line_sum=499.0)
    cases = [
        FraudDetectionEngine.evaluate(
            ordinary_claim,
            context=FraudEvaluationContext(duplicate_claim_match_id="CLM-01"),
        ),
        FraudDetectionEngine.evaluate(ordinary_claim, build_mock_kyc_output(tamper_score=0.30)),
        FraudDetectionEngine.evaluate(
            ordinary_claim,
            context=FraudEvaluationContext(resubmission_count_48h=2),
        ),
        FraudDetectionEngine.evaluate(
            build_mock_claims_output(total_amount=130.0, line_sum=130.0),
            context=FraudEvaluationContext(cpt_benchmark_mean=100.0, cpt_benchmark_std=10.0),
        ),
        FraudDetectionEngine.evaluate(
            future_claim,
            context=FraudEvaluationContext(submission_date=datetime.now(UTC).date()),
        ),
        FraudDetectionEngine.evaluate(drifted_claim),
        FraudDetectionEngine.evaluate(
            ordinary_claim,
            context=FraudEvaluationContext(member_claim_count_30d=4),
        ),
    ]
    assert len(cases) == 7
    for result in cases:
        assert result.fraud_score == 1.0
        assert result.fraud_review_required is True
        assert result.risk_level == RiskLevel.REVIEW_REQUIRED
        assert result.verdict == AgentVerdict.INDETERMINATE
        assert result.anomalies


def test_multiple_factors_keep_binary_review_signal():
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

    assert result.fraud_score == 1.0
    assert result.fraud_review_required is True
    assert result.risk_level == RiskLevel.REVIEW_REQUIRED
    assert result.verdict == AgentVerdict.INDETERMINATE
    assert result.duplicate_claim_detected is True
    assert len(result.anomalies) >= 4


def test_no_detected_factors_yield_zero_and_low_risk():
    result = FraudDetectionEngine.evaluate(
        build_mock_claims_output(),
        build_mock_kyc_output(tamper_score=0.0),
    )
    assert result.fraud_score == 0.0
    assert result.fraud_review_required is False
    assert result.risk_level == RiskLevel.LOW
    assert result.verdict == AgentVerdict.PASS


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


def test_service_date_inconsistency_slash_format_parsing():
    """F-05: Verifies MM/DD/YYYY and MM-DD-YYYY dates are parsed accurately."""
    from medishield.domain.fraud import _extract_date_value

    parsed_slash = _extract_date_value("10/11/2025")
    assert parsed_slash == date(2025, 10, 11)

    parsed_dash = _extract_date_value("03-28-2025")
    assert parsed_dash == date(2025, 3, 28)

    # Future date with slash format
    claims_out = build_mock_claims_output()
    claims_out.service_date = ProvenanceValue(value="12/31/2030", confidence=0.99)
    ctx = FraudEvaluationContext(submission_date=date(2025, 1, 1))

    res = evaluate_claim_fraud(claims_out, context=ctx)
    f5 = next(f for f in res.features if f.feature_name == "service_date_inconsistency")
    assert f5.detected is True
    assert "Future service date detected" in f5.description

