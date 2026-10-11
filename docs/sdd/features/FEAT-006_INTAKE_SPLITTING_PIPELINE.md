# Feature Specification: FEAT-006 Multi-Document Intake, S3 PDF Splitting & Case Completeness Engine

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-006`
- **Feature Name:** Multi-Document Intake, S3 PDF Splitting & Case Completeness Engine
- **Phase:** Phase 3 (Intake & Ingestion Pipeline)
- **Status:** Proposed
- **Authoritative References:**
  - `docs/canonical/03_MULTI_DOCUMENT_INTAKE_AND_SPLIT_CONTRACT.md` (Sections 1–5)
  - `docs/canonical/07_DATABASE_AND_STORAGE_CONTRACTS.md` (Object Storage Key Hierarchy & Tables)
  - `docs/canonical/08_REST_API_SPECIFICATION.md` (Section 1: Ingestion API)
  - `docs/adr/ADR-001_WORKFLOW_STATE_VERSUS_BUSINESS_DECISION_SEPARATION.md`
  - `agents.md` (Rules 1, 3, 4, 5, 6, 11, 12, 13)

---

## 2. Purpose
Implement the multi-document intake ingestion pipeline that validates incoming file bundles, inspects magic bytes, calculates cryptographic SHA-256 checksums, splits multi-page PDFs into discrete page records, persists raw and rendered assets to object storage (S3/local), and evaluates case completeness against category intake matrices before specialist agents execute.

---

## 3. Requirement References
- **REQ-INTK-01 (Spec 03 § 2.1):** Magic byte MIME validation supporting `image/jpeg`, `image/png`, `application/pdf`, and `image/tiff`. Never trust file extensions. Reject files failing static security/macro checks.
- **REQ-INTK-02 (Spec 03 § 2.1):** Size and boundary constraints: Max 25 MB per file, max 100 MB aggregate per case, max 50 pages per PDF.
- **REQ-INTK-03 (Spec 03 § 1, 3):** Hierarchical 1:N Case-to-Document model. Decompose incoming files into `Document` and `DocumentPage` entities registered in PostgreSQL.
- **REQ-INTK-04 (Spec 03 § 5.2 & Spec 07):** Object storage layout separation:
  - Raw uploads: `cases/{case_id}/raw/{document_id}_{filename}`
  - Page assets: `cases/{case_id}/pages/{document_id}_p{page_number}.png`
  - OCR payloads: `ocr/{case_id}/{document_id}.json` (referenced by URI, never stored in LangGraph state).
- **REQ-INTK-05 (Spec 03 § 4):** Case Completeness Engine enforcing `REQUIRED_DOCUMENTS_MATRIX`:
  - `STANDARD_REIMBURSEMENT_CLAIM`: `["CLAIM_FORM", "ID_DOCUMENT"]`
  - `HOSPITAL_CASHLESS_PREAUTH`: `["CLAIM_FORM", "ID_DOCUMENT", "DISCHARGE_SUMMARY"]`
  - `POLICY_AMENDMENT_REQUEST`: `["POLICY_AMENDMENT", "ID_DOCUMENT"]`
  - Classification confidence threshold $\ge 0.70$.
  - Incomplete cases transition to `WorkflowState.AWAITING_INFO` with `ESCALATE(MISSING_INFORMATION)`.

---

## 4. Architectural Scope & Affected Components
- **Input Interfaces:**
  - Raw binary file streams / byte buffers with declared filenames.
  - Case metadata (e.g. `case_id`, `submission_channel`, `claim_type`).
- **Output Interfaces:**
  - `ValidatedUpload`: validated file metadata, checksum, verified MIME type.
  - `CaseCompletenessResult`: completion status, missing types, recommended state and decision.
  - Ingestion records for `Case`, `Document`, and `DocumentPage`.
- **Files Created / Modified:**
  - `src/medishield/intake/validation.py` (Magic byte inspection, static security scanner, SHA-256 checksum)
  - `src/medishield/intake/storage.py` (Object storage protocol & local/S3 key hierarchy driver)
  - `src/medishield/intake/splitter.py` (Multi-page PDF splitter, page metadata extractor)
  - `src/medishield/intake/completeness.py` (Case completeness engine & intake matrix)
  - `src/medishield/intake/__init__.py` (Package exports)
  - `tests/unit/test_intake_pipeline.py` (Comprehensive unit test suite)

---

## 5. Acceptance Criteria
1. **Magic Byte & Security Validation:**
   - Accurately detects PDF, JPEG, PNG, TIFF regardless of file extension.
   - Rejects unauthorized MIME types (e.g. executables, plain text, zip).
   - Rejects files exceeding 25 MB or cases exceeding 100 MB.
   - Detects and rejects malicious PDF payloads (e.g. embedded `/JavaScript` or `/Launch` actions).
2. **Object Storage Compliance:**
   - Correctly formats and resolves canonical S3 URI paths for raw documents, page images, and OCR payloads.
3. **PDF Splitting & Page Extraction:**
   - Multi-page PDFs correctly split into individual 1-indexed page records.
   - Page dimensions and page object keys generated deterministically.
4. **Completeness Adjudication:**
   - Complete cases emit `AgentVerdict.PASS` and recommend `WorkflowState.ANALYZING`.
   - Incomplete cases emit `AgentVerdict.INDETERMINATE`, `BusinessDecision.ESCALATE`, `EscalationSubReason.MISSING_INFORMATION`, and recommend `WorkflowState.AWAITING_INFO`.
   - Documents with confidence $< 0.70$ are excluded from completeness satisfaction.
5. **Quality & Test Coverage:**
   - 100% test pass on validation, splitting, storage, and completeness checks.
   - Passes `ruff` and `pytest`.
