# MediShield Claims Validation Root-Cause Analysis and Targeted Remediation Plan

**Status:** Proposed, not implemented  
**Evidence window:** October 11, 2026 dataset run; source/repository inspection on October 12, 2026  
**Constraints observed:** No application, dataset, migration, canonical contract, dependency, or framework was changed. The supplied dataset labels are preserved as-is. This document is the only new file for this request.

## Executive Finding

The observed results do not point to one OCR model failure or a need to rebuild the system. They arise at several narrow boundaries:

1. OCR payload generation succeeded, but the classifier consumes only one type per uploaded file, uses deterministic keyword scoring with a cumulative-score bug, and has no effective page/document splitter for composite pages.
2. CPT extraction filters OCR candidates through a small synthetic allowlist. Unknown codes are dropped rather than preserved as unverified evidence; claims verdicts can still be `PASS` without a CPT or a parsed service line.
3. Policy coverage accepts verified text as a citation without requiring that a positive clause apply to every billed line/code. A line parser can also treat a date year such as `2025` as a revenue code.
4. The expiry parser misses the common `EXP:` label. DOB proximity extraction can bind a birth-date label to an adjacent admission date.
5. Fraud feature calculators work when given context, but the workflow supplies no duplicate/history context. Text service dates in `MM/DD/YYYY` form are also not parsed by the fraud date helper.
6. The P6 decision rule trusts agent verdicts without checking that material claim/policy evidence is complete and applicable. Its unit test creates Claims output with no codes and still expects approval.
7. Dataset labels conflict with their source files, so metadata-derived expected decisions cannot be treated as independent ground truth.
8. Workflow reruns append decisions without deactivating the previous active decision.

**Architecture answer:** A broad redesign is not required. Targeted changes in the existing classifier, Claims Agent, KYC extractor, policy evaluator, fraud node/context, coordinator, and task persistence are sufficient. One strengthened evidence-to-decision admissibility gate should live at the current deterministic Decision Engine boundary. This is a targeted safety gate, not a new orchestration framework. Production CPT/ICD/provider/history facts still require approved external data sources; no source should be invented.

**Important rejection invariant:** ADR-002 says confirmed hard failures must be automatically rejected and uncertainty must escalate. The current P5 implementation instead returns `ESCALATE/RECOMMEND_DENIAL`. A future patch must align P5 with the accepted ADR while rejecting only verified hard failures; it must not turn unknown or missing evidence into rejection. Human review/override remains a separately authorized actor in the existing canonical contract. If “Decision Engine only” is meant literally to include human overrides, that conflicts with the existing human-review specification and needs an explicit contract decision before implementation.

## Evidence Boundary

- The saved normal-workflow run contains 30 patient cases, 151 document OCR payloads, and 12,322 OCR spans. All 151 OCR endpoints were available. That proves execution/payload availability, not character-level accuracy.
- Comparing saved `doc_type` against the dataset category yielded 118/151 matches. This is category agreement against metadata, not OCR WER or a validated independent truth set.
- The deterministic classifier was rerun on representative saved OCR with its LLM fallback explicitly unavailable. It reproduced high-confidence wrong types: `amend_PT_19116.png` as ID, `discharge_PT_19116.png` as prescription, and `id_PT_15075.png` as prescription. The file is [classifier.py](../../../src/medishield/agents/classifier.py); the workflow path is [nodes.py](../../../src/medishield/workflows/nodes.py).
- A diagnostic score-only simulation that adds each matched signature weight once changed the prediction agreement on this metadata from 118/151 to 140/151, with 11 mismatches remaining. This is not a proposed accuracy claim: source pages can contain more than one document, and some dataset labels are contradicted by the sources.
- `scripts/fast_eval_30_clusters.py` derives `expected_decision` from `fraud_label`; [`fast_eval_results.json`](../../../scripts/fast_eval_results.json) is therefore not independent truth. It is also a fast evaluator, not the normal API workflow. Do not use its false-approval count as a verified adjudication score.
- `metadata.json` has `expected_decision` only for the four `unknown/` documents, not for the 151 patient-group documents. Six patient clusters are fraud-labeled, but three labels inspected here conflict with source evidence.

## Failure 1: 118/151 Document-Type Agreement

**A. Reproducible cases/documents.** 33 saved type disagreements. Examples: `discharge_PT_19116.png` saved as `PRESCRIPTION`; `id_PT_15075.png` saved as `PRESCRIPTION`; `amend_PT_19116.png` saved as `ID_DOCUMENT`. Category totals: claim forms 31/31, prescriptions 30/30, discharge summaries 22/30, IDs 25/30, policy amendments 10/30.

**B. Expected versus actual.** Expected here means the supplied `metadata.json.category`, not independently adjudicated truth. The saved `Document.doc_type` differs for 33 files. `amend_PT_19116.png` visibly contains a policy-amendment request plus a separate driver-license image, while metadata supplies one `policy_amendments` category. That is a bundled-document/data-contract ambiguity in addition to classifier error.

**C. Exact path.** `POST /api/v1/cases` in [cases.py](../../../src/medishield/api/routers/cases.py) stores one row per uploaded file -> `process_case_workflow_task` in [tasks.py](../../../src/medishield/worker/tasks.py) hydrates `file_name`/`object_key` -> `CaseWorkflowCoordinator.execute` -> `ocr_preprocessor_node` -> `classifier_node` -> `DocumentClassifierAgent.classify` -> `doc_type` is copied to workflow state and persisted back to `Document.doc_type`.

