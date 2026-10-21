# MediShield Multi-Agent Document Intake System
## Canonical LangGraph State & Orchestration Specification

**Status:** Authoritative Engineering Contract  
**Version:** 1.0.0 (Canonical Baseline)  
**Governing Principle:** Graph state maintains light identifiers and execution references; heavy binary and text payloads remain in external stores. High-throughput parallelism is preserved across independent branches.

---

## 1. Graph State Model

LangGraph state is managed via Python `TypedDict` and Pydantic models. It is strictly constrained to structural references, metadata, verdicts, and evidence pointers.

### 1.1 `CaseState` Definition

```python
from typing import TypedDict, List, Dict, Optional, Any
from docs.canonical.decision_engine import WorkflowState, BusinessDecision, EscalationSubReason, AgentVerdict

class DocumentReference(TypedDict, total=False):
    document_id: str
    object_key: str
    mime_type: str
    checksum: str
    ocr_payload_uri: str               # S3 URI to OCR JSON
    doc_type: str                      # CLAIM_FORM, ID_DOCUMENT, etc.
    classification_confidence: float
    routing_tags: List[str]
    split_source_page_indices: List[int]

class CaseState(TypedDict, total=False):
    # Core Case Identifiers
    case_id: str
    workflow_version: str
    execution_id: str
    trace_id: str
    created_at: str

    # Workflow Progression
    workflow_state: str                # WorkflowState string
    completed_nodes: List[str]
    errors: List[Dict[str, Any]]

    # 1:N Document Inventory
    documents: List[DocumentReference]
    is_complete: bool
    missing_required_documents: List[str]

    # Specialist Execution References & Outcomes (Pointers + Summary Verdicts)
    kyc_execution_id: Optional[str]
    kyc_verdict: Optional[str]          # AgentVerdict string
    kyc_confidence: Optional[float]
    kyc_summary: Optional[Dict[str, Any]]

    claims_execution_id: Optional[str]
    claims_verdict: Optional[str]       # AgentVerdict string
    claims_confidence: Optional[float]
    claims_summary: Optional[Dict[str, Any]]

    policy_context_id: Optional[str]
    policy_context_verdict: Optional[str]
    candidate_policies: Optional[List[Dict[str, Any]]]  # From Policy Context discovery
    policy_id: Optional[str]             # Resolved at JoinClaimReady barrier
    policy_version: Optional[str]        # Resolved at JoinClaimReady barrier
    policy_effective_date_valid: Optional[bool]  # Set by JoinClaimReady

    # Missing documents tracking (B-3)
    missing_documents: Optional[List[str]]  # Doc types still required, e.g. ["ID_DOCUMENT"]

    # Stage B Specialists
    policy_coverage_execution_id: Optional[str]
    policy_coverage_verdict: Optional[str]
    policy_coverage_summary: Optional[Dict[str, Any]]
    
    clinical_alignment_execution_id: Optional[str]
    clinical_alignment_verdict: Optional[str]
    clinical_alignment_confidence: Optional[float]
    clinical_alignment_summary: Optional[Dict[str, Any]]

    fraud_execution_id: Optional[str]
    fraud_verdict: Optional[str]
    fraud_score: Optional[float]        # Compatibility indicator: 1.0 when review is required, else 0.0
    fraud_review_required: Optional[bool]
    fraud_risk_level: Optional[str]     # REVIEW_REQUIRED or LOW
    fraud_summary: Optional[Dict[str, Any]]

    # Faithfulness & Integrity Gate
    faithfulness_gate_passed: bool
    provenance_integrity_score: float
    unsupported_claims_count: int

    # Final Business Decision Output
    decision: Optional[str]             # BusinessDecision string
    escalation_sub_reason: Optional[str]# EscalationSubReason string
    decision_confidence: float
    justification: str
    rule_id_matched: str
    is_auto_decided: bool
```

---

## 2. Orchestration DAG & Parallel Execution Flow

The LangGraph directed acyclic graph (DAG) maximizes processing throughput by executing independent tasks in parallel while respecting true technical data dependencies:

