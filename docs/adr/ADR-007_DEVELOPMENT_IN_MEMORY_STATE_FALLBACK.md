# Architecture Decision Record (ADR)
## ADR-007: Development In-Memory State Fallback for Local Interactive Previews

**Status:** Accepted  
**Date:** 2026-10-04  
**Context:** Developer Experience, Rapid Prototyping, and Local Evaluation (Spec 08 § 1, Spec 11 § 1)  
**Deciders:** Lead Software Architect & AI Systems Engineer

---

## 1. Context and Problem Statement

MediShield is architected for high-reliability enterprise operations using PostgreSQL 16 (system of record), Redis 7 (broker/cache), and MinIO (private object storage).

However, during local frontend evaluation, user testing, or constrained developer environments where Docker Desktop or a local PostgreSQL instance is not yet running, API requests requiring persistence fail with connection refusal (`OperationalError: connection to server at 'localhost:5432' failed`). This completely blocks developers and stakeholders from testing the Web Operator Console (`http://localhost:3000`), uploading files via the intake modal, or verifying human-in-the-loop review interactions.

---

## 2. Decision

We establish an **automatic, isolated in-memory development state fallback** within `src/medishield/api/deps.py`:

1. **Development & Test Environment Isolation:**
   - In `development` and `test` environments (`settings.ENVIRONMENT in ("development", "test")`), the database dependency probe tests PostgreSQL availability.
   - If PostgreSQL is reachable on `localhost:5432`, the production `SyncSessionLocal` session is utilized.
   - If PostgreSQL is offline or unreachable, the system gracefully falls back to a shared in-memory database session (`InMemoryDBSession`) mirroring the PostgreSQL interface.

2. **Strict Production Enforcement:**
   - In `staging` and `production` environments, the system strictly enforces live PostgreSQL connectivity. If PostgreSQL is unreachable in production, requests fail fast with explicit database infrastructure alerts.

3. **In-Memory Store Fidelity:**
   - The fallback store provides full CRUD operations for `Case`, `Document`, `ReviewLock`, `HumanReview`, `Policy`, `PolicyVersion`, and `AuditEvent`.
   - Seeded with realistic demo cases upon initialization to enable immediate inspection of the Review Queue, document viewers, and metrics charts.

---

## 3. Rationale & Consequences

### Positive Consequences
- **Zero-Friction Local Testing:** Users can immediately run and test the frontend console and backend endpoints with zero external database prerequisites.
- **Full Workflow Simulation:** Case intake (`POST /cases`), queue queries (`GET /cases`), review locks (`POST /lock`), and human overrides (`POST /override`) work out-of-the-box.
- **Complete Test Suite Preservation:** Existing unit and integration tests remain 100% unaffected.

### Negative / Mitigated Consequences
- **Transient Dev State:** State held in memory resets when the development server restarts. This is standard behavior for development mock environments. Production deployments remain backed by durable PostgreSQL 16.

