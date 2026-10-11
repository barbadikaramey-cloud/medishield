# Architecture Decision Record (ADR)
## ADR-002: Autonomous Hard Rejection Preservation

**Status:** Accepted  
**Date:** 2026-10-02  
**Context:** Reviewer Recommendation on Autonomous Denials vs. Assignment 02 Core Behavior  
**Deciders:** Lead Software Architect & AI Systems Engineer

---

## 1. Context and Problem Statement

The reviewer feedback recommended removing autonomous `REJECT` decisions entirely from the automated pipeline, arguing that all automated denials should be downgraded to `RECOMMEND_DENIAL` and routed to a human reviewer for formal sign-off.

While human confirmation is desirable for high-liability borderline cases, eliminating automated rejection entirely directly violates the core assignment specification:
1. Assignment 02 explicitly establishes `APPROVE`, `REJECT`, and `ESCALATE` as the automated orchestrator decisions.
2. The functional requirement explicitly mandates: *"Auto-reject must remain supported for confirmed hard failures."*
3. Operational reality dictates that sending every clear-cut, invalid, non-enrolled, or expired claim to human review destroys Straight-Through Processing (STP) and recreates the exact 4.2-day backlog and $18.4M overhead that the multi-agent system was commissioned to eliminate.

---

## 2. Decision

We **deliberately reject** the reviewer's proposal to eliminate autonomous rejection.

We establish the following canonical policy:
1. **Autonomous Auto-Reject is Preserved for Confirmed Hard Failures:**
   - When a failure is deterministic, verifiable, and free of OCR ambiguity (e.g., Member ID is verified but does not exist in MediShield's enrollment database; Government ID has passed expiration date by $> 180$ days; Procedure CPT code is explicitly listed under policy exclusions with $0.0\%$ coverage).
   - The Decision Engine emits `BusinessDecision.REJECT`, and the case transitions to `WorkflowState.COMPLETED`.
2. **Uncertainty Never Auto-Rejects:**
   - If an extraction is blurry, an OCR span confidence is low ($< 0.70$), data contradicts another document, or a dependency fails, the agent must emit `INDETERMINATE` or `CONFLICT`.
   - The Decision Engine **never** converts uncertainty into a rejection; it routes immediately to `BusinessDecision.ESCALATE` (`WorkflowState.AWAITING_HUMAN`).
3. **Auditability & Appealability:**
   - Every auto-reject decision includes explicit, grounded reason codes and cited policy/database evidence so the member receives an unambiguous legal denial notice.

---

## 3. Rationale & Consequences

### Positive Consequences
- **Preserves Straight-Through Processing (STP):** Allows legitimate bad claims (e.g. fraudulent re-submissions, non-members) to be rejected instantly without human reviewer fatigue.
- **Strict Compliance with Assignment Baseline:** Maintains the expected `APPROVE` / `REJECT` / `ESCALATE` decisioning gate.
- **Protection Against False Negatives:** Soft failures, low OCR confidence, and technical errors are prevented from turning into false rejections through Decision Rule P1–P4 precedence.

### Negative / Mitigated Consequences
- Legal/compliance teams may require sample-audit oversight on auto-rejected claims. This is mitigated by logging every auto-rejection to `audit_events` with full rule matching justification and enabling post-hoc manager review.