```mermaid
flowchart TD
    START([Start Node]) --> NodeOCR[Node 1: OCR & Preprocessing<br/>Per Document]
    NodeOCR --> NodeSplit[Node 2: Split & Group<br/>Page Clustering]
    NodeSplit --> NodeClassify[Node 3: Classifier Agent<br/>Type & Tags]
    NodeClassify --> NodeCompleteness{Node 4: Case Completeness<br/>Check}

    NodeCompleteness -- Incomplete --> StateAwaitingInfo[State: AWAITING_INFO<br/>Decision: ESCALATE<br/>Sub: MISSING_INFORMATION]
    StateAwaitingInfo --> END_PAUSE([End: Paused for Upload])

    NodeCompleteness -- Complete --> ForkParallel[Parallel Dispatcher]

    subgraph ParallelPhase[Parallel Branch Execution]
        ForkParallel --> NodeKYC[Node 5A: KYC Agent<br/>Member DB + Identity Match]
        ForkParallel --> NodeClaims[Node 5B: Claims Extraction<br/>ICD-10, CPT, Line Items]
        ForkParallel --> NodePolContext[Node 5C: Policy Context<br/>Member Enrollment & Version]
    end

    NodeClaims --> JoinClaimReady{JoinClaimReady Barrier:<br/>Service-Date Policy Selection}
    NodePolContext --> JoinClaimReady

    JoinClaimReady -- Valid Claims + Resolved Policy Version --> NodePolCoverage[Node 6A: Policy Coverage RAG<br/>Docling Chunks + Vector Search]
    
    NodeClaims --> ForkClin[Trigger Clinical Alignment]
    ForkClin --> NodeClinical[Node 6C: Clinical Alignment Specialist<br/>Deterministic Catalog & Line Evidence]

    NodeClaims --> ForkFraud[Trigger Fraud]
    NodeKYC --> ForkFraud
    ForkFraud --> NodeFraud[Node 6B: Fraud Detection Agent<br/>Deterministic Scoring & History]

    NodePolCoverage --> JoinAll[Sync Barrier: All Specialists Complete]
    NodeClinical --> JoinAll
    NodeFraud --> JoinAll
    NodeKYC --> JoinAll

    JoinAll --> NodeGate[Node 7: Faithfulness & Provenance Gate<br/>Span Verification Engine]
    NodeGate --> NodeDecision[Node 8: Deterministic Decision Engine<br/>Priority P1-P8 Rules]

    NodeDecision --> CheckOutcome{Final Decision}
    CheckOutcome -- APPROVE / REJECT --> StateCompleted[State: COMPLETED]
    CheckOutcome -- ESCALATE --> StateAwaitingHuman[State: AWAITING_HUMAN]

    StateCompleted --> AuditPersist[Node 9: Audit & State Persistence]
    StateAwaitingHuman --> AuditPersist
    AuditPersist --> END_FINISH([End: Workflow Complete])
```

---

## 3. Detailed Node Contracts & Transition Logic

### Node 1: OCR & Preprocessing (`ocr_preprocessor_node`)
- **Input:** Case document storage paths.
- **Action:** Generates OCR bounding boxes and text spans. Writes full OCR JSON to private object storage.
- **State Mutation:** Updates `documents[i]["ocr_payload_uri"]`. Transitions `workflow_state` to `SCANNING`.

### Node 2: Split & Group (`split_group_node`)
- **Input:** OCR payloads and page bounding boxes.
- **Action:** Clusters logical pages into distinct `DocumentReference` objects.
- **State Mutation:** Updates `documents` array. Transitions `workflow_state` to `SPLIT`.

### Node 3: Classifier Agent (`classifier_node`)
- **Input:** Document text snippets and page features.
- **Action:** Assigns document type and confidence.
- **State Mutation:** Updates `doc_type`, `classification_confidence`, `routing_tags`. Transitions `workflow_state` to `CLASSIFYING`.

### Node 4: Case Completeness Check (`case_completeness_node`)
- **Conditional Branch:**
  - If required documents are missing: Mutates `workflow_state = "AWAITING_INFO"`, `decision = "ESCALATE"`, `escalation_sub_reason = "MISSING_INFORMATION"`. Routes directly to `AuditPersist` and pauses graph.
  - If complete: Transitions `workflow_state = "ANALYZING"`. Dispatches parallel branches.

### Node 5A: KYC Agent (`kyc_agent_node`)
- **Input:** Member details extracted from `ID_DOCUMENT` and `CLAIM_FORM`.
- **Action:** Performs deterministic member DB lookup, DOB matching, policy status check, and tamper detection.
- **Output:** Emits `kyc_verdict` (`PASS`, `FAIL`, `INDETERMINATE`, `CONFLICT`, `UNAVAILABLE`).

### Node 5B: Claims Extraction Agent (`claims_extraction_node`)
- **Input:** OCR spans from `CLAIM_FORM` and bills.
- **Action:** Extracts diagnosis codes (ICD-10), procedure codes (CPT), provider NPI, service dates, line items, and itemized amounts. Validates against schema.
- **Output:** Emits `claims_verdict` and structured claim payload.

### Node 5C: Policy Context Discovery (`policy_context_node`)
- **Input:** Member ID only. Does **not** require `service_date` at dispatch time.
- **Action:** Queries relational database to discover all candidate enrollments and policy versions for the member. Returns the set of candidate policies and their effective date windows without filtering by service date.
- **Output:** Emits `candidate_policies` (list of policy_id, version, effective date range), `member_enrollment_id`, and context readiness.
- **Service-Date Filtering:** Deferred to the `JoinClaimReady` barrier (see below), where validated `service_date` from Claims is used to select the exact active policy version.

