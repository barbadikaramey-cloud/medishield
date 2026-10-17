# MediShield Claims Validation Alignment Audit

**Audit type:** Read-only implementation assessment  
**Audit date:** 2026-10-12  
**Scope:** Seven validation areas supplied in the audit request, current code, canonical contracts, SDD feature specifications, migrations, relevant tests, and observed local dataset run.  
**Change constraint:** This audit created only this report. No application files, schemas, migrations, or existing specifications were changed.

## 1. Executive Summary

The repository contains working extraction, case classification, policy date filtering, deterministic adjudication, OCR provenance, and synthetic-data paths. Several of those paths are narrower than their names or design documents imply. In particular, there is no dependent eligibility model; CPT/ICD and provider checks are not authoritative reference validation; claim-line math uses floats and does not verify unit-price multiplication; the policy runtime uses a built-in in-memory corpus rather than the admin-published policy index; supplementary-document recovery is not end-to-end; and no code calculates member cost share.

The requested `claims_validation_matrix.md` was not found. The repository has lowercase `agents.md`, canonical specifications, and SDD feature specs. FEAT-008 and FEAT-009 are marked Proposed; FEAT-010/012 are specifications, not proof of implementation. The audit uses the seven areas in the request as the matrix baseline and calls out that the matrix itself was unavailable.

Observed in the preceding local run: 30 patient groups and 151 dataset documents were submitted through the normal intake workflow, standalone Gold/Silver plan PDFs excluded, and all 151 documents had retrievable OCR payloads. Case outcomes were 13 `COMPLETED`, 16 `AWAITING_HUMAN`, and 1 `AWAITING_INFO`. These are runtime observations, not accuracy evidence; the dataset and member records are synthetic.

The retrospective check of those persisted October 11 outcomes found a measured document-type agreement of 118/151 against dataset category labels, several unsupported approvals, at least three fraud-scenario labels that contradict their own source records, and explanations that repeat those incorrect decisions. The run has no field-level OCR transcript gold set or patient-case expected-decision labels, so no overall OCR word-accuracy or adjudication-accuracy percentage is claimed.

**Highest-risk findings:** the P5 `RECOMMEND_DENIAL` value is absent from the initial PostgreSQL enum migration; the policy execution path is disconnected from admin-published policy chunks and selects using enrollment dates rather than the version's effective dates; supplementary evidence cannot be posted and the resume task constructs an empty case state; and billed/service-line amounts cannot support a defensible patient-payable calculation.

## 2. Repository and Implementation Scope Inspected

- Instructions: [`agents.md`](../../../agents.md) is present; no `AGENTS.md` was found.
- Matrix: no `claims_validation_matrix.md` was found under the repository. The only filename search hit resembling the topic was FEAT-008.
- Canonical contracts inspected: architecture, decision/state machine, multi-document intake, orchestration, agent interfaces, faithfulness/provenance, database/storage, REST API, policy RAG/admin, security/evaluation references. Key files include [`02_DECISION_ENGINE_AND_STATE_MACHINE.md`](../../canonical/02_DECISION_ENGINE_AND_STATE_MACHINE.md), [`04_LANGGRAPH_STATE_AND_ORCHESTRATION_CONTRACT.md`](../../canonical/04_LANGGRAPH_STATE_AND_ORCHESTRATION_CONTRACT.md), [`05_AGENT_INTERFACE_CONTRACTS.md`](../../canonical/05_AGENT_INTERFACE_CONTRACTS.md), [`06_FAITHFULNESS_PROVENANCE_AND_VERIFICATION.md`](../../canonical/06_FAITHFULNESS_PROVENANCE_AND_VERIFICATION.md), [`07_DATABASE_AND_STORAGE_CONTRACTS.md`](../../canonical/07_DATABASE_AND_STORAGE_CONTRACTS.md), [`08_REST_API_SPECIFICATION.md`](../../canonical/08_REST_API_SPECIFICATION.md), [`09_POLICY_RAG_AND_ADMINISTRATION_SPECIFICATION.md`](../../canonical/09_POLICY_RAG_AND_ADMINISTRATION_SPECIFICATION.md), and [`12_EVALUATION_AND_SYNTHETIC_DATA_FRAMEWORK.md`](../../canonical/12_EVALUATION_AND_SYNTHETIC_DATA_FRAMEWORK.md).
- SDD specs inspected: [`FEAT-008_KYC_AND_CLAIMS_AGENTS.md`](../features/FEAT-008_KYC_AND_CLAIMS_AGENTS.md), [`FEAT-009_POLICY_RAG_AGENT.md`](../features/FEAT-009_POLICY_RAG_AGENT.md), [`FEAT-010_LANGGRAPH_WORKFLOW_COORDINATOR.md`](../features/FEAT-010_LANGGRAPH_WORKFLOW_COORDINATOR.md), and [`FEAT-012_REST_API_CONTROL_PLANE.md`](../features/FEAT-012_REST_API_CONTROL_PLANE.md).
- Backend: Python 3.11, FastAPI, SQLAlchemy, Pydantic, imperative Python workflow coordinator, Celery task wrapper, local/SQLite development fallback, OCR pipeline, Groq-assisted extraction/reasoning, and an embedded Qdrant policy-ingestion component.
- Schema: SQLAlchemy definitions in [`models.py`](../../../src/medishield/db/models.py); two Alembic revisions, [`001_initial_schema.py`](../../../migrations/versions/001_initial_schema.py) and [`002_policy_chunk_provenance.py`](../../../migrations/versions/002_policy_chunk_provenance.py).
- Main implementation surfaces: [`kyc.py`](../../../src/medishield/agents/kyc.py), [`claims.py`](../../../src/medishield/agents/claims.py), [`policy.py`](../../../src/medishield/agents/policy.py), [`ocr_reconcile.py`](../../../src/medishield/ocr/ocr_reconcile.py), [`completeness.py`](../../../src/medishield/intake/completeness.py), [`coordinator.py`](../../../src/medishield/workflows/coordinator.py), [`nodes.py`](../../../src/medishield/workflows/nodes.py), [`decision.py`](../../../src/medishield/domain/decision.py), [`faithfulness.py`](../../../src/medishield/domain/faithfulness.py), [`cases.py`](../../../src/medishield/api/routers/cases.py), [`review.py`](../../../src/medishield/api/routers/review.py), and [`policies.py`](../../../src/medishield/api/routers/policies.py).
- Relevant tests: [`test_kyc_and_claims_agents.py`](../../../tests/unit/test_kyc_and_claims_agents.py), [`test_policy_agents.py`](../../../tests/unit/test_policy_agents.py), [`test_case_graph.py`](../../../tests/unit/test_case_graph.py), [`test_intake_pipeline.py`](../../../tests/unit/test_intake_pipeline.py), [`test_api.py`](../../../tests/unit/test_api.py), [`test_models.py`](../../../tests/unit/test_models.py), [`test_policy_ingestion.py`](../../../tests/unit/test_policy_ingestion.py), and [`test_worker_tasks.py`](../../../tests/unit/test_worker_tasks.py).
- The source coordinator calls OCR, split placeholder, classifier, completeness, KYC, claims, policy context, policy join/RAG, fraud, faithfulness, and decision in a direct sequence. No LangGraph package or `StateGraph` implementation was found in the runtime dependency/source paths inspected.
- The dataset and database seeder define synthetic members and claims; they are assignment/test inputs, not an external payer enrollment, provider, CPT/ICD, or financial accumulator source.

