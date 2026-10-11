# Feature Specification: FEAT-009 Two-Stage Policy Agent & Policy RAG Evaluation Engine

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-009`
- **Feature Name:** Two-Stage Policy Agent & Policy RAG Evaluation Engine
- **Phase:** Phase 6 (Policy Context Discovery & Coverage RAG Pipeline)
- **Status:** Proposed
- **Authoritative References:**
  - `docs/canonical/05_AGENT_INTERFACE_CONTRACTS.md` (Section 6: Policy Agent Contract)
  - `docs/canonical/09_POLICY_RAG_AND_ADMINISTRATION_SPECIFICATION.md` (Sections 1–4)
  - `docs/canonical/06_FAITHFULNESS_PROVENANCE_AND_VERIFICATION.md` (Section 1 Gate 2: Policy Grounding)
  - `docs/canonical/04_LANGGRAPH_STATE_AND_ORCHESTRATION_CONTRACT.md` (Node 5C, JoinClaimReady, Node 6A)
  - `docs/adr/ADR-003_PARALLEL_POLICY_CONTEXT_AND_INDEPENDENT_FRAUD.md`
  - `docs/adr/ADR-006_SEPARATION_OF_POLICY_ADMIN_FROM_CUSTOMER_SUBMISSIONS.md`
  - `agents.md` (Rules 1, 2, 3, 4, 5, 6, 10, 11, 12, 13, 14, 15)

---

## 2. Purpose
Implement the Two-Stage Policy Evaluation subsystem:
1. **Stage A (Policy Context Discovery - `PolicyContextAgent`):** Executes in parallel with KYC and Claims Extraction, querying member enrollment records with only `member_id` to discover candidate policies and effective dates.
2. **JoinClaimReady Synchronization Barrier (`resolve_claim_ready_policy`):** Intersects candidate policies with validated `service_date` from Claims Extraction to resolve the active policy version.
3. **Stage B (Policy Coverage Evaluation - `PolicyCoverageAgent`):** Takes validated CPT/ICD codes and resolved policy version, performs clinical code expansion, retrieves authoritative policy chunks, evaluates coverage/deductibles/exclusions/prior-auth, verifies all citations against corpus chunks (Gate 2), and outputs typed `PolicyCoverageOutput`.
4. **Policy Corpus Management (`PolicyCorpusRepository`):** Provides admin-isolated storage and hybrid retrieval for authoritative corporate policy chunks (ADR-006).

---

## 3. Requirement References
- **REQ-POL-01 (Spec 05 § 6.1 & ADR-003):** Stage A `PolicyContextOutput` contract:
  - Input: `member_id: str`.
  - Output: `candidate_policies: list[CandidatePolicy]`, `member_enrollment_id`, `verdict: AgentVerdict`.
  - Concurrency: Executes in parallel without waiting for `service_date`.
- **REQ-POL-02 (Spec 09 § 3.1 & Spec 04 § 2):** `JoinClaimReady` barrier logic:
  - Resolves exact policy version satisfying: $\text{effective\_start\_date} \le \text{service\_date} \le \text{effective\_end\_date}$.
  - If no active enrollment exists on `service_date`, emits `effective_date_valid = False` and sets verdict to `FAIL` (`POLICY_NOT_ACTIVE_ON_SERVICE_DATE`).
- **REQ-POL-03 (Spec 05 § 6.2 & Spec 09 § 3.2):** Stage B `PolicyCoverageOutput` contract:
  - Fields: `covered: bool`, `coverage_percentage: float`, `policy_deductible_limit`, `policy_copay_percentage`, `cited_clauses: list[PolicyClauseCitation]`, `identified_exclusions: list[str]`, `prior_authorization_required: bool`, `prior_authorization_present: bool`, `reasoning_summary: str`, `latency_ms: int`.
- **REQ-POL-04 (Spec 06 § 1 Gate 2 & Spec 09 § 4.1):** Policy Grounding Verification:
  - Every cited clause in `cited_clauses` must achieve $\ge 0.95$ verbatim character containment against authoritative policy chunk text (`verified_in_corpus = True`).
  - Strict negative grounding: A claim is never marked `verdict = FAIL` (NOT COVERED) without an authoritative cited exclusion verified in the corpus.
- **REQ-POL-05 (ADR-006 & Spec 09 § 1):** Customer upload isolation:
  - Untrusted customer documents (`POLICY_AMENDMENT`) cannot write to or alter the authoritative policy corpus. Only admin publishing writes to `policy_chunks`.

---

## 4. Architectural Scope & Affected Components
- **Input Interfaces:**
  - `member_id: str` (Stage A)
  - `service_date: date`, `cpt_codes: list[str]`, `icd10_codes: list[str]`, `claim_amount: float` (Stage B)
- **Output Interfaces:**
  - `PolicyContextOutput` (Stage A)
  - `PolicyCoverageOutput` (Stage B)
- **Files Created / Modified:**
  - `src/medishield/agents/policy.py` (`PolicyContextAgent`, `PolicyCoverageAgent`, `resolve_claim_ready_policy`, `PolicyCorpusRepository`)
  - `src/medishield/agents/__init__.py` (Exports for policy agent components)
  - `tests/unit/test_policy_agents.py` (Comprehensive unit test suite)

---

## 5. Acceptance Criteria
1. **Stage A Policy Context:**
   - Discovers candidate policies for a valid member ID and outputs `AgentVerdict.PASS`.
   - Emits `AgentVerdict.FAIL` when no member enrollments exist.
2. **JoinClaimReady Synchronization:**
   - Correctly selects the active policy version when `service_date` is within effective date bounds.
   - Flags policy as inactive when `service_date` is outside coverage bounds.
3. **Stage B Coverage RAG & Clinical Code Expansion:**
   - Expands CPT codes into clinical terms (e.g. CPT 99213, CPT 93000, CPT 47562).
   - Retrieves matching policy chunks and evaluates covered benefit percentages, deductibles, copays.
   - Detects exclusions (e.g. cosmetic surgery, unapproved experimental procedures) and prior authorization mandates.
4. **Faithfulness Gate 2 Grounding:**
   - Verifies verbatim text of cited clauses against the corpus (`verified_in_corpus = True`).
   - Rejects ungrounded citations or forces `AgentVerdict.INDETERMINATE` when coverage denials lack cited exclusion clauses.
5. **Quality & Engineering Standards:**
   - Zero unapproved dependencies added to `pyproject.toml`.
   - 100% test pass rate across new and existing tests ($\ge 110$ total tests).
   - 100% clean check from `ruff check src/ tests/`.