### JoinClaimReady Barrier (`join_claim_ready`)
- **Precondition:** Triggered when both Node 5B (Claims) and Node 5C (Policy Context) have completed.
- **Action:** Uses validated `service_date` from Claims output to filter the candidate policies from Policy Context. Selects the exact policy version where `effective_start_date ≤ service_date ≤ effective_end_date`. If no active enrollment matches the service date, emits `policy_context_verdict = FAIL` with reason `POLICY_NOT_ACTIVE_ON_SERVICE_DATE`.
- **State Mutation:** Sets `policy_id`, `policy_version`, `policy_effective_date_valid` on CaseState.
- **Output:** Routes to Node 6A (Policy Coverage RAG) with resolved `policy_id`, `policy_version`, and validated CPT/ICD codes from Claims.

### Node 6A: Policy Coverage RAG (`policy_coverage_rag_node`)
- **Precondition:** Requires validated CPT codes from Node 5B and **resolved** policy version from `JoinClaimReady` barrier.
- **Action:** Retrieves matching policy clauses using hybrid lexical/dense search. Evaluates coverage percentage and exclusions.
- **Output:** Emits `policy_coverage_verdict` (`PASS`, `FAIL`, `INDETERMINATE`).

### Node 6B: Fraud Detection Agent (`fraud_agent_node`)
- **Concurrency Advantage:** Executes immediately once claims amounts and provider NPI are available, without waiting for the Policy RAG node.
- **Action:** Evaluates 7 deterministic fraud features against historical claims DB.
- **Output:** Emits one boolean `detected` value per factor. Any detected factor sets `fraud_review_required = True`, `fraud_score = 1.0` (binary compatibility field), `fraud_risk_level = REVIEW_REQUIRED`, and `fraud_verdict = INDETERMINATE`; no detected factors yield `False`, `0.0`, `LOW`, and `PASS`. Incomplete billing extraction does not count as arithmetic drift.

### Node 6C: Clinical Alignment Specialist (`clinical_alignment_node`)
- **Precondition:** Triggered in Stage B parallel phase once Claims extraction (Node 5B) completes and clinical documents are available.
- **Action:** Cross-verifies billed claim service lines (CPT/HCPCS, revenue codes, bed-day units) against physical clinical documentation using versioned LCD/NCD coverage policies (ADR-015). Diagnosis-to-service relevancy is assessed by at most one structured LLM call per case that returns only `SUPPORTED`, `UNCERTAIN`, or `NOT_ESTABLISHED` per line with span citations (ADR-025). Citations are verified against supplied OCR spans; invalid, uncited, or unavailable output stays `INDETERMINATE`. The model returns a verdict only; performance, guideline criteria, prior authorization, bed days, and the P1–P8 business decision remain deterministic. Mandatory `EVIDENCE_REGEX` guideline criteria left unassessed by pattern matching are sent in at most one additional structured call per case. the model may only return `SATISFIED` or `UNCERTAIN` with citations to supplied verified spans, and it never produces negated or contradictory outcomes (ADR-031).
- **State Mutation:** Updates `clinical_alignment_execution_id`, `clinical_alignment_verdict`, `clinical_alignment_confidence`, and `clinical_alignment_summary` on `CaseState`.
- **Output:** Emits typed `ClinicalAlignmentOutput` to `JoinAll` synchronization barrier.

### Node 7: Faithfulness Gate (`faithfulness_gate_node`)
- **Action:** Cross-verifies all extracted fields against physical OCR text spans and verifies all cited policy clauses against retrieved vector texts.
- **Output:** Sets `faithfulness_gate_passed = True/False`. Flags any ungrounded assertions.

### Node 8: Decision Engine (`decision_engine_node`)
- **Action:** Purely deterministic execution of priority rules P1–P8. Emits binding `BusinessDecision`. Generates rich clinical and financial `detailed_explanation` (via Groq LLM or deterministic fallback per ADR-011).
- **Clinical discrepancy approval (ADR-028):** When `CLINICAL_UNCERTAINTY_APPROVAL_ENABLED` is true, a clinical `INDETERMINATE` result caused only by configured missing-support reasons may satisfy P6 if clinical schema/OCR quality are valid, no line is contradicted/failed/unavailable, and no bed-day conflict exists. Other P6 gates remain mandatory. Verified catalog contraindications, explicit performance contradictions, and bed-day conflicts remain P3 blockers. An approval using this exception includes a clinical discrepancy notice in `justification`; the full specialist output remains in the immutable agent snapshot.
- **State Mutation:** Updates `decision`, `escalation_sub_reason`, `workflow_state` (`COMPLETED` or `AWAITING_HUMAN`), `justification`, and `detailed_explanation`.

### Node 9: Audit & State Persistence (`audit_persistence_node`)
- **Action:** Writes immutable audit entry to PostgreSQL with SHA-256 hash chaining. Updates `cases` table. Releases job lease.

---

## 4. Human Review Decoupling Principle

The architecture guarantees: **The Celery/Redis worker thread never blocks waiting for human intervention.**

1. When a case escalates, Node 8 transitions `workflow_state` to `AWAITING_HUMAN`, and Node 9 persists the exact checkpoint in PostgreSQL.
2. The LangGraph worker run immediately terminates with status `SUCCESS`. The compute thread is released back to the worker pool.
3. When an Operations Reviewer submits an override via the Next.js UI, the API updates the case directly or dispatches an event-driven resumption task.