## 3. Validation Alignment Scorecard

| Area | Status | Highest gap severity | Summary |
|---|---|---|---|
| Member/dependent eligibility | `PARTIALLY_IMPLEMENTED` | High | Member ID lookup and DOB/ID checks exist. No dependent relationship data, name reconciliation, or reliable distinction between enrollment absence and lookup unavailability. |
| Policy effective-date selection | `PARTIALLY_IMPLEMENTED` | High | A date-window helper and tests exist, but SQL candidates use enrollment dates, overlapping matches select the first row, publication state is ignored, and production policy chunks are not wired into runtime RAG. |
| CPT/ICD validation | `PARTIALLY_IMPLEMENTED` | High | CPT uses a small synthetic allowlist; ICD-10 is regex extraction only. No versioned authoritative code source or safe reference-data outage result. |
| Provider validation | `PARTIALLY_IMPLEMENTED` | High | Provider name/NPI can be extracted. NPI validation only checks ten numeric digits; no check digit or provider identity/enrollment reference exists. |
| Claim-line arithmetic | `PARTIALLY_IMPLEMENTED` | Critical | Multi-line totals can be summed, but calculations are binary floats, unit-charge multiplication is absent, and no service lines can still yield an arithmetic PASS. Extracted claims are not persisted to `Claim`/`ClaimLine`. |
| Case completeness | `PARTIALLY_IMPLEMENTED` | High | Required-document matrix, per-document classification, and `AWAITING_INFO` exist. No supplementary-document endpoint exists and resume does not hydrate attached documents. |
| Copay/member cost share | `MISSING` | High | Policy percentage/deductible-limit fields are returned, but no allowed amount, remaining deductible, accumulator, member responsibility, or insurer liability is calculated. |

No numeric aggregate alignment percentage is assigned.

## 4. Detailed Traceability Matrix

| Requirement | Purpose and canonical reference | Actual trace and dependencies | Existing coverage / runtime evidence | Status and gap severity |
|---|---|---|---|---|
| CV-01 Member/dependent eligibility | Identify the covered person and validate eligibility. Canonical 05 §4, 07 members/enrollments, 04 Node 5A. | [`SqlMemberRepository.find_member`](../../../src/medishield/agents/kyc.py) queries synthetic `Member` and `Enrollment`; [`KYCAgent.execute`](../../../src/medishield/agents/kyc.py) extracts member ID/DOB/ID expiry. ORM [`Member`](../../../src/medishield/db/models.py) has no relationship/dependent type. SQL depends on seeded SQLite/PostgreSQL data. | KYC unit tests cover matching, unknown member, DOB mismatch, expiry, tamper, and missing ID. No dependent test. Prior runtime processed the 30 synthetic patient clusters. | `PARTIALLY_IMPLEMENTED`; High. See F-01. |
| CV-02 Policy effective-date selection | Resolve member's applicable published policy/version on the service date. Canonical 04 Node 5C/JoinClaimReady, 05 §6, 07 policy versions/enrollments, 09 §3. | [`SqlPolicyEnrollmentRepository.find_enrollments`](../../../src/medishield/agents/policy.py) maps `Enrollment.coverage_start_date/end_date` into candidate effective dates; [`resolve_claim_ready_policy`](../../../src/medishield/agents/policy.py) returns the first matching candidate. [`join_claim_ready_barrier`](../../../src/medishield/workflows/nodes.py) parses extracted service date. Policy admin publishes versions/chunks through [`policies.py`](../../../src/medishield/api/routers/policies.py), but coordinator defaults to [`PolicyCorpusRepository`](../../../src/medishield/agents/policy.py). | Unit tests cover basic date windows, open-ended end date, and outside bounds. They do not cover overlapping windows, SQL version dates, `is_published`, or admin-publish-to-runtime retrieval. | `PARTIALLY_IMPLEMENTED`; High. See F-02 and F-09. |
| CV-03 CPT/ICD validation | Establish that extracted codes are valid for a recognized code set/version. Canonical 05 §5, 06 §2, 09 §3.2. | [`ClaimsAgent`](../../../src/medishield/agents/claims.py) parses ICD-like strings with regex and calls `extract_cpt_codes_from_table` using `SYNTHETIC_VALID_CPT_CODES` in [`ocr_reconcile.py`](../../../src/medishield/ocr/ocr_reconcile.py). LLM fallback can create code candidates without provenance. There is no versioned code database. | Clean-claim and arithmetic tests assert one CPT and ICD string; no authoritative invalid-but-well-formed code tests or version tests. The exclusion graph test for CPT 15780 failed in the focused run when Groq returned 429 and no CPT was extracted. | `PARTIALLY_IMPLEMENTED`; High. See F-03. |
| CV-04 Provider validation | Confirm provider identity and applicable qualification/network state. Canonical 05 §5 and 07 claims/provider fields. | [`ClaimsAgent._extract_provider_npi`](../../../src/medishield/agents/claims.py) extracts an NPI; validation is only a `^\d{10}$` regex. [`Claim`](../../../src/medishield/db/models.py) stores NPI as text but has no provider foreign key/reference repository. | Test rejects a five-digit NPI only. No check-digit, identity, payer enrollment, credential, license, network, missing-provider conflict, or evidence record tests. | `PARTIALLY_IMPLEMENTED`; High. See F-04. |
| CV-05 Claim-line arithmetic | Reconcile line totals to the claim total using sound financial rules. Canonical 05 §5, 06 §4, 07 claims/claim_lines. | [`ServiceLine`](../../../src/medishield/domain/contracts.py) contains units and amount, not unit charge. [`ClaimsAgent.execute`](../../../src/medishield/agents/claims.py) sums floats; [`evaluate_claim_totals`](../../../src/medishield/ocr/ocr_reconcile.py) sums line amount floats. SQL `ClaimLine` has `Numeric` fields, but no processing code constructs `Claim` or `ClaimLine`. | Tests cover clean two-line sum, drift, and negative charge. No multiplication/rounding/duplicate-line/currency tests. `test_claims_agent_pass_clean_claim` demonstrates multiple lines only at summed-amount level. | `PARTIALLY_IMPLEMENTED`; Critical. See F-05. |
| CV-06 Case completeness | Determine required evidence and allow evidence-driven recovery. Canonical 03 §4, 04 Node 4, 07 case/documents, 08 §2.4–2.5. | [`evaluate_case_completeness`](../../../src/medishield/intake/completeness.py) uses a required/supporting matrix and confidence threshold. Intake classifies each submitted file and stores missing types. `/review/request-info` and `/process` routes exist; no `POST /cases/{id}/documents` route exists in [`cases.py`](../../../src/medishield/api/routers/cases.py). [`resume_case_workflow_task`](../../../src/medishield/worker/tasks.py) constructs empty docs absent explicit state. | Tests verify required and low-confidence/missing documents and request-info state; no supplementary upload/resume integration coverage. | `PARTIALLY_IMPLEMENTED`; High. See F-06. |
| CV-07 Copay/member cost share | Reconcile policy benefits and member financial accumulators into patient/insurer liabilities. Canonical 05 §6.2, 07 claim financial data, 09 §4. | Policy chunks carry `covered_percentage`, `deductible_limit`, and `copay_percentage`. [`PolicyCoverageAgent.execute`](../../../src/medishield/agents/policy.py) returns these descriptive values. Claim amount is accepted as a parameter but does not drive a payment calculation. No deductible/OOP accumulator or allowed-amount model/repository exists. | Tests check policy verdicts/citation and contain benefit fixtures; no cost-share calculation tests. | `MISSING`; High. See F-07. |