**D. Earliest divergence.** `DocumentClassifierAgent.classify` runs a keyword/filename score. It executes `type_scores[doc_type] += score` inside the per-pattern loop while `score` is already a cumulative subtotal; each matched pattern re-adds prior matches. Generic terms such as “Rx,” dosage/medication terms, `DOB`, “license,” or “Dr.” can outweigh the filename and document title. Incorrect results can be `PASS` at confidence 0.98, so the LLM disambiguation branch (which only runs for indeterminate results) is never invoked. Output mapping is a direct enum value copy, not the divergence point. OCR payloads in reviewed examples contain the relevant document headings/text; this is not evidence that preprocessing erased all useful text.

**E. Root-cause classification.** Implementation defect (score accumulation and over-broad signatures); missing splitting behavior for bundled pages/images; dataset taxonomy assumption (single category per composite image). Not primarily an LLM/prompt/output-mapping defect.

**F. Smallest justified fix.** Use a staged cascade. First, for the controlled generated dataset, recognize exact anchored basenames such as `claim_`, `id_`, `discharge_`, `rx_`, and `amend_` as the primary category hint; support duplicate-claim suffixes. Second, for unrecognized names, use the current OCR-signature classifier after fixing cumulative scoring and making document-title/form-header terms dominate incidental `Rx`, dosage, DOB, “license,” and “Dr.” text. For ordinary uploads, filenames are untrusted hints, not definitive proof: if filename and strong content signals conflict, do not assign a confident class from the filename alone. Third, invoke the existing Groq text classifier only for remaining ambiguous text cases. Its current acceptance threshold is 0.70. If ambiguity depends on visual layout or a page contains multiple documents, evaluate a vision fallback only for that bounded case; existing Gemini/Hugging Face `VisionClient`s in `ocr_reconcile.py` are field readers, not document classifiers, and are not wired into this path. If neither fallback can classify reliably or a mixed page cannot be split, return `UNKNOWN/INDETERMINATE` and route to review. No model/framework replacement or new dependency is justified before this cascade is evaluated.

**G. Architecture required?** Targeted classifier correction plus implementation of the already-specified split boundary for bundled material; no repository rewrite. The filename-first rule is appropriate for the controlled dataset naming convention but must remain a hint for arbitrary uploads. If same-page segmentation is out of scope, classify the composite as ambiguous and route to review rather than making a high-confidence single-type claim.

**H. Regression test.** Add no-LLM tests using saved/frozen OCR spans and exact basenames for the discharge, member-card, and amendment examples; assert the anchored dataset prefix and corrected OCR-signature fallback behavior. Add unknown-name and filename/content-conflict cases; conflicts must invoke the existing fallback or remain indeterminate, not be forced by filename. Add a composite amendment+ID case that either produces two logical documents or `INDETERMINATE`, never a confident wrong class. Add a confusion-matrix test only after evaluator-approved labels exist.

**I. Regression risk.** More conservative classification may increase `AWAITING_INFO`/human review. This is acceptable where required-document identity is uncertain; do not lower confidence thresholds just to restore throughput.

## Failure 2: CPT Values Present in OCR but Absent from Claims Output

**A. Reproducible documents.** `claim_PT_62350.png` OCR contains CPT `59400` and `27447`; `claim_PT_24208.png`, `claim_PT_30028.png`, and `claim_PT_45400.png` contain CPT `21120`. Their saved OCR text includes those strings, while at least the reviewed claim snapshots have `cpt_codes=[]`.

**B. Expected versus actual.** The OCR text contains the billed code; the saved Claims Agent output omits it. This is downstream code filtering/extraction, not proof of a recognition failure.

**C. Exact path.** Intake/OCR -> `claims_extraction_node` -> `ClaimsAgent.execute` gathers all spans -> `_extract_cpt_codes` -> `extract_cpt_codes_from_table` in [ocr_reconcile.py](../../../src/medishield/ocr/ocr_reconcile.py) -> `SYNTHETIC_VALID_CPT_CODES` membership filter -> optional Groq fallback -> `ClaimsAgentOutput.cpt_codes`; `policy_coverage_rag_node` consumes only saved `cpt_codes` and parsed `rev_code`s.

**D. Earliest divergence.** `extract_cpt_codes_from_table` requires a standalone 5-digit token, a table-region coordinate, and membership in the 16-entry `SYNTHETIC_VALID_CPT_CODES` set. It drops unlisted codes with no “unsupported code” result. The set does not contain 59400, 27447, or 21120. `extract_service_lines_from_table` uses the same set. LLM fallback may produce candidates when available, but a 429/unavailable LLM leaves no code, and its returned code list is not independently revalidated against a reference catalog.

**E. Root-cause classification.** Implementation defect plus missing/limited reference data. The synthetic allowlist is not an authoritative current CPT catalog. Do not invent that the omitted code is invalid just because it is unsupported locally.

**F. Smallest justified fix.** Preserve a syntactically extracted table code as a candidate even when absent from the supported catalog; represent validation separately as `VALIDATED`, `UNSUPPORTED/UNKNOWN`, or `UNAVAILABLE`. Unknown/unverified code => Claims `INDETERMINATE`, not silent omission, `PASS`, or hard rejection. A production catalog requires an explicitly approved version/source.

**G. Architecture required?** Small Claims/OCR contract refinement; no new agent or framework. The external code reference remains a separate dependency decision.

**H. Regression test.** From a span containing each affected code, assert the raw candidate survives extraction. With no authoritative code reference, assert `INDETERMINATE`; with a fixture-authorized code entry, assert validation succeeds. Include the LLM-unavailable path and ensure no unsupported LLM code can be marked validated.

**I. Regression risk.** More unsupported codes will become visible and route to review. Do not auto-reject unknown codes. Valid codes in the existing synthetic set should retain current behavior.

## Failure 3: Missing CPT/Essential Claim Evidence Still Reaches APPROVE

