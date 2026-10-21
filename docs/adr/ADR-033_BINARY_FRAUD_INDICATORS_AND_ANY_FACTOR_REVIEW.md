# Architecture Decision Record
## ADR-033: Binary Fraud Indicators and Any-Factor Human Review

**Status:** Accepted  
**Date:** 2026-10-21  
**Supersedes:** The weighted aggregation and risk-tier portions of ADR-005 and Canonical Spec 10  
**Related:** ADR-001, ADR-002, ADR-003, ADR-005, ADR-029

## 1. Source Requirement

The product owner requested that fraud factors not contribute fractional weights such as `0.15` or `0.20`. Each factor is a yes/no finding; if any one factor is detected, the case must be considered for fraud review.

## 2. Why This Exists

Weighted aggregation can dilute a significant individual finding below a cumulative threshold. It also presents fractional scores as though they were calibrated probabilities or severity estimates, despite the absence of validated fraud labels. A direct binary review signal is simpler to explain and does not imply a statistical confidence the system has not established.

## 3. Decision

1. The seven deterministic fraud factors each emit `detected: bool`, a raw measurement, and a human-readable description. Factor-specific measurement and detection criteria remain explicit; fractional feature weights and normalized contribution scores are removed.
2. `fraud_review_required` is true when one or more factors are detected. The decision engine routes that case to `ESCALATE / FRAUD_REVIEW` (P4), ahead of ambiguity and hard-denial rules, after P1 security/dependency handling and P2 completeness handling.
3. Fraud review is not a denial. The fraud engine emits `INDETERMINATE` when review is required and never emits the final business decision. Only verified non-fraud business rules may reject a claim.
4. For compatibility with existing API consumers, `fraud_score` remains temporarily as a binary indicator: `1.0` when review is required and `0.0` otherwise. It is not a probability, percentage, or severity score. New consumers should use `fraud_review_required`.
5. Newly computed `risk_level` is `REVIEW_REQUIRED` or `LOW`. `MEDIUM` and `HIGH` enum values remain accepted for historical snapshots and compatibility, not as current aggregation tiers.
6. The arithmetic-drift fraud factor is evaluated only when billing extraction is complete. An incomplete or ambiguous bill remains an extraction uncertainty and must not become a fraud finding solely because extracted rows fail to reconcile.

## 4. Factors

| Factor | Binary finding basis |
|---|---|
| Duplicate claim key | A duplicate tuple match is present |
| Image tamper anomaly | The deterministic image analysis reports a tamper indicator |
| Rapid resubmission velocity | The configured resubmission detector reports excess submissions |
| Provider billing outlier | The available provider/CPT comparison crosses its detector criterion |
| Service-date inconsistency | The deterministic date check identifies an applicable inconsistency |
| Line-total arithmetic drift | Complete, verified service lines disagree with the declared total |
| Member claim frequency | The configured frequency detector reports excess claims |

The factor measurements and criteria are not claimed to be fraud probabilities. Their operational purpose is to flag a case for review.

## 5. Affected Components

- `src/medishield/domain/contracts.py`: binary feature contract, explicit review flag, compatibility enum/value.
- `src/medishield/domain/fraud.py`: remove weights and composite thresholding; derive review from `any(feature.detected)`.
- `src/medishield/domain/decision.py`: route any detected factor to P4 fraud review and block P6 approval.
- `src/medishield/workflows/`: persist and report the review flag.
- Reports, deterministic explanations, benchmark views, tracing, and frontend badges: show review-required state, not fractional percentages.
- Fraud, decision, workflow, explanation, report, and API contract tests; Canonical Specs 02, 04, 05, 08, and 10; feature documentation.

No database migration is required: the new fields are carried in JSON summaries and Pydantic contracts, not new relational columns.

## 6. Acceptance Criteria

- Each of the seven factors independently yields `fraud_review_required=true`, `fraud_score=1.0`, and `risk_level=REVIEW_REQUIRED` when detected.
- A clean factor set yields `fraud_review_required=false`, `fraud_score=0.0`, `risk_level=LOW`, and verdict `PASS`.
- Any single detected factor routes to `ESCALATE / FRAUD_REVIEW` even when the former weighted sum would have been below `0.70`.
- Fraud indicators never directly yield `REJECT`.
- Incomplete bill extraction does not create an arithmetic-drift fraud finding.
- No runtime, report, UI, or benchmark copy presents the binary field as a probability or fractional weighted score.
- Fraud and decision tests pass; relevant frontend typecheck/lint and API contract checks pass.
