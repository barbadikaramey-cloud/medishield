# ADR-021: Revenue Code Clinical Alignment and Surgical Room & Board Substantiation

## Status
Accepted

## Context
In institutional hospital billing (UB-04 / CMS-1450), claims for inpatient surgeries and major medical admissions legitimately combine:
1. Procedural CPT/HCPCS codes (e.g., CPT `27447` for Total Knee Arthroplasty, `47562` for Cholecystectomy).
2. Institutional Revenue Codes (e.g., `0120` for Semi-Private Room & Board, `0250` for Inpatient Pharmacy, `0360` for Operating Room Services).

Previously, the Clinical Alignment Engine had two critical gaps:
1. In `clinical_alignment_node`, service line codes were extracted strictly from `hcpcs`:
   `code = str(l.get("hcpcs", {}).get("value") or l.get("service_code") or "").strip()`
   When a service line contained an institutional revenue code (`rev_code`) without a CPT code, `code` resolved to `""`, causing the line to be classified as `OTHER_SERVICE` and marked unindicated.
2. In `evaluate_line_item`, line verdict derivation contained:
   `or resolved_guideline is None` $\to$ `AgentVerdict.INDETERMINATE`.
   Because LCD/NCD procedure guidelines only exist for clinical procedures (CPT), ancillary facility lines (Room & Board, Pharmacy) were unconditionally forced to `INDETERMINATE`, blocking surgical claims from ever achieving approval.

## Decision
1. **Revenue Code Awareness in Modality Classification**:
   - `classify_modality` recognizes standard UB-04 institutional revenue code ranges:
     - `0100`–`0219`: `ServiceModality.ROOM_AND_BOARD`
     - `0250`–`0259`: `ServiceModality.PHARMACEUTICAL`
     - `0300`–`0319`: `ServiceModality.LABORATORY_TEST`
     - `0320`–`0359`, `0400`–`0409`: `ServiceModality.DIAGNOSTIC_IMAGING`
     - `0360`–`0379`, `0710`, `0720`: `ServiceModality.SURGICAL_PROCEDURE`
     - `0450`–`0459`, `0510`–`0529`: `ServiceModality.MEDICAL_VISIT_OR_EVAL`
2. **Surgical Inpatient Substantiation for Ancillary Revenue Codes**:
   - In `clinical_alignment_node`, resolve `code` from `hcpcs`, `service_code`, or `rev_code`.
   - When an inpatient surgical procedure or hospital admission is documented (`has_stay`, `has_surgery`, or admit/discharge dates):
     - Room and Board charges are clinically substantiated, and their units are reconciled via `BedDayReconciliation`.
     - Inpatient Pharmacy and Operating Room charges are clinically substantiated as necessary ancillary services of the verified admission.
3. **Ancillary Medical Necessity Resolution**:
   - In `evaluate_line_item`, ancillary revenue codes substantiated by an inpatient admission or surgery do not require a separate CPT LCD/NCD guideline.
   - When performance is documented and indication is explicit, their necessity is resolved as `MedicalNecessityStatus.MET`, allowing the line verdict to pass.

## Consequences
- **Positive**:
  - Inpatient surgical claims with Room & Board and Pharmacy charges can be fully validated and clinically aligned.
  - Zero hardcoded domain lists; uses standard UB-04 format specifications and database catalog lookups.
  - Aligns with Rule 11 and Rule 12.
- **Negative / Risks**:
  - Outpatient claims incorrectly billing inpatient room and board will be properly caught and flagged as conflicting with lack of inpatient admission.

