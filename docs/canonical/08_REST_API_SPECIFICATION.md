# MediShield Multi-Agent Document Intake System
## Canonical REST API Specification

**Status:** Authoritative Engineering Contract  
**Version:** 1.0.0 (Canonical Baseline)  
**Governing Framework:** FastAPI (Python 3.11+)  
**Security Standard:** Bearer JWT Authentication, RBAC Scopes, Pre-signed Storage URLs

---

## 1. Global API Standards

1. **Base URL:** `/api/v1`
2. **Standard Headers:**
   - `Authorization: Bearer <JWT>` (Required for all endpoints except `/health` and `/ready`)
   - `X-Correlation-ID: <UUID>` (Unique request trace propagated across logs)
   - `Idempotency-Key: <UUID>` (Required for mutating `POST` operations)
3. **Standard Error Envelope:**
   ```json
   {
     "error_code": "RESOURCE_CONFLICT",
     "message": "The case has been modified by another reviewer. Please refresh.",
     "correlation_id": "c7a82b9e-64d1-4d3b-9a28-9842bf9d8321",
     "details": {
       "expected_version": 2,
       "current_version": 3
     },
     "timestamp": "2026-10-02T10:14:32Z"
   }
   ```
4. **RBAC Role Matrix:**
   - `OPERATIONS_REVIEWER`: View cases, view documents, execute human overrides.
   - `OPERATIONS_MANAGER`: All reviewer permissions + view operational metrics, queues, and audit exports.
   - `ADMINISTRATOR`: Full system access + publish authoritative policy documents (`POST /api/v1/admin/policies`).

---

## 2. Document Intake & Case Management Endpoints

### 2.1 Multipart Case Intake
- **Endpoint:** `POST /api/v1/cases`
- **Auth:** `OPERATIONS_REVIEWER`, `OPERATIONS_MANAGER`, `ADMINISTRATOR`
- **Request:** `multipart/form-data`
  - `files`: List of binary files (JPEG, PNG, PDF, TIFF; max 25MB each, max 100MB aggregate).
  - `claim_type`: Optional category string (`STANDARD_REIMBURSEMENT_CLAIM`).
  - `metadata`: Optional JSON string.
- **Behavior:** Validates MIME magic bytes, generates SHA-256 checksums, creates PostgreSQL case record in state `RECEIVED`, streams files to private object storage (`/raw/{case_id}/{doc_id}`), and enqueues asynchronous processing job in Redis. Does not block for AI inference.
- **Response:** `202 Accepted`
  ```json
  {
    "case_id": "CASE-20261002-000421",
    "workflow_state": "RECEIVED",
    "document_count": 2,
    "documents": [
      {
        "document_id": "DOC-20261002-000421-01",
        "file_name": "claim_submission.pdf",
        "checksum": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "size_bytes": 1420580
      }
    ],
    "created_at": "2026-10-02T10:15:00Z"
  }
  ```

### 2.2 List Cases
- **Endpoint:** `GET /api/v1/cases`
- **Auth:** `OPERATIONS_REVIEWER`, `OPERATIONS_MANAGER`, `ADMINISTRATOR`
- **Query Parameters:**
  - `workflow_state`: Optional filter (`AWAITING_HUMAN`, `COMPLETED`, etc.)
  - `decision`: Optional filter (`APPROVE`, `REJECT`, `ESCALATE`)
  - `doc_type`: Optional filter (`CLAIM_FORM`, `ID_DOCUMENT`)
  - `page`: Integer (default 1)
  - `page_size`: Integer (default 20, max 100)
- **Response:** `200 OK`
  ```json
  {
    "total_count": 142,
    "page": 1,
    "page_size": 20,
    "items": [
      {
        "case_id": "CASE-20261002-000421",
        "workflow_state": "AWAITING_HUMAN",
        "current_decision": "ESCALATE",
        "current_sub_reason": "FRAUD_REVIEW",
        "decision_confidence": 0.820,
        "document_count": 2,
        "is_complete": true,
        "version": 1,
        "created_at": "2026-10-02T10:15:00Z",
        "updated_at": "2026-10-02T10:16:12Z"
      }
    ]
  }
  ```

