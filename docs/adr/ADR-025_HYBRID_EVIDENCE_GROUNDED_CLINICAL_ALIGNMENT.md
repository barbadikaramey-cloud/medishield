# ADR-025: Hybrid Evidence-Grounded Clinical Alignment

**Status:** Accepted at product-owner request; clinical reference data remains subject to clinical governance  
**Date:** 2026-10-20  
**Supersedes:** ADR-015 §2.2's deterministic diagnosis-to-service indication matching only  
**Context:** Clinical alignment audit; diagnosis/procedure and institutional revenue-code relationship assessment; ADR-015, ADR-016, ADR-021, ADR-024

## 1. Source Requirement

The product owner requested that clinical alignment assess whether extracted CPT/HCPCS and revenue-code service lines are clinically related to documented diagnoses using medical-coding expertise, rather than relying on hardcoded diagnosis-prefix matching. The audit found that codes extracted from claims can be lost when the clinical node filters only spans from clinical documents, causing explanations to say "None documented" despite extracted claim diagnoses.

## 2. Decision

Use one bounded, structured LLM interpretation call per case to propose diagnosis-to-service relationships. The model receives only relevant, verified OCR spans, catalog-validated diagnosis/service codes, and dynamically retrieved code descriptions and applicable guideline context. Document text is explicitly untrusted input.

The LLM may report `SUPPORTED`, `NOT_ESTABLISHED`, or `UNCERTAIN` and cite input span IDs. Application code validates the response schema, line numbers, codes, and each citation against the exact verified OCR spans supplied to the model. Only a `SUPPORTED` response with a catalog-valid diagnosis code, a verified claim-code span, and verified clinical-document support may set `indication_status=EXPLICITLY_INDICATED`. Other responses, missing/invalid citations, unavailable models, and malformed outputs remain `POTENTIALLY_RELATED` / `INDETERMINATE`; model text cannot create a clinical fact or a denial.

Deterministic catalog/version validation, code-system classification, service performance, prior authorization, guideline criteria, bed-day reconciliation, evidence provenance, and final P1–P8 business decisions remain in application code. Model interpretation cannot satisfy those independent gates. Pharmacovigilance context is used only for pharmaceutical/drug lines and does not replace drug-safety references or medication-event verification.

## 3. Rationale and Trade-offs

Clinical concept relationships expressed in narrative notes are semantic and are not reliably represented by a fixed list of diagnosis-code prefixes. A constrained model can propose these relationships while exact source-citation validation and deterministic decision rules preserve auditability and prevent the model from becoming the adjudicator.

The design adds bounded latency, provider availability dependence, and potential sensitivity-data exposure. Inputs are minimized to relevant clinical snippets and codes; identifiers, demographics, claim amounts, and unrelated OCR are excluded. No new dependency is introduced. Provider failure degrades to an explicit indeterminate result, not a pass or denial.

## 4. Affected Components

- `src/medishield/domain/clinical_interpreter.py`: structured request/response, prompt boundary, and citation validation.
- `src/medishield/workflows/nodes.py`: collect verified diagnosis/service context and apply only validated interpretations.
- `src/medishield/domain/clinical_align.py`: preserve caller-supplied indication status instead of re-running prefix matching.
- `tests/unit/test_clinical_alignment.py`: relationship, citation, prompt-injection, malformed-response, and provider-unavailable tests.
- `docs/canonical/04_LANGGRAPH_STATE_AND_ORCHESTRATION_CONTRACT.md`: describe the hybrid specialist and unchanged decision boundary.

## 5. Acceptance Criteria

- At most one structured interpretation call is made per case; no call is made without sufficient verified source spans and valid catalog codes.
- The prompt contains only relevant, minimized evidence and treats all document text as inert untrusted content.
- A positive relationship is accepted only when the cited span IDs exactly resolve to supplied, verified OCR evidence and the diagnosis code is valid in the active catalog.
- The model cannot add codes, citations, policy requirements, prior-authorization outcomes, performance facts, guideline outcomes, or business decisions.
- Invalid, missing, conflicting, or unavailable model output yields `INDETERMINATE`/`POTENTIALLY_RELATED` with a structured reason; it never increases approval eligibility.
- Existing P1–P8 decision authority and API response shape are unchanged.
- Unit tests use a mocked model client; no test or implementation validation requires a live provider.

## 6. Governance

Catalogs, coding terminology, guidelines, pharmacovigilance references, and policy requirements remain dynamically managed authoritative data, not prompt-embedded business rules. Clinical reference sources, validation thresholds, and production use require clinical-informatics review. This ADR authorizes the integration boundary, not the medical validity of any generated interpretation.
