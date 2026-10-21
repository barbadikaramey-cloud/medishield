# ADR-029: Layout-Adaptive Billing Extraction and Reconciliation

**Status:** Accepted at product-owner request  
**Date:** 2026-10-21  
**Context:** Claims extraction, arithmetic validation, CMS-1500 and UB-04 layout variability; ADR-018, ADR-020, ADR-022

## 1. Source Requirement

The product owner identified that claim forms vary in layout and that provenance validation alone cannot ensure every billed row has been extracted. C001's UB-04 room charge was omitted from the line sum; C022's room-and-board quantity was printed in the description but extracted as one unit.

## 2. Decision

Billing table extraction will use OCR text and geometry together, anchored on detected column headings and charge-bearing rows rather than fixed page coordinates. Row boundaries are determined from adjacent amount anchors so vertically offset cells can be recovered without merging neighboring service lines. Explicit quantities in a row description (for example, "3 days") may supply a units value only when the phrase is physically present in a verified OCR span.

Currency amounts with recognizable OCR punctuation corruption may be normalized only when a conservative format rule produces one unambiguous numeric value; the original OCR span remains the provenance. Uncertain numeric readings are not normalized into facts.

Arithmetic reconciliation is tri-state: `PASS` only when the service-line extraction is complete and totals reconcile; `CONFLICT` only when extraction is complete and verified amounts disagree; `INDETERMINATE` when table structure, row coverage, quantities, or monetary values remain incomplete or ambiguous. Unresolved extraction must not be described as confirmed billing drift.

## 3. Guardrails

- Existing claim totals, service-line values, and quantities retain exact OCR-span provenance.
- A row is not considered complete solely because an amount span exists; relevant code/description or revenue-code evidence must be accounted for.
- Page totals, estimated amount due, non-covered amounts, and payer fields are excluded from service-line sums using detected semantic labels and table structure.
- No model output can clear arithmetic or invent a row. A visual model may propose candidates only; candidates must be independently corroborated against OCR/image evidence.
- The business decision remains deterministic under P1–P8.

## 4. Affected Components

- `src/medishield/ocr/ocr_reconcile.py`: adaptive columns/rows, quantity recovery, conservative amount normalization, completeness signal.
- `src/medishield/agents/claims.py`: map incomplete extraction to `INDETERMINATE`; retain `CONFLICT` for verified mismatch after complete extraction.
- `tests/unit/test_ocr_reconciliation.py` and `tests/unit/test_kyc_and_claims_agents.py`: cross-layout and tri-state regressions.
- `docs/canonical/04_LANGGRAPH_STATE_AND_ORCHESTRATION_CONTRACT.md`: update Claims-node reconciliation behavior.

## 5. Acceptance Criteria

- C001's UB-04 room-and-board charge and C022's three billed room days are recovered from their actual OCR evidence with cited spans.
- C001's line sum reconciles to its header when all itemized rows are captured.
- Two adjacent rows remain separate even when cell text and monetary anchor baselines differ.
- A deliberately omitted row yields `INDETERMINATE`, not `CONFLICT`; a complete, verified mismatch remains `CONFLICT`.
- Existing CMS-1500 and UB-04 extraction tests pass, including exclusion of page totals and payer estimates.
