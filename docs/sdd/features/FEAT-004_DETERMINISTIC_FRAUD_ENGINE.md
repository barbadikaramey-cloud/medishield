# Feature Specification: FEAT-004 Deterministic Explainable Fraud Engine

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-004`
- **Feature Name:** Deterministic Explainable Fraud Detection Engine
- **Phase:** Phase 3 (Fraud Detection)
- **Status:** Implemented & Verified
- **Authoritative References:**
  - `docs/canonical/10_FRAUD_DETECTION_ENGINE_SPECIFICATION.md`
  - `docs/adr/ADR-005_DETERMINISTIC_EXPLAINABLE_FRAUD_SCORING.md`
  - `AGENTS.md` (Rules 1–15)

---

## 2. Purpose
Provides deterministic fraud scoring and explainability:
1. **Mathematical Scoring:** Evaluates signals across billing, provider, identity, and duplicate metrics with calibrated weights.
2. **Deterministic Risk Buckets:** LOW (< 0.40), MEDIUM (0.40–0.69), HIGH (>= 0.70).
3. **No LLM Decision Authority:** Deterministic arithmetic guarantees explainable audit trails without hallucinated fraud scores.

