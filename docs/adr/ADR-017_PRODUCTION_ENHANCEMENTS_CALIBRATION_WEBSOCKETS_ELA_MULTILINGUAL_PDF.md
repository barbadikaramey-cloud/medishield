# ADR-017: Production Enhancements — Calibration, WebSockets, ELA, Multilingual OCR, and Case Reports

## Status
Accepted

## Date
2026-10-18

## Context
MediShield has established an automated multi-agent claims adjudication system with deterministic safety gates, relational persistence, and full audit provenance. To achieve production readiness for enterprise insurance carriers, five critical architectural enhancements are required:

1. **Orchestrator Confidence Calibration:** Raw heuristic confidence bottleneck scores must be calibrated against empirical correctness using statistical metrics (Brier score, ECE, reliability diagrams, Platt scaling, and Isotonic regression) to prevent uncalibrated overconfidence in automated determinations.
2. **Real-Time Agent Status Streaming:** Operations reviewers and claimers require trustworthy, real-time lifecycle visibility via WebSockets (`agent.started`, `agent.completed`, `agent.failed`, `case.escalated`, `case.completed`) with connection authorization, sequence numbering, and replay resilience.
3. **JPEG Tamper Detection Using Error Level Analysis (ELA):** KYC document verification requires forensic screening of JPEG compression inconsistencies to flag manipulated claim forms or altered ID cards as an auxiliary evidence signal without creating brittle rejection rules.
4. **Multilingual Document OCR (Hindi & Spanish):** Document intake must handle Devanagari Unicode script, Spanish diacritics, localized date/number formats, and mixed-language healthcare invoices locally without transmitting PHI to external APIs.
5. **Downloadable PDF Case Reports:** Compliance officers and claims adjusters require auditable, tamper-evident PDF case reports generated from immutable database snapshots using ReportLab, complete with page numbering, agent execution tables, and audit ledgers.

## Decision

### 1. Confidence Calibration Architecture
- Implement `src/medishield/eval/calibration.py` in pure `numpy` to eliminate heavy external ML dependencies.
- Provide `compute_brier_score`, `compute_expected_calibration_error` (ECE), `generate_reliability_diagram_data`, `PlattCalibrator` (logistic sigmoid), and `IsotonicCalibrator` (Pool Adjacent Violators Algorithm / PAVA).
- Separate raw heuristic confidence from calibrated confidence. Calibrators are fit strictly on training/validation splits and evaluated on held-out test sets.
- Expose `/api/v1/metrics/calibration` and render reliability curves on the frontend.

### 2. WebSocket Real-Time Event Architecture
- Implement `src/medishield/api/events.py` providing `CaseEventBroadcaster` using Starlette/FastAPI WebSockets.
- Enforce JWT authentication via query parameter `?token=...` or headers with case-specific role authorization.
- Versioned event contract: `event_id`, `schema_version="1.0"`, `case_id`, `run_id`, `agent_name`, `event_type`, `status`, `sequence_number`, `timestamp`, `duration_ms`, `safe_summary`.
- Bounded client queues with backpressure and in-memory event ring-buffer for reconnect replay.
- Integrate lifecycle emission hooks into workflow node execution.

### 3. Error Level Analysis (ELA) Forensics Architecture
- Implement `src/medishield/ocr/forensics.py` using Pillow.
- Enforce safe decompression limits (max 25MB, max 50 megapixels) to prevent decompression bombs.
- Recompress normalized JPEGs at quality=90, calculate absolute difference $D = |I_{orig} - I_{recomp}|$, compute statistical error distribution, and identify suspicious regions.
- Structured typed output: `ELAResult` with explicit states (`ANALYZED_NO_ANOMALY_DETECTED`, `ANOMALY_DETECTED`, `NOT_APPLICABLE`, `ANALYSIS_FAILED`).
- ELA acts strictly as an auxiliary screening signal within `KYCAgent` and cannot independently trigger an automatic rejection.

### 4. Multilingual OCR Pipeline Architecture
- Implement `src/medishield/ocr/multilingual.py`.
- Script & language detection for Devanagari and Latin/Spanish.
- Locale-aware parsing: Hindi numerals (०-९ to 0-9 for calculation while preserving original script), Spanish currency/date conventions (comma as decimal separator, Spanish month names).
- Normalization: NFKC normalization preserving matras and combining characters.
- RapidOCR integration with localized fallback handling.

### 5. PDF Case Report Generator Architecture
- Implement `src/medishield/reports/generator.py` using ReportLab.
- Create professional multi-page document layout using two-pass `NumberedCanvas` (`Page X of Y`).
- Sections: Report Metadata, Case Outcome Summary, Specialist Agent Matrix, Claims & Verification Breakdown, Discrepancies & Reviewer Decisions, Cryptographic Audit Trail.
- Authenticated endpoint: `GET /api/v1/cases/{case_id}/report`.
- Frontend download integration with loading state and blob cleanup.

## Consequences
- Zero external hosted AI APIs or paid services introduced.
- Strict preservation of existing decision outcomes (`APPROVE`, `REJECT`, `ESCALATE`) and safety rules.
- Complete auditability and provenance preserved across all new capabilities.

