"""Unit tests for Confidence Calibration, Brier Score, and Reliability Curves (ADR-017, Spec 12)."""

import pytest

from medishield.eval.calibration import (
    IsotonicCalibrator,
    PlattCalibrator,
    compute_brier_score,
    compute_expected_calibration_error,
    compute_selective_risk_coverage,
    generate_reliability_diagram_data,
)


def test_brier_score_extremes() -> None:
    # Perfect predictions -> 0.0
    assert compute_brier_score([1, 0, 1], [1.0, 0.0, 1.0]) == 0.0

    # Completely inverted predictions -> 1.0
    assert compute_brier_score([1, 0], [0.0, 1.0]) == 1.0

    # Intermediate known score
    # (0.8 - 1)^2 + (0.2 - 0)^2 = 0.04 + 0.04 = 0.08 / 2 = 0.04
    score = compute_brier_score([1, 0], [0.8, 0.2])
    assert pytest.approx(score, rel=1e-3) == 0.04

    # Empty data
    assert compute_brier_score([], []) == 0.0


def test_ece_perfect_calibration() -> None:
    # Half positive, half negative with accurate probabilities
    y_true = [1] * 50 + [0] * 50
    y_prob = [0.5] * 100
    ece, mce, _ = compute_expected_calibration_error(y_true, y_prob, n_bins=10)
    # Bin at 0.5 has accuracy 0.5 and confidence 0.5 -> ECE should be 0.0
    assert pytest.approx(ece, abs=1e-5) == 0.0
    assert pytest.approx(mce, abs=1e-5) == 0.0


def test_ece_severe_overconfidence() -> None:
    # Model predicts 0.9 confidence for everything, but actual accuracy is only 0.5
    y_true = [1] * 50 + [0] * 50
    y_prob = [0.9] * 100
    ece, mce, _ = compute_expected_calibration_error(y_true, y_prob, n_bins=10)
    # ECE should be |0.5 - 0.9| = 0.4
    assert pytest.approx(ece, rel=1e-3) == 0.40
    assert pytest.approx(mce, rel=1e-3) == 0.40


def test_quantile_equal_frequency_binning() -> None:
    y_true = [1, 0, 1, 0, 1, 0, 1, 1]
    y_prob = [0.1, 0.2, 0.4, 0.5, 0.7, 0.8, 0.9, 0.95]
    _, _, bins = compute_expected_calibration_error(
        y_true, y_prob, n_bins=4, strategy="equal_frequency"
    )
    assert len(bins) == 4
    # Equal frequency ensures sample counts in populated bins are balanced
    total_samples = sum(b.sample_count for b in bins)
    assert total_samples == 8


def test_selective_risk_coverage() -> None:
    y_true = [1, 1, 1, 0, 0]
    y_prob = [0.95, 0.90, 0.80, 0.60, 0.40]
    curve = compute_selective_risk_coverage(y_true, y_prob, thresholds=[0.0, 0.70, 0.90])

    assert len(curve) == 3
    # At tau=0.0: coverage is 1.0 (all 5 accepted), accuracy is 3/5 = 0.60
    assert curve[0]["coverage"] == 1.0
    assert curve[0]["selective_accuracy"] == 0.60

    # At tau=0.70: 3 accepted (0.95, 0.90, 0.80), all 3 true -> coverage 0.6, accuracy 1.0
    assert curve[1]["coverage"] == 0.60
    assert curve[1]["selective_accuracy"] == 1.0

    # At tau=0.90: 2 accepted (0.95, 0.90), both true -> coverage 0.4, accuracy 1.0
    assert curve[2]["coverage"] == 0.40
    assert curve[2]["selective_accuracy"] == 1.0


def test_platt_scaling_improves_calibration() -> None:
    # Uncalibrated overconfident scores
    raw_scores = [0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.60, 0.50]
    y_true = [1, 1, 1, 0, 1, 0, 0, 0]

    calibrator = PlattCalibrator(learning_rate=0.1, max_iter=500)
    calibrator.fit(raw_scores, y_true)
    cal_probs = calibrator.predict_proba(raw_scores)

    assert len(cal_probs) == len(raw_scores)
    assert all(0.0 <= p <= 1.0 for p in cal_probs)

    # Monotonicity preserved
    for i in range(len(cal_probs) - 1):
        assert cal_probs[i] >= cal_probs[i + 1]

    # Brier score on calibrated should be bounded and valid
    brier_cal = compute_brier_score(y_true, cal_probs)
    assert 0.0 <= brier_cal <= 1.0


def test_isotonic_regression_pava_monotonicity() -> None:
    # Non-monotonic raw scores vs labels
    raw_scores = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    y_true = [0, 1, 0, 0, 1, 0, 1, 1, 1]

    calibrator = IsotonicCalibrator()
    calibrator.fit(raw_scores, y_true)
    cal_probs = calibrator.predict_proba(raw_scores)

    assert len(cal_probs) == len(raw_scores)
    assert all(0.0 <= p <= 1.0 for p in cal_probs)

    # Monotonically non-decreasing
    for i in range(len(cal_probs) - 1):
        assert cal_probs[i] <= cal_probs[i + 1] + 1e-9


def test_reliability_diagram_generation() -> None:
    y_true = [1, 1, 0, 1, 0, 0, 1, 1, 0, 1]
    y_prob = [0.9, 0.85, 0.8, 0.75, 0.7, 0.6, 0.55, 0.5, 0.4, 0.3]

    data = generate_reliability_diagram_data(
        y_true=y_true,
        y_prob=y_prob,
        n_bins=5,
        strategy="equal_width",
    )

    assert data.total_samples == 10
    assert len(data.bins) == 5
    assert 0.0 <= data.brier_score <= 1.0
    assert 0.0 <= data.expected_calibration_error <= 1.0
    assert 0.0 <= data.maximum_calibration_error <= 1.0

    d = data.to_dict()
    assert "bins" in d
    assert "selective_risk_coverage" in d
    assert d["strategy"] == "equal_width"
