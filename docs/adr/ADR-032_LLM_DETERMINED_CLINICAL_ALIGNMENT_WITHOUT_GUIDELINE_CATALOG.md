# Architecture Decision Record
## ADR-032: LLM-Determined Clinical Alignment Without a Seeded Guideline Catalog

**Status:** Accepted  
**Date:** 2026-10-21  
**Supersedes (in part):** ADR-015 (versioned LCD/NCD guideline criteria), ADR-016 (guideline catalog storage), ADR-031 (LLM necessity matching over guideline criteria)  
**Context:** ADR-025 (indication LLM), ADR-027 (clinical-only INDETERMINATE exemption), ADR-028 (clinical discrepancy approval), AGENTS.md Rules 1, 3, 4, 12, 13, 14, 15  
**Deciders:** MediShield Engineering, product owner

---

## 1. Source Requirement

The product owner directed that clinical alignment be determined by an LLM rather than by hard-coded guideline criteria, and that guideline data not be stored in the database or the code base. Current state: `db/seeder.py` (`seed_clinical_guidelines_and_indications`) seeds 18 hard-coded LCDs into `clinical_guidelines`, `guideline_criteria`, and `diagnosis_indications`. The clinical engine reads them through `SqlClinicalGuidelineRepository` and `DeterministicClinicalAlignmentEngine.get_guideline_for_code`. Guideline data is not derived from the policy documents. Result on the current 35 cases: 97 of 97 service lines are INDETERMINATE with `GUIDELINE_UNAVAILABLE` and `NOT_DOCUMENTED` performance, so no line can pass.

## 2. Feature

Clinical alignment is decided by one structured LLM call per case. For each billed service line, the model receives the line's service description, codes, service date, billed units, and the verified clinical spans of the case. It returns, per line, one of `SUPPORTED`, `UNCERTAIN`, or `NOT_ESTABLISHED`, with span citations, under a strict prompt that forbids use of any external guideline. Deterministic code validates the output and assembles the line verdict.

## 3. Why It Exists

Hard-coded guideline criteria are a fixed list that cannot cover the range of services on real claims, and they are not traceable to a governed source. Clinical necessity over narrative notes is a semantic judgment, which is the case Rule 12 allows an LLM to handle. The decision authority stays deterministic.

## 4. Decision

1. **Discard the guideline catalog.** A migration drops `clinical_guidelines`, `guideline_criteria`, and the guideline portion of `diagnosis_indications`. The seeder's guideline definitions, `get_guideline_for_code`, `SqlClinicalGuidelineRepository`, `DefaultClinicalGuidelineRepository`, the in-memory guideline repository, and the `EVIDENCE_REGEX`/`DIAGNOSIS_CODE_PREFIX` criterion evaluation are removed. No guideline data remains in code or database (Rule 1).
2. **Single LLM determination per case.** One structured call covers all lines. Output per line: `outcome` in {`SUPPORTED`, `UNCERTAIN`, `NOT_ESTABLISHED`} and `evidence_ids` (non-empty for `SUPPORTED` and `NOT_ESTABLISHED`). Schema is enforced by Pydantic with `extra="forbid"`.
3. **Strict prompt.** The system prompt states: use only the supplied clinical spans; do not apply external guidelines or policy knowledge; return `SUPPORTED` only when a cited span states the service is clinically necessary for the documented condition; return `UNCERTAIN` when evidence is partial or ambiguous; never invent evidence IDs; never decide coverage, prior authorization, or payment. Clinical text is wrapped with `wrap_untrusted_content`.
4. **Citation validation (deterministic).** A `SUPPORTED` outcome counts only if every cited ID matches a supplied verified span. Invalid, uncited, or malformed outcomes become `UNCERTAIN`.
5. **Verdict mapping (deterministic).** `SUPPORTED` with valid citations maps to PASS for the necessity component. `UNCERTAIN` maps to INDETERMINATE. `NOT_ESTABLISHED` maps to INDETERMINATE, not FAIL, because the LLM cannot create a FAIL (Rule 14, Rule 15). FAIL comes only from deterministic sources: verified prior-authorization denial and a verified contraindication (see Open Question 2).
6. **Failure.** Provider unavailable, schema failure, or timeout yields INDETERMINATE for all lines with reason `CLINICAL_LLM_UNAVAILABLE`. No silent PASS (Rule 14).
7. **Unchanged.** Indication assessment (ADR-025), performance status, prior authorization, bed-day validation, the composite rules in `build_clinical_alignment_output`, ADR-027/028 exemptions, and P1–P8 remain as defined. Their inputs no longer depend on guideline lookup.
8. **Provenance.** Every LLM decision stores the model identifier, prompt version, the cited span IDs, and the raw outcome in the agent snapshot (Rule 13).

