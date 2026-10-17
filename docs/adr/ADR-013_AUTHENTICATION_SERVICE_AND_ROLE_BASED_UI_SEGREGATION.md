# Architecture Decision Record (ADR)
## ADR-013: User Authentication Service, Credential Verification, and Role-Gated UI Segmentation

**Status:** Accepted  
**Date:** 2026-10-13  
**Context:** Multi-User Access Governance, Persona-Specific Authentication, and UI Route Segmentation (Spec 03 § 1, Spec 11 § 1, ADR-012)  
**Deciders:** Lead Software Architect & AI Systems Engineer

---

## 1. Context and Problem Statement

ADR-012 established the Three-Tier User Architecture (`CLAIMER`, `OPERATIONS_REVIEWER`, `ADMINISTRATOR`) on the backend API layer. However:
1. **Lack of Authentication Interface:** The frontend displayed all user views (Review Queue, Claimer Portal, Admin Console, STP Metrics) in a single navigation bar without credential challenges or login screens.
2. **Missing Auth Controller:** The backend accepted static bearer tokens in headers (`bearer reviewer-token`, `bearer admin-token`, `bearer claimer-token`) but lacked an interactive `POST /api/v1/auth/login` endpoint to authenticate credentials against verified user accounts.
3. **Absence of Client-Side Route Guards:** An unauthenticated user or a Claimer could access operator queues and admin configuration panes directly without persona enforcement.

---

## 2. Decision

We introduce an authoritative **Authentication Subsystem** and **Role-Gated Client Portal**:

### 2.1 Backend Auth Endpoint (`/api/v1/auth`)

1. **`POST /api/v1/auth/login`**:
   - Accepts JSON payload: `LoginRequest(username: str, password: str)`.
   - Validates credentials using secure comparison against registered user accounts:
     - `claimer` / `claimer123` $\rightarrow$ Role: `CLAIMER` (Patient / Provider)
     - `reviewer` / `reviewer123` $\rightarrow$ Role: `OPERATIONS_REVIEWER` (Claims Specialist)
     - `admin` / `admin123` $\rightarrow$ Role: `ADMINISTRATOR` (System Administrator)
   - Issues a JSON Web Token (JWT) signed with `settings.SECRET_KEY` (HS256) containing:
     - `sub`: `user_id`
     - `username`: `username`
     - `role`: Canonical role enum
     - `exp`: Token expiration timestamp
   - Returns `TokenResponse(access_token: str, token_type: "bearer", role: str, username: str, user_id: str)`.

2. **`GET /api/v1/auth/me`**:
   - Inspects `Authorization: Bearer <token>` and returns active `UserContext`.

### 2.2 Frontend Authentication & Persona Segmentation

1. **Authentication Context (`AuthProvider`):**
   - Stores authenticated user state, persists JWT in browser `localStorage`, and injects `Authorization: Bearer <token>` into all `apiFetch` requests.
2. **Dedicated Login Screen (`/login`):**
   - Standalone sign-in page with username/password inputs and role-preset buttons for streamlined evaluation.
3. **Role-Gated Navigation & View Isolation:**
   - **`CLAIMER`**: Sees only the Claimer Portal (`/claimer`); Review Queue and Admin Console links are hidden. Attempted navigation to `/` or `/admin` displays an access barrier.
   - **`OPERATIONS_REVIEWER`**: Sees Review Queue (`/`) and Case Workbench (`/cases/[id]`); Admin Console is hidden.
   - **`ADMINISTRATOR`**: Sees Admin Console (`/admin`), STP Metrics (`/metrics`), and Review Queue (`/`).
   - Profile status badge with current role and active session "Sign Out" functionality.

---

## 3. Consequences

### Positive:
- Strict segregation of duties matching real-world healthcare compliance (HIPAA Minimum Necessary standard).
- Eliminates accidental data leakage from administrative or reviewer interfaces to claimers.
- Easy to test and demonstrate via pre-configured personas.

### Negative / Trade-offs:
- Requires client-side session hydration on page reload.
