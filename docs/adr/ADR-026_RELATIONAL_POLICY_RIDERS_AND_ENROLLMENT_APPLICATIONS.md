# Architecture Decision Record
## ADR-026: Relational Policy Riders and Enrollment Applications

**Status:** Accepted  
**Date:** 2026-10-20  
**Context:** Policy RAG and Administration Specification § 1, Database and Storage Contracts § 4, ADR-016  
**Deciders:** MediShield Engineering

---

## 1. Context and Problem Statement

Policy versions carry effective dates, and policy context exposes an `active_riders` identifier list, but the relational schema does not persist rider terms or patient-specific rider state. The synthetic Gold policy generator contains rider benefits and waiting periods. Its patient amendment documents are explicitly requests, and the generated authorization fields are blank. Treating a requested rider as active would incorrectly turn unverified evidence into coverage.

## 2. Decision

1. Add `policy_riders`, keyed by policy version, to persist rider code/name, waiting-period duration and unit, the clause-defined start event, benefit limits, coverage/exclusion text, and source clause/PDF provenance.
2. Add `enrollment_rider_applications`, linked to an enrollment and policy rider, to represent requested, approved, denied, or withdrawn status and associated dates/source evidence.
3. Default applications to `REQUESTED`. An application can be `APPROVED` only when an approver, approval timestamp, and effective start date are present.
4. Seed Gold rider terms from the policy generator's shared structured rider definitions. Seed synthetic amendment requests as pending only. Generated content is marked unverified until checked against the authoritative policy artifact.
5. OCR may create or update a pending request with provenance. Customer documents cannot publish policy terms or authorize rider activation.
6. Do not use these records for coverage decisions until a separate canonical specification defines deterministic service applicability and waiting-period boundary semantics.

## 3. Consequences

### Positive
- Rider definitions are versioned with their controlling policy rather than duplicated in application logic.
- Member-specific requested and approved rider state is queryable and auditable.
- A request cannot silently become active coverage, and the relational schema enforces required approval evidence.
- Generated PDF policy terms and DB seed terms share one structured definition.

### Trade-offs and Follow-up
- Current seed data proves rider requests, not insurer approval; no benchmark patient will be assigned an active rider solely from an amendment request.
- `source_verified` remains false for generated policy text pending verification against the canonical artifact.
- A subsequent ADR/specification is required before implementing waiting-period calculations, rider precedence, or rider-driven approval/denial.
- API administration and OCR workflow integration are not part of this decision.