## 5. Member/Dependent Eligibility Findings

**Business problem.** The adjudicator must identify the covered person, not just find a similarly named policyholder, and must prove active coverage for that person on the service date.

**What exists.** `KYCAgent` extracts member ID, first/last name, DOB, and government ID expiry from OCR spans. `SqlMemberRepository` resolves a direct `members.member_id`, with a fallback lookup by policy ID through an enrollment join. It compares DOB if both sides are available, checks member status, and flags expired IDs. The source data is synthetic: [`seeder.py`](../../../src/medishield/db/seeder.py) creates benchmark members/enrollments; the database spec calls the member/enrollment adapter synthetic/read-only.

**Data flow and what works.** OCR spans become `ProvenanceValue`s, then member ID selects a database record, then DOB/status/expiry contribute to KYC verdict and decision-engine input. Unit tests demonstrate a matching synthetic member and known negative/missing cases. No KYC name comparison is performed, so a different patient name is not independently rejected by the current method.

**Gaps.** `Member` has no policyholder/dependent relation or relationship category. A dependent cannot be proven covered under another member using a first-class relationship. KYC does not receive `service_date` from the workflow node; effective coverage is deferred to a separate policy context path. `find_member` selects `.first()` and then the first active enrollment, without ambiguity reporting. `PolicyContextAgent` returns `UNAVAILABLE` both for no enrollment rows and repository exceptions, losing the distinction between a confirmed negative and a dependency outage.

**Example.** A dependent's card can identify the dependent, but if the database only contains the subscriber, the code cannot establish the dependent-to-subscriber relationship; it does not have enough data to safely conclude either covered or uncovered.

**Next smallest testable improvement.** Add characterization tests for subscriber/dependent identity, absent/duplicate member rows, missing DOB, and enrollment boundaries before designing a relationship schema. Do not use name inequality alone as the dependent exclusion rule.

**Findings.**

- **F-01 — High — dependent/member coverage is not modeled.** Requirement CV-01; FEAT-008 REQ-KYC-01; canonical 05 §4 and 07 members/enrollments. Evidence: `Member` only has member ID/name/DOB/status; `Enrollment` links one member to a policy version; there is no subscriber relationship. Why it matters: valid dependents cannot be reliably associated or distinguished from an unmatched patient. Type: missing data model/source and functional limitation. Next: define an authoritative relationship source and add isolated tests before implementation.
- **F-02a — Medium — KYC is not service-date-aware in the workflow.** Requirement CV-01/CV-02; canonical 04 Nodes 5A/5C. Evidence: `kyc_agent_node` calls `execute(case_id, document_payloads)` without `service_date`; policy-date check therefore uses member status only on this path. Why it matters: KYC output's `policy_active_on_service_date` does not mean a service-date check occurred. Type: integration/contract gap. Next: test and document which single component owns date eligibility.

## 6. Policy Effective-Date Findings

**Business problem.** Select the correct authoritative policy version for the validated service date; a member's current plan or a customer-supplied amendment must not silently determine coverage.

**What exists.** Stage A accepts member ID only and enumerates candidate enrollments. `join_claim_ready_barrier` parses a service date string and `resolve_claim_ready_policy` uses inclusive start/end comparisons. The focused policy tests prove ordinary 2025/2026 selection, open-ended end dates, and before/after boundaries. The customer upload path classifies `POLICY_AMENDMENT` as case evidence; the admin-only `/policies` and `/{policy_id}/publish` paths are separate.

**Data flow and what works.** Service date is extracted in Claims, candidate policies are read from enrollment data, and the join barrier chooses a candidate before coverage evaluation. A missing/unparseable date does not manufacture a date; the barrier preserves indeterminate state. Policy admin requires administrator role and stores a draft/published policy version and chunks.

**Gaps.** SQL candidate construction copies `Enrollment.coverage_start_date/end_date`, not `PolicyVersion.effective_start_date/end_date`; it does not filter `PolicyVersion.is_published` or `Policy.is_active`. Overlapping windows are resolved by first row, not reported as conflicting. Stage B defaults to a hand-built in-memory `PolicyCorpusRepository` with a small static synthetic corpus; no runtime adapter loads admin-published relational/Qdrant chunks into it. Policy context lookup with no rows is `UNAVAILABLE`, unlike FEAT-009's stated confirmed-no-enrollment `FAIL`. `ClaimsAgent` does not expose a dedicated validated date flag; the barrier parses the summary's value rather than requiring explicit date/provenance validation.

**Example.** If a policy version is effective 2026-01-01 through 2026-06-30 but an enrollment row extends through 2026-12-31, the repository supplies the enrollment range as the candidate window and can select that version for a July service date. If two candidates overlap, the first one wins.

**Next smallest testable improvement.** Add SQL repository tests with differing enrollment/version dates, unpublished versions, overlapping candidates, and no-enrollment versus database exception. Keep the policy corpus source in the test fixture explicit.

