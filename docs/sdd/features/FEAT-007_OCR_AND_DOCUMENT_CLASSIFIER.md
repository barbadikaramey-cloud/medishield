# Feature Specification: FEAT-007 OCR Preprocessor & Document Classification Engine

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-007`
- **Feature Name:** OCR Preprocessor & Document Classification Engine
- **Phase:** Phase 4 (OCR & Classification Pipeline)
- **Status:** Proposed
- **Authoritative References:**
  - `docs/canonical/03_MULTI_DOCUMENT_INTAKE_AND_SPLIT_CONTRACT.md` (Sections 3 & 5)
  - `docs/canonical/04_LANGGRAPH_STATE_AND_ORCHESTRATION_CONTRACT.md` (Sections 1 & 2)
  - `docs/canonical/05_AGENT_INTERFACE_CONTRACTS.md` (Sections 2 & 3)
  - `docs/canonical/06_FAITHFULNESS_PROVENANCE_AND_VERIFICATION.md` (Sections 1 & 2)
  - `docs/adr/ADR-004_PROVENANCE_VERIFICATION_VIA_OCR_SPANS_OVER_BBOXES.md`
  - `agents.md` (Rules 1, 2, 3, 4, 5, 6, 10, 11, 12, 13, 14, 15)

---

## 2. Purpose
Implement the OCR Preprocessor and Document Classification engines for multi-document intake.
1. The **OCR Preprocessor** ingests raw documents/pages, generates deterministic text token spans with normalized bounding box geometry and discrete `span_id`s, computes quality scores, stores the heavy `OCRDocumentPayload` in Object Storage (`ocr/{case_id}/{document_id}.json`), and returns lightweight `OCRPreprocessorOutput` for LangGraph state.
2. The **Document Classifier** performs fast multi-signal classification over extracted text and form features, assigns canonical `DocumentType` categories and routing tags, evaluates confidence, and produces `ClassifierAgentOutput` for downstream completeness checking and specialist routing.

---

## 3. Requirement References
- **REQ-OCR-01 (Spec 03 § 5.1 & Spec 05 § 3):** Canonical OCR schemas (`OCRBoundingBox`, `OCRTextSpan`, `OCRPagePayload`, `OCRDocumentPayload`). Each span must feature a unique `span_id` (e.g. `span-p1-0001`), 1-indexed `page_number`, text token string, confidence, and normalized bounding box coordinates $[0.0, 1.0]$.
- **REQ-OCR-02 (Spec 03 § 5.2):** Storage Separation Principle: Full `OCRDocumentPayload` is serialized and persisted to Object Storage (`s3://.../ocr/{case_id}/{document_id}.json`). Only the URI reference (`payload_storage_uri`) and metadata (`image_quality_score`, `verdict`, `latency_ms`) are returned to workflow state.
- **REQ-OCR-03 (Spec 05 § 3 & Spec 06 § 1):** Preprocessor quality assessment & verdicts:
  - `AgentVerdict.PASS`: Image quality $\ge 0.40$ and intelligible spans extracted.
  - `AgentVerdict.INDETERMINATE`: Low image quality ($< 0.40$) or unreadable/degraded page text.
  - `AgentVerdict.UNAVAILABLE`: OCR processing engine failure or unparseable corrupted format.
- **REQ-CLS-01 (Spec 05 § 2):** Document Classifier output contract:
  - Canonical types: `CLAIM_FORM`, `ID_DOCUMENT`, `DISCHARGE_SUMMARY`, `ITEMIZED_BILL`, `PRESCRIPTION`, `POLICY_AMENDMENT`, `UNKNOWN`.
  - Confidence threshold: High confidence ($\ge 0.70$) emits `AgentVerdict.PASS`. Below threshold or `UNKNOWN` emits `AgentVerdict.INDETERMINATE`.
- **REQ-CLS-02 (Spec 04 § 2 & Spec 05 § 2):** Semantic routing tags and visual characteristics:
  - Extract relevant tags (e.g., `["EMERGENCY_ADMISSION"]`, `["HIGH_VALUE_CLAIM"]`, `["PHARMACY_RX"]`, `["GOVERNMENT_ID"]`).
  - Extract structural layout features (e.g., table structure, form fields, stamp/signature presence).
- **REQ-CLS-03 (ADR-004):** Text span provenance preservation: Classification and downstream specialist extractions must ground themselves in discrete OCR token spans, guaranteeing verifiable auditability.

---

## 4. Architectural Scope & Affected Components
- **Input Interfaces:**
  - Raw document byte streams, decomposed page images, document/case identifiers, storage backend instance.
- **Output Interfaces:**
  - `OCRDocumentPayload`: Full JSON representation written to Object Storage.
  - `OCRPreprocessorOutput`: Lightweight reference passed to LangGraph state.
  - `ClassifierAgentOutput`: Document category, confidence, verdict, routing tags.
- **Files Created / Modified:**
  - `src/medishield/ocr/preprocessor.py` (OCR extraction, span tokenization, bounding box normalization, storage persistence)
  - `src/medishield/ocr/__init__.py` (Package exports)
  - `src/medishield/agents/classifier.py` (Multi-signal classifier agent, pattern matching, routing tags)
  - `src/medishield/agents/__init__.py` (Package exports)
  - `tests/unit/test_ocr_and_classifier.py` (Comprehensive unit test suite)

---

## 5. Acceptance Criteria
1. **OCR Preprocessor:**
   - Produces structured `OCRDocumentPayload` with discrete `span_id`s, normalized bounding box coordinates $[0.0, 1.0]$, and page text.
   - Saves OCR payload to object storage at canonical key `ocr/{case_id}/{document_id}.json`.
   - Returns lightweight `OCRPreprocessorOutput` containing `payload_storage_uri`, quality score, and verdict.
   - Correctly marks degraded/blank inputs as `INDETERMINATE`.
2. **Document Classifier:**
   - Correctly classifies standard health insurance documents (`CLAIM_FORM`, `ID_DOCUMENT`, `DISCHARGE_SUMMARY`, `ITEMIZED_BILL`, `PRESCRIPTION`, `POLICY_AMENDMENT`).
   - Assigns `AgentVerdict.PASS` when confidence $\ge 0.70$ and doc type is known.
   - Assigns `AgentVerdict.INDETERMINATE` when confidence $< 0.70$ or doc type is `UNKNOWN`.
   - Emits relevant operational routing tags (`EMERGENCY_ADMISSION`, `PHARMACY_RX`, etc.).
3. **Integration & Quality:**
   - Zero unapproved dependencies added to `pyproject.toml`.
   - 100% test pass rate across `tests/unit/test_ocr_and_classifier.py` and all existing tests (total $\ge 95$ tests).
   - Passes `ruff` linting and formatting checks.
