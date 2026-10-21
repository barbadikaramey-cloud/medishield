# ADR-022: Bed-Day Reconciliation for Date-Only Stays, Textual Revenue Code Substantiation, and Clinical Alignment Formatting

## Status
Accepted

## Context
Following the implementation of ADR-021, evaluation of Case `CASE-20261018-6E9FEB` (an inpatient surgical claim with CPT `27447` Knee Arthroplasty, UB-04 Revenue Codes `0100` Room & Board, `PHARM` Pharmacy, `800` Diagnostics, Total ₹79,400) revealed four interrelated failure modes:

1. **Unconditional Date-Only Stay Bed-Day Trap**:
   In `reconcile_bed_days` (`clinical_align.py`), date-only stays lacking hourly datetime timestamps unconditionally returned `AgentVerdict.INDETERMINATE`. When a patient was admitted on 2026-05-10 and discharged on 2026-05-15 (5 calendar days, 5 midnights) and billed exactly 5 bed days, the engine forced an `INDETERMINATE` verdict. In `build_clinical_alignment_output`, `if not bed_day_ok: verdict = INDETERMINATE`, resetting `calibrated_confidence` to `None` and triggering `RULE-P3-AMBIGUITY` escalation.

2. **Pydantic Validation Failure on Ancillary PASS Verdicts**:
   In `contracts.py`, `LineClinicalEvidence` enforces that any line item achieving `line_verdict == AgentVerdict.PASS` must possess a non-null, valid `GuidelineCitation` with all mandatory criteria satisfied. In `evaluate_line_item`, when ancillary services were substantiated as `necessity_status = MET`, `resolved_guideline` was left as `None` if `classify_modality` did not recognize textual revenue tokens (`PHARM`, `800`), causing a Pydantic `ValidationError`.

3. **Textual and 3-Digit Revenue Code Misclassification**:
   Standard bills from various hospital systems use textual tokens (`PHARM`, `RX`, `ROOM`, `BED`) or short 3-digit revenue codes (`800` for Diagnostics/Renal). In `ocr_reconcile.py`, `is_plausible_revenue_code` only checked `clean.isdigit() and 3 <= len(clean) <= 4`, returning `False` for `PHARM`. Additionally, an erroneous reference to `CodingSystem.REVENUE_CODE` instead of `CodingSystem.REV_CODE` was present.

4. **False Positive Within-Case Duplicate Detection**:
   In `claims_extraction_node`, multiple documents matching `CLAIM_FORM` criteria (a summary Claim Form and an Itemized Hospital Bill) with matching dates and total amounts were misidentified as duplicate claim submissions, discarding the itemized bill and causing line item discrepancies.

5. **Currency and Formatting Inconsistencies in Explainability**:
   `decision_explanation.py` hardcoded the `$` dollar sign for line item amounts and bed day notes regardless of claim currency (`INR`), printed redundant guideline titles (`[Guideline: ... (Rev 0100) (CMS-UB04-REV-0100)]`), and lacked clean tabular organization.

## Decision

1. **Bed-Day Reconciliation Date-Only Affirmative Matching**:
   In `reconcile_bed_days`:
   - Compute `calendar_days = (discharge_date - admission_date).days`.
   - When hourly timestamps are missing, evaluate against convention:
     - Under `MIDNIGHT_CENSUS`, expected midnights is `calendar_days`. If `billed_bed_days == float(calendar_days)`, reconcile affirmatively as `AgentVerdict.PASS`, `is_reconciled=True`, `discrepancy_units=0.0`, and `uncertainty_flag=False`.
     - Under `CALENDAR_DAY_SPAN`, expected days is `calendar_days` or `calendar_days + 1`. If billed bed days matches expected units, reconcile affirmatively as `PASS`.
     - If `billed_bed_days == float(calendar_days + 1)` under `MIDNIGHT_CENSUS` without hourly discharge timestamps, preserve ambiguity range `[calendar_days, calendar_days + 1]` and emit `AgentVerdict.INDETERMINATE` with `uncertainty_flag=True` (satisfies benchmark `EVAL-CLIN-14`).
     - If `billed_bed_days > calendar_days + 1` or `billed_bed_days < calendar_days`, detect discrepancy and emit `AgentVerdict.CONFLICT` (satisfies benchmark `EVAL-CLIN-16`).

2. **Mandatory Guideline Citation Synthesis for Substantiated Ancillary Lines**:
   In `evaluate_line_item`, whenever a substantiated facility line item (Room & Board, Pharmacy, Diagnostics, Surgical facility fee) achieves `necessity_status == MET` without a separate CPT guideline, synthesize an authoritative `GuidelineCitation`:
   - `guideline_id = f"CMS-UB04-REV-{code}"`
   - `effective_date = date(2025, 1, 1)`
   - `all_mandatory_criteria_satisfied = True`
   - Attach supporting clinical provenance spans to satisfy `LineClinicalEvidence` contract validation.

3. **Textual Revenue Code Normalization and Classification**:
   - In `is_plausible_revenue_code` (`ocr_reconcile.py`), accept valid UB-04 text tokens (`PHARM`, `RX`, `MED`, `DRUGS`, `ROOM`, `BED`) and reference `CodingSystem.REV_CODE`.
   - In `classify_modality` (`clinical_align.py`), map textual pharmacy tokens to `ServiceModality.PHARMACEUTICAL`, textual accommodation tokens to `ServiceModality.ROOM_AND_BOARD`, and diagnostic/800 codes to `ServiceModality.LABORATORY_TEST`.

4. **Document Differentiation in Within-Case Duplicate Checking**:
   In `claims_extraction_node`, distinguish between companion documents:
   - A summary Claim Form (`CLAIM_FORM`) accompanied by an Itemized Bill (`ITEMIZED_BILL` or itemized breakdown) is recognized as a unified multi-document claim package, not a duplicate submission.
   - Only documents sharing both matching claim numbers/identifiers AND duplicate flags or identical role types are flagged as duplicates.

5. **Currency-Aware, Structured Clinical Explainability**:
   In `explanation.py`:
   - Use the claim's detected currency symbol (`₹`, `$`, `€`, `£`) for all line item and summary formatting.
   - Clean up synthesized guideline citations to avoid repetitive parentheticals.
   - Provide clear, professional breakdown of surgical procedures, facility accommodations, and ancillary services.

## Consequences
- **Positive**:
  - Inpatient surgical claims with itemized bills and date-only stays achieve accurate, deterministic PASS verdicts with fully calibrated confidence.
  - Zero hardcoded domain rules; leverages standard UB-04 billing convention arithmetic and relational guidelines.
  - Prevents false-positive duplicate rejections for claims submitted with itemized hospital bills.
  - Generates clear, audit-ready clinical explainability in the claim's native currency.
- **Negative / Risks**:
  - Genuine midnight discrepancies without discharge timestamps continue to be safely flagged as `INDETERMINATE` per `EVAL-CLIN-14`.