**Findings.**

- **F-02 — High — candidate dates do not come from the policy version and overlap is arbitrary.** Requirement CV-02; FEAT-009 REQ-POL-02; canonical 07 policy versions and 09 §3.1. Evidence: `SqlPolicyEnrollmentRepository` maps enrollment coverage dates to `CandidatePolicy`; resolver returns the first matching candidate. Why it matters: date-valid selection can use the wrong version or silently choose among conflicting versions. Type: functional/data-integrity defect. Next: test and resolve against published version dates and explicitly handle overlapping candidates.
- **F-09 — High — admin-published policies are disconnected from runtime coverage RAG.** Requirement CV-02; FEAT-009 REQ-POL-04/05; canonical 09 §§1–4. Evidence: the admin router indexes via `EmbeddedQdrantPolicyIndex` and stores `PolicyChunk`; coordinator creates default `PolicyCorpusRepository()` with hard-coded clauses and never injects a published-policy adapter. Why it matters: an approved policy publication may not change adjudication, which can instead use synthetic sample clauses. Type: integration/data-source defect. Next: define and test one authoritative publication-to-query path, including exact policy/version filtering.
- **F-10 — Medium — no enrollment and dependency outage collapse to the same policy verdict.** Requirement CV-02; FEAT-009 Stage A acceptance; canonical 05 §6.1 and 02 uncertainty rules. Evidence: `PolicyContextAgent.execute` returns `UNAVAILABLE` for both empty records and caught repository exceptions. Why it matters: audit/review cannot distinguish no coverage from unavailable evidence; final outcome is necessarily an operational escalation, not a confirmed policy result. Type: contract/data-state gap. Next: add tests and distinct domain outcomes without turning absence into an unsupported denial.

## 7. CPT/ICD Findings

**Business problem.** Extracted medical codes must be grounded and then checked against recognized, applicable code sets; matching a regex is not code validation.

**What exists.** Claims extracts diagnosis-like strings with an ICD pattern and recognizes CPTs only in service-table regions against `SYNTHETIC_VALID_CPT_CODES` (16 hard-coded values). Provenance is attached for OCR-derived values. The policy agent has a small CPT expansion dictionary for retrieval. Tests verify basic extraction of `J02.9`, `99213`, and extraction/arithmetic behavior.

**Data flow and what works.** OCR spans feed deterministic parsers; selected CPTs then become policy query terms. The system does not let a result of the format regex alone become proof of clinical validity in documentation, though there is no separate validator enforcing that distinction.

**Gaps.** The CPT set is a finite assignment list, not a licensed/current AMA code service or versioned public reference; it has no effective-date/code-set metadata. ICD-10-CM is regex-only with no existence, billable specificity, or version check. LLM fallback can add code candidates with `provenance=None`; Claims verdict does not require provenance on each code/line. FEAT-010's example exclusion CPT `15780` is in policy expansion/static exclusion fixtures but not in `SYNTHETIC_VALID_CPT_CODES`; the end-to-end exclusion test failed while Groq was rate limited, demonstrating dependence on fallback for this missing catalog entry.

**Example.** `ABC.1` or a syntactically plausible but nonexistent ICD string can pass the regex extraction shape; no code-set lookup rejects it. CPT `15780` is known to the test policy corpus but absent from the deterministic extraction allowlist.

**Next smallest testable improvement.** Keep extraction and validation as separate outputs; add tests for valid, malformed, well-formed-but-unknown, version-boundary, and unavailable-reference cases using a clearly named, versioned fixture/source.

**Findings.**

- **F-03 — High — code parsing is not authoritative CPT/ICD validation.** Requirement CV-03; FEAT-008 REQ-CLM-02/03; canonical 05 §5 and 09 §3.2. Evidence: ICD regex and hard-coded `SYNTHETIC_VALID_CPT_CODES`; no catalog version or ICD reference source. Why it matters: invalid or obsolete codes can be interpreted as valid evidence, and valid unsupported codes can become indeterminate. Type: missing data source/functional limitation. Next: specify licensing/version/source and represent unavailable reference data as unavailable/indeterminate.
- **F-03b — Medium — code provenance is not a required decision gate.** Requirement CV-03; canonical 05 §5, 06 §§1–2. Evidence: Groq fallback creates provenance-free `ProvenanceValue`s; claims verdict only checks schema, totals, and NPI. Why it matters: ungrounded code assertions can reach policy lookup without a grounding failure. Type: functional verification gap. Next: test that ungrounded candidates cannot be treated as validated claim codes.

## 8. Provider Validation Findings

**Business problem.** A provider number must be linked to the correct provider and payer-specific status; number shape is only one small syntactic check.

**What exists.** Claims extracts provider name and NPI from OCR spans and marks NPI schema-invalid unless it is exactly ten digits. The SQL claim model has a provider NPI text field.

**Data flow and what works.** NPI text is preserved as a provenance value from OCR; malformed-length input becomes `INDETERMINATE` through claims schema validation. Existing tests prove rejection of a five-digit value.

**Gaps.** No NPI check digit/Luhn validation exists, and no provider registry, NPI directory adapter, provider table, credentialing/licensure/network contract, or payer enrollment source was found. The 10-digit regex cannot distinguish a real provider from a fabricated number. Provider name is extracted but not checked against NPI.

**Example.** Any ten numeric digits satisfy the current regex, even when the NPI check digit or provider identity does not correspond.

**Next smallest testable improvement.** Add a unit test using a known invalid check digit and document the boundary between identifier-format validation and authoritative provider status; do not call the format check credentialing.

**Findings.**

- **F-04 — High — NPI shape is treated as the only provider validation.** Requirement CV-04; FEAT-008 REQ-CLM-02; canonical 05 §5. Evidence: `ClaimsAgent.execute` checks only `^\d{10}$`; no provider repository/model exists. Why it matters: fabricated, mismatched, unlicensed, or out-of-network providers can pass this check. Type: missing data source and functional limitation. Next: define separate evidence-backed checks and test each supported level.

## 9. Claim-Line Arithmetic Findings

**Business problem.** The claim total must reconcile with the charges actually billed; units, unit price, allowed amount, and patient responsibility are distinct quantities.

**What exists.** The extraction pipeline can parse multiple rows and compare summed line `amount` to header amount within one cent. It flags negative line totals/nonpositive units. A Pydantic `ServiceLine` includes units and amount, and SQLAlchemy `ClaimLine` stores `Numeric` quantity/unit/total fields.

**Data flow and what works.** OCR table geometry yields line amounts; [`evaluate_claim_totals`](../../../src/medishield/ocr/ocr_reconcile.py) sums line amounts and compares against extracted header. Tests demonstrate two-line reconciliation, drift conflict, and negative-charge failure.

