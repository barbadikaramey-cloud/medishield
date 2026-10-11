# Feature Specification: FEAT-008 Specialist Extraction Agents (KYC Agent & Claims Agent)

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-008`
- **Feature Name:** Specialist Extraction Agents — KYC Agent & Claims Extraction Agent
- **Phase:** Phase 5 (Specialist Extraction Pipeline)
- **Status:** Proposed
- **Authoritative References:**
  - `docs/canonical/01_CANONICAL_ARCHITECTURE.md` (Section 4: Sequence Steps 208–210)
  - `docs/canonical/04_LANGGRAPH_STATE_AND_ORCHESTRATION_CONTRACT.md` (Node 5A & 5B)
  - `docs/canonical/05_AGENT_INTERFACE_CONTRACTS.md` (Sections 4 & 5)
  - `docs/canonical/06_FAITHFULNESS_PROVENANCE_AND_VERIFICATION.md` (Sections 1 & 2)
  - `docs/canonical/07_DATABASE_AND_STORAGE_CONTRACTS.md` (Tables: `members`, `claims`, `claim_lines`)
  - `docs/adr/ADR-004_PROVENANCE_VERIFICATION_VIA_OCR_SPANS_OVER_BBOXES.md`
  - `agents.md` (Rules 1, 2, 3, 4, 5, 6, 10, 11, 12, 13, 14, 15)

---

## 2. Purpose
Implement the two core parallel specialist extraction agents executing in LangGraph state `ANALYZING`:
1. **KYC Agent (`KYCAgent`):** Extracts patient and member identity attributes from ID documents (`ID_DOCUMENT`) and claim forms (`CLAIM_FORM`), matches against authoritative member records in PostgreSQL (`members`), evaluates date of birth and ID expiration, assesses image tamper signals (ELA anomalies, font inconsistencies), anchors every fact to discrete OCR text spans, and outputs typed `KYCAgentOutput`.
2. **Claims Extraction Agent (`ClaimsAgent`):** Extracts claim header financial attributes (claimed total, service date, provider NPI, provider name), clinical diagnostic coding (ICD-10), procedural billing lines (CPT codes, units, charges), executes deterministic arithmetic validation ($\sum \text{line\_charges} == \text{total\_claim}$), grounds each item in physical OCR spans, and outputs typed `ClaimsAgentOutput`.

---

## 3. Requirement References
- **REQ-KYC-01 (Spec 05 § 4):** Authoritative `KYCAgentOutput` contract containing:
  - Identity fields: `member_id`, `first_name`, `last_name`, `date_of_birth`, `government_id_number` as `ProvenanceValue`.
  - Database checks: `member_db_matched`, `dob_matched`, `id_expired`, `id_expiration_date`, `policy_active_on_service_date`.
  - Integrity signals: `tamper_detected`, `tamper_score`, `tamper_signals: list[TamperSignal]`, `flags`.
  - Verdict: `PASS`, `FAIL`, `INDETERMINATE`, `CONFLICT`, `UNAVAILABLE`.
- **REQ-KYC-02 (Spec 06 § 2 & ADR-004):** Text span verification: All extracted identity values must be verified against physical OCR text spans using `verify_extracted_span()`. Ungrounded values receive `evidence_quality_score = 0.0`.
- **REQ-KYC-03 (Spec 05 § 4 & Spec 02 § 3):** Deterministic verdicts:
  - `AgentVerdict.PASS`: Member matched in DB, DOB matches, ID not expired, tamper score $\le 0.80$, facts grounded.
  - `AgentVerdict.FAIL`: Member not found in DB or demographic mismatch (DOB mismatch).
  - `AgentVerdict.INDETERMINATE`: Missing required fields, low OCR readability, or ungrounded evidence.
- **REQ-CLM-01 (Spec 05 § 5):** Authoritative `ClaimsAgentOutput` contract containing:
  - Header: `claim_amount`, `service_date`, `provider_name`, `provider_npi` as `ProvenanceValue`.
  - Coding: `icd10_codes`, `cpt_codes`, `service_lines: list[ServiceLineItem]`.
  - Validation: `schema_valid`, `validation_errors`, `line_items_sum_valid`, `calculated_lines_sum`.
- **REQ-CLM-02 (Spec 05 § 5 & Spec 10 § 2):** Mathematical consistency validation:
  - Line sum consistency: $|\sum \text{total\_charge} - \text{claim\_amount}| < 0.01$.
  - Line unit consistency: $\text{quantity} \times \text{unit\_charge} == \text{total\_charge}$.
  - NPI validation: 10-digit numeric format and Luhn formula verification.
- **REQ-CLM-03 (Spec 06 § 2):** Grounding & Provenance: Each CPT line, charge, date, and diagnostic code must cite physical `span_id` with coordinates.
- **REQ-CLM-04 (Spec 05 § 5):** Claims verdicts:
  - `AgentVerdict.PASS`: Valid schema, line items match header claim amount within $0.01, valid NPI, verified spans.
  - `AgentVerdict.INDETERMINATE`: Low extraction confidence, ungrounded lines, or unparseable service lines.
  - `AgentVerdict.FAIL`: Negative charges, invalid billing schema, or unresolvable line corruption.

---

## 4. Architectural Scope & Affected Components
- **Input Interfaces:**
  - `case_id`, `execution_id`.
  - Document inventory references (`DocumentReference` list).
  - `OCRDocumentPayload` instances loaded from object storage (`retrieve_ocr_payload`).
  - Optional member database repository / session.
- **Output Interfaces:**
  - `KYCAgentOutput` conforming strictly to Spec 05 § 4.
  - `ClaimsAgentOutput` conforming strictly to Spec 05 § 5.
- **Files Created / Modified:**
  - `src/medishield/agents/kyc.py` (KYC Agent implementation, member DB matcher, tamper evaluator)
  - `src/medishield/agents/claims.py` (Claims Extraction Agent, ICD-10/CPT parser, arithmetic validator)
  - `src/medishield/agents/__init__.py` (Export KYC and Claims agent classes)
  - `tests/unit/test_kyc_and_claims_agents.py` (Comprehensive unit test suite)

---

## 5. Acceptance Criteria
1. **KYC Agent Acceptance:**
   - Extracts member identity fields and grounds them in verified OCR spans (`evidence_quality_score == 1.0`).
   - Correctly matches active member records from DB (or mock member repository).
   - Flags expired IDs (`id_expired = True`).
   - Emits `AgentVerdict.FAIL` on member mismatch.
   - Flags image tamper signals and computes `tamper_score`.
2. **Claims Agent Acceptance:**
   - Extracts claim header (amount, date, NPI) and service line items with verified OCR spans.
   - Calculates line items sum and verifies against claim amount.
   - Detects arithmetic drift and flags `line_items_sum_valid = False`.
   - Validates 10-digit Provider NPI.
   - Emits `AgentVerdict.PASS` on clean mathematical consistency, and `AgentVerdict.INDETERMINATE` on ungrounded or incomplete claims.
3. **Quality & Engineering Standards:**
   - Zero unapproved dependencies added to `pyproject.toml`.
   - 100% test pass across all new and existing tests ($\ge 100$ total tests).
   - 100% clean check from `ruff check src/ tests/`.
