# Feature Specification: FEAT-004 Deterministic Explainable Fraud Engine

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-004`
- **Feature Name:** Deterministic Explainable Fraud Detection Engine
- **Phase:** Phase 3 (Fraud Detection)
- **Status:** Implemented & Verified
- **Authoritative References:**
  - `docs/canonical/10_FRAUD_DETECTION_ENGINE_SPECIFICATION.md`
  - `docs/adr/ADR-005_DETERMINISTIC_EXPLAINABLE_FRAUD_SCORING.md`
  - `docs/adr/ADR-033_BINARY_FRAUD_INDICATORS_AND_ANY_FACTOR_REVIEW.md`
  - `AGENTS.md` (Rules 1–15)

---

## 2. Purpose
Provides deterministic fraud screening and explainability:
1. **Binary factors:** Each detector reports yes/no while preserving its raw measurement and description.
2. **Any-factor review:** One detected factor sets `fraud_review_required=true` and routes to `ESCALATE(FRAUD_REVIEW)`; factors are not weighted or accumulated.
3. **No fraud denial authority:** Fraud findings require review and do not themselves reject a claim; the deterministic decision engine owns the final business outcome.
4. **Compatibility value:** `fraud_score` is `1.0` when review is required and `0.0` otherwise; it is not a probability or severity estimate.
5. **Incomplete billing safeguard:** Incomplete service-line extraction is not treated as confirmed arithmetic drift.