**Gaps.** The runtime model has no unit-charge value on `ServiceLine`; extracted quantity is not multiplied by unit price. It uses Python `float` and `round`, not Decimal with an explicit currency/rounding policy. If a declared header total exists but no lines are extracted, `evaluate_claim_totals` returns PASS; missing itemization is not distinguished from a validated zero-line sum. Duplicate line identities and per-line dates/currencies are not checked. Although claim/line ORM models and migrations exist, source search found no `Claim(...)` or `ClaimLine(...)` construction outside model definitions, so processed values remain in workflow state/decision snapshots instead of normalized claim records.

**Example.** A claim with a $100 header and no detected service lines can be marked line-sum valid because the total validator returns PASS in its no-lines branch. A line with quantity 3 and unit charge $10 cannot be independently verified because the runtime line schema only carries total amount.

**Next smallest testable improvement.** Add pure tests for no-lines-with-header, quantity × unit charge, decimal half-cent boundaries, duplicates, and missing amounts; first specify which amount each field represents. Do not invent a payer rounding or allowed-amount rule.

**Findings.**

- **F-05 — Critical — line reconciliation can pass without line evidence and is not unit-price math.** Requirement CV-05; FEAT-008 REQ-CLM-02; canonical 05 §5 and 07 claims/claim_lines. Evidence: no service lines plus declared amount returns `PASS`; extraction sums floats and has no unit charge in `ServiceLine`. Why it matters: arithmetic confidence can overstate the evidence supporting the billed total. Type: functional/data-integrity defect. Next: specify evidence requirements and deterministic decimal/line rules, then cover boundary cases.
- **F-05b — High — normalized claims and service lines are not persisted.** Requirement CV-05; canonical 07 claims/claim_lines. Evidence: models/migrations exist but processing source does not instantiate `Claim` or `ClaimLine`. Why it matters: downstream audit/history/accumulators have no durable normalized claim-line source. Type: persistence/integration gap. Next: establish and test the authoritative persistence transaction before building history-dependent financial logic.

## 10. Case Completeness Findings

**Business problem.** A case must distinguish required from supporting evidence and be recoverable after the requester supplies what is missing.

**What exists.** Intake stores multiple documents per case, classifies each document, and checks a claim-type matrix at 0.70 classification confidence. The default reimbursement case requires claim and ID; supporting discharge summary/prescription do not block. Missing required types persist on the Case and transition the workflow to `AWAITING_INFO`. Tests verify required-vs-missing/low-confidence decisions. Review can request information, and a `/process` endpoint exists.

**Data flow and what works.** Each uploaded document gets its own classification; completeness produces missing types, case state, escalation reason, and a decision snapshot. Normal intake was observed to OCR all 151 submitted dataset documents.

**Gaps.** The canonical `POST /cases/{case_id}/documents` supplementary upload endpoint is absent. More importantly, `resume_case_workflow_task` with no explicit state creates `create_initial_case_state(..., documents=[])`; it does not hydrate persisted case documents. Thus `/process` cannot re-evaluate new evidence using the request path as currently wired. The `split_group_node` only adds page indices; it does not implement the canonical PDF page splitting/grouping contract. Completeness currently counts document types, not optional documents or per-case evidence/field requirements beyond the configured matrix.

**Example.** A case paused because its ID was missing can enter `/process`, but there is no public route to attach the ID; even if rows were inserted elsewhere, the resume task starts with an empty document list and hits completeness again.

**Next smallest testable improvement.** Add an integration test from `AWAITING_INFO` through supplementary upload, OCR/classification, resume, and terminal state; assert versioning, missing-document clearing, evidence retention, and audit events.

**Findings.**

- **F-06 — High — AWAITING_INFO has no functioning evidence recovery loop.** Requirement CV-06; FEAT-012 REQ-API-03; canonical 03 §4 and 08 §§2.4–2.5. Evidence: no supplementary `POST /documents` route; resume task creates an empty state unless supplied one. Why it matters: incomplete claims can remain stuck and cannot be completed through the intended API lifecycle. Type: functional/API/workflow defect. Next: design and test the full supplement-and-resume transaction before further completeness rules.
- **F-06b — Medium — completeness bypasses the Decision Engine on early halt.** Requirement CV-06 and cross-component decision ownership; canonical 02 §§1–3 and 04 Node 4. Evidence: `case_completeness_node` directly sets `decision=ESCALATE`; coordinator audits/returns before calling `decision_engine_node`. Why it matters: the stated single authoritative decision component is not the only producer of a business decision. Type: architecture/contract contradiction. Next: reconcile the canonical ownership rule with early completeness output and characterize it in tests.

## 11. Copay and Cost-Share Findings

**Business problem.** A coverage percentage or plan copay is not a payable amount. Correct member responsibility depends on negotiated allowed charges, benefit order, deductible already met, limits, network, and applicable prior payments.

**What exists.** `PolicyChunkRecord` and `PolicyCoverageOutput` carry policy coverage percentage, a deductible limit, and copay percentage. These are sourced from hard-coded default clauses or ingested text/chunk metadata. The policy agent can identify a verified exclusion and prior authorization requirement. It produces a coverage verdict and reasoning string.

**What is absent.** No allowed/negotiated charge, remaining deductible, annual out-of-pocket accumulator, benefit maximum accumulator, network status input, prior-payment/adjustment data, member responsibility, or insurer liability model/repository was found. `claim_amount` is accepted by `PolicyCoverageAgent.execute` but unused in its calculation. The LLM's policy reasoning can describe benefits but does not have the structured member financial history needed to calculate a member payable. Monetary policy fields are floats; rounding and benefit order are not specified.

**Example.** A policy clause says “80% after a $500 deductible.” For a $1,000 billed claim, the code can report 80% coverage and a $500 deductible limit but cannot know whether the member has already met $0, $500, or $499 of that deductible, nor whether $1,000 is an allowed amount. It therefore cannot derive a defensible member/insurer split.

**Next smallest testable improvement.** Before implementation, define data sources and contracts for allowed amount, remaining deductible, copay/coinsurance ordering, limits, network, and rounding. Then create table-driven Decimal tests from structured benefit and accumulator fixtures; do not ask the LLM to fill missing values.

**Findings.**

- **F-07 — High — no patient-responsibility calculation exists.** Requirement CV-07; canonical 05 §6.2 and 07 claims data. Evidence: only percentages/limits are represented; no accumulators or member-payable output; `claim_amount` does not participate in policy calculation. Why it matters: the system cannot calculate reimbursement/member liability and must not imply that a policy percentage is an amount payable. Type: missing data source/model/functionality. Next: define the benefit and accumulator system-of-record before building a deterministic calculator.

