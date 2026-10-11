# Feature Specification: FEAT-012 FastAPI REST API & Control Plane

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-012`
- **Feature Name:** FastAPI REST API & Control Plane
- **Phase:** Phase 9 (API & Control Plane)
- **Status:** Specified
- **Authoritative References:**
  - `docs/canonical/08_REST_API_SPECIFICATION.md` (Sections 1–5)
  - `docs/canonical/11_SECURITY_HIPAA_AND_PROMPT_INJECTION_DEFENSES.md` (Section 1 RBAC & Section 2 PHI Defenses)
  - `docs/canonical/07_DATABASE_AND_STORAGE_CONTRACTS.md` (Section 2 Cases, Section 8 Review Locks, Section 10 Idempotency)
  - `docs/canonical/01_CANONICAL_ARCHITECTURE.md` (Section 2 Layer 2)
  - `docs/adr/ADR-005_DETERMINISTIC_EXPLAINABLE_FRAUD_SCORING.md`
  - `docs/adr/ADR-006_ADMIN_PUBLISHING_FIREWALL_FOR_POLICY_CORPUS.md`
  - `agents.md` (Rules 1–15)

---

## 2. Purpose
Provides the authoritative HTTP control plane mediating client operations (intake, case queuing, human reviewer console, policy administration, and audit exports):
1. **Sub-200ms Multipart Case Ingestion (`POST /api/v1/cases`):**
   - Validates MIME magic bytes, enforces 25MB file and 100MB total limits.
   - Generates SHA-256 checksums, streams uploads to private object storage (`/raw/{case_id}/{doc_id}`).
   - Creates PostgreSQL case record in state `RECEIVED` and enqueues background processing on `workflow_queue`.
   - Never blocks on OCR or LLM inference in the HTTP request thread; returns `HTTP 202 Accepted` within 200ms.
2. **Role-Based Access Control (RBAC) & JWT Security:**
   - Enforces role hierarchies: `OPERATIONS_REVIEWER`, `OPERATIONS_MANAGER`, and `ADMINISTRATOR`.
   - Restricts policy corpus publication strictly to `ADMINISTRATOR` (ADR-006 publishing firewall).
3. **Optimistic Locking & Human Review Exception Console:**
   - Acquires short-lived (15-minute) review locks (`review_locks` table) to prevent adjuster collisions.
   - Requires `expected_case_version` check on all override and confirmation submissions, preventing stale writes (HTTP 409 Conflict).
   - Dispatches decoupled workflow resumption via background workers.
4. **Idempotency-Key Protection:**
   - Detects repeated submissions via `Idempotency-Key` header and cached SHA-256 response storage.
5. **On-the-Fly Cryptographic Audit Chain Verification:**
   - Endpoint `GET /api/v1/audit/cases/{case_id}` verifies SHA-256 HMAC integrity from genesis to terminal state, returning boolean `chain_intact`.
6. **HIPAA & Telemetry Compliance:**
   - Emits Logfire telemetry spans with scrubbed headers and payload metadata (zero SSNs, dates of birth, or PHI in access logs).

---

## 3. Requirement References
- **REQ-API-01 (Spec 08 § 2.1):** `POST /api/v1/cases` multipart intake returning `202 Accepted`.
- **REQ-API-02 (Spec 08 § 2.2-2.3):** `GET /api/v1/cases` and `GET /api/v1/cases/{case_id}` with state/decision filters and pagination.
- **REQ-API-03 (Spec 08 § 2.4-2.5):** `POST /api/v1/cases/{case_id}/documents` supplementary intake and `POST /api/v1/cases/{case_id}/process` workflow resumption.
- **REQ-API-04 (Spec 08 § 2.6-2.7):** `GET /api/v1/cases/{case_id}/documents/{document_id}/ocr` and `GET /api/v1/cases/{case_id}/agents` evidence retrieval.
- **REQ-API-05 (Spec 08 § 3.1-3.5):** Human review console endpoints (`/review/lock`, `/review/override`, `/review/confirm`, `/review/request-info`).
- **REQ-API-06 (Spec 08 § 4.1-4.2 & ADR-006):** Policy administration and publishing firewall (`/policies`, `/policies/{policy_id}/publish`).
- **REQ-API-07 (Spec 08 § 5.1-5.2):** `GET /api/v1/audit/cases/{case_id}` and `GET /api/v1/metrics/operational`.
- **REQ-API-08 (Spec 11 § 1 & Spec 08 § 1.1):** RBAC dependency enforcement across Reviewer, Manager, and Admin roles.

---

## 4. Architectural Scope & Affected Components
- **Source Directory:** `src/medishield/api/`
  - `src/medishield/api/main.py` — FastAPI application factory, CORS, exception handlers, telemetry.
  - `src/medishield/api/deps.py` — Authentication, RBAC, DB session, storage, and idempotency dependencies.
  - `src/medishield/api/schemas/` — Request and response Pydantic schemas.
  - `src/medishield/api/routers/cases.py` — Case ingestion and lifecycle router.
  - `src/medishield/api/routers/review.py` — Human review and locking router.
  - `src/medishield/api/routers/policies.py` — Policy corpus administration router.
  - `src/medishield/api/routers/audit.py` — Cryptographic audit and operational metrics router.
  - `src/medishield/api/__init__.py` — Package exports.
- **Test Directory:** `tests/unit/`
  - `tests/unit/test_api.py` — Comprehensive API test suite verifying intake, RBAC, locks, overrides, and audit checks.

---

## 5. Acceptance Criteria
- [ ] `POST /api/v1/cases` accepts valid multipart submissions, streams to S3, writes `cases` record in `RECEIVED`, and enqueues worker task returning `202 Accepted`.
- [ ] Spoofed or unauthorized file types (e.g. `.exe`, `.bat`, corrupt headers) are rejected with `415 Unsupported Media Type` or `400 Bad Request`.
- [ ] File exceeding 25MB or case aggregate exceeding 100MB is rejected with `413 Payload Too Large`.
- [ ] `GET /api/v1/cases` correctly paginates and filters by `workflow_state` and `decision`.
- [ ] `POST /api/v1/cases/{case_id}/review/lock` grants 15-minute lock; second adjuster receives `409 Conflict`.
- [ ] `POST /api/v1/cases/{case_id}/review/override` validates `expected_case_version` and transitions case to `COMPLETED` with human decision.
- [ ] RBAC guard rejects unauthorized roles with `403 Forbidden` (e.g., non-admin calling policy publish).
- [ ] `GET /api/v1/audit/cases/{case_id}` verifies cryptographic HMAC chain and returns `chain_intact: true`.
- [ ] All unit and integration tests pass (`pytest tests/`) and lint checks pass (`ruff check`).

