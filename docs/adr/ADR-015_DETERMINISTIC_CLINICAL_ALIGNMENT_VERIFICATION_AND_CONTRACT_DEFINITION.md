# Architecture Decision Record (ADR)
## ADR-015: Deterministic Clinical Alignment Verification and Contract Definition

**Status:** Accepted  
**Date:** 2026-10-16  
**Context:** Clinical Alignment Audit, Stage B Claim Line Verification, Prior Authorization, Inpatient Bed-Day Reconciliation, Straight-Through Processing Guard (Spec 01 § 4, Spec 02 § 3, Spec 05 § 4, Spec 06 § 5, ADR-001, ADR-002, ADR-005, ADR-012)  
**Deciders:** Lead Software Architect, Clinical Informatics Reviewer & Claims Decision-Engine Specialist  

---

## 1. Context and Problem Statement

MediShield audits ([`clinical_alignment_gap_audit.md`](../sdd/audits/clinical_alignment_gap_audit.md) and [`mediShield_master_gap_summary.md`](../sdd/audits/mediShield_master_gap_summary.md)) demonstrated that the platform previously performed **zero line-level clinical verification** between billed medical services (CPT/HCPCS codes, revenue codes, bed-day units) and submitted clinical documentation (discharge summaries, operative notes, physician orders, laboratory reports).

Under prior implementations:
1. **Rule P6 Auto-Approval Blindspot:** Rule P6 (`RULE-P6-CLEAN-PASS`) auto-approved claims whenever billed codes matched broad policy text and arithmetic reconciled, permitting unperformed surgeries, phantom bed days, unindicated diagnostic imaging, and services lacking mandatory prior authorization to pass directly to reimbursement.
2. **Conflation of Missing Documentation with Fraud/Rejection:** Absence of documentation for routine services was frequently conflated with fraud or hard denial, violating MediShield Rule 13 (*"Never silently convert uncertainty into failure"*).
3. **Uncalibrated Confidence Fallbacks:** Missing evidence provenance occasionally defaulted to arbitrary numeric scores (e.g. 0.70), bypassing confidence gates.
4. **Bed-Day Arithmetic Gaps:** Length of stay was calculated with rigid calendar formulas (`LOS + 1`) rather than approved facility billing rules, failing to handle date-only ambiguity safely.

---

## 2. Decision

We establish an authoritative, deterministic **Clinical Alignment Verification Contract and Engine**:

### 2.1 Pydantic Domain Contracts (`src/medishield/domain/contracts.py`)
1. **Line-Level Clinical Evidence (`LineClinicalEvidence`):**
   - Granular per-line assessment across six service modalities (`SURGICAL_PROCEDURE`, `MEDICAL_VISIT_OR_EVAL`, `DIAGNOSTIC_IMAGING`, `LABORATORY_TEST`, `PHARMACEUTICAL`, `ROOM_AND_BOARD`).
   - Disaggregates clinical state into orthogonal enums:
     - `PerformanceEvidenceStatus`: `ORDERED_ONLY`, `DIRECTLY_DOCUMENTED_PERFORMANCE`, `RESULTED`, `NOT_DOCUMENTED`, `EXPLICITLY_CONTRADICTED`, `CONFIRMED_NON_PERFORMANCE`.
     - `ClinicalIndicationStatus`: `EXPLICITLY_INDICATED`, `POTENTIALLY_RELATED`, `NO_DOCUMENTED_INDICATION`, `CLINICALLY_CONTRAINDICATED`.
     - `MedicalNecessityStatus`: `MET`, `NOT_MET`, `CRITERIA_INCOMPLETE`, `GUIDELINE_UNAVAILABLE`.
     - `PriorAuthRequirementStatus`: `NOT_REQUIRED`, `REQUIRED`, `REQUIREMENT_UNKNOWN` (mandatory, never defaulted).
     - `PriorAuthVerificationStatus`: `NOT_APPLICABLE`, `VERIFIED_APPROVED`, `VERIFIED_DENIED`, `MISSING_OR_UNVERIFIED`, `AUTH_SERVICE_UNAVAILABLE` (mandatory, never defaulted).
     - `RepeatServiceStatus`: `NOT_A_REPEATED_SERVICE`, `REPEATED_AND_JUSTIFIED`, `REPEATED_AND_UNJUSTIFIED`, `UNASSESSED` (mandatory, never defaulted).
   - Invariant validator: Enforces that `AgentVerdict.PASS` requires verified performance (`DIRECTLY_DOCUMENTED_PERFORMANCE` or `RESULTED`), explicit clinical indication, `MedicalNecessityStatus.MET` under an active, applicable guideline with all mandatory criteria satisfied and corroborated by `FieldProvenance` spans, and verified prior authorization (`VERIFIED_APPROVED` if required).

