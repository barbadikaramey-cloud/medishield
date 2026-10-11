# Feature Specification: FEAT-001 Foundation & Persistence Infrastructure

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-001`
- **Feature Name:** Foundation & Persistence Infrastructure
- **Phase:** Phase 1 (Foundation)
- **Status:** Approved for Implementation
- **Authoritative References:**
  - `docs/canonical/01_CANONICAL_ARCHITECTURE.md` (Sections 2, 3)
  - `docs/canonical/07_DATABASE_AND_STORAGE_CONTRACTS.md` (Sections 1, 2, 3)
  - `docs/canonical/11_SECURITY_HIPAA_AND_PROMPT_INJECTION_DEFENSES.md` (Sections 1, 2, 4)
  - `docs/adr/ADR-001_WORKFLOW_STATE_VERSUS_BUSINESS_DECISION_SEPARATION.md`
  - `docs/adr/ADR-006_SEPARATION_OF_POLICY_ADMIN_FROM_CUSTOMER_SUBMISSIONS.md`
  - `agents.md` (Rules 1, 4, 5, 6, 8, 12)

---

## 2. Purpose
Establish the foundational runtime configuration, package architecture, environment profiles, containerized backing services (PostgreSQL 16 with pgcrypto/uuid-ossp, Redis 7+, MinIO object storage), and production-grade database schema migrations implementing the 16 canonical tables and 6 enumerated types. This provides the durable persistence foundation upon which all subsequent business logic, agent workflows, and API layers operate.

---

## 3. Requirement References
- **REQ-ARCH-01:** System must maintain clean separation of concerns across 6 layers (Presentation, API, Async Jobs, AI Orchestration, Decision Authority, Persistence).
- **REQ-DB-01:** Implement 16 relational tables with strict referential integrity, foreign key cascades, and check constraints matching Canonical Spec 07.
- **REQ-DB-02:** Implement 6 PostgreSQL ENUM types decoupling workflow lifecycle state, business decisions, escalation sub-reasons, and agent verdicts per ADR-001.
- **REQ-DB-03:** Provide optimistic concurrency locking via integer `version` field on `cases` and `expected_case_version` on `human_reviews`.
- **REQ-DB-04:** Establish cryptographic SHA-256 hash chaining on `audit_events` (tamper-evident audit trail).
- **REQ-STORE-01:** Define S3/MinIO bucket layout with strict folder isolation (`raw/`, `ocr/`, `rendered/`, `policies/`, `reports/`).

---

## 4. Scope
- **In Scope:**
  - Standardized Python package structure under `src/medishield/` with dependency definitions (`pyproject.toml`).
  - Configuration management using `pydantic-settings` with strict environment variable validation and `.env.example`.
  - Docker Compose manifest orchestrating PostgreSQL 16 (with uuid-ossp/pgcrypto), Redis 7, and MinIO (with pre-provisioned bucket).
  - SQLAlchemy 2.0 declarative database models mirroring the 16 canonical tables and 6 enums.
  - Alembic migration environment and Initial baseline migration generating DDL matching Canonical Spec 07.
  - Storage client abstraction for local/MinIO/S3 object storage path resolution.
  - Unit and integration tests verifying schema generation, enum constraints, foreign keys, and audit hash calculation.
- **Out of Scope (Non-Goals):**
  - AI agent prompts or LLM invocation pipelines (Phase 2 & 3).
  - LangGraph workflow DAG orchestration (Phase 4).
  - FastAPI HTTP route handlers beyond basic DB health checks (Phase 5).
  - Frontend React/Next.js components (Phase 7).

---

## 5. Preconditions
- Python 3.11+ installed.
- Docker & Docker Compose or local PostgreSQL 16 instance available for migrations.
- Isolated Python virtual environment active.

---

## 6. Inputs & Outputs
- **Inputs:**
  - Environment configuration (`DATABASE_URL`, `REDIS_URL`, `S3_ENDPOINT_URL`, credentials).
  - Migration commands (`alembic upgrade head`).
- **Outputs:**
  - 16 initialized database tables and 6 ENUM types in PostgreSQL.
  - S3 bucket namespace readiness.
  - Reusable database session factory (`async_sessionmaker` / synchronous engine).

---

## 7. Data Model Impact (16 Tables)
1. `cases` (Case management aggregate, optimistic versioning, state tracking)
2. `documents` (Uploaded physical files, SHA-256 checksums, MIME types)
3. `document_pages` (1:N rasterized pages with pixel dimensions)
4. `members` (Read-only synthetic member master data)
5. `policies` (Authoritative master policy definitions)
6. `policy_versions` (Effective date ranges, source PDF references)
7. `enrollments` (Member coverage binding)
8. `policy_chunks` (Vectorized policy clauses)
9. `claims` (Extracted medical claim aggregate, total billed)
10. `claim_lines` (Individual CPT billing line items with provenance span IDs)
11. `agent_runs` (Telemetry, execution latency, verdicts, confidence)
12. `decisions` (Adjudication decision history, rule IDs, justifications)
13. `review_locks` (Reviewer concurrency lease management)
14. `human_reviews` (Human reviewer overrides and confirmations)
15. `audit_events` (Append-only cryptographic hash chain)
16. `idempotency_keys` (API request deduplication store)

**Enums:**
- `workflow_state_enum`: `RECEIVED`, `SCANNING`, `SPLIT`, `CLASSIFYING`, `ANALYZING`, `AWAITING_INFO`, `AWAITING_HUMAN`, `COMPLETED`, `FAILED`
- `business_decision_enum`: `APPROVE`, `REJECT`, `ESCALATE`
- `escalation_sub_reason_enum`: `MISSING_INFORMATION`, `HUMAN_REVIEW`, `FRAUD_REVIEW`, `SECURITY_REVIEW`, `TECHNICAL_FAILURE`, `AMBIGUITY`
- `agent_verdict_enum`: `PASS`, `FAIL`, `INDETERMINATE`, `CONFLICT`, `UNAVAILABLE`
- `document_type_enum`: `CLAIM_FORM`, `ID_DOCUMENT`, `DISCHARGE_SUMMARY`, `PRESCRIPTION`, `POLICY_AMENDMENT`, `UNKNOWN`
- `actor_type_enum`: `SYSTEM`, `HUMAN`

---

## 8. API & Workflow Impact
- No API breaking changes (establishes DB connection and health check foundation).
- No LangGraph workflow mutations.

---

## 9. Business Rules & Invariants
- **BR-DB-01:** Primary key UUIDs must be generated using `uuid_generate_v4()` or equivalent.
- **BR-DB-02:** Operational lifecycle states must never be conflated with business decisions.
- **BR-DB-03:** Case version increments on every state transition or human action to prevent lost updates.
- **BR-DB-04:** Every record inserted into `audit_events` must compute `current_hash = SHA256(id + prev_hash + payload + created_at)`. Genesis events use `"0" * 64` as `prev_hash`.

---

## 10. Security & Compliance Requirements
- **SEC-01:** Passwords, API tokens, and secret keys must only be loaded from environment variables, never hardcoded.
- **SEC-02:** Database connections must support SSL/TLS encryption.
- **SEC-03:** Object keys must follow rigid namespace conventions preventing path traversal (`/raw/{case_id}/{doc_id}`).

---

## 11. Error Handling & Observability
- Database connection errors must surface with explicit retry logs.
- Migration scripts must be transactional and rollback-safe.
- Structured logging (JSON format) with trace IDs for database queries.

---

## 12. Acceptance Criteria
1. `pyproject.toml` defines all required runtime dependencies (FastAPI, SQLAlchemy, Alembic, Pydantic v2, psycopg2/asyncpg, etc.) with locked minor versions.
2. `docker-compose.yml` starts PostgreSQL 16, Redis 7, and MinIO with healthchecks.
3. Alembic migration generates 100% of the DDL specified in Canonical Spec 07 with matching column types, indices, and constraints.
4. Test suite verifies:
   - Model definitions instantiate cleanly.
   - Enums reject invalid string values.
   - Foreign key cascading works as specified (e.g. deleting case cascades documents, agent runs, claims).
   - Audit event hash chaining generates mathematically correct SHA-256 digests.