### 2.3 Get Case Detail
- **Endpoint:** `GET /api/v1/cases/{case_id}`
- **Auth:** `OPERATIONS_REVIEWER`, `OPERATIONS_MANAGER`, `ADMINISTRATOR`
- **Response:** `200 OK`
  ```json
  {
    "case_id": "CASE-20261002-000421",
    "workflow_state": "COMPLETED",
    "workflow_version": "v1.0.0",
    "current_decision": "APPROVE",
    "decision_confidence": 0.925,
    "is_complete": true,
    "version": 2,
    "active_decision": {
      "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
      "decision": "APPROVE",
      "actor_type": "SYSTEM",
      "actor_id": "decision-engine-v1",
      "rule_id_matched": "RULE-P6-CLEAN-PASS",
      "justification": "All criteria met: KYC verified, claim valid, procedure covered, fraud score 0.08 < 0.30.",
      "detailed_explanation": "### Executive Summary\nClaim approved under Clean Straight-Through Pass (P6)...",
      "created_at": "2026-10-02T10:16:15Z"
    },
    "documents": [
      {
        "document_id": "DOC-20261002-000421-01",
        "file_name": "claim_submission.pdf",
        "doc_type": "CLAIM_FORM",
        "classification_confidence": 0.980,
        "signed_view_url": "https://storage.medishield.internal/...15min-ttl..."
      }
    ],
    "created_at": "2026-10-02T10:15:00Z",
    "updated_at": "2026-10-02T10:16:15Z"
  }
  ```

### 2.4 Upload Supplementary Documents
- **Endpoint:** `POST /api/v1/cases/{case_id}/documents`
- **Auth:** `OPERATIONS_REVIEWER`, `OPERATIONS_MANAGER`, `ADMINISTRATOR`
- **Purpose:** Allows uploading additional documents to an existing case (e.g., when case is in `AWAITING_INFO` state).
- **Request:** `multipart/form-data`
  - `files`: List of binary files (JPEG, PNG, PDF, TIFF; max 25MB each).
  - `metadata`: Optional JSON string.
- **Behavior:** Validates MIME magic bytes, generates SHA-256 checksums, creates new `documents` rows linked to the existing case, streams files to object storage (`/raw/{case_id}/{doc_id}`). Updates `cases.missing_documents` by removing doc types that are now satisfied. Does **not** automatically resume processing — the client must explicitly call `POST /api/v1/cases/{case_id}/process`.
- **Response:** `201 Created`
  ```json
  {
    "case_id": "CASE-20261002-000421",
    "added_documents": [
      {
        "document_id": "DOC-20261002-000421-03",
        "file_name": "drivers_license.jpg",
        "checksum": "a1b2c3...",
        "size_bytes": 524288
      }
    ],
    "missing_documents": [],
    "total_document_count": 3
  }
  ```

### 2.5 Resume Case Processing
- **Endpoint:** `POST /api/v1/cases/{case_id}/process`
- **Auth:** `OPERATIONS_REVIEWER`, `OPERATIONS_MANAGER`, `ADMINISTRATOR`
- **Purpose:** Triggers workflow resumption after supplementary documents are uploaded or after a technical failure is resolved.
- **Request Body:**
  ```json
  {
    "resume_reason": "SUPPLEMENTARY_DOCUMENTS_ADDED",
    "expected_case_version": 2
  }
  ```
- **Behavior:** Validates case is in a resumable state (`AWAITING_INFO` or `FAILED`). Uses cached agent results for previously completed nodes. Only re-processes newly added documents through OCR → Split → Classify, then re-evaluates completeness. If now complete, dispatches remaining workflow nodes. Concurrency-checked via `expected_case_version`.
- **Response:** `202 Accepted`
  ```json
  {
    "case_id": "CASE-20261002-000421",
    "workflow_state": "SCANNING",
    "resume_reason": "SUPPLEMENTARY_DOCUMENTS_ADDED",
    "version": 3,
    "updated_at": "2026-10-02T11:30:00Z"
  }
  ```

### 2.6 Get OCR Spans & Bounding Boxes
- **Endpoint:** `GET /api/v1/cases/{case_id}/documents/{document_id}/ocr`
- **Auth:** `OPERATIONS_REVIEWER`, `OPERATIONS_MANAGER`, `ADMINISTRATOR`
- **Response:** `200 OK` (Streams `OCRDocumentPayload` JSON with all pages, words, spans, and coordinates).