**A. Reproducible cases.** PT_62350, PT_24208, PT_30028, and PT_45400 completed `APPROVE` with no saved CPT codes. PT_20322 completed `APPROVE` despite the explicit duplicate-claim evidence below. The unit test `test_rule_p6_clean_auto_approve` builds claims output with default-empty CPT/ICD/service-line fields and expects P6 approval.

**B. Expected versus actual.** A claim with unvalidated/missing service coding or a detected duplicate must not be called a clean, fully validated straight-through approval. Actual P6 uses `APPROVE` for several such cases. There is no patient-level expected-decision field in the dataset, so this conclusion is based on the explicit edge/fraud labels plus source evidence, not a fabricated global gold decision.

**C. Exact path.** Claims output -> `DeterministicDecisionEngine.adjudicate` in [decision.py](../../../src/medishield/domain/decision.py) -> P1 through P5 -> P6. P6 accepts all three core verdicts at or above the confidence threshold and either no fraud output or low-score `PASS`; task persists the resulting Case and `Decision` in [tasks.py](../../../src/medishield/worker/tasks.py).

**D. Earliest divergence.** `ClaimsAgent._adjudicate_verdict` checks negative charges/arithmetic drift, schema flag, claim amount, and NPI; it does not require validated CPT/ICD codes or complete code-bearing service lines before returning PASS. `evaluate_claim_totals` returns `PASS` for a declared total when there are no service lines. The Decision Engine P6 does not check claims `schema_valid`, `line_items_sum_valid`, the faithfulness gate, policy `covered`/code applicability, or that the fraud output exists. `fraud_output is None` is explicitly treated as clean. `DecisionInputContext` carries no `faithfulness_gate_passed` flag. Thus a missing fact can be represented upstream as PASS or omitted and never block P6.

**E. Root-cause classification.** Missing business preconditions and missing evidence-to-decision gate, plus an existing permissive test fixture. The central rule is deterministic, but its inputs do not prove the stated clean-pass predicate.

**F. Smallest justified fix.** Add a deterministic approval-admissibility check at the existing decision boundary. Require every required specialist result, valid claim schema/amount/service date, supported code evidence for every charge-bearing line, reconciled line totals where itemized, a positive policy clause explicitly applicable to every billed service code, verified provenance/faithfulness, and a completed fraud evaluation. Missing/unavailable/unknown evidence must take an existing `ESCALATE` path. Keep APPROVE for fully validated cases and REJECT for confirmed hard failures only.

**G. Architecture required?** A targeted extension to the existing Deterministic Decision Engine and its input contract; no new orchestration framework. Existing early completeness and worker-exception code also write decisions outside the engine; route those automatic outcomes through the engine or explicitly document an approved exception. Human reviewer overrides are a distinct authority in the canonical review contract.

**H. Regression test.** Add P6 negative tests for missing CPT/service-line code, empty claims output, false `line_items_sum_valid`, unverified provenance, policy PASS without applicable verified clauses, and missing fraud result. Retain a full-evidence P6 approval test. Add an end-to-end API case for PT_62350/cosmetic-code spans that asserts no P6 approval.

**I. Regression risk.** Short-term approval/STP rate will decrease and review queue may rise. This is the expected consequence of no longer treating missing evidence as affirmative proof. Calibrate only after the authoritative sources and dataset labels are validated.

## Failure 4: Policy PASS from Non-Applicable Clauses

**A. Reproducible cases.** PT_62350 (`59400`/`27447` visible in OCR, no saved CPT); PT_24208, PT_30028, PT_45400 (visible CPT 21120, missing from saved CPT list; all completed APPROVE). PT_45400 also has room/revenue lines that retrieve an inpatient room-and-board clause.

**B. Expected versus actual.** A verified clause only proves that text exists in a corpus; it does not prove the clause applies to the billed service. Actual outputs cite SEC-5.5 “Inpatient Room and Board Limits,” often with no matching billed code, and return policy PASS/80%. The returned `applicable_service_codes` is empty for these positive cases.

**C. Exact path.** `policy_coverage_rag_node` in [nodes.py](../../../src/medishield/workflows/nodes.py) builds `cpt_list`, `icd_list`, and `rev_list` from the Claims snapshot -> `PolicyCoverageAgent.execute` in [policy.py](../../../src/medishield/agents/policy.py) builds query terms -> `PolicyCorpusRepository.query_chunks` scores exact codes plus token overlap -> `verify_clause` verifies text -> Policy verdict flows into P6.

**D. Earliest divergence.** The positive-clause path records `matches_scope`, but uses it only for exclusions and prior authorization. It does not require `matches_scope` when deciding ordinary positive coverage. `covered_chunks` includes every retrieved non-exclusion chunk; if all citations verify and there is no exclusion/auth issue, the agent returns PASS. The document line parser can produce `rev_code='2025'` from a date-year span; the query “revenue code 2025” gets a lexical overlap with the static SEC-5.5 chunk, whose actual revenue codes are 0110/0111/0120. The citation is genuine but not applicable. Runtime also defaults to a hard-coded in-memory corpus rather than demonstrably querying the published policy index.

**E. Root-cause classification.** Policy applicability bug, missing positive-evidence gate, and runtime policy-source integration gap. It is not a citation-text-verification failure alone.

**F. Smallest justified fix.** Record code-to-clause applicability for positive and negative matches. A PASS must demonstrate a verified clause for each material billed code/line; retrieved but out-of-scope clauses do not count. Remove/reject malformed revenue codes and never infer them from an arbitrary 3–4 digit span. Any uncovered/unmatched material line, unsupported code, or unavailable corpus => INDETERMINATE, not PASS or REJECT. The default corporate corpus must be replaced by or explicitly wired to the authorized published corpus before production use.

