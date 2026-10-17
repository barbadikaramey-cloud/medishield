"""Deterministic, explainable fraud detection engine for MediShield (Spec 10, ADR-005)."""

import time
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from medishield.db.enums import AgentVerdict
from medishield.domain.contracts import (
    ClaimsAgentOutput,
    FraudAgentOutput,
    FraudFeature,
    KYCAgentOutput,
    RiskLevel,
)
from medishield.domain.provenance import ProvenanceValue

# ── Feature Weights (Canonical Spec 10 § 2.1) ─────────────────────────────────
# Sum of weights strictly equals 1.00
WEIGHT_DUPLICATE_CLAIM_KEY = 0.30
WEIGHT_IMAGE_TAMPER_ANOMALY = 0.20
WEIGHT_RAPID_RESUBMISSION_VELOCITY = 0.15
WEIGHT_PROVIDER_BILLING_OUTLIER = 0.15
WEIGHT_SERVICE_DATE_INCONSISTENCY = 0.10
WEIGHT_LINE_TOTAL_ARITHMETIC_DRIFT = 0.05
WEIGHT_MEMBER_CLAIM_FREQUENCY_30D = 0.05

FEATURE_WEIGHTS = {
    "duplicate_claim_key": WEIGHT_DUPLICATE_CLAIM_KEY,
    "image_tamper_anomaly": WEIGHT_IMAGE_TAMPER_ANOMALY,
    "rapid_resubmission_velocity": WEIGHT_RAPID_RESUBMISSION_VELOCITY,
    "provider_billing_outlier": WEIGHT_PROVIDER_BILLING_OUTLIER,
    "service_date_inconsistency": WEIGHT_SERVICE_DATE_INCONSISTENCY,
    "line_total_arithmetic_drift": WEIGHT_LINE_TOTAL_ARITHMETIC_DRIFT,
    "member_claim_frequency_30d": WEIGHT_MEMBER_CLAIM_FREQUENCY_30D,
}

# ── Risk Tiering Thresholds (Canonical Spec 10 § 3.1) ─────────────────────────
FRAUD_THRESHOLD_LOW = 0.30     # score < 0.30 -> LOW (PASS)
FRAUD_THRESHOLD_HIGH = 0.70    # score >= 0.70 -> HIGH (FAIL)


class FraudEvaluationContext(BaseModel):
    """External relational database & statistical context for claim fraud evaluation."""

    duplicate_claim_match_id: str | None = Field(
        default=None,
        description="ID of matched duplicate historical claim if hash collision occurred",
    )
    resubmission_count_48h: int = Field(
        default=1,
        ge=0,
        description="Total claim submissions for member within past 48 hours (including current)",
    )
    member_claim_count_30d: int = Field(
        default=1,
        ge=0,
        description="Total claim submissions for member within past 30 days (including current)",
    )
    cpt_benchmark_mean: float | None = Field(
        default=None,
        ge=0.0,
        description="Historical mean charge for CPT code across all providers",
    )
    cpt_benchmark_std: float | None = Field(
        default=None,
        ge=0.0,
        description="Historical standard deviation of charges for CPT code across all providers",
    )
    submission_date: date | None = Field(
        default=None,
        description="Date of claim intake (defaults to current date)",
    )
    discharge_date: date | None = Field(
        default=None,
        description="Date of hospital discharge from discharge summary",
    )
    dates_reconciled: bool = Field(
        default=False,
        description="True if claim service date was reconciled against discharge summary",
    )


# ── Individual Feature Calculations ───────────────────────────────────────────

def calculate_duplicate_claim_feature(duplicate_match_id: str | None) -> FraudFeature:
    """
    F-01: Hash collision of (member_id, provider_npi, service_date, total_amount)
    against historical claims table.
    Score: 1.0 if match exists, 0.0 if unique.
    """
    is_duplicate = duplicate_match_id is not None
    normalized_score = 1.0 if is_duplicate else 0.0
    return FraudFeature(
        feature_name="duplicate_claim_key",
        raw_value=1.0 if is_duplicate else 0.0,
        normalized_score=normalized_score,
        weight=WEIGHT_DUPLICATE_CLAIM_KEY,
        description=(
            f"Collision detected with historical claim '{duplicate_match_id}'"
            if is_duplicate
            else "Unique claim tuple; no collision detected"
        ),
    )


