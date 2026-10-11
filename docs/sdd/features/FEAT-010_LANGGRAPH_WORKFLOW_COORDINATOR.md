# Feature Specification: FEAT-010 LangGraph Multi-Agent Workflow Coordinator & Barriers

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-010`
- **Feature Name:** LangGraph Multi-Agent Workflow Coordinator & Synchronization Barriers
- **Phase:** Phase 7 (LangGraph Workflow Coordinator & DAG Orchestration)
- **Status:** Specified
- **Authoritative References:**
  - `docs/canonical/04_LANGGRAPH_STATE_AND_ORCHESTRATION_CONTRACT.md` (Sections 1–4)
  - `docs/canonical/02_DECISION_ENGINE_AND_STATE_MACHINE.md` (Sections 1–4)
  - `docs/canonical/01_CANONICAL_ARCHITECTURE.md` (Section 3)
  - `docs/adr/ADR-001_WORKFLOW_STATE_VERSUS_BUSINESS_DECISION_SEPARATION.md`
  - `docs/adr/ADR-003_PARALLEL_POLICY_CONTEXT_AND_INDEPENDENT_FRAUD.md`
  - `docs/adr/ADR-004_PROVENANCE_VERIFICATION_VIA_OCR_SPANS_OVER_BBOXES.md`
  - `docs/adr/ADR-005_DETERMINISTIC_EXPLAINABLE_FRAUD_SCORING.md`
  - `agents.md` (Rules 1–15)

---

## 2. Purpose
Orchestrates all MediShield specialist agents, validation engines, and decision layers into an end-to-end directed acyclic graph (DAG) workflow:
1. **Lightweight State Architecture (`CaseState`):** Stores structural references, URIs, confidence scores, and verdicts; isolates heavy document binaries and OCR JSON payloads to S3/storage backend.
2. **Sequential Intake Phase:**
   - Node 1: OCR Preprocessing (`ocr_preprocessor_node`)
   - Node 2: Split & Group (`split_group_node`)
   - Node 3: Document Classification (`classifier_node`)
   - Node 4: Case Completeness (`case_completeness_node`) — early exit to `AWAITING_INFO` if mandatory documents are missing.
3. **Parallel Specialist Phase & Synchronization Barriers:**
   - Dispatches Node 5A (KYC), Node 5B (Claims), and Node 5C (Policy Context Stage A) concurrently.
   - **`JoinClaimReady` Barrier:** Intersects Claims `service_date` with Policy Context candidate versions to resolve active `policy_id` and `policy_version`.
   - Node 6A: Policy Coverage RAG executes with the resolved policy version.
   - Node 6B: Fraud Detection Agent executes concurrently as soon as claims amounts/NPI are extracted without blocking on Policy Coverage RAG.
   - **`JoinAll` Barrier:** Synchronizes completion of KYC, Policy Coverage RAG, and Fraud Agent before downstream evaluation.
4. **Adjudication & Persistence Phase:**
   - Node 7: Faithfulness & Provenance Gate (`faithfulness_gate_node`).
   - Node 8: Deterministic Decision Engine (`decision_engine_node` applying priority rules P1–P8).
   - Node 9: Audit & State Persistence (`audit_persistence_node` with HMAC-SHA256 audit chaining).
5. **Decoupled Human-in-the-Loop Resumption (ADR-005):**
   - Escalated cases transition to `AWAITING_HUMAN` (or `AWAITING_INFO`) and release worker execution immediately without thread blocking.

---

## 3. Requirement References
- **REQ-WF-01 (Spec 04 § 1.1):** `CaseState` TypedDict and Pydantic models with structural references and agent verdict pointers.
- **REQ-WF-02 (Spec 04 § 2):** Complete DAG node implementation wrapping specialist engines:
  - `OCRPreprocessorEngine` (Node 1)
  - `PageSplitter` (Node 2)
  - `DocumentClassifierAgent` (Node 3)
  - `CaseCompletenessEngine` (Node 4)
  - `KYCAgent` (Node 5A)
  - `ClaimsAgent` (Node 5B)
  - `PolicyContextAgent` (Node 5C)
  - `resolve_claim_ready_policy` (`JoinClaimReady` barrier)
  - `PolicyCoverageAgent` (Node 6A)
  - `DeterministicFraudEngine` (Node 6B)
  - `FaithfulnessEngine` (Node 7)
  - `DeterministicDecisionEngine` (Node 8)
  - `AuditEngine` (Node 9)
- **REQ-WF-03 (Spec 04 § 3 Node 4 & Spec 02 § 3):** Case completeness early exit:
  - Incomplete case sets `workflow_state = WorkflowState.AWAITING_INFO`, `decision = BusinessDecision.ESCALATE`, `escalation_sub_reason = EscalationSubReason.MISSING_INFORMATION`.
  - Routes directly to Node 9 (Audit & State Persistence) and cleanly terminates graph execution.
- **REQ-WF-04 (ADR-003 & Spec 04 § 3):** `JoinClaimReady` barrier synchronizing Claims service date with candidate policy versions, resolving active `policy_id` and `policy_version`.
- **REQ-WF-05 (Spec 04 § 3 Node 6B):** Independent concurrency of Fraud Detection: evaluates fraud features as soon as Claims and KYC are complete, without blocking on Policy Coverage RAG.
- **REQ-WF-06 (Spec 04 § 3 JoinAll):** `JoinAll` sync barrier gating Node 7 and Node 8 until all specialists complete.
- **REQ-WF-07 (ADR-001 & Spec 02 § 1):** Strict semantic separation:
  - Workflow State (`RECEIVED`, `SCANNING`, `SPLIT`, `CLASSIFYING`, `ANALYZING`, `AWAITING_INFO`, `AWAITING_HUMAN`, `COMPLETED`).
  - Business Decision (`APPROVE`, `REJECT`, `ESCALATE`).
- **REQ-WF-08 (ADR-005 & Spec 04 § 4):** Non-blocking human escalation:
  - On `ESCALATE`, `workflow_state = WorkflowState.AWAITING_HUMAN`, audit persisted, execution completes with success status.
- **REQ-WF-09 (Spec 04 § 3 Node 9 & Spec 01 § 4):** HMAC tamper-evident audit persistence on workflow completion or escalation.

---

## 4. Architectural Scope & Affected Components
- **Source Directory:** `src/medishield/workflows/`
  - `src/medishield/workflows/state.py` — `CaseState`, `DocumentReference`, and state mutation helpers.
  - `src/medishield/workflows/nodes.py` — Granular workflow nodes wrapping domain engines.
  - `src/medishield/workflows/coordinator.py` — Directed graph coordinator, barriers, and runner.
  - `src/medishield/workflows/__init__.py` — Package exports.
- **Test Directory:** `tests/unit/`
  - `tests/unit/test_case_graph.py` — Comprehensive unit and integration test suite.

---

## 5. Acceptance Criteria
- [ ] `CaseState` schema contains strictly lightweight pointers, verdicts, and scores conforming to Spec 04 § 1.1.
- [ ] Incomplete document submission halts immediately at `WorkflowState.AWAITING_INFO` with `EscalationSubReason.MISSING_INFORMATION`.
- [ ] Clean claim flows through the complete DAG to `WorkflowState.COMPLETED` with `BusinessDecision.APPROVE` (Rule P6).
- [ ] Excluded procedure (CPT 15780) flows to `WorkflowState.COMPLETED` with `BusinessDecision.REJECT` (Rule P5).
- [ ] High fraud risk claim flows to `WorkflowState.AWAITING_HUMAN` with `BusinessDecision.ESCALATE` (`FRAUD_REVIEW`, Rule P4).
- [ ] Suspicious tamper signal flows to `WorkflowState.AWAITING_HUMAN` with `BusinessDecision.ESCALATE` (`SECURITY_REVIEW`, Rule P1).
- [ ] `JoinClaimReady` barrier accurately selects the active policy version matching the claim `service_date`.
- [ ] Every graph execution persists an HMAC-SHA256 audit event.
- [ ] All unit tests pass (`pytest tests/`) and lint checks pass (`ruff check`).