## 12. Cross-Component Contradictions

- **F-08 — High — production P5 subreason is incompatible with the initial PostgreSQL enum migration.** Requirement: consistent enums, API, model, and persistence; canonical 02 P5 and 07 enums. `EscalationSubReason.RECOMMEND_DENIAL` exists in [`enums.py`](../../../src/medishield/db/enums.py) and the decision engine emits it, but `001_initial_schema.py` creates `escalation_sub_reason_enum` without that value and no later migration adds it. A PostgreSQL insert/update for this P5 escalation can fail. Local SQLite runtime does not verify this PostgreSQL enum compatibility. The canonical 02 document also conflicts internally: its P5 table names `RECOMMEND_DENIAL`, while its earlier prose says this must not be an escalation subreason/top-level outcome and describes confirmed failures as `REJECT`.
- **F-11 — Medium — approval confidence threshold differs from canonical.** `CONFIDENCE_PASS_THRESHOLD` is 0.70 in [`decision.py`](../../../src/medishield/domain/decision.py), while canonical 02/FEAT-010 describe 0.75 for P6/P7. Tests/configuration should pin the authoritative threshold; otherwise an agent at 0.70–0.74 can be approved contrary to the spec.
- **F-12 — Medium — documented parallel LangGraph execution is not the runtime orchestration.** FEAT-010/canonical 04 specify parallel KYC/Claims/Policy context and parallel fraud/RAG. [`CaseWorkflowCoordinator.execute`](../../../src/medishield/workflows/coordinator.py) calls these functions serially in a hand-written sequence; no `StateGraph` wiring/dependency was found. This is a real implementation/design delta, not merely a performance claim.
- **F-13 — High — consistency/provenance gates are narrower than the contract.** `evaluate_case_consistency` contains a `pass` for identity matching and has no call sites; `calculate_composite_decision_confidence` also has no call sites. `faithfulness_gate_node` checks schema, line-total flag, and policy citation booleans but does not validate every extracted field's provenance. LLM-returned code/line candidates can be provenance-free. Thus the documented cross-agent identity and complete evidence gate is not enforced end-to-end.
- **F-14 — Medium — snapshot and decision persistence have not been proven against PostgreSQL.** The inspected local runtime used SQLite fallback because PostgreSQL was unavailable. SQLAlchemy models and migrations were read, but no live PostgreSQL migration or enum/write test was run. Treat PostgreSQL compatibility as unverified.
- **F-15 — Low — cross-platform intake filename test currently fails.** A focused test expected Windows backslash traversal to reduce to `win.ini`; actual POSIX result was `.._.._windows_win.ini`. This test failure is outside the seven claims areas and does not by itself prove filesystem traversal, but the sanitizer contract needs an explicit platform-independent test/decision.

## 13. Test Results and Coverage Gaps

**Executed focused run A:**

```text
uv run --extra dev pytest tests/unit/test_kyc_and_claims_agents.py tests/unit/test_policy_agents.py tests/unit/test_decision_engine.py tests/unit/test_case_graph.py tests/unit/test_api.py tests/unit/test_models.py -q
72 passed, 1 failed
```

Failure: `test_policy_exclusion_hard_reject` expected `RULE-P5-HARD-REJECT`, got `RULE-P3-AMBIGUITY`. The live Groq call returned HTTP 429; the claims summary had no CPT, so policy coverage returned `INDETERMINATE`. The deterministic CPT allowlist also omits 15780, making this case depend on the LLM fallback. The failure is a real test result, not a claim that all code paths always fail.

**Executed focused run B:**

```text
uv run --extra dev pytest tests/unit/test_intake_pipeline.py tests/unit/test_sqlite_fallback.py tests/unit/test_policy_ingestion.py -q
28 passed, 1 failed
```

Failure: `test_sanitize_filename_traversal`; POSIX sanitization returned `.._.._windows_win.ini`, while the test expected `win.ini`.

**Coverage that exists:** KYC identity/DOB/expiry/missing/tamper tests; simple policy windows and no-window cases; clean/multiple-line total, arithmetic drift, negative charge, and malformed-NPI-length tests; required/missing/low-confidence completeness tests; decision priority/unit tests; API review/request-info tests; model creation tests; embedded Qdrant filter tests.

**Coverage gaps:** dependent eligibility and conflicting member rows; SQL effective-date/version selection and overlapping versions; authoritative CPT/ICD code validity and code-set dates; NPI checksum/provider identity/network status; quantity×unit price, Decimal rounding, missing lines, duplicate lines; published policy to runtime retrieval; supplementary upload plus resume integration; copay/deductible/coinsurance/accumulator calculations; unavailable source/reference-data handling. No complete full-suite run or live PostgreSQL migration/write test was performed.

## 14. Missing Data Sources and Dependencies

- **Member/dependent:** authoritative member and dependent roster with relationship and coverage-effective dates. Current seed data is synthetic and read-only.
- **Policy:** published-policy/version registry joined to the actual chunks used at runtime; effective dates and publication state must be enforced. The built-in corpus is a fixed demonstration corpus.
- **CPT/ICD:** a licensed/versioned CPT source and identified ICD-10-CM source, including effective dates and update policy; no such source is configured.
- **Provider:** NPI reference (including check digit/identity), plus distinct payer credentialing, licensure, enrollment, and network sources if those decisions are required.
- **Claim finances:** negotiated/allowed amount, claim adjustment/prior payment history, remaining deductible and out-of-pocket accumulators, policy-specific benefit limits, and explicit calculation order/rounding.
- **Case recovery:** API for supplementary evidence and a resume state hydrated from persisted documents plus valid prior node results.
- **Production persistence verification:** PostgreSQL service was unavailable during this audit; local fallback tests cannot prove native PostgreSQL ENUM/schema behavior.

## 15. Prioritized Remediation Plan

1. **Restore the AWAITING_INFO recovery loop (F-06).** Specify supplementary upload, case-version handling, persisted document/OCR references, resume hydration, audit events, and integration acceptance tests. This is the nearest end-to-end workflow defect.
2. **Make policy selection and retrieval authoritative (F-02/F-09/F-10).** Use published policy versions and their effective dates, reject/flag overlaps, connect the admin index to runtime query, and distinguish no enrollment from service/database unavailable.
3. **Tighten claim evidence and arithmetic (F-03/F-05/F-13).** Specify reference versions; require grounded fields; represent line quantity/unit/total with Decimal and explicit currency/rounding; do not mark missing lines as a reconciled total.
4. **Resolve member/provider data dependencies (F-01/F-04).** Define subscriber/dependent relationships and provider checks as separate verifiable assertions; do not infer them from names or NPI shape.
5. **Specify and implement member cost share only after data sources exist (F-07).** First contract allowed amount, accumulators, network, benefit order, limits, and rounding; then test deterministic arithmetic.
6. **Reconcile enum and confidence contracts (F-08/F-11).** Decide the legal P5 outcome/subreason and ship a migration consistent with it; align approval threshold with canonical contract. Verify on PostgreSQL.
7. **Decide whether parallel LangGraph execution is a requirement (F-12).** If it remains normative, add graph-level behavior and concurrency tests; otherwise update the specification only through the governed SDD/ADR process.

