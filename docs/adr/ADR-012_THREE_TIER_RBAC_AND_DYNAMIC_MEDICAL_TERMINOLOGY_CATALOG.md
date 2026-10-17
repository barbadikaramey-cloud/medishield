# Architecture Decision Record (ADR)
## ADR-012: Three-Tier User Persona Architecture and Dynamic Medical Terminology Catalog

**Status:** Accepted  
**Date:** 2026-10-13  
**Context:** Multi-User Access Governance, Customer Self-Service Intake, Operator Review, Policy Administration, and Scale-Out Clinical Terminology Storage (Spec 03 § 1, Spec 08 § 1.4, Spec 09 § 1, Spec 11 § 1)  
**Deciders:** Lead Software Architect & AI Systems Engineer

---

## 1. Context and Problem Statement

MediShield's initial prototype operated with a single-portal, developer-focused operational console and hardcoded in-memory catalogs:
1. **Lack of User Persona Segmentation:**
   - **Claimers (Patients / Healthcare Providers):** Could not independently submit claims, upload documents, or monitor claim adjudication status without access to the internal operator console.
   - **Reviewers (Claims Adjusters):** Need dedicated queues, locking semantics, OCR bounding box provenance overlays, and override controls to adjudicate human-escalated claims.
   - **Administrators (Actuarial & Operations Managers):** Lacked authorized tooling to ingest corporate master policies (PDFs) and upload medical terminology fee schedules (CPT, ICD-10, HCPCS CSVs) or monitor executive system health.
2. **Scalability Ceiling of Hardcoded Medical Catalogs:**
   - Procedural codes, format validation, and RAG semantic expansions were hardcoded as static Python data structures (`SYNTHETIC_VALID_CPT_CODES`, `SUPPORTED_CPT_CATALOG`, `CPT_EXPANSION_DICTIONARY`).
   - Production operations require managing **over 100,000 active codes** (AMA CPT ~10,000, HCPCS ~5,000, ICD-10-CM ~72,000, Revenue & NDC codes) that change quarterly through CMS updates. Hardcoded Python sets require engineering code deployments for data-only updates and lack temporal validity (`effective_from`, `effective_to`), modifiers, and fees.
3. **Data Security & PHI Boundary (Rule 12 & Spec 11):**
   - Untrusted customer claim submissions must be strictly segregated from authoritative corporate policy storage (ADR-006). Customer uploads must never write to or pollute vector stores.
   - Internal fraud anomaly scores, injection defenses, and raw specialist reasoning traces must remain private to reviewers and administrators, while claimers receive sanitized, plain-language adjudication summaries.

---

## 2. Decision

We establish a **Three-Tier User Persona Architecture** and a **Dynamic Relational Medical Terminology Catalog**:

### 2.1 Three-Tier Role-Based Access Control (RBAC)

We define three first-class user personas with strictly separated route authorization:

