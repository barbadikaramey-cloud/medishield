# Architecture Decision Record
## ADR-028: Clinical Uncertainty Approval with Verified-Contradiction Escalation

**Status:** Accepted  
**Date:** 2026-10-21  
**Context:** Canonical Decision Engine Specification § 3 (P3/P6), ADR-027, Clinical Alignment Contract, relational diagnosis-indication catalog  
**Deciders:** MediShield Engineering

---

## 1. Context and Problem Statement

MediShield does not yet have facility-submitted clinical performance documentation for every billed service. Treating every missing performance span, unavailable guideline, or unvalidated diagnosis mapping as a blocking P3 finding prevents otherwise admissible claims from passing, even when no evidence contradicts the claim. Conversely, inferring a hard diagnosis/procedure incompatibility from missing data or an LLM judgment is unsafe.

## 2. Decision

1. Add `CLINICAL_UNCERTAINTY_APPROVAL_ENABLED` configuration (default `true`). When enabled, clinical `INDETERMINATE` may be admissible for P6 despite zero fully supported lines if the uncertainty has no verified contradiction, failed-necessity line, unavailable line, invalid schema, insufficient OCR quality, or bed-day `CONFLICT`.
2. Existing P6 requirements for KYC, claims schema/arithmetic/coding, complete policy-code scope, fraud, calibrated core confidence, and consistency remain unchanged.
3. When the exception is used, the P6 rationale must state that clinical findings are unresolved, identify the fully-supported/assessed line counts, and preserve discrepancy descriptions in the case snapshot. The exception is not a claim that services were independently verified as performed.
4. Diagnosis-to-procedure incompatibility is a hard clinical blocker only when a validated diagnosis code matches a configured `contraindicated_prefixes` entry in the relational `diagnosis_indications` catalog. The adjudicator must not hardcode diagnosis/procedure examples or infer contraindication from lack of a matching positive indication.
5. A catalogued contraindication produces a failed clinical line and remains escalated under P3. Verified performance contradictions and bed-day conflicts also remain escalated. Missing/unknown support alone is not converted into a denial.
6. The exception only changes the clinical P3/P6 gate; it does not override other decision priorities or policy requirements.

## 3. Consequences

### Positive
- Missing facility records and unavailable clinical guidelines can be represented as an explicit approval discrepancy when all independent adjudication gates pass.
- Verified contraindication, contradictory performance, failed necessity, and bed-day conflict remain blockers.
- Approval rationale and clinical findings preserve the uncertainty for reviewer audit.

### Risks and Mitigations
- Straight-through approval may occur with incomplete clinical support. The setting is explicit and configurable, and the approval rationale must disclose the unresolved findings.
- The quality of hard contradiction detection depends on curated, effective relational diagnosis-indication rules. Missing catalog entries remain uncertainty; they do not become inferred contradictions.
- A P6 approval is not a human-reviewed confirmation that documentation was received; downstream audit/UI must display the discrepancy notice.