## 16. Suggested Next SDD Feature

Create the next feature specification for **Supplementary Evidence Upload and AWAITING_INFO Recovery**. It is the smallest user-visible lifecycle feature with a directly observed broken contract: define the supplementary upload API, persist and classify new documents, hydrate existing document references on resume, clear satisfied missing types, preserve prior evidence/audit, and test an `AWAITING_INFO → upload → resume → adjudicated` path. Do not fold cost-share rules into that feature.

## 17. Explicit Assumptions and Unverified Areas

- Because `claims_validation_matrix.md` was not present, the seven area names and detailed checklist in the user request are the audit requirements; requirement IDs CV-01…CV-07 are report-local labels, not claimed matrix IDs.
- `IMPLEMENTED_AND_TESTED` is intentionally not used for an entire validation area: existing tests prove only selected behaviors and synthetic fixtures.
- The prior dataset run is reported as observed local development behavior, not a benchmark accuracy result. It used synthetic data and local SQLite/policy fixtures.
- PostgreSQL-native migration and enum behavior was not exercised because PostgreSQL was unavailable. The enum mismatch finding is based on direct comparison of ORM enum values, initial migration values, and engine output.
- No real member, dependent, provider, coding, plan accumulator, or negotiated-amount source was available to validate production semantics.
- The external Groq rate limit affected one test; the audit does not infer provider uptime or claim production behavior from that result.
- No implementation changes are recommended as already performed; all remediation items require a separate approved SDD feature/task.

## 18. Retrospective Runtime Run Review (2026-10-11)

**Evidence reviewed.** I correlated the 30 `CASE-20261011-*` case records with the saved decision snapshots, per-document OCR endpoints and files under `data/storage/ocr`, the dataset's patient/category/fraud/edge metadata, retained backend output, and source images for the highest-risk cases. The API and stored OCR artifacts agree that 151/151 uploaded documents produced a payload and each payload has a page. The 151 payloads contain 12,322 OCR spans. This confirms OCR execution and payload completeness, not transcription correctness.

**Document classification against metadata.** The persisted type assigned by the classifier matched the metadata category for 118/151 files. The breakdown was:

| Metadata category | Correct / total |
|---|---:|
| Claim forms | 31 / 31 |
| Prescriptions | 30 / 30 |
| Discharge summaries | 22 / 30 |
| ID documents | 25 / 30 |
| Patient policy amendments | 10 / 30 |

Most patient policy amendments were labeled as `ID_DOCUMENT`; eight discharge summaries and five IDs were labeled as `PRESCRIPTION`. These are measurable category mismatches, not an OCR character-error rate. There is no gold transcription for every field in `metadata.json`, so exact OCR WER/field accuracy cannot be calculated from this run.

**High-confidence source-to-output discrepancies.**

- **PT_15075 / CASE-20261011-0A2917:** The submitted `id_PT_15075.png` was classified as `PRESCRIPTION`. The workflow therefore recorded `ID_DOCUMENT` as missing, skipped specialist KYC, and explained that the ID was absent even though the ID file was uploaded. Outcome: `AWAITING_INFO`. The related persisted OCR is [`DOC-0A2917-03.json`](../../../data/storage/ocr/CASE-20261011-0A2917/DOC-0A2917-03.json); the source image is [`id_PT_15075.png`](../../../dataset/id_documents/id_PT_15075.png).
- **PT_39816 / CASE-20261011-49A8EA:** The source ID visibly says `EXP 03/28/2025` and `EXPIRED`; its service date is 04/24/2025. Saved OCR also contains `EXP: 03/28/2025` and `**EXPIRED**`, but KYC stored no expiration date, set `id_expired=false`, and the case was `COMPLETED/APPROVE` with a clean-pass explanation. The parser expects `EXP DATE`, `EXPIRATION DATE`, or `EXPIRES`, not the source's bare `EXP:` label; the separate textual expired-status check does not recognize bare `**EXPIRED**`. Evidence: [`DOC-49A8EA-03.json`](../../../data/storage/ocr/CASE-20261011-49A8EA/DOC-49A8EA-03.json), [`id_PT_39816.png`](../../../dataset/id_documents/id_PT_39816.png).
- **PT_62350 / CASE-20261011-DBC62A:** The claim image visibly lists CPT 59400 and 27447; saved OCR contains both strings. The claims snapshot has `cpt_codes=[]`, yet returns `PASS`; policy then cites an inpatient room-and-board clause (SEC-5.5), records no applicable codes/exclusions, returns `PASS`, and the case is approved. Evidence: [`claim_PT_62350.png`](../../../dataset/claim_forms/claim_PT_62350.png), [`DOC-DBC62A-01.json`](../../../data/storage/ocr/CASE-20261011-DBC62A/DOC-DBC62A-01.json).
- **PT_24208, PT_30028, PT_45400:** Source claims visibly list cosmetic CPT 21120, and their OCR text contains the code. All three claim snapshots have no CPT code; their policy snapshots pass while citing only SEC-5.5 room-and-board terms; all three cases were `COMPLETED/APPROVE`. Example source: [`claim_PT_24208.png`](../../../dataset/claim_forms/claim_PT_24208.png). This is a downstream extraction/policy-scope defect even though the code text was present in OCR.
- **PT_20322 / CASE-20261011-CA1DD4:** Both uploaded claim documents' saved OCR contains the same claim number `CLM-2024-000005` and service date `10/11/2025`. The case includes six documents, but fraud output is score `0.0` with no duplicate anomaly; the case is approved with a P6 clean-pass explanation. Evidence: [`DOC-CA1DD4-01.json`](../../../data/storage/ocr/CASE-20261011-CA1DD4/DOC-CA1DD4-01.json) and [`DOC-CA1DD4-02.json`](../../../data/storage/ocr/CASE-20261011-CA1DD4/DOC-CA1DD4-02.json).
- **PT_15075 and PT_39816 explanations:** In the former, the explanation calls an uploaded ID “missing”; in the latter, it says identity checks passed despite the expired ID evidence. The explanation layer is therefore presenting the selected workflow outcome, not independently checking it against source documents.