2. **Configurable Bed-Day Reconciliation (`BedDayReconciliation`):**
   - Requires an explicit, approved `BedDayBillingRuleContract` applicable to the facility type, claim type, and service date window.
   - Enforces agreement: `is_reconciled == True` iff `reconciliation_verdict == AgentVerdict.PASS`.
   - Ambiguous date-only durations ($U_{\min} \ne U_{\max}$) or missing admission/discharge dates strictly fail closed to `AgentVerdict.INDETERMINATE`.

3. **Composite Specialist Output & Factory (`ClinicalAlignmentOutput`):**
   - Enforces strict set equality between expected claim lines and assessed lines, unanimous line pass for composite `PASS`, and mandatory bed-day reconciliation if room-and-board charges exist.
   - Aggregation factory `build_clinical_alignment_output` sanitizes inputs, stubs missing lines with fail-closed entries, and derives composite confidence via canonical Spec 06 § 5:
     $$\text{Confidence} = 0.40 \times \text{Provenance Quality} + 0.35 \times \text{Validation Completeness} + 0.25 \times \text{Model Confidence}$$
     If calibration inputs are unavailable, confidence is set to `None` (unavailable).

### 2.2 Deterministic Alignment Engine (`src/medishield/domain/clinical_align.py`)
- Executes deterministic evaluation without LLM decisioning:
  - Validates CPT/HCPCS and Revenue codes against authoritative catalogs.
  - Reconciles service dates against encounter admission and discharge boundaries.
  - Evaluates multi-factor guideline criteria and bed-day arithmetic.
  - Differentiates absence of mention ($\to$ `INDETERMINATE`), unavailable guidelines ($\to$ `INDETERMINATE`), affirmative failure ($\to$ `FAIL`), and clinical contradictions ($\to$ `CONFLICT`).

### 2.3 Decision Engine Integration (`src/medishield/domain/decision.py`)
- Incorporates `clinical_output` into `DecisionInputContext`.
- Protects Rule P6 (`RULE-P6-CLEAN-PASS`): requires `clinical_output.verdict == AgentVerdict.PASS` and calibrated confidence $\ge 0.75$.
- Uncalibrated confidence (`None`) fails the P6 gate and routes safely to Rule P7 (`RULE-P7-BORDERLINE-REVIEW`, `ESCALATE(HUMAN_REVIEW)`).
- Reconciles duplicate tuple collisions ($F\text{-}01 = 0.30$) to canonical Rule P7 (`ESCALATE(HUMAN_REVIEW)`), noting boolean P4 override remains pending governance approval.

---

## 3. Consequences

### Positive:
- **Zero Hallucinated / Phantom Pass:** Unperformed procedures, unindicated tests, and unapproved bed days cannot pass straight-through to approval.
- **Strict Compliance with MediShield Rules:** Preserves provenance (Rule 12), never converts uncertainty to failure (Rule 13), and keeps all adjudication strictly in deterministic code (Rules 10, 11, 14).
- **Graceful Fail-Closed Execution:** Malformed specialist outputs, missing dates, and unverified authorization fail closed to structured human review rather than throwing unhandled exceptions.

### Negative / Trade-offs:
- Claims with missing clinical notes for minor routine services will now escalate to `AWAITING_HUMAN` rather than auto-approving, slightly increasing initial human reviewer queue volume until clinics upload complete documentation.