**G. Architecture required?** Local policy evaluator and a small output-contract update may be required; no broader design. The authorized index connection is a targeted integration, not a RAG rewrite.

**H. Regression test.** Inject a valid-but-irrelevant SEC-5.5 clause for a non-room-board code and assert INDETERMINATE; inject a code-matched verified covered clause and assert PASS; inject a verified matching exclusion and assert the confirmed hard-failure route; test multi-line partial match and `rev_code=2025` as unverified.

**I. Regression risk.** More claims with partial corpus coverage will escalate. This preserves evidence and prevents an unrelated clause from silently becoming coverage.

## Failure 5: PT_39816 Expired ID Approved

**A. Reproducible identifiers.** PT_39816, `CASE-20261011-49A8EA`, ID `DOC-49A8EA-03`, file [`id_PT_39816.png`](../../../dataset/id_documents/id_PT_39816.png), saved OCR [`DOC-49A8EA-03.json`](../../../data/storage/ocr/CASE-20261011-49A8EA/DOC-49A8EA-03.json).

**B. Expected versus actual.** Source image and OCR show `EXP 03/28/2025` and `**EXPIRED**`; claim service date is 04/24/2025. Actual KYC has `id_expiration_date=null`, `id_expired=false`, verdict PASS; the final case is COMPLETED/APPROVE with P6.

**C. Exact path.** Intake/OCR -> `kyc_agent_node` -> `KYCAgent.execute` in [kyc.py](../../../src/medishield/agents/kyc.py) -> `_extract_gov_id_and_expiry` -> `id_expired` comparison -> `_adjudicate_verdict` -> Decision Engine P6 -> persistence. `kyc_agent_node` also invokes KYC without a validated service date.

**D. Earliest divergence.** The expiry regex recognizes “exp date”, “expiration date”, or “expires”; saved OCR says `EXP:`. The fallback string check recognizes only `cardexpired`, `card expired`, or `expired-notvalid`, not generic `**EXPIRED**`. Consequently the date parse/comparison is never reached. The source image is an Illinois state ID, so this is not the member-card category ambiguity seen in PT_15075.

**E. Root-cause classification.** Parser implementation defect and missing date/reference-date contract.

