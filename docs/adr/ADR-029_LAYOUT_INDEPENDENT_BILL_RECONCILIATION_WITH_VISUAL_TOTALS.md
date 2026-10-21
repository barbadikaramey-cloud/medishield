# Architecture Decision Record
## ADR-029: Layout-Independent Bill Reconciliation with Visual Totals Extraction

**Status:** Proposed  
**Date:** 2026-10-21  
**Context:** Decision Engine and State Machine Specification § 3 (P3, P6), ADR-018 (institutional allowable caps), ADR-020 (VLM cross-check), ADR-028  
**Deciders:** MediShield Engineering

---

## 1. Context and Problem Statement

`evaluate_claim_totals` in `src/medishield/ocr/ocr_reconcile.py` compares the sum of itemized line amounts to the declared total with an equality rule. Any amount present on the bill but not expressed as a line item (tax, discount, copay, facility surcharge, prior payment) produces an `ARITHMETIC_DRIFT` FAIL unless a hardcoded facility/revenue-code allowance matches. Each new claim-form layout then needs its own allowance or provenance patch, and the same mismatch reappears on the next form.

The line items on the bill are usually extracted correctly. The gap is in the totals block, where layouts differ most and where text OCR loses labels, column alignment, and sign conventions. A text-only parser cannot reliably tell "Tax 8%: 12.00" from a line item, and cannot tell "Less copay: (25.00)" from an additive amount.

## 2. Decision

1. **Reconciliation is relational, not layout-specific.** The arithmetic check becomes: `declared_total = sum(line amounts) + sum(labelled adjustments)`, where each adjustment term is classified by a row in a new relational catalog `billing_adjustment_terms` (label patterns, language, sign, and adjustment kind: `ADDITIVE`, `DEDUCTIVE`, `INFORMATIONAL`). No label vocabulary, sign rule, or facility allowance is hardcoded in application code (AGENTS.md rule 1).
2. **Visual totals extraction via VLM.** When the text-derived totals block does not reconcile or lacks labelled adjustments, the totals region of the claim form is sent to the existing VLM client (ADR-020 path) with a structured request: return every labelled monetary row in the totals block with label, amount, and sign as printed. The VLM output is treated as evidence with provenance (page, region, model id), not as a decision. Deterministic code validates each returned label against the catalog and each amount against the page's money-format parser.
3. **Outcome semantics.**
   - Line sum plus catalogued adjustments equals declared total within the currency tolerance: arithmetic `PASS`.
   - A mismatch remaining after all catalogued and visually extracted terms are applied: `INDETERMINATE` with the numeric detail (line sum, adjustments applied, declared total, unexplained gap). Not `FAIL`. Per Rule 14 and § 4 note 1, uncertainty is not converted into failure.
   - `FAIL` (`ARITHMETIC_DRIFT`) is reserved for a mismatch where every term is catalogued and visually confirmed and the gap still exceeds tolerance under the same layout evidence. This keeps the hard-fail path narrow.
4. **Where it lives.** `ClaimsAgent` receives the adjustment catalog through constructor injection, following the `code_catalog` pattern, and passes it to the three existing `evaluate_claim_totals` call sites (`claims.py` ~317, ~367, ~544). The VLM totals request runs only when deterministic reconciliation is not already `PASS`, consistent with the existing Stage C budget (ADR-020).
5. **Hardcoded facility allowance is removed** once the catalog and visual extraction cover its cases. Its seed rows move into `billing_adjustment_terms`.
6. **No LLM decides the outcome.** The VLM reads text; the reconciliation and the decision remain deterministic (Rules 12, 15).

## 3. Consequences

### Positive
- One reconciliation rule covers all claim layouts. New layouts need catalog rows, not code.
- Adjustments are explicit, with sign and provenance, which supports audit.
- Unexplained gaps become reviewable numeric findings instead of hard FAILs caused by extraction gaps.

### Trade-offs and Follow-up
- VLM calls add latency and cost on arithmetic mismatches. The budget and caching rules of ADR-020 apply.
- Catalog coverage is a curation task. Unknown labels remain unexplained gaps and escalate; they are not guessed.
- Moving unexplained gaps from FAIL to INDETERMINATE changes the existing test expectations (`test_ocr_reconciliation.py`, `test_multilingual_clinical.py`, `test_kyc_and_claims_agents.py`) and must be updated with the code.
- Requires a migration after `007_verified_catalog_code_coverage` and updates to the workflow specification (Rule 10) and the claims output contract (Rule 8) if the response shape changes.