def calculate_image_tamper_feature(kyc_tamper_score: float) -> FraudFeature:
    """
    F-02: Visual tamper score from Error Level Analysis (ELA) and font inconsistency check.
    Normalized score in [0.0, 1.0].
    """
    score_clamped = min(1.0, max(0.0, float(kyc_tamper_score)))
    return FraudFeature(
        feature_name="image_tamper_anomaly",
        raw_value=float(kyc_tamper_score),
        normalized_score=score_clamped,
        weight=WEIGHT_IMAGE_TAMPER_ANOMALY,
        description=(
            f"Image tamper analysis reported anomaly score {score_clamped:.2f}"
            if score_clamped > 0.0
            else "Document image passed visual tamper & ELA integrity checks"
        ),
    )


def calculate_rapid_velocity_feature(submissions_48h: int) -> FraudFeature:
    """
    F-03: Count of claims submitted by the same member within the past 48 hours (N_48h).
    Formula: min(1.0, (N_48h - 1) / 4) if N_48h > 1 else 0.0.
    """
    if submissions_48h > 1:
        normalized = min(1.0, (submissions_48h - 1) / 4.0)
    else:
        normalized = 0.0

    return FraudFeature(
        feature_name="rapid_resubmission_velocity",
        raw_value=float(submissions_48h),
        normalized_score=round(normalized, 4),
        weight=WEIGHT_RAPID_RESUBMISSION_VELOCITY,
        description=(
            f"Member submitted {submissions_48h} claims within 48 hours"
            if submissions_48h > 1
            else "Standard submission velocity within 48 hours"
        ),
    )


def calculate_provider_billing_outlier_feature(
    claim_amount: float,
    mean_charge: float | None,
    std_charge: float | None,
) -> FraudFeature:
    """
    F-04: Z-score of provider claim amount relative to median/mean charge for CPT code:
    Z = (Amount - mean) / std.
    Formula: min(1.0, max(0.0, Z - 2.0) / 3.0).
    """
    if mean_charge is None or std_charge is None or std_charge <= 0.0:
        z_score = 0.0
        normalized = 0.0
        desc = "No benchmark provider charge distribution available for CPT code"
    else:
        z_score = (claim_amount - mean_charge) / std_charge
        excess_z = max(0.0, z_score - 2.0)
        normalized = min(1.0, excess_z / 3.0)
        if normalized > 0.0:
            desc = (
                f"Provider charge ${claim_amount:,.2f} is an outlier "
                f"(Z-score: {z_score:.2f}, baseline mean: ${mean_charge:,.2f}, std: ${std_charge:,.2f})"
            )
        else:
            desc = f"Provider charge ${claim_amount:,.2f} falls within normal statistical range (Z-score: {z_score:.2f})"

    return FraudFeature(
        feature_name="provider_billing_outlier",
        raw_value=round(z_score, 4),
        normalized_score=round(normalized, 4),
        weight=WEIGHT_PROVIDER_BILLING_OUTLIER,
        description=desc,
    )