**Fraud-label comparison and fixture defects.** The six patients with `fraud_label=true` were PT_19116, PT_99733, PT_62350, PT_69470, PT_20322, and PT_39451. Four of those six ended `APPROVE`; all six stored fraud scores were `0.0`, with no anomaly features recorded. Two were escalated for other reasons (policy ambiguity and a DOB mismatch), not because the fraud signal was detected. However, three fixture labels must not be treated as reliable expected truth without correction:

- PT_19116 is labeled `readmission_30d`, but the source discharge summary and saved OCR show only the 12/02/2025–12/05/2025 admission; no previous hospitalization appears in the image or OCR text. Source: [`discharge_PT_19116.png`](../../../dataset/discharge_summaries/discharge_PT_19116.png).
- PT_69470 is labeled `amount_under_10k`, but its source bill and OCR show total charges of `$23,290.00`, not less than $10,000. Source: [`claim_PT_69470.png`](../../../dataset/claim_forms/claim_PT_69470.png) and [`DOC-3749A2-01.json`](../../../data/storage/ocr/CASE-20261011-3749A2/DOC-3749A2-01.json). The case escalated for a KYC DOB mismatch, not the labeled amount/fraud scenario.
- PT_39451 is labeled `name_mismatch`; its claim, ID, and seeded member record all show Sandra Hall C. The fraud label is unsupported by the inspected sources.

The remaining labeled cases expose missed logic: PT_62350 (procedure/diagnosis mismatch) and PT_20322 (duplicate claim) were both approved; PT_99733's prescription image is dated 01/03/2026 after the claim service date, but the case escalated as generic ambiguity with fraud score zero. Thus raw label comparison says four of six approved, while source inspection indicates the benchmark labels themselves are not uniformly trustworthy.

**Other adversarial outcomes.** Two of five explicitly uncovered-procedure cases, PT_71993 and PT_16658, had CPT 17000 extracted and reached `ESCALATE/RECOMMEND_DENIAL`. The three CPT 21120 cases above were approved because extraction omitted the code. Of five expired-ID edge cases, PT_20745 and PT_41439 were caught; PT_39816 was approved despite explicit source/OCR expiry; PT_15075 was held as incomplete due to misclassification; PT_47353 escalated for arithmetic ambiguity with `id_expired=false`.

**Decision/explanation history.** The SQLite database contains 30 dataset case rows and 34 decision rows. Three cases were rerun after initial processing; their older and newer decision rows remain `is_active=true`. The latest Case state is observable, but active decision history is ambiguous for those reruns. Explainability must therefore identify which execution/decision version it describes. Several false approvals above have detailed explanations that assert all identity, claim, and policy checks passed despite the missing procedure code, duplicate claim, or expired-ID evidence.

**Runtime findings.**

- **F-16 — High — document classifier does not reliably identify required evidence.** Requirement CV-06; canonical 03 §§3–4 and FEAT-007/010. Evidence: only 118/151 type labels match metadata; 8/30 discharge summaries, 5/30 IDs, and 20/30 amendments are mislabeled. PT_15075 then enters `AWAITING_INFO` for a file that was supplied. Type: measured functional defect. Next: add per-category confusion tests and thresholded completeness integration tests using all dataset clusters.
- **F-17 — Critical — source-visible procedure codes fail to reach policy denial logic.** Requirements CV-03/CV-05; canonical 02 P3–P5, 05 §5, 09 §4.1. Evidence: OCR contains CPT 59400/27447 and CPT 21120, but agent code arrays are empty; unrelated SEC-5.5 room-and-board citations lead to `PASS` and approval in PT_62350, PT_24208, PT_30028, and PT_45400. Type: functional decision defect. Next: require the policy clause's applicability to a validated billed code and block approval when material codes are unvalidated/unsupported.
- **F-18 — High — an explicit expired ID is ignored and approved.** Requirement CV-01; FEAT-008 REQ-KYC-01/03. Evidence: PT_39816 image and OCR show expiry before service plus `EXPIRED`; KYC has null expiry/false expired, then P6 approval. Type: functional extraction/verdict defect. Next: test observed `EXP:` and `EXPIRED` variants and compare expiration against the defined relevant date.
- **F-19 — High — fraud signals are not evaluated from case evidence.** Requirements CV-03/CV-06 and fraud scenario handling; canonical 10 §2. Evidence: duplicate claim number/date in PT_20322 yields fraud score zero and approval; PT_19116 has no supporting prior-admission source despite its label. Type: functional gap plus synthetic-label quality issue. Next: separate verified fraud evidence from unsubstantiated labels and test duplicate claims across documents in one case.
- **F-20 — High — explanations confidently repeat incorrect outcomes.** Cross-component explanation requirement; canonical 02/06 and ADR-011. Evidence: PT_39816 explanation says identity checks passed; PT_15075 explanation says ID missing although file exists; cosmetic/diagnosis cases are described as clean passes while snapshots have no validated CPT and unrelated policy citations. Type: explanation-faithfulness defect. Next: build explanation claims only from validated decision inputs and add assertions linking each stated fact to its evidence/verdict.
- **F-21 — High — dataset scenario labels conflict with source documents.** Requirement: evaluation ground truth integrity; canonical 12 §4. Evidence: PT_19116 readmission, PT_69470 under-$10k, and PT_39451 name-mismatch labels are contradicted by inspected discharge/bill/member/ID evidence. Also, `expected_decision` is absent for the 151 patient-group documents; only four unknown/OOD documents have `expected_decision=ESCALATE`. Type: dataset/specification defect that blocks a trustworthy overall correctness score. Next: repair/verify source labels and create case-level gold decisions with field-level extraction truth before using benchmark accuracy claims.
- **F-22 — Medium — reruns leave multiple active decisions.** Cross-component audit-history requirement; canonical 02 §5.1, 07 decisions. Evidence: 34 decision rows for 30 run cases; three cases have multiple workflow decision rows and each row is still marked active. Type: persistence/audit defect. Next: test rerun/decision supersession semantics and enforce one active decision per case while retaining history.

**Runtime conclusion.** OCR ran for every file, but completeness is not correctness. The strongest root gaps are classifier confusion, parser assumptions about label text (`EXP:`), a short synthetic code allowlist that drops source-visible procedures, incomplete fraud feature application, a policy evaluator that accepts unrelated cited clauses, and dataset truth that contradicts source documents. Consequently, neither the 13 approvals nor the escalations can be called correct solely from the persisted final decision/explanation; manual/source-grounded adjudication is required for the flagged cases.
