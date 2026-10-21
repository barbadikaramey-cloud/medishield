# Architecture Decision Record (ADR)
## ADR-016: Production Migration of In-Code Catalogs to Relational Storage

**Status:** Accepted  
**Date:** 2026-10-18  
**Context:** Production Readiness, Elimination of Hardcoded Domain Dictionaries, Dynamic Clinical Guidelines, Relational Persistence as Single Source of Truth (Spec 05 § 4, Spec 06 § 2, Spec 09 § 1, ADR-008, ADR-012, ADR-015)  
**Deciders:** Lead Software Architect & AI Systems Engineer  

---

## 1. Context and Problem Statement

During MediShield's initial prototype and benchmarking phase, multiple clinical and procedural dictionaries were embedded directly in Python source files (`ocr_reconcile.py`, `claims.py`, `policy.py`, `clinical_align.py`, `kyc.py`, `seeder.py`):
1. **Hardcoded CPT / HCPCS Sets:** `SYNTHETIC_VALID_CPT_CODES`, `SUPPORTED_CPT_CATALOG`, and `CPT_EXPANSION_DICTIONARY` statically defined ~33 CPT codes with descriptions and categories in code.
2. **Hardcoded Clinical Guidelines & Indications (ADR-015):** `STANDARD_GUIDELINE_CATALOG` and `DIAGNOSIS_INDICATION_MAP` embedded 18 LCD guidelines and 9 anatomical/ICD compatibility mappings as in-memory Python dictionaries.
3. **Hardcoded Member Records:** `_get_default_seed_members()` in `kyc.py` maintained fallback member objects in code.
4. **Maintenance & Regulatory Inflexibility:** In production, quarterly updates from CMS, AMA CPT changes, or new payer clinical policies cannot require engineering source code changes or redeployments. Furthermore, running with in-memory shadow catalogs creates drift between what is in the database and what the agent logic evaluates.

Since MediShield supports full relational persistence via SQLite local fallback (ADR-008) and PostgreSQL 16 production, **all domain entities must reside strictly within the database schema as the single source of truth**.

---

## 2. Decision

We eliminate all in-code hardcoded medical catalogs, clinical guidelines, and member fallbacks in favor of relational persistence:

### 2.1 Database Schema Additions (`src/medishield/db/models.py`)
1. **`ClinicalGuideline` & `GuidelineCriterion` Tables:**
   - Persists all clinical practice guidelines, LCD identifiers, effective/expiration dates, and prior authorization flags.
   - Child table `GuidelineCriterion` persists mandatory evaluation criteria.
2. **`DiagnosisIndication` Table:**
   - Persists procedure-to-diagnosis compatibility rules, permitted ICD-10 prefixes, target anatomy, contraindicated prefixes, and discharge summary clinical keywords.
3. **`MedicalCode` Table (ADR-012):**
   - Serves as the authoritative source for all CPT, HCPCS, and Revenue codes, including short/long descriptions and taxonomy categories.

### 2.2 Relational Repositories (`src/medishield/db/catalog.py`)
1. **`SqlCodeCatalogRepository`:**
   - Extended to supply active CPT/HCPCS code sets and code metadata to `ClaimsAgent`, `OCRReconciler`, and `PolicyCoverageAgent`.
2. **`SqlClinicalGuidelineRepository`:**
   - Implements `ClinicalGuidelineRepository` protocol, querying `ClinicalGuideline`, `GuidelineCriterion`, and `DiagnosisIndication` from the database session.

### 2.3 Agent Decoupling from In-Code Sets
1. **`ClaimsAgent` & `OCRReconciler`:** Injected with `CodeCatalogRepository`; all CPT detection and validation queries the SQL catalog.
2. **`PolicyCoverageAgent`:** Queries CPT metadata dynamically from the `medical_codes` table, eliminating `CPT_EXPANSION_DICTIONARY`.
3. **`DeterministicClinicalAlignmentEngine`:** Queries `SqlClinicalGuidelineRepository`; eliminates `STANDARD_GUIDELINE_CATALOG` and `DIAGNOSIS_INDICATION_MAP` module dictionaries.
4. **`KYCAgent`:** Defaults to `SqlMemberRepository` querying the `members` database table; eliminates `_get_default_seed_members()`.

### 2.4 Relational Migration & Seeding (`migrations/versions/004_...`)
An Alembic migration creates the new clinical tables and seeds the initial medical codes, clinical guidelines, and diagnosis indication rules into SQLite and PostgreSQL.

---

## 3. Consequences

### Positive
- **Single Source of Truth:** Zero shadow catalogs in Python files. What is in SQLite/PostgreSQL is exactly what the agents evaluate.
- **Runtime Extensibility:** New CPT codes, guidelines, or diagnosis rules can be added via SQL or admin endpoints (`POST /api/v1/admin/terminology/upload`) without code edits.
- **Audit & Governance Compliance:** Rules, guidelines, and fee schedules are stored in relational tables with timestamps and versioning.

### Mitigations
- **Database Dependency:** Agents require database connectivity. In development and testing, SQLite (`data/medishield.db`) provides immediate local zero-config persistence per ADR-008.

