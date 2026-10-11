# Architecture Decision Record (ADR)
## ADR-008: SQLite Development Persistence Fallback & Benchmark Dataset Seeding

**Status:** Accepted  
**Date:** 2026-10-06  
**Context:** Developer Experience, Persistence Without External Services, Benchmark Evaluation (Spec 05 § 4, Spec 08 § 1, Spec 11 § 1)  
**Deciders:** Lead Software Architect & AI Systems Engineer  

---

## 1. Context and Problem Statement

MediShield is architected for high-reliability enterprise operations using PostgreSQL 16 (system of record), Redis 7 (broker/cache), and S3/MinIO (object storage).

In ADR-007, an in-memory state fallback (`InMemoryDBSession`) was introduced for development environments when PostgreSQL was offline. While this enabled initial frontend and API rendering, the in-memory fallback had significant architectural limitations:
1. **Lack of Persistence:** Case records, uploads, and decisions were lost every time the backend server or worker restarted.
2. **Missing Master Data Tables:** Only `cases`, `documents`, and `decisions` were simulated; master relational entities such as `members`, `enrollments`, `policies`, and `policy_versions` were not queryable via SQL.
3. **Inconsistent KYC Lookups:** When users submitted claims for evaluation benchmark cases (such as Patient `PT_16658` in `CASE-20261006-7ACA03`), the KYC Agent failed with *"member id not found"* because the patient was absent from the rudimentary 3-item hardcoded in-memory list.
4. **Developer Prerequisite Friction:** Demanding a live PostgreSQL instance for local development or evaluation hinders quick prototyping and evaluation runs.

---

## 2. Decision

We establish an **automatic, file-backed SQLite persistence engine and benchmark dataset seeder** for development and testing environments:

1. **Automatic Database Engine Fallback:**
   - On application startup and session provisioning, the system probes PostgreSQL reachability (`SELECT 1`).
   - If PostgreSQL is reachable on `localhost:5432`, the production PostgreSQL engine (`SyncSessionLocal`) is used.
   - If PostgreSQL is offline or unreachable in `development` or `test` mode, the system automatically binds to a SQLite database at `data/medishield.db` (sync engine with `check_same_thread=False`).
   - In `staging` and `production` environments, PostgreSQL remains mandatory, failing fast with an infrastructure exception if unreachable.

2. **Cross-Dialect Schema Bootstrap Hook:**
   - To ensure SQLite compatibility without modifying production PostgreSQL models or Alembic migrations, a schema preparation utility adapts PostgreSQL-specific DDL syntax (e.g. server defaults containing `::jsonb` or `uuid_generate_v4()`) during SQLite table creation.
   - All standard tables (`cases`, `documents`, `document_pages`, `members`, `policies`, `policy_versions`, `policy_chunks`, `enrollments`, `claims`, `claim_lines`, `decisions`, `human_reviews`, `review_locks`, `audit_events`, `idempotency_keys`) are automatically created in SQLite via `Base.metadata.create_all()`.

3. **Authoritative 30-Patient Benchmark Dataset Seeding:**
   - An automated seeder populates SQLite on startup with all 30 patient clusters (`PT_19116` through `PT_41439`) deterministically derived from the canonical benchmark generator (`scripts/scripts/generate_docs.py`).
   - Each cluster seeds:
     - `members`: Patient ID, first name, last name, DOB, SSN last 4, address, status (`ACTIVE`).
     - `policies`: Gold Comprehensive Plan (`MED-GLD-...`), active status.
     - `policy_versions`: Effective version tag (`v1.0`), active coverage dates.
     - `enrollments`: Member-to-policy mapping covering the treatment dates.
   - Patient `PT_16658` (Cluster `C_012`, Karen Harris, DOB `1996-11-17`, Policy `MED-GLD-5437001`) is fully present and active.

4. **Repository Integration:**
   - KYC Agent supports `SqlMemberRepository` querying the `members` and `enrollments` tables directly.
   - Fallback `InMemoryMemberRepository` is updated to include all 30 benchmark members for standalone unit tests.

---

## 3. Rationale & Consequences

### Positive Consequences
- **True Relational Persistence:** Data persists across backend server restarts in `data/medishield.db` without requiring Docker or PostgreSQL.
- **Zero New Dependencies:** Utilizes Python's standard library `sqlite3` and SQLAlchemy's built-in SQLite dialect.
- **Complete Dataset Coverage:** All 30 benchmark patient clusters resolve correctly in KYC matching, eliminating spurious "member id not found" rejections.
- **Full Production Fidelity:** Production code paths and Alembic migrations targeting PostgreSQL remain 100% untouched and authoritative.

### Mitigated Consequences
- **Concurrency in High-Write Dev Environments:** SQLite uses file-level locking for writes. In single-developer local evaluation, WAL mode and standard SQLAlchemy connection management provide ample throughput. Production remains strictly PostgreSQL.

