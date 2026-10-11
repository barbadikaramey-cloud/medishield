# Feature Specification: FEAT-003 Domain Contracts, Provenance Types & Faithfulness Engine

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-003`
- **Feature Name:** Domain Contracts, Provenance Types & Faithfulness Engine
- **Phase:** Phase 2 (Domain & Engines)
- **Status:** Proposed
- **Authoritative References:**
  - `docs/canonical/05_AGENT_INTERFACE_CONTRACTS.md` (Sections 1–8)
  - `docs/canonical/06_FAITHFULNESS_PROVENANCE_AND_VERIFICATION.md` (Sections 1–5)
  - `docs/adr/ADR-001_WORKFLOW_STATE_VERSUS_BUSINESS_DECISION_SEPARATION.md`
  - `docs/adr/ADR-004_PROVENANCE_VERIFICATION_VIA_OCR_SPANS_OVER_BBOXES.md`
  - `agents.md` (Rules 1, 3, 4, 6, 11, 12, 13)

---

## 2. Purpose
Establish the shared domain vocabulary, typed Pydantic v2 interface contracts for all specialist agents, span-level provenance tracking structures, and the deterministic Faithfulness & Provenance Verification Engine. This forms the analytical bridge ensuring that no LLM or agent output reaches the Decision Engine without verified physical grounding in underlying OCR spans and policy texts.

---

## 3. Requirement References
- **REQ-AGNT-01:** All agent inputs and outputs must communicate exclusively via versioned, typed Pydantic schemas.
- **REQ-PROV-01 (ADR-004):** Authoritative provenance must be grounded in physical OCR text span IDs (`span_id`), with bounding boxes treated as secondary visual conveniences.
- **REQ-FAITH-01 (Spec 06, § 1–2):** Implement the 3-stage span verification algorithm:
  - Direct span match yields `evidence_quality_score = 1.0` and `ProvenanceType.SPAN`.
  - Page-level match yields `evidence_quality_score = 0.5` and `ProvenanceType.PAGE`.
  - Unmatched facts yield `evidence_quality_score = 0.0` and `ProvenanceType.INFERRED` (triggering escalation).
- **REQ-FAITH-02 (Spec 06, § 4):** Case-level cross-agent consistency verification ensuring identity, dates, and amounts reconcile across KYC, Claims, and Policy outputs.
- **REQ-FAITH-03 (Spec 06, § 5):** Composite Decision Confidence calculation weighting Provenance Quality (40%), Validation Completeness (35%), and Minimum Model Confidence (25%).

---

## 4. Scope
- **In Scope:**
  - `src/medishield/domain/provenance.py`: `ProvenanceType`, `FieldProvenance`, `ProvenanceValue`.
  - `src/medishield/domain/contracts.py`: Complete Pydantic schemas for OCR, Classifier, KYC, Claims, Policy Context, Policy Coverage, Fraud, and Decision outputs.
  - `src/medishield/domain/faithfulness.py`: Text normalization, span verification algorithm, cross-agent consistency engine, composite confidence calculator.
  - Unit tests verifying serialization, span matching, fallback penalties, cross-agent consistency, and calibration mathematics.
- **Out of Scope (Non-Goals):**
  - Concrete LLM prompt construction or OpenAI/Anthropic API calls (Phase 3).
  - LangGraph workflow DAG orchestration (Phase 4).
  - Fraud detection calculation rules F-01 through F-07 (FEAT-004).

---

## 5. Preconditions
- Virtual environment `.venv` active with Pydantic v2 installed.
- Phase 1 Persistence & Telemetry (`FEAT-001`, `FEAT-002`) completed.

---

## 6. Inputs & Outputs
- **Inputs:**
  - OCR text spans (`OCRTextSpan`) and page payloads.
  - Extracted string values and candidate span IDs.
  - Agent output snapshots (`KYCAgentOutput`, `ClaimsAgentOutput`, `PolicyCoverageOutput`).
- **Outputs:**
  - Grounded `ProvenanceValue` objects with calibrated `evidence_quality_score`.
  - `ConsistencyCheckReport` identifying any cross-agent discrepancies.
  - Calibrated float `decision_confidence` $\in [0.0, 1.0]$.

---

## 7. Data Model Impact
- Pure domain and memory layer; no database schema modifications or migrations required.

---

## 8. API & Workflow Impact
- Provides the data contracts that will be passed between LangGraph state nodes and returned by API endpoints.

---

## 9. Business Rules & Invariants
- **BR-FAITH-01:** Hallucinated values with no physical grounding must receive `evidence_quality_score = 0.0`.
- **BR-FAITH-02:** When cross-agent factual conflicts exist, the consistency engine must set `verdict_override = "CONFLICT"`.
- **BR-FAITH-03:** Composite decision confidence must never exceed 1.0 or fall below 0.0.

---

## 10. Security Requirements
- Ensure domain models scrub or mask sensitive fields when printed or logged in string representations.

---

## 11. Error Handling & Observability
- Span verification handles missing spans and malformed text gracefully without raising unhandled exceptions.
- Traced via Logfire spans.

---

## 12. Acceptance Criteria
1. `src/medishield/domain/provenance.py` defines all 4 provenance types and base structures.
2. `src/medishield/domain/contracts.py` defines complete Pydantic models for all 8 agent interfaces matching Spec 05.
3. `src/medishield/domain/faithfulness.py` implements:
   - `verify_extracted_span()` with exact 1.0, 0.5, and 0.0 score thresholds.
   - `evaluate_case_consistency()` detecting identity, date, and line-item sum discrepancies.
   - `calculate_composite_decision_confidence()` matching the $0.40 / 0.35 / 0.25$ formula.
4. Test suite verifies all algorithms with 100% test pass rate.
