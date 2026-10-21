# Architecture Decision Record
## ADR-031: LLM-Assisted Guideline Necessity Criteria Matching

**Status:** Accepted  
**Date:** 2026-10-21  
**Context:** ADR-025 (hybrid clinical alignment), ADR-024 (evidence-grounded explanations), ADR-027, AGENTS.md Rules 12, 14, 15  
**Deciders:** MediShield Engineering, product owner

---

## 1. Source Requirement

The product owner asked that clinical alignment be determined by the LLM for the indication and necessity match, and that deterministic logic continue to own the final decision. The indication half is already implemented by ADR-025 (`ClinicalRelationshipInterpreter`). The necessity half remains keyword and regex matching in `DeterministicClinicalAlignmentEngine.evaluate_guideline_criteria` (`src/medishield/domain/clinical_align.py`, `EVIDENCE_REGEX` branch). Clinical wording such as "failed conservative management for six weeks" does not match a fixed pattern, so the criterion stays UNASSESSED and the line becomes INDETERMINATE.

## 2. Feature

For each billed service line whose applicable guideline has an `EVIDENCE_REGEX` mandatory criterion that the deterministic matcher left UNASSESSED, one structured LLM call per case asks whether the verified clinical evidence satisfies that criterion. The model returns `SATISFIED` or `UNCERTAIN` with cited span IDs.

## 3. Why It Exists

Regex patterns are brittle against clinical phrasing and produce false INDETERMINATE results. Medical-necessity judgments over narrative notes are semantic, so an LLM is justified under Rule 12 only for this matching step.

## 4. Decision

1. **Scope.** Only mandatory `EVIDENCE_REGEX` criteria whose deterministic outcome is `UNASSESSED_EVIDENCE_UNAVAILABLE` are candidates. `DIAGNOSIS_CODE_PREFIX` stays deterministic because it is a catalog-code check. Criteria that are already SATISFIED, NOT_SATISFIED, or CONTRADICTORY are never sent to the model.
2. **Authority.** The model may only upgrade an UNASSESSED criterion to `SATISFIED`. It cannot produce `NOT_SATISFIED` or `CONTRADICTORY`. Explicit negation and contradiction therefore remain deterministic, so the model cannot create a FAIL. Model `UNCERTAIN` leaves the criterion UNASSESSED.
3. **Evidence.** Accepted matches must cite span IDs that exactly match verified OCR clinical spans supplied in that request. Unknown, empty, or malformed citations are rejected. Accepted spans become the criterion's `supporting_spans`, with `assertion_status=AFFIRMED`, `evidence_relevance_verified=True`, and `evidence_interpretation` set to the cited span text.
4. **Single call.** At most one structured call is made per case, batching all lines and criteria. No call is made when there are no candidates, the provider is unavailable, or no verified clinical spans exist.
5. **Failure.** Provider errors, schema failures, and unavailable models leave the criterion UNASSESSED, which yields the existing INDETERMINATE behavior with no approval uplift (Rule 14). Failures are logged without document text.
6. **Untrusted input.** Clinical text is wrapped with `wrap_untrusted_content` and the system prompt states that it is data, not instructions.
7. **Unchanged boundaries.** Criterion descriptions come from the guideline database (Rule 1). Final verdicts, P1–P8 decisions, prior-authorization status, and performance status are computed by deterministic code (Rule 15). The API response shape is unchanged.

## 5. Consequences

### Positive
- Clinically phrased necessity evidence can satisfy guideline criteria without new regex patterns.
- The change is additive: no regression is possible for lines whose criteria are already decided deterministically.

### Trade-offs
- Adds one bounded provider call per case when candidates exist, with latency and availability dependence.
- The LLM can yield false SATISFIED. The mitigation is that the decision is still gated by the rest of the line checks, and every match is traceable to a cited span for review.
- Clinical governance must review the criterion descriptions the model is asked to match.

## 6. Affected Components

- `src/medishield/domain/necessity_interpreter.py` (new): candidate request, structured response, citation validation, application to criteria.
- `src/medishield/workflows/nodes.py`: one pre-loop call alongside the relationship interpreter; apply matches to evaluated criteria per line.
- `tests/unit/test_necessity_interpreter.py` (new).
- `docs/canonical/04_LANGGRAPH_STATE_AND_ORCHESTRATION_CONTRACT.md`: describe the specialist step.

No database schema, migration, or API contract change.

## 7. Acceptance Criteria

- Only UNASSESSED mandatory `EVIDENCE_REGEX` criteria produce candidates; deterministic NOT_SATISFIED or CONTRADICTORY results are never sent or overridden.
- At most one LLM call per case; zero calls when there are no candidates or the provider is unavailable.
- An accepted match requires SATISFIED plus non-empty citations that all resolve to supplied verified span IDs.
- The model cannot emit NOT_SATISFIED or CONTRADICTORY, and such responses are rejected.
- Malformed, invalid-citation, and unavailable responses leave the criterion UNASSESSED.
- Injected instructions inside clinical text do not alter the schema or accepted set (tested with a mocked client).
- Existing clinical alignment tests continue to pass.
