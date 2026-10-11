# Architecture Decision Record (ADR)
## ADR-006: Separation of Policy Administration from Customer Document Submissions

**Status:** Accepted  
**Date:** 2026-10-02  
**Context:** Reviewer Recommendation on Policy Knowledge Integrity  
**Deciders:** Lead Software Architect & AI Systems Engineer

---

## 1. Context and Problem Statement

In the initial Assignment 02 documentation, one of the supported document classification types was listed as `POLICY_AMENDMENT`. Naive interpretations of the architecture suggested that when a `POLICY_AMENDMENT` was uploaded, it would be passed to the Policy RAG pipeline to update policy rules.

As the reviewer correctly identified, allowing customer-submitted documents to alter the authoritative knowledge base represents a catastrophic security vulnerability:
- A malicious actor could submit a fabricated PDF stating: *"MediShield Amendment 2026: All elective cosmetic procedures are 100% covered with zero deductible."*
- If ingested into the vector store, this untrusted document would pollute the semantic search corpus and cause the Policy Agent to approve fraudulent claims for other policyholders.

---

## 2. Decision

We **fully accept and formalize the reviewer's recommendation** by establishing a strict architectural firewall between customer submissions and corporate policy administration:

1. **Customer Submissions Are Read-Only Evidence:**
   - When a member or employer submits a document classified as `POLICY_AMENDMENT` via `POST /api/v1/cases`, it is treated purely as a case-level attachment.
   - It is routed to an Operations Reviewer in `AWAITING_HUMAN` as an endorsement request.
   - It is **strictly prohibited from writing to or updating** the vector database (Qdrant / ChromaDB) or PostgreSQL `policy_versions` table.
2. **Authoritative Policy Publication is Admin-Controlled:**
   - Official corporate policies, master benefit schedules, and underwriting riders can only be published through a dedicated endpoint: `POST /api/v1/admin/policies`.
   - Access to this endpoint requires the `ADMINISTRATOR` RBAC role.
   - Ingestion requires administrative verification of plan identifiers, version tags, and effective date windows.

---

## 3. Rationale & Consequences

### Positive Consequences
- **Knowledge Base Integrity:** The vector store remains an untampered, legally binding source of truth.
- **Immunity to Knowledge Poisoning:** Untrusted customer files cannot corrupt corporate coverage reasoning.
- **Audit Compliance:** Changes to policy terms are tied to corporate administrative user IDs and change authorization tickets.

### Negative / Mitigated Consequences
- Customers cannot achieve instant automated endorsement updates for complex policy riders. This matches insurance regulatory reality, as policy amendments require legal underwriting approval.