def calculate_service_date_inconsistency_feature(
    service_date: date | None,
    submission_date: date | None,
    discharge_date: date | None = None,
    dates_reconciled: bool = False,
) -> FraudFeature:
    """
    F-05: Date of service occurs in the future relative to submission date,
    or precedes policy inception / current date by > 365 days.
    Score: 1.0 if future date; 0.5 if > 365 days stale; 0.0 if valid.
    Reconciles against discharge summary dates across differing date formats
    before concluding that a claim is stale.
    """
    if service_date is None:
        return FraudFeature(
            feature_name="service_date_inconsistency",
            raw_value=0.0,
            normalized_score=0.0,
            weight=WEIGHT_SERVICE_DATE_INCONSISTENCY,
            description="Service date not available for chronological evaluation",
        )

    # If dates were reconciled against discharge summary or align within inpatient stay window,
    # the claim is chronologically valid and confirmed not stale
    if dates_reconciled or (discharge_date and abs((discharge_date - service_date).days) <= 60):
        return FraudFeature(
            feature_name="service_date_inconsistency",
            raw_value=0.0,
            normalized_score=0.0,
            weight=WEIGHT_SERVICE_DATE_INCONSISTENCY,
            description=f"Service date {service_date} chronologically reconciles with hospital discharge documentation ({discharge_date or 'verified'})",
        )

    ref_date = submission_date or (discharge_date if discharge_date else datetime.now(UTC).date())
    days_delta = (ref_date - service_date).days

    if service_date > ref_date:
        # Future service date: critical anomaly
        normalized = 1.0
        raw_val = float((service_date - ref_date).days)
        desc = f"Future service date detected: {service_date} occurs after submission date {ref_date}"
    elif days_delta > 365 and submission_date is not None:
        # Stale claim older than 365 days relative to explicit submission date
        normalized = 0.5
        raw_val = float(days_delta)
        desc = f"Stale service date detected: {service_date} is {days_delta} days prior to submission"
    elif days_delta > 365 and submission_date is None:
        normalized = 0.0
        raw_val = 0.0
        desc = f"Service date {service_date} is chronologically valid"
    else:
        normalized = 0.0
        raw_val = 0.0
        desc = f"Service date {service_date} is chronologically valid"

    return FraudFeature(
        feature_name="service_date_inconsistency",
        raw_value=raw_val,
        normalized_score=normalized,
        weight=WEIGHT_SERVICE_DATE_INCONSISTENCY,
        description=desc,
    )


def calculate_line_total_arithmetic_drift_feature(
    total_amount: float,
    line_sum: float,
) -> FraudFeature:
    """
    F-06: Percentage discrepancy between declared total claim amount and sum of line charges:
    delta = |Total - sum(Lines)| / Total.
    Formula: min(1.0, delta / 0.10).
    """
    if total_amount <= 0.0:
        drift = 0.0
        normalized = 0.0
        desc = "Declared claim amount is zero or negative; drift calculation bypassed"
    else:
        drift = abs(total_amount - line_sum) / total_amount
        normalized = min(1.0, drift / 0.10)
        if normalized > 0.0:
            drift_pct = drift * 100.0
            desc = (
                f"Line items arithmetic discrepancy: sum (${line_sum:,.2f}) drifts from "
                f"declared total (${total_amount:,.2f}) by {drift_pct:.2f}%"
            )
        else:
            desc = "Declared claim total perfectly reconciles with itemized service line charges"

    return FraudFeature(
        feature_name="line_total_arithmetic_drift",
        raw_value=round(drift, 4),
        normalized_score=round(normalized, 4),
        weight=WEIGHT_LINE_TOTAL_ARITHMETIC_DRIFT,
        description=desc,
    )


def calculate_member_frequency_30d_feature(submissions_30d: int) -> FraudFeature:
    """
    F-07: Total claim count for member in past 30 days (N_30d) exceeding standard baseline of 3:
    Formula: min(1.0, max(0, N_30d - 3) / 5).
    """
    excess_claims = max(0, submissions_30d - 3)
    normalized = min(1.0, excess_claims / 5.0)

    return FraudFeature(
        feature_name="member_claim_frequency_30d",
        raw_value=float(submissions_30d),
        normalized_score=round(normalized, 4),
        weight=WEIGHT_MEMBER_CLAIM_FREQUENCY_30D,
        description=(
            f"Elevated 30-day claim utilization: {submissions_30d} claims submitted in last 30 days"
            if submissions_30d > 3
            else f"Standard 30-day claim utilization: {submissions_30d} claim(s) submitted"
        ),
    )


# ── Helper to Coerce Clean Types ──────────────────────────────────────────────

def _extract_numeric_value(val: Any, default: float = 0.0) -> float:
    """Safely extracts a float from ProvenanceValue, Decimal, int, float, or numeric string."""
    if isinstance(val, ProvenanceValue):
        val = val.value

    if val is None:
        return default
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val.replace("$", "").replace(",", "").strip())
        except ValueError:
            return default
    return default


def _extract_date_value(val: Any) -> date | None:
    """Safely extracts a date from ProvenanceValue, date, datetime, ISO string, or slash/hyphen/textual formats."""
    if isinstance(val, ProvenanceValue):
        val = val.value

    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        from medishield.domain.faithfulness import parse_medical_date
        return parse_medical_date(val)
    return None


# ── Fraud Detection Engine ───────────────────────────────────────────────────