## 5. Consequences

### Positive
- No hard-coded clinical catalog to maintain, and no dependency on guideline freshness.
- Lines are no longer INDETERMINATE purely because a catalog entry is missing.

### Trade-offs
- Necessity is no longer checked against a versioned, reviewable source. Reviewers see only the LLM's cited evidence.
- LLM output may be inconsistent across runs; temperature 0 and a fixed prompt version reduce but do not remove this.
- Clinical governance must approve the strict prompt, since it is now the necessity standard.
- Live Groq availability and daily quota become a hard dependency for clinical alignment.

## 6. Affected Components

- `src/medishield/db/seeder.py`: remove guideline seeding; keep the indication seed only if Open Question 2 keeps it.
- `src/medishield/db/models.py`, `src/medishield/db/catalog.py`: remove `ClinicalGuideline`, `GuidelineCriterion`, and the guideline repository classes.
- `src/medishield/domain/clinical_align.py`: remove guideline lookup and criterion evaluation; add the LLM determination path.
- `src/medishield/domain/necessity_interpreter.py` (ADR-031): replace with the new per-case determination module.
- `src/medishield/workflows/nodes.py`: `clinical_alignment_node` pre-pass and loop.
- `src/medishield/domain/contracts.py`: criterion types that reference guideline criteria.
- Migration: drop the guideline tables (Rule 9).
- API contract: remove guideline citation fields from clinical alignment output (Rule 8). Document the change in `docs/canonical/08_REST_API_SPECIFICATION.md`.
- Canonical specs: `04_LANGGRAPH_STATE_AND_ORCHESTRATION_CONTRACT.md`, `07_DATABASE_AND_STORAGE_CONTRACTS.md`, `12_EVALUATION_AND_SYNTHETIC_DATA_FRAMEWORK.md`.
- Tests: remove guideline-dependent tests; add unit tests for citation validation, verdict mapping, prompt-injection containment, and provider failure.

## 7. Acceptance Criteria

- No guideline catalog table, seed definition, or repository class remains; a grep for `clinical_guidelines`, `get_guideline`, and `EVIDENCE_REGEX` returns no application hits.
- One LLM call per case when clinical spans exist; zero calls when none exist (then INDETERMINATE with `NO_CLINICAL_EVIDENCE`).
- `SUPPORTED` without a valid citation is never counted as PASS.
- The model cannot produce FAIL; FAIL arises only from deterministic prior-authorization denial or the contraindication rule.
- Injected instructions inside clinical text do not change the schema or verdict mapping (mocked client).
- Provider failure yields INDETERMINATE, never PASS.
- Migration applies cleanly on SQLite; full clinical unit tests pass; ruff and mypy clean on changed files.
- Re-run of the 35 cases is recorded with before/after verdict counts.

## 8. Decisions Recorded at Acceptance

1. **Amended: retain, do not delete.** The owner directed that nothing be deleted. The guideline tables, seeder definitions, and repository classes stay in place. The clinical alignment path no longer reads them for necessity. The drop migration in section 6 is withdrawn.
2. **Indication catalog kept.** `diagnosis_indications` remains the only seeded clinical table. Contraindication FAIL stays deterministic.
3. **Approval path.** A clinical `UNCERTAIN` maps to INDETERMINATE and may use the ADR-027/028 exemption.
4. **Prior-authorization requirement.** Still read from the retained guideline row's `requires_prior_auth`. This is a known gap against the "no guideline data" goal and needs a decision before the catalog is removed.

## 9. Implementation Status

Phase 1 complete: `domain/clinical_determination.py` with tests.
Phase 2 in progress: `clinical_alignment_node` sets necessity from the LLM determination (SUPPORTED maps to MET, otherwise CRITERIA_INCOMPLETE). `evaluate_line_item` skips the guideline lookup when an LLM necessity status is supplied. Performance and indication remain deterministic (ADR-032 section 4.7).
The `domain/necessity_interpreter.py` module (ADR-031) is no longer called by the node and is retained unused.
