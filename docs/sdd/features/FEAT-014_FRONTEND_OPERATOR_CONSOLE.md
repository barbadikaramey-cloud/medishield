# Feature Specification: FEAT-014 Frontend Operations Dashboard & Review Console

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-014`
- **Feature Name:** Frontend Operations Dashboard & Human Review Console
- **Phase:** Phase 11 (Frontend Operator Console)
- **Status:** In Progress
- **Authoritative References:**
  - `docs/canonical/01_CANONICAL_ARCHITECTURE.md` (Section 2 Layer 1)
  - `docs/canonical/08_REST_API_SPECIFICATION.md` (Sections 1–5 API endpoints)
  - `docs/canonical/07_DATABASE_AND_STORAGE_CONTRACTS.md` (Section 8 Review Locks)
  - `docs/canonical/12_EVALUATION_AND_SYNTHETIC_DATA_FRAMEWORK.md` (Section 5.1 UI Functionality)
  - `AGENTS.md` (Rules 1–15)

---

## 2. Purpose
Provides the human interface for operations reviewers and managers:
1. **Intake & Queue Management:**
   - Drag-and-drop multipart document intake handshake (`POST /api/v1/cases`).
   - Paginated review queue with filter chips (`AWAITING_HUMAN`, `AWAITING_INFO`, `COMPLETED`, `FAILED`) and SLA countdowns.
2. **Side-by-Side Adjudication & Document Viewer:**
   - Multi-document roster with rendered page previews and physical OCR text spans.
   - Evidence inspector displaying specialist verdicts: KYC Agent, Claims Agent, Policy Coverage RAG clauses, and Fraud Signals (P1–P8).
3. **Human-in-the-Loop Review Console:**
   - 15-minute optimistic review lock acquisition and automatic release countdown.
   - Structured override dialog enforcing mandatory `reason_code` and $\ge 10$-character remarks.
   - Request-for-Information (RFI) modal for `AWAITING_INFO` supplementary uploads.
4. **Operational Analytics & Cryptographic Verification:**
   - Live STP rate meter, decision distribution charts, and real-time HMAC-SHA256 audit chain verification badge (`chain_intact: true`).

---

## 3. Requirement References
- **REQ-FE-01 (Spec 01 § 2 Layer 1):** Operator Dashboard with real-time review queues.
- **REQ-FE-02 (Spec 08 § 2.1-2.3):** Case ingestion and paginated case exploration.
- **REQ-FE-03 (Spec 08 § 3.1-3.5):** Review lock acquisition, decision override, confirmation, and RFI.
- **REQ-FE-04 (Spec 08 § 5.1-5.2):** Cryptographic audit integrity badge and operational metrics.
- **REQ-FE-05 (Spec 12 § 5.1):** End-to-end UI functionality validation.

---

## 4. Architectural Scope & Affected Components
- **Frontend Project:** `frontend/`
  - `package.json`, `tsconfig.json`, `tailwind.config.ts`, `next.config.js`
  - `src/app/layout.tsx`: Main application shell with navigation and theme.
  - `src/app/page.tsx`: Operations Queue Dashboard with filter chips, search, and intake modal.
  - `src/app/cases/[id]/page.tsx`: Side-by-side Case Detail, Document Viewer, Evidence Inspector, and Review Console.
  - `src/app/metrics/page.tsx`: Operational KPIs, STP rate, and audit chain verification.
  - `src/lib/api.ts`: Typed API client connecting to MediShield REST API (`http://localhost:8000/api/v1`).
  - `src/components/`: Reusable UI components (CaseTable, DocumentViewer, ReviewLockBar, OverrideModal, AuditBadge).

---

## 5. Acceptance Criteria (Verification Matrix)
1. **API Client Integration:** Correctly types and consumes all REST endpoints (`/cases`, `/review/lock`, `/review/override`, `/audit`, `/metrics`).
2. **Optimistic Locking:** Displays active lock owner and expiration countdown, disables override when not locked.
3. **Validation Guards:** Enforces remarks length $\ge 10$ characters before allowing override submission.
4. **Audit Verification:** Shows visual indicator of HMAC cryptographic chain intact status.
5. **Clean Compilation:** Next.js application compiles with zero TypeScript errors.