class FraudDetectionEngine:
    """
    Authoritative, deterministic healthcare insurance fraud adjudicator.
    Complies with Canonical Spec 10, ADR-003, and ADR-005.
    Zero generative AI hallucinations. Zero access to protected demographic attributes.
    """

    @classmethod
    def evaluate(
        cls,
        claims_output: ClaimsAgentOutput,
        kyc_output: KYCAgentOutput | None = None,
        context: FraudEvaluationContext | None = None,
        start_time: float | None = None,
    ) -> FraudAgentOutput:
        """
        Computes the 7 canonical fraud features, weighted composite score, and risk tier.
        """
        t0 = start_time if start_time is not None else time.perf_counter()
        ctx = context or FraudEvaluationContext()

        # 1. Coerce input values
        total_amount = _extract_numeric_value(claims_output.claim_amount)
        calculated_line_sum = float(claims_output.calculated_lines_sum)
        service_date = _extract_date_value(claims_output.service_date)
        kyc_tamper_score = float(kyc_output.tamper_score) if kyc_output else 0.0

        # 2. Compute the 7 canonical features
        f1 = calculate_duplicate_claim_feature(ctx.duplicate_claim_match_id)
        f2 = calculate_image_tamper_feature(kyc_tamper_score)
        f3 = calculate_rapid_velocity_feature(ctx.resubmission_count_48h)
        f4 = calculate_provider_billing_outlier_feature(
            claim_amount=total_amount,
            mean_charge=ctx.cpt_benchmark_mean,
            std_charge=ctx.cpt_benchmark_std,
        )
        f5 = calculate_service_date_inconsistency_feature(
            service_date=service_date,
            submission_date=ctx.submission_date,
            discharge_date=ctx.discharge_date,
            dates_reconciled=ctx.dates_reconciled,
        )
        f6 = calculate_line_total_arithmetic_drift_feature(
            total_amount=total_amount,
            line_sum=calculated_line_sum,
        )
        f7 = calculate_member_frequency_30d_feature(ctx.member_claim_count_30d)

        features = [f1, f2, f3, f4, f5, f6, f7]

        # 3. Compute composite weighted fraud score: sum(w_i * f_i)
        composite_score = sum(f.normalized_score * f.weight for f in features)
        composite_score = min(1.0, max(0.0, composite_score))
        rounded_score = round(composite_score, 4)

        # 4. Map score to calibrated risk tier and agent verdict (Spec 10 § 3)
        if rounded_score < FRAUD_THRESHOLD_LOW:
            risk_level = RiskLevel.LOW
            verdict = AgentVerdict.PASS
        elif rounded_score < FRAUD_THRESHOLD_HIGH:
            risk_level = RiskLevel.MEDIUM
            verdict = AgentVerdict.INDETERMINATE
        else:
            risk_level = RiskLevel.HIGH
            verdict = AgentVerdict.FAIL

        # 5. Extract human-readable anomaly descriptions
        anomalies: list[str] = [
            f.description
            for f in features
            if f.normalized_score > 0.0
        ]

        # 6. Flag specific indicators
        duplicate_detected = f1.normalized_score > 0.0
        duplicate_id = ctx.duplicate_claim_match_id if duplicate_detected else None
        velocity_detected = (f3.normalized_score > 0.0 or f7.normalized_score > 0.0)

        latency_ms = max(1, int((time.perf_counter() - t0) * 1000))

        return FraudAgentOutput(
            agent_name="fraud_agent",
            verdict=verdict,
            confidence=1.0,
            fraud_score=rounded_score,
            risk_level=risk_level,
            features=features,
            anomalies=anomalies,
            duplicate_claim_detected=duplicate_detected,
            duplicate_claim_id=duplicate_id,
            velocity_anomaly_detected=velocity_detected,
            latency_ms=latency_ms,
        )


def evaluate_claim_fraud(
    claims_output: ClaimsAgentOutput,
    kyc_output: KYCAgentOutput | None = None,
    context: FraudEvaluationContext | None = None,
) -> FraudAgentOutput:
    """Convenience functional interface to FraudDetectionEngine.evaluate."""
    return FraudDetectionEngine.evaluate(
        claims_output=claims_output,
        kyc_output=kyc_output,
        context=context,
    )


# Alias for canonical specification naming consistency
DeterministicFraudEngine = FraudDetectionEngine

