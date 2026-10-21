"""Confidence Calibration, Reliability Diagrams, and Empirical Uncertainty Metrics (ADR-017, Spec 12).

Provides mathematically grounded calibration metrics:
- Brier Score
- Expected Calibration Error (ECE) and Maximum Calibration Error (MCE)
- Equal-width and Equal-frequency (quantile) reliability binning
- Selective classification / coverage vs accuracy curves
- Platt Scaling (logistic sigmoid) and Isotonic Regression (PAVA) calibrators
Implemented in pure NumPy without heavy external ML dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np


@dataclass
class CalibrationBin:
    """Statistical summary for a single confidence partition."""

    bin_index: int
    lower_bound: float
    upper_bound: float
    mean_predicted_confidence: float
    empirical_accuracy: float
    sample_count: int
    weight: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "bin_index": self.bin_index,
            "lower_bound": round(float(self.lower_bound), 4),
            "upper_bound": round(float(self.upper_bound), 4),
            "mean_predicted_confidence": round(float(self.mean_predicted_confidence), 4),
            "empirical_accuracy": round(float(self.empirical_accuracy), 4),
            "sample_count": int(self.sample_count),
            "weight": round(float(self.weight), 4),
        }


@dataclass
class ReliabilityCurveData:
    """Full reliability diagram dataset including calibration errors and selective accuracy."""

    strategy: Literal["equal_width", "equal_frequency"]
    bins: list[CalibrationBin]
    total_samples: int
    brier_score: float
    expected_calibration_error: float
    maximum_calibration_error: float
    raw_confidence_mean: float
    calibrated_confidence_mean: float | None = None
    selective_risk_coverage: list[dict[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "total_samples": self.total_samples,
            "brier_score": round(float(self.brier_score), 4),
            "expected_calibration_error": round(float(self.expected_calibration_error), 4),
            "maximum_calibration_error": round(float(self.maximum_calibration_error), 4),
            "raw_confidence_mean": round(float(self.raw_confidence_mean), 4),
            "calibrated_confidence_mean": round(float(self.calibrated_confidence_mean), 4) if self.calibrated_confidence_mean is not None else None,
            "bins": [b.to_dict() for b in self.bins],
            "selective_risk_coverage": self.selective_risk_coverage,
        }


def compute_brier_score(y_true: list[int] | np.ndarray, y_prob: list[float] | np.ndarray) -> float:
    """
    Computes the standard Brier Score: BS = (1/N) * sum((y_prob_i - y_true_i)^2).
    Ranges from 0.0 (perfect probabilistic forecast) to 1.0 (completely inverted forecast).
    """
    y_t = np.asarray(y_true, dtype=np.float64)
    y_p = np.clip(np.asarray(y_prob, dtype=np.float64), 0.0, 1.0)
    if len(y_t) == 0:
        return 0.0
    return float(np.mean((y_p - y_t) ** 2))


def compute_expected_calibration_error(
    y_true: list[int] | np.ndarray,
    y_prob: list[float] | np.ndarray,
    n_bins: int = 10,
    strategy: Literal["equal_width", "equal_frequency"] = "equal_width",
) -> tuple[float, float, list[CalibrationBin]]:
    """
    Computes Expected Calibration Error (ECE) and Maximum Calibration Error (MCE).
    ECE = sum_m (|B_m| / N) * |acc(B_m) - conf(B_m)|
    MCE = max_m |acc(B_m) - conf(B_m)|
    """
    y_t = np.asarray(y_true, dtype=np.float64)
    y_p = np.clip(np.asarray(y_prob, dtype=np.float64), 0.0, 1.0)
    n = len(y_t)
    if n == 0:
        return 0.0, 0.0, []

    bins_list: list[CalibrationBin] = []
    ece = 0.0
    mce = 0.0

    if strategy == "equal_width":
        bin_edges = np.linspace(0.0, 1.0, n_bins + 1)
    else:
        # Quantile binning for equal-frequency
        quantiles = np.linspace(0.0, 1.0, n_bins + 1)
        bin_edges = np.percentile(y_p, quantiles * 100.0)
        bin_edges[0] = 0.0
        bin_edges[-1] = 1.0
        # Ensure monotonically increasing edges
        bin_edges = np.maximum.accumulate(bin_edges)

    for i in range(n_bins):
        low = bin_edges[i]
        high = bin_edges[i + 1]

        if i == n_bins - 1:
            idx = (y_p >= low) & (y_p <= high)
        else:
            idx = (y_p >= low) & (y_p < high)

        count = int(np.sum(idx))
        if count > 0:
            bin_conf = float(np.mean(y_p[idx]))
            bin_acc = float(np.mean(y_t[idx]))
            weight = count / n
            diff = abs(bin_acc - bin_conf)
            ece += weight * diff
            mce = max(mce, diff)
        else:
            bin_conf = float((low + high) / 2)
            bin_acc = 0.0
            weight = 0.0

        bins_list.append(
            CalibrationBin(
                bin_index=i + 1,
                lower_bound=float(low),
                upper_bound=float(high),
                mean_predicted_confidence=bin_conf,
                empirical_accuracy=bin_acc,
                sample_count=count,
                weight=weight,
            )
        )

    return float(ece), float(mce), bins_list


def compute_selective_risk_coverage(
    y_true: list[int] | np.ndarray,
    y_prob: list[float] | np.ndarray,
    thresholds: list[float] | None = None,
) -> list[dict[str, float]]:
    """
    Computes Coverage vs. Selective Accuracy when low-confidence cases are withheld for human review.
    Coverage = Cases with confidence >= tau / Total cases
    Accuracy = Correct cases with confidence >= tau / Cases with confidence >= tau
    """
    y_t = np.asarray(y_true, dtype=np.float64)
    y_p = np.clip(np.asarray(y_prob, dtype=np.float64), 0.0, 1.0)
    n = len(y_t)
    if n == 0:
        return []

    if thresholds is None:
        thresholds = [0.0, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.98]

    curve: list[dict[str, float]] = []
    for tau in thresholds:
        accepted = y_p >= tau
        num_accepted = int(np.sum(accepted))
        if num_accepted > 0:
            coverage = num_accepted / n
            selective_acc = float(np.mean(y_t[accepted]))
        else:
            coverage = 0.0
            selective_acc = 1.0

        curve.append({
            "confidence_threshold": round(float(tau), 2),
            "coverage": round(float(coverage), 4),
            "selective_accuracy": round(float(selective_acc), 4),
            "accepted_samples": num_accepted,
        })

    return curve


def generate_reliability_diagram_data(
    y_true: list[int] | np.ndarray,
    y_prob: list[float] | np.ndarray,
    calibrated_prob: list[float] | np.ndarray | None = None,
    n_bins: int = 10,
    strategy: Literal["equal_width", "equal_frequency"] = "equal_width",
) -> ReliabilityCurveData:
    """Generates a complete reliability analysis dataset for visualization and reporting."""
    y_t = np.asarray(y_true, dtype=np.float64)
    y_p = np.clip(np.asarray(y_prob, dtype=np.float64), 0.0, 1.0)
    total_samples = len(y_t)

    brier = compute_brier_score(y_t, y_p)
    ece, mce, bins = compute_expected_calibration_error(y_t, y_p, n_bins=n_bins, strategy=strategy)
    selective_curve = compute_selective_risk_coverage(y_t, y_p)

    raw_mean = float(np.mean(y_p)) if total_samples > 0 else 0.0
    cal_mean = float(np.mean(calibrated_prob)) if calibrated_prob is not None and len(calibrated_prob) > 0 else None

    return ReliabilityCurveData(
        strategy=strategy,
        bins=bins,
        total_samples=total_samples,
        brier_score=brier,
        expected_calibration_error=ece,
        maximum_calibration_error=mce,
        raw_confidence_mean=raw_mean,
        calibrated_confidence_mean=cal_mean,
        selective_risk_coverage=selective_curve,
    )


class PlattCalibrator:
    """
    Parametric Logistic Calibration (Platt Scaling).
    Fits P(y=1|s) = 1 / (1 + exp(-(A * s + B))) via gradient descent in pure NumPy.
    Strictly fit on training/validation data; never fit on held-out test sets.
    """

    def __init__(self, learning_rate: float = 0.05, max_iter: int = 1000, l2_reg: float = 1e-4) -> None:
        self.learning_rate = learning_rate
        self.max_iter = max_iter
        self.l2_reg = l2_reg
        self.a: float = 1.0
        self.b: float = 0.0
        self.is_fitted: bool = False

    def fit(self, raw_scores: list[float] | np.ndarray, y_true: list[int] | np.ndarray) -> PlattCalibrator:
        s = np.asarray(raw_scores, dtype=np.float64)
        y = np.asarray(y_true, dtype=np.float64)

        if len(s) < 2 or len(np.unique(y)) < 2:
            self.a = 1.0
            self.b = 0.0
            self.is_fitted = True
            return self

        # Initialize parameters
        a = 1.0
        b = 0.0
        m = len(s)

        for _ in range(self.max_iter):
            z = a * s + b
            # Safe sigmoid computation
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))
            err = p - y

            grad_a = (1.0 / m) * np.sum(err * s) + self.l2_reg * a
            grad_b = (1.0 / m) * np.sum(err)

            a -= self.learning_rate * grad_a
            b -= self.learning_rate * grad_b

            if abs(grad_a) < 1e-6 and abs(grad_b) < 1e-6:
                break

        self.a = float(a)
        self.b = float(b)
        self.is_fitted = True
        return self

    def predict_proba(self, raw_scores: list[float] | np.ndarray) -> np.ndarray:
        s = np.asarray(raw_scores, dtype=np.float64)
        if not self.is_fitted:
            return np.clip(s, 0.0, 1.0)
        z = self.a * s + self.b
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30.0, 30.0)))


class IsotonicCalibrator:
    """
    Non-parametric Monotonic Calibration using Pool Adjacent Violators Algorithm (PAVA).
    Ensures that predicted probabilities are non-decreasing with respect to raw scores.
    """

    def __init__(self) -> None:
        self.breakpoints_x: np.ndarray = np.array([])
        self.breakpoints_y: np.ndarray = np.array([])
        self.is_fitted: bool = False

    def fit(self, raw_scores: list[float] | np.ndarray, y_true: list[int] | np.ndarray) -> IsotonicCalibrator:
        s = np.asarray(raw_scores, dtype=np.float64)
        y = np.asarray(y_true, dtype=np.float64)

        if len(s) == 0:
            self.is_fitted = True
            return self

        # Sort by raw score
        order = np.argsort(s)
        x_sorted = s[order]
        y_sorted = y[order]

        # PAVA implementation
        # Blocks are represented as: [sum_weights, sum_y, x_min, x_max]
        blocks: list[list[float]] = []
        for x_val, y_val in zip(x_sorted, y_sorted, strict=False):
            blocks.append([1.0, float(y_val), float(x_val), float(x_val)])
            while len(blocks) >= 2:
                prev = blocks[-2]
                curr = blocks[-1]
                prev_val = prev[1] / prev[0]
                curr_val = curr[1] / curr[0]

                if prev_val > curr_val:
                    # Pool adjacent violators
                    prev[0] += curr[0]
                    prev[1] += curr[1]
                    prev[3] = curr[3]
                    blocks.pop()
                else:
                    break

        bx = []
        by = []
        for b in blocks:
            val = max(0.0, min(1.0, b[1] / b[0]))
            bx.append(b[2])
            by.append(val)
            if b[3] != b[2]:
                bx.append(b[3])
                by.append(val)

        self.breakpoints_x = np.asarray(bx, dtype=np.float64)
        self.breakpoints_y = np.asarray(by, dtype=np.float64)
        self.is_fitted = True
        return self

    def predict_proba(self, raw_scores: list[float] | np.ndarray) -> np.ndarray:
        s = np.asarray(raw_scores, dtype=np.float64)
        if not self.is_fitted or len(self.breakpoints_x) == 0:
            return np.clip(s, 0.0, 1.0)
        # Linear interpolation between breakpoints
        interp_res = np.interp(s, self.breakpoints_x, self.breakpoints_y, left=self.breakpoints_y[0], right=self.breakpoints_y[-1])
        return np.asarray(interp_res, dtype=np.float64)
