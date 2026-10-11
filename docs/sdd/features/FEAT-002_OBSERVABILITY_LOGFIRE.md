# Feature Specification: FEAT-002 Observability & Telemetry with Pydantic Logfire

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-002`
- **Feature Name:** Observability & Telemetry Infrastructure with Logfire
- **Phase:** Phase 1 (Foundation / Cross-Cutting Observability)
- **Status:** Proposed
- **Authoritative References:**
  - `docs/canonical/01_CANONICAL_ARCHITECTURE.md` (Section 2: Layer 2, Layer 3, Layer 4)
  - `docs/canonical/11_SECURITY_HIPAA_AND_PROMPT_INJECTION_DEFENSES.md` (Section 1.1: Technical Safeguard Controls & PHI Sanitization)
  - `agents.md` (Rule 1, 3, 5, 6, 12)

---

## 2. Purpose
Integrate unified structured logging, OpenTelemetry tracing, and execution diagnostics using **Pydantic Logfire** across the MediShield ecosystem. Provide clear developer debugging capabilities and production-grade distributed tracing while strictly enforcing HIPAA-mandated PHI/PII sanitization and scrubbing rules on log records and trace spans.

---

## 3. Requirement References
- **REQ-OBS-01:** Unified structured logging and distributed tracing across HTTP requests, database transactions, async Celery tasks, and LangGraph multi-agent execution steps.
- **REQ-SEC-HIPAA-01 (Spec 11, § 1.1):** Application logs and trace spans must be stripped of Protected Health Information (PHI) and Personally Identifiable Information (PII), including Social Security Numbers, member names, and medical notes. Only anonymized IDs (`case_id`, `document_id`, `execution_id`) may appear in log contexts.
- **REQ-OBS-02:** Logfire telemetry must support local offline debugging (`send_to_logfire='if-token-present'` or console-only mode) so tests and offline development run cleanly without remote network egress.

---

## 4. Scope
- **In Scope:**
  - Addition of `logfire` dependency to `pyproject.toml` with explicit version constraint.
  - Configuration settings for Logfire (`LOGFIRE_TOKEN`, `LOGFIRE_SEND_TO_LOGFIRE`, `LOGFIRE_SERVICE_NAME`, `LOGFIRE_ENVIRONMENT`).
  - Core logging initialization module (`src/medishield/core/telemetry.py`) with HIPAA PHI scrubbing rules.
  - Instrumentation hooks for SQLAlchemy database queries and Pydantic schemas.
  - Unit tests verifying initialization, offline graceful degradation, and PHI regex scrubbing.
- **Out of Scope (Non-Goals):**
  - Third-party cloud dashboard setup or proprietary cloud credentials.
  - Instrumenting FastAPI or Celery before those components are constructed in subsequent phases.

---

## 5. Preconditions
- Virtual environment `.venv` active with Python 3.11+.
- `FEAT-001` (Persistence models & configuration) complete.

---

## 6. Inputs & Outputs
- **Inputs:**
  - Environment variables: `LOGFIRE_TOKEN`, `LOGFIRE_SEND_TO_LOGFIRE`, `LOG_LEVEL`.
  - Application log calls (`logfire.info(...)`, `logfire.error(...)`, `logfire.span(...)`).
- **Outputs:**
  - Sanitized OpenTelemetry trace spans and structured log entries formatted as JSON or console output.
  - Traced database transactions via SQLAlchemy listener hooks.

---

## 7. Data Model Impact
- No database schema migrations required (this is a cross-cutting application telemetry feature).
- No relational tables modified.

---

## 8. API & Workflow Impact
- Transparent middleware and telemetry wrappers.
- No changes to existing REST contracts or LangGraph state machine specs.

---

## 9. Business Rules & Invariants
- **BR-OBS-01 (Zero PHI in Telemetry):** Every log record and span attribute must pass through the HIPAA scrubber. Any regex match for SSN (`\d{3}-\d{2}-\d{4}`), credit cards, or raw medical text in span keys must be masked with `[REDACTED_PHI]`.
- **BR-OBS-02 (Offline Safe):** If `LOGFIRE_TOKEN` is unset or empty, Logfire runs in local console fallback mode with zero network requests and zero blocking overhead.

---

## 10. Security Requirements
- Tokens and API keys must never be logged.
- PHI scrubbing occurs before any trace data leaves the memory boundary.

---

## 11. Error Handling & Observability
- Telemetry initialization failures must log a local warning to `sys.stderr` and fail open, never preventing application boot.

---

## 12. Acceptance Criteria
1. `pyproject.toml` includes `logfire` dependency.
2. `src/medishield/core/telemetry.py` exports `setup_telemetry()` and helper logging bindings.
3. PHI scrubbing filter automatically masks SSNs and sensitive fields.
4. SQLAlchemy engine can be instrumented without leaking query bind parameters that contain PHI.
5. Unit tests pass verifying telemetry configuration and PHI scrubbing.