### 2.7 Get Agent Executions & Summaries
- **Endpoint:** `GET /api/v1/cases/{case_id}/agents`
- **Auth:** `OPERATIONS_REVIEWER`, `OPERATIONS_MANAGER`, `ADMINISTRATOR`
- **Response:** `200 OK`
  ```json
  {
    "case_id": "CASE-20261002-000421",
    "agent_results": {
      "kyc": {
        "verdict": "PASS",
        "confidence": 0.950,
        "member_matched": true,
        "dob_matched": true,
        "tamper_score": 0.04
      },
      "claims": {
        "verdict": "PASS",
        "confidence": 0.920,
        "total_amount": 1250.00,
        "provider_npi": "1942857102",
        "cpt_codes": ["99213", "71045"]
      },
      "policy": {
        "verdict": "PASS",
        "confidence": 0.890,
        "covered": true,
        "coverage_percentage": 80.0,
        "policy_id": "POL-CA-GOLD-2026"
      },
      "fraud": {
        "verdict": "PASS",
        "fraud_score": 0.08,
        "risk_level": "LOW",
        "anomalies": []
      }
    }
  }
  ```

### 2.8 Human Review & Override
- **Endpoint:** `POST /api/v1/cases/{case_id}/review`
- **Auth:** `OPERATIONS_REVIEWER`, `ADMINISTRATOR`
- **Headers:** `Idempotency-Key` (Mandatory)
- **Request Body:**
  ```json
  {
    "action": "OVERRIDE",
    "new_decision": "APPROVE",
    "reason_code": "MANUAL_DOCUMENT_VERIFIED",
    "remarks": "Verified hospital stamp and physician signature on page 2 directly with provider.",
    "expected_case_version": 1
  }
  ```
  **Validation Rules:**
  - `action` must be one of: `OVERRIDE`, `CONFIRM`, `REQUEST_INFO`.
  - `new_decision` is **required** for `OVERRIDE` and `CONFIRM` actions (must be `APPROVE`, `REJECT`, or `ESCALATE`).
  - `new_decision` must be **null or omitted** for `REQUEST_INFO` action (no business decision is created; case transitions to `AWAITING_INFO`).
  - `reason_code` and `remarks` (minimum 10 characters) are always required.

  **REQUEST_INFO Example:**
  ```json
  {
    "action": "REQUEST_INFO",
    "new_decision": null,
    "reason_code": "MISSING_GOVERNMENT_ID",
    "remarks": "Member government-issued photo ID not included in submission. Please upload.",
    "expected_case_version": 1
  }
  ```
- **Concurrency Rule:** If current `cases.version != expected_case_version`, returns `409 Conflict`.
- **Response:** `200 OK`
  ```json
  {
    "case_id": "CASE-20261002-000421",
    "workflow_state": "COMPLETED",
    "current_decision": "APPROVE",
    "actor_type": "HUMAN",
    "reviewer_id": "user-review-094",
    "version": 2,
    "updated_at": "2026-10-02T10:22:10Z"
  }
  ```

### 2.9 Get Chronological Audit Trail
- **Endpoint:** `GET /api/v1/cases/{case_id}/audit`
- **Auth:** `OPERATIONS_MANAGER`, `ADMINISTRATOR`
- **Response:** `200 OK`
  ```json
  {
    "case_id": "CASE-20261002-000421",
    "events": [
      {
        "event_type": "CASE_RECEIVED",
        "actor_type": "SYSTEM",
        "actor_id": "fastapi-ingest",
        "current_hash": "3f82a9...sha256",
        "created_at": "2026-10-02T10:15:00Z"
      },
      {
        "event_type": "DECISION_ESCALATED",
        "actor_type": "SYSTEM",
        "actor_id": "decision-engine-v1",
        "prev_hash": "3f82a9...sha256",
        "current_hash": "9c14b2...sha256",
        "created_at": "2026-10-02T10:16:15Z"
      }
    ]
  }
  ```

---

## 3. Authoritative Policy Administration Endpoints

To prevent customer uploads from polluting authoritative policy corpora, policy publication is strictly separated into an admin-only endpoint:

- **Endpoint:** `POST /api/v1/admin/policies`
- **Auth:** `ADMINISTRATOR`
- **Request:** `multipart/form-data`
  - `policy_file`: PDF file.
  - `policy_id`: String (e.g., `POL-CA-GOLD-2026`).
  - `version_tag`: String (e.g., `v2026.1`).
  - `effective_start_date`: Date string (`2026-01-01`).
  - `effective_end_date`: Optional date string.
- **Behavior:** Executes Docling layout parsing, hierarchical chunking, generates embeddings, registers collection in Qdrant/ChromaDB, and sets `is_published = True` in PostgreSQL.
- **Response:** `201 Created`

---

## 4. Health & Liveness Endpoints

- `GET /api/v1/health`: Checks basic HTTP service availability. (Returns `200 OK`)
- `GET /api/v1/ready`: Deep health check verifying connectivity to PostgreSQL, Redis, Object Storage, and Vector DB. (Returns `200 OK` or `503 Service Unavailable`)