**F. Smallest justified fix.** Parse anchored `EXP`/`EXP:` variants and explicit expired-status tokens, retaining the exact OCR span. Compare the parsed expiry against the contractually relevant date (the source claim's validated service date for this case); if an essential expiry cannot be read or is ambiguous, emit INDETERMINATE. Only a verified expired ID is a hard failure.

**G. Architecture required?** Small KYC parser/node contract update. No redesign. This path must be repaired before changing P5 to automatic REJECT, or a false KYC FAIL can become a false automatic denial.

**H. Regression test.** Feed the saved OCR payload (or a stable span fixture) with `EXP: 03/28/2025` and service date 04/24/2025; assert exact span/date, KYC FAIL, and final `REJECT` only after ADR-002 P5 alignment. Include valid-at-service-date but expired-at-current-date and unreadable-expiry cases.

**I. Regression risk.** Tightening hard-failure rules may reject only when evidence is grounded and date comparison is explicit; unreadable expiry must escalate. The current broader `EXPIRED` text heuristic needs tests against incidental narrative text.

## Failure 6: PT_20322 Duplicate Claim Not Detected

**A. Reproducible identifiers.** PT_20322, `CASE-20261011-CA1DD4`, claim documents `DOC-CA1DD4-01` (`claim_PT_20322.png`) and `DOC-CA1DD4-02` (`claim_PT_20322_dup.png`). Both saved OCR payloads show claim number `CLM-2024-000005` and service date `10/11/2025`. Metadata labels the second claim `duplicate_claim`.

**B. Expected versus actual.** Expected from the supplied scenario label: deterministic duplicate signal leading to fraud review. Actual claims snapshot/fraud output has score 0.0/no duplicate anomaly; case completed APPROVE. The label is independently corroborated by identical claim number/date in both submitted files; no claim-history assumption is needed for this within-case duplicate.

**C. Exact path.** Intake accepts both files -> OCR -> `claims_extraction_node` aggregates all spans -> `ClaimsAgent` extracts one case-level claim output -> `fraud_agent_node` invokes `engine.evaluate(claims_output, kyc_output)` -> `FraudDetectionEngine` calculates F-01 -> Decision Engine P4/P6 -> worker persists decision. The feature helper is in [fraud.py](../../../src/medishield/domain/fraud.py); the node is in [nodes.py](../../../src/medishield/workflows/nodes.py).

**D. Earliest divergence.** `calculate_duplicate_claim_feature` works only when `FraudEvaluationContext.duplicate_claim_match_id` is supplied. The workflow node supplies no context, so default `duplicate_claim_match_id=None`, counts 1, and no benchmark stats are used. No historical repository/lookup is wired. ORM `Claim`/`ClaimLine` are not created by the intake/workflow path. Additionally, ClaimsAgent does not extract claim number or compare multiple claim forms inside one case. The fraud helper unit test only tests an injected match, not workflow wiring.

**E. Root-cause classification.** Missing integration and claim-history persistence/data source; missing within-case duplicate business rule. The deterministic score math itself is not the cause.

**F. Smallest justified fix.** First compare all claim-form documents within the same case using grounded normalized claim number and corroborating tuple fields; pass a verified duplicate match into the existing fraud context. Then, only when a historical claims source is populated and available, perform the same deterministic comparison across cases. If the required history is unavailable, report UNAVAILABLE/INDETERMINATE rather than a confident “unique” result. Do not use patient protected attributes for fraud scoring.

**G. Architecture required?** A targeted repository/context adapter and possibly use of the existing Claim schema; no new fraud engine. Historical cross-case lookup needs normalized claims to be persisted, which may require a migration/transaction task after requirements are approved.

**H. Regression test.** Workflow-level test with two OCR documents carrying the same verified claim number/date, asserting fraud features contain duplicate evidence and Decision Engine escalates per policy. Add a cross-case repository test with seeded prior claim. Add unavailable-history test asserting no false “unique” PASS.

**I. Regression risk.** Near-duplicate OCR keys can false-positive; require verified tuple evidence and preserve conflict/ambiguity for partial matches. Do not automatically reject duplicates; fraud policy routes to review.

## Failure 7: Three Fraud Labels Conflict with Source Evidence

**A. Reproducible identifiers.** PT_19116 (`readmission_30d`), PT_69470 (`amount_under_10k`), PT_39451 (`name_mismatch`); source files in `dataset/discharge_summaries`, `dataset/claim_forms`, and `dataset/id_documents` with those patient IDs.

**B. Expected versus actual.** The metadata labels say fraud-positive; inspected source data disagrees:

- PT_19116 discharge image/OCR documents only a 12/02/2025–12/05/2025 stay; no prior admission appears. The run approved it, but the label is not supported by this source.
- PT_69470 source bill/OCR totals `$23,290.00`, not below `$10,000`; the run escalated for DOB mismatch, not the amount label.
- PT_39451 claim/ID images and seeded SQLite member all identify Sandra Hall C., DOB 09/25/1993; the run approved it. The name-mismatch label is unsupported by reviewed evidence.

**C. Exact path.** Dataset generator/metadata -> [`fast_eval_30_clusters.py`](../../../scripts/fast_eval_30_clusters.py) groups `fraud_label` and derives its expected decision from that boolean -> [`fast_eval_results.json`](../../../scripts/fast_eval_results.json). The normal API run does not consult those labels; it processes the source documents, seeded members, and fraud context.

**D. Earliest divergence.** The labels are already inconsistent at the supplied dataset/ground-truth layer; no model is needed to explain the conflict. For PT_19116, the cited prior-admission fact is not in the provided discharge document. For PT_69470, the amount is directly legible and over $10k. For PT_39451, the policyholder/member and ID/claim identity match in the inspected records.

**E. Root-cause classification.** Missing/incorrect test data and absence of an evaluator-approved case-level truth register. The audit must not reinterpret the labels as facts or silently edit metadata.

**F. Smallest justified fix.** Preserve `dataset/metadata.json` unchanged. Propose a separate evaluator-controlled review/override register keyed by cluster/patient and `doc_id`; store original supplied label, observed source evidence, reviewer disposition (`PENDING`, `CONFIRMED`, `REJECTED`, `CORRECTED`), rationale, reviewer, timestamp, and approved case-level expected outcome. Evaluators approve entries before a benchmark consumes them.

**G. Architecture required?** No runtime architecture change. This is evaluation governance/data quality.

**H. Regression test.** Validate that benchmark inputs preserve original metadata and exclude disputed labels from decision scoring while `PENDING`; after explicit review, test that only the sidecar-approved outcome is used. Test the three cases against the register, not edited source metadata.

**I. Regression risk.** Until reviewed, case-level precision/recall and “false approval” numbers for these labels remain unverified. The audit should report scenario outputs and evidence, not an aggregate correctness score.

## Failure 8: Reruns Leave Multiple Active Decision Rows

**A. Reproducible identifiers.** SQLite contains 34 Decision rows for 30 `CASE-20261011-*` cases. Three rerun cases have repeated decision records, including `CASE-20261011-5D7A28` (PT_88139), `CASE-20261011-64723C` (PT_99687), and `CASE-20261011-9C0BC8` (PT_99733); inspected rows remained `is_active=true`.

**B. Expected versus actual.** Each rerun should retain previous decisions as history but mark the former active decision superseded and expose exactly one current active decision. Actual rerun path appends a second decision without superseding the earlier active record.

**C. Exact path.** `POST /api/v1/cases/{case_id}/rerun` in [cases.py](../../../src/medishield/api/routers/cases.py) invokes `process_case_workflow_task.run(... force_rerun=True)` when Redis is offline -> task rehydrates case/docs -> coordinator executes -> task updates the Case and adds a new `Decision(is_active=True)` -> one SQLAlchemy session commit. `get_case_detail` selects from active decisions through the ORM relationship; `Decision` and migration 001 have a non-unique index, not a one-active-row constraint.

**D. Earliest divergence.** Normal workflow task persistence never sets existing `Decision.is_active=False` before adding the new record. `resume_case_workflow_task` has a deactivation loop, but the rerun task does not. There is no partial unique index/constraint to prevent concurrent reruns from leaving multiple active rows.

**E. Root-cause classification.** Persistence implementation defect and missing database invariant/test. Not an OCR or decision-rule problem.

**F. Smallest justified fix.** In one transaction, lock/check the expected Case version, mark every previous active decision inactive, append the new decision, increment Case version, and commit. Add a partial unique index/constraint for one active decision per case after an evaluator-approved migration plan for existing rows; do not silently pick or discard prior decisions during cleanup.

**G. Architecture required?** Targeted persistence change plus a migration/invariant; no workflow redesign. Concurrent reruns need optimistic locking or a row lock as well as application logic.

**H. Regression test.** Execute two sequential reruns and assert both historical rows remain, exactly one row is active, the latest row's version matches the Case, and GET case detail returns that decision. Add a concurrent/version-conflict test and an existing-multiple-active migration-preflight test.

**I. Regression risk.** A migration may find current databases already have multiple active decisions. Require explicit reconciliation/backup and preserve the old rows as inactive history; never silently delete decision evidence.

## False-Approval Conditions in the Current P6 Rule

P6 currently checks: KYC, Claims, and Policy verdicts equal PASS with confidence at least 0.70; fraud is either absent or PASS below 0.30; and minimum collected confidence passes threshold. That is not equivalent to “all required evidence is verified.” It does not directly check:

- Claims `schema_valid`, line-total validity, service-date parse/provenance, any CPT/ICD/service-line code, or supported-code status.
- Policy `covered`, effective-date status, code-to-clause applicability, positive clause coverage of each billed line, or completeness of cited service-code mappings.
- `faithfulness_gate_passed` (the gate result is not part of `DecisionInputContext`).
- A mandatory fraud result; `fraud_output=None` is treated as clean.
- A missing document inventory when `present_document_types=[]` and `missing_required_documents=false`; P2 checks for missing types only when the list is truthy. Normal intake has a separate completeness step, but this is not a safe invariant for all callers.
- Required fields hidden by an agent that returns PASS despite missing codes/lines.

The most direct tests for this are the existing P6 helper/test: default-empty codes/service-lines can be admitted as `PASS`, and [test_decision_engine.py](../../../tests/unit/test_decision_engine.py) asserts P6 with that shape. This test should be replaced with a complete evidence fixture plus explicit negative tests; it must not continue to define incomplete evidence as a valid claim.

## Current Decision-Ownership Mismatch

The automated coordinator should leave final `decision` creation to `DeterministicDecisionEngine`, but `case_completeness_node` directly writes `state['decision']='ESCALATE'` and the coordinator early-returns before `decision_engine_node`; worker exception handling also writes the Case decision directly. The human-review router separately writes reviewer decisions by design in canonical 08/ADR-005. Recommended interpretation: route all automated P1/P2/P8 decisions through the deterministic engine; preserve human review as a separately authorized human action. If the owner intends literally no component except the engine (including human review endpoints) to write a final decision, resolve that contradiction against the canonical human-review contract before implementing.

## Proposed Priority Plan

### P0 — Prevent unsupported automated outcomes

1. **Preserve code candidates and fail closed on unvalidated/missing service codes.** Earliest code path is the allowlist filter in `extract_cpt_codes_from_table`; retain the OCR-grounded raw code candidate, separate extraction from validation, and make unsupported/unavailable reference results Claims `INDETERMINATE`. Do not label unknown CPT/ICD as invalid and reject it. This is the **single smallest high-value fix to implement first**: it is local to Claims extraction/verdict, has an exact reproducer (`59400`, `27447`, `21120`), prevents these missing-code cases from presenting as clean claims, preserves approval for the existing validated-code fixture, and routes uncertainty through existing P3 escalation.
2. **Add P6 approval admissibility at the existing Decision Engine boundary.** Require complete specialist outputs, a passed faithfulness/evidence gate, supported billing-code evidence, and code-scoped verified coverage for each material billed line. Any missing/unavailable/unmatched fact routes to ESCALATE; it never becomes REJECT. A fully supported claim still auto-approves.
3. **Restore confirmed hard auto-rejection per ADR-002.** Only after evidence is verified and uncertainty precedence P1–P4 is clear, map confirmed member/DOB hard failure or verified applicable exclusion to `BusinessDecision.REJECT` and `COMPLETED`. Current P5 emits `ESCALATE/RECOMMEND_DENIAL`; that contradicts ADR-002 and the user requirement. Unknown/unreadable/conflicting values remain ESCALATE. Tests and enum/migration contracts must be reconciled before coding.
4. **Correct KYC source-field association and expiry parsing.** Parse `EXP:` and generic expired marker from the correct ID document/span; associate DOB with the DOB-labeled value by document/geometry, not the next date in flattened span order. Missing/ambiguous evidence => INDETERMINATE; confirmed hard failure only after verified comparison. This prevents false hard failures such as PT_69470 as well as false approvals like PT_39816.
5. **Require positive policy scope match.** PASS only if every material billed code/line has a verified matching coverage clause for the selected published policy/version. A real but unrelated clause is not coverage evidence. Unknown/partial/unmatched scope => INDETERMINATE.

### P1 — Repair classification, fraud integration, and audit history

1. Fix classifier score accumulation; use anchored filename prefixes for the known generated naming convention, fall back to corrected OCR signatures, then use the existing LLM only for unresolved text ambiguity. Consider a VLM only if visual/layout ambiguity remains and only behind an explicit confidence/evidence contract. Implement the existing split/group contract for composites or return ambiguity. Do not use the disputed metadata as the only oracle.
2. Wire deterministic duplicate checks into the actual workflow. First compare multiple claim forms within one case using grounded claim number/service date and corroborating fields. Then use a populated claim-history repository for cross-case keys. The current helper works only when `duplicate_claim_match_id` is injected; `fraud_agent_node` supplies no context and defaults to no duplicate.
3. Parse normalized service dates (`MM/DD/YYYY` as well as ISO) for F-05, and explicitly implement or remove unsupported scenario expectations such as prescription-versus-claim temporal conflict/readmission; do not claim those features are present from the seven generic features alone.
4. Make rerun persistence supersede previous active decisions transactionally, with optimistic versioning and a one-active-decision constraint migration after explicit database reconciliation.
5. Generate explanations from final validated decision evidence; include source-linked claims/code/coverage fields and refuse “all checks passed” wording when any required evidence is missing. Keep LLM explanation text non-authoritative and test that every factual statement is backed by the summary/source.
6. Create a separate evaluator-owned review register for the disputed labels; preserve `metadata.json` byte-for-byte. Exclude pending rows from any decision metric.

### P2 — Establish external/reference data and evaluation coverage

1. Approve and version CPT/HCPCS and ICD-10-CM sources; do not silently expand the synthetic catalog into a production claim that it is complete.
2. Identify required provider checks and their separate authorities (NPI check digit/registry, payer enrollment/network, credential/license); do not equate ten digits with provider validity.
3. Define plan publication/runtime policy corpus linkage and verify policy version/effective dates against the correct system of record. Add service line and historical-claim persistence only with an explicit data/migration contract.
4. Add field-level OCR references and case-level reviewed expected decisions to the evaluator sidecar; otherwise report only classification and scenario-specific observations, not aggregate correctness.

## Regression Test Acceptance Matrix

| Fix | Minimum regression evidence |
|---|---|
| Classifier score/split | Exact saved OCR, LLM disabled; discharge medication terms do not override discharge title; member card isn't classified as prescription solely due to “Rx Tier”; bundled amendment+ID yields split/ambiguous outcome; replay all 151 only after labels reviewed. |
| CPT candidates and validation | OCR spans with 59400, 27447, 21120 remain visible as candidates; unsupported code -> Claims INDETERMINATE; LLM-off path behaves identically; supported code fixture still passes. |
| P6 safety gate | Missing code, line evidence, positive coverage scope, faithfulness flag, or mandatory fraud output cannot approve; all required valid inputs still approve. |
| Policy scope | Unrelated verified room-board clause cannot cover CPT 21120; partial code coverage on a multi-line claim is INDETERMINATE; verified matching exclusion follows auto-REJECT; published policy/version is the corpus queried. |
| KYC | `EXP: 03/28/2025` on PT_39816 ID yields grounded expiry; PT_69470 DOB selection uses the birth date, not admission date; unknown date remains INDETERMINATE; confirmed failure auto-REJECTs per ADR-002. |
| Duplicate fraud | Within-case duplicate claim number/date causes duplicate feature; cross-case feature only activates with history repository; unavailable history is not score-zero “unique”; nonduplicate remains low risk. |
| Ground truth | Sidecar state `PENDING` does not score a label; reviewer-confirmed disposition is auditable; source metadata is unchanged. |
| Rerun | Reprocessing preserves old decision rows as inactive history, has exactly one active row, returns current version, and detects concurrent stale rerun. |

## Architecture Decision

**Required:** targeted source fixes, a focused P6 evidence-admissibility gate in the existing Deterministic Decision Engine, small output-contract additions where a validated code-to-clause mapping is currently unrepresented, and one migration for active-decision uniqueness if adopted.

**Not required:** a repository rebuild, replacement of FastAPI/Python/OCR/Groq, a new agent framework, or broad orchestration rewrite. The existing normal intake and deterministic engine are adequate ownership boundaries once evidence status is explicit. A policy corpus connection and claims history are targeted data integrations, not reasons to rebuild the workflow.

**Decision-owner caveat:** ADR-002 requires automatic REJECT for confirmed hard failures, whereas the current engine returns ESCALATE/RECOMMEND_DENIAL. The future code change must align that path with ADR-002 and preserve the P1–P4 uncertainty precedence. The existing human-review API is a separately authorized human override under canonical 08/ADR-005; confirm whether the “Decision Engine only” constraint applies to automated decisions only or is intended to supersede that human-review contract.

## First Fix and Stop Point

Implement first: **preserve OCR-grounded but unsupported CPT candidates and return Claims `INDETERMINATE` instead of silently omitting them and returning PASS.** It is the smallest isolated change with direct source evidence for the most important false approvals (PT_62350 and the PT_24208/PT_30028/PT_45400 cosmetic claims). Its acceptance test can prove the uncertain-code-to-P3 escalation path while retaining P6 approval for a known supported-code clean claim. It does not invent code validity or touch the dataset.

This is a proposal only. No implementation or dataset-label edit has been performed; pause here for approval.

## Verification Against Current Working Tree (2026-10-12)

The user/worktree supplied targeted source and test changes after the original audit. I inspected those current files and ran the following checks without editing them.

**Focused automated tests:**

```text
GROQ_API_KEY='' GEMINI_API_KEY='' .venv/bin/pytest \
	tests/unit/test_ocr_and_classifier.py \
	tests/unit/test_kyc_and_claims_agents.py \
	tests/unit/test_ocr_reconciliation.py \
	tests/unit/test_policy_agents.py \
	tests/unit/test_policy_semantics.py \
	tests/unit/test_decision_engine.py \
	tests/unit/test_fraud_engine.py \
	tests/unit/test_case_graph.py \
	tests/unit/test_worker_tasks.py \
	tests/unit/test_evaluator_register.py \
	tests/unit/test_evaluation_framework.py \
	tests/unit/test_metrics_benchmark_api.py -q
146 passed, 1 warning in 12.58s
```

The warning is the existing Starlette/httpx TestClient deprecation. This test run validates unit contracts; it does not prove a live PostgreSQL transaction, external policy corpus, or all 30 workflow outcomes.

**Read-only normal-workflow replay:** I replayed all 30 existing `CASE-20261011-*` records through the current `CaseWorkflowCoordinator` using their saved OCR payloads and raw file references. Incoming document types were reset to `UNKNOWN` in memory so classification ran again. SQLite was set `PRAGMA query_only=ON`; a read-only storage adapter rejected writes/deletes; no API rerun endpoint was invoked; LLM calls were disabled. Thus saved Case/Decision rows and OCR files were not mutated. The replay used the coordinator's default in-memory policy corpus; it did **not** ingest or evaluate the Gold/Silver PDFs. It re-evaluated classification/claims/policy/decision over saved OCR, not OCR extraction itself.

```text
Cases replayed: 30
Metadata-category agreement: 140 / 151 (previous persisted labels: 118 / 151)
Dry replay outcomes: 28 ESCALATE/AWAITING_HUMAN, 1 REJECT/COMPLETED, 1 APPROVE/COMPLETED
Database/storage writes: disabled
LLM/VLM calls: disabled
```

This is a behavioral replay against synthetic fixtures, not a correctness/accuracy score. The patient metadata has no complete case-level expected outcomes, and three fraud labels remain pending review.

### Prior-Failure Status

| Prior issue | Current verification | Status |
|---|---|---|
| 1. 118/151 document types | Fixed cumulative signature accumulation raises replay agreement to 140/151. Eleven cases remain misclassified, mainly discharge summaries and amendments. Filename regex remains only a +5 score, not a filename-first cascade; mixed amendment/ID pages remain unsplit. | `PARTIALLY_FIXED` |
| 2. OCR-present CPT omitted | `extract_cpt_codes_from_table` now retains grounded 5-digit candidates outside the synthetic allowlist. Replay retained 59400/27447 for PT_62350 and 21120 for cosmetic cases; unsupported codes add an indeterminate error in cases not short-circuited by earlier arithmetic conflict. | `PARTIALLY_FIXED`: extraction is fixed; no authoritative CPT validation source exists, and validation-error ordering can hide unsupported status when arithmetic already conflicts. |
| 3. Missing CPT allowed to approve | Claims verdict now requires supported procedural codes on billed claims, and P6 has claims admissibility checks. The dry replay did not auto-approve the former no-code cosmetic cases. | `PARTIALLY_FIXED`: tested cases are blocked, but completeness is not line-specific and the P6 gate does not require every material line to have a validated code. |
| 4. Policy PASS on unrelated clause | Positive clauses are now scope-filtered and empty positive scope becomes indeterminate. However, policy admissibility in P6 checks only that `applicable_service_codes` is nonempty, not that every billed CPT/revenue line is covered. PT_19116 replay approved with CPTs 90837/93000 while policy scope listed 93000 and revenue code 0120, not 90837. The runtime corpus is still the hard-coded default, not the attached PDFs. | `PARTIALLY_FIXED`; remaining P6 false-approval path and external corpus integration. |
| 5. PT_39816 expired ID approved | Dry replay now extracts/flags expiry and does not approve PT_39816; it escalates because other evidence is ambiguous. | `FIXED_FOR_REPRODUCER`; service-date argument is still not passed from `kyc_agent_node`, so date-boundary behavior beyond this fixture remains unverified. |
| 6. PT_20322 duplicate missed | Dry replay detects the shared claim number `CLM-2024-000005`, sets duplicate feature, and scores 0.3716. Final result is ESCALATE/AMBIGUITY because arithmetic conflict P3 precedes P4; it is not auto-approved. | `FIXED_WITHIN_CASE`; duplicate is detected, though reason precedence remains P3. Historical cross-case duplicate source is not wired. |
| 7. Disputed labels | `scripts/evaluator_review_register.json` contains the three disputed records with `PENDING` and `EXCLUDE_FROM_SCORING`, but source search found no reference to the register in benchmark/evaluator code. `scripts/analyze_adjudication.py` still derives expected outcomes directly from `fraud_label`. | `NOT FIXED`; register is a sidecar only. Preserve labels and wire scoring only after evaluator approval. |
| 8. Multiple active decisions after rerun | `process_case_workflow_task` now deactivates previously active rows before adding a decision, and a worker unit test covers this behavior. No partial unique DB constraint/migration exists; the old saved SQLite rows were not modified by this read-only replay, and no live transactional rerun was performed. | `PARTIALLY FIXED`; sequential application path tested, database invariant/concurrent rerun unverified. |

### Additional Replay Finding: PT_69470 DOB Association

PT_69470 still has KYC `FAIL/DEMOGRAPHIC_DOB_MISMATCH` in the dry replay. The OCR source/member record agree on DOB 02/25/1976, but KYC's proximity matcher associates the 09/09/2025 claim/admission date with a birth-date heading in flattened OCR spans. The newly added skip for spans containing admission/service/discharge labels does not prevent this case's false mismatch. Final case escalates under P3 because Claims are also indeterminate; it does not auto-reject. This remains an implementation defect and needs a document-local, field-label-associated DOB regression fixture before P5 can safely auto-reject it.

### Additional Replay Finding: PT_19116 Approval Scope

PT_19116 was the sole replay P6 approval. Its supplied `readmission_30d` label is pending and unsupported by the source discharge summary, so this is not counted as a confirmed fraud false approval. However, the replayed policy result lists applicability for 93000 and revenue code 0120 while the Claims result also includes CPT 90837; the static runtime corpus has no matching 90837 covered clause. Current P6 only checks that some applicable code exists, so the approval still demonstrates F-17's partial-code-coverage hole.

### What Was Not Rerun

- No case API `/rerun` requests were sent, to avoid mutating the existing 30 cases or creating more decision-history rows.
- [`fast_eval_30_clusters.py`](../../../scripts/fast_eval_30_clusters.py) was not run because it drops all tables and deletes `data/qdrant_eval` before executing.
- [`analyze_adjudication.py`](../../../scripts/analyze_adjudication.py) was not run because it writes `scripts/diagnostic_summary.json`, calls the external LLM path, and scores decisions from the disputed metadata labels.
- The benchmark review register remains disconnected from evaluator code; no independent case-level gold truth exists for a global correctness score.

**Current conclusion:** several local defects are now mitigated, but the earlier issues are not all fixed. The most important remaining risk is P6's existential policy-scope check: at least one matched service code can admit approval even when another billed code lacks applicable policy evidence. Next are the 11 classification mismatches, PT_69470 DOB misassociation, evaluator-register wiring, external policy corpus source, and persistent one-active-decision invariant.
