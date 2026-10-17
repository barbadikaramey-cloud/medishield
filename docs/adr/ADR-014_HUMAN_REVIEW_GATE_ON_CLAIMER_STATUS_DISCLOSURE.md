# Architecture Decision Record (ADR)
## ADR-014: Human Review Barrier on Claimer Adjudication Disclosure

**Status:** Accepted  
**Date:** 2026-10-14  
**Context:** Customer-Facing Adjudication Disclosure, HIPAA PHI & Defense Isolation, Human-in-the-Loop Governance (Spec 08 § 1.4, Spec 11 § 2, ADR-001, ADR-012)  
**Deciders:** Lead Software Architect & AI Systems Engineer

---

## 1. Context and Problem Statement

Under MediShield's automated pipeline, claims pass through OCR extraction, KYC identity matching, clinical terminology validation, and the decision engine.
When a claim cannot be auto-approved via Straight-Through Processing (STP P6), the pipeline assigns an interim recommendation (`APPROVE`, `REJECT`, or `ESCALATE`) and places the case in `WorkflowState.AWAITING_HUMAN`.

Previously, the patient-facing status endpoint (`GET /api/v1/cases/{case_id}/status`) inspected `case.current_decision` before checking lifecycle completeness (`case.is_complete` and `workflow_state == COMPLETED`). Consequently:
1. **Premature Adjudication Exposure:** A claim queued in `AWAITING_HUMAN` returned `status_label = "Claim Approved"` or `"Claim Denied"` along with itemized financial numbers and explanation letters before a human claims adjuster had reviewed or confirmed the case.
2. **Internal Deliberation Leakage:** For escalated cases (`ESCALATE`), the claimer received internal system justification notes (such as `"Fraud Escalation (P4): High fraud risk score... Duplicate billing detected"`) in the explanation letter field.
3. **Regulatory & Compliance Violation:** Presenting unfinalized automated recommendations as binding determinations violates healthcare claims regulations (HIPAA Minimum Necessary standard, NAIC claims settlement practices) and MediShield Rule 14 (*"Never let the LLM directly determine the final business decision"*).

---

## 2. Decision

We establish an authoritative **Human Review Gate on Claimer Status Disclosure**:

### 2.1 Two-Phase Adjudication Lifecycle

1. **Phase 1: In-Progress / Awaiting Human Review (`is_complete == False` or `workflow_state != COMPLETED`):**
   - The case is in an unfinalized state (`RECEIVED`, `SCANNING`, `CLASSIFYING`, `ANALYZING`, `AWAITING_HUMAN`, `AWAITING_INFO`).
   - The preliminary automated decision (`APPROVE`, `REJECT`, `ESCALATE`), fraud scores, and unfinalized financial settlement figures **must be strictly withheld** (`adjudication = None`).
   - The user-facing `status_label` reflects the operational pipeline stage:
     - `AWAITING_HUMAN` $\rightarrow$ **`"Under Specialist Review"`**
     - `AWAITING_INFO` $\rightarrow$ **`"Action Required: Missing Information"`**
     - `SCANNING` / `CLASSIFYING` / `ANALYZING` $\rightarrow$ **`"Processing Documents"`**
     - `RECEIVED` $\rightarrow$ **`"Claim Received"`**
     - `FAILED` $\rightarrow$ **`"Processing Issue Encountered"`**

2. **Phase 2: Finalized Adjudication (`is_complete == True` and `workflow_state == COMPLETED`):**
   - The case has concluded either through autonomous Straight-Through Processing (STP Clean Pass P6) or explicit human reviewer action (`/review/confirm` or `/review/override`).
   - The final determination is now legally binding and rendered to the claimer:
     - Final `APPROVE` $\rightarrow$ **`"Claim Approved"`**
     - Final `REJECT` $\rightarrow$ **`"Claim Denied"`**
   - The `adjudication` summary is populated with the finalized financial numbers (`covered_amount`, `total_billed`, `deductible_applied`, `copay_percentage`, `member_responsibility`) and the formal explanation notice.

---

## 3. Consequences

### Positive:
- **Zero Premature Commitment:** Eliminates the risk of communicating unapproved financial commitments or denials to patients.
- **Strict HIPAA & Proprietary Defense Isolation:** Internal fraud heuristics, specialist deliberations, and rule IDs are never exposed to claimers.
- **Clear Member Transparency:** Claimers see transparent progress indicating automated pre-checks are complete and specialist review is active.

### Negative / Trade-offs:
- Claimers cannot see estimated coverage amounts while their claim is sitting in `AWAITING_HUMAN`; they must wait until the reviewer completes the review.