```
┌────────────────────────────────────────────────────────────────────────┐
│ 1. CLAIMER (Member / Healthcare Provider Portal)                       │
│    • Role: CLAIMER                                                     │
│    • Submit multi-file claim batches (POST /api/v1/cases/submit)       │
│    • Real-time status tracking (GET /api/v1/cases/{case_id}/status)    │
│    • Sanitized adjudication view (covered amount, deductible, letters) │
│    • RESTRICTION: No access to internal reviewer queue, fraud vectors, │
│      or system prompts. Scoped strictly to the member's own claims.    │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│ 2. REVIEWER (Claims Specialist Adjudication Console)                   │
│    • Roles: OPERATIONS_REVIEWER, OPERATIONS_MANAGER                    │
│    • Operational Queue: Filter by AWAITING_HUMAN, P4, P5, P7           │
│    • Concurrent Review Locking (15-min lease) to prevent collisions     │
│    • Deep Provenance: OCR image overlays, itemized line arithmetic     │
│    • Binding Human Adjudication (Confirm, Override, Request Info)       │
│    • RESTRICTION: Cannot publish corporate policies or modify catalogs.│
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│ 3. ADMINISTRATOR (Policy & Terminology Governance)                    │
│    • Role: ADMINISTRATOR                                               │
│    • Master Policy Ingestion: Upload policy PDFs (Docling -> Qdrant)   │
│    • Medical Terminology Ingestion: Upload CPT/ICD CSV fee schedules   │
│    • Executive Dashboard: STP rate, SLA latency, audit verification    │
└────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Relational Medical Terminology Entity (`medical_codes`)

We introduce an authoritative database model `MedicalCode` in `src/medishield/db/models.py` backed by Alembic migration `003_medical_codes_catalog`:
- `id` (UUID, primary key)
- `code` (VARCHAR(32), indexed)
- `coding_system` (ENUM: `CPT`, `HCPCS`, `ICD10_CM`, `REV_CODE`, `NDC`, indexed)
- `short_description` (VARCHAR(255))
- `long_description` (TEXT, nullable)
- `category` (VARCHAR(64), nullable)
- `standard_charge` (NUMERIC(10, 2), nullable)
- `requires_prior_auth` (BOOLEAN, default False)
- `effective_from` (DATE, nullable)
- `effective_to` (DATE, nullable)
- `is_active` (BOOLEAN, default True, indexed)
- `uq_medical_codes_system_code` (Unique constraint on `(coding_system, code)`)

### 2.3 Repository Pattern & Caching Tier

1. **`CodeCatalogRepository` Protocol:**
   - `is_valid_code(code: str, coding_system: CodingSystem) -> bool`
   - `get_code_details(code: str, coding_system: CodingSystem) -> MedicalCode | None`
   - `search_codes(query: str, coding_system: CodingSystem | None, limit: int) -> list[MedicalCode]`
   - `bulk_upsert_codes(codes: list[MedicalCodePayload]) -> int`
2. **`SqlCodeCatalogRepository`:**
   - Production implementation reading from PostgreSQL/SQLite.
   - Caches active code sets in Redis (`SET active_cpt_codes`) or local LRU cache to maintain sub-millisecond ($O(1)$) lookups during high-frequency OCR spatial reconciliation and claim line validation.
3. **`InMemoryCodeCatalogRepository`:**
   - Retained as a lightweight, zero-dependency test fixture seeded with benchmark codes to guarantee unit test independence.

### 2.4 Agent Decoupling Strategy

- **`ClaimsAgent`:** Injects `CodeCatalogRepository` to validate procedure and diagnosis codes against the dynamic catalog instead of hardcoded `SUPPORTED_CPT_CATALOG`. Malformed or unknown codes fail deterministically as `INDETERMINATE`.
- **`PolicyCoverageAgent`:** Hydrates clinical descriptions dynamically via `CodeCatalogRepository.get_code_details(code)` instead of static `CPT_EXPANSION_DICTIONARY`.
- **`OCR Spatial Reconciler`:** Uses `CodeCatalogRepository.is_valid_code()` or in-memory set fallback for spatial token disambiguation.

---

## 3. Rationale & Consequences

### Positive Consequences
- **Enterprise Scale:** Supports 100,000+ codes loaded via Admin CSV upload without application redeployment or code modifications.
- **Role Isolation & Compliance:** Strict segregation of untrusted claimer files vs. reviewer adjudication vs. administrator master knowledge protects HIPAA confidentiality and system integrity.
- **High Performance:** Retains sub-millisecond validation speeds via Redis/memory caching layer.
- **Deterministic Governance (Rules 10, 11, 14):** All code lookups, fee schedule validations, and permission checks are 100% deterministic code paths.
- **100% Backward Compatibility:** The 30 benchmark cases and 256 existing unit tests continue to pass seamlessly via test fixtures.

### Mitigated Consequences
- **Cache Invalidation:** When an administrator uploads a new code CSV, the API triggers an atomic cache refresh (`POST /api/v1/admin/codes/upload` refreshes Redis set/cache).

