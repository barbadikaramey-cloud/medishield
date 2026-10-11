# Architecture Decision Record (ADR)
## ADR-001: Workflow State vs. Business Decision Separation

**Status:** Accepted  
**Date:** 2026-10-02  
**Context:** Reviewer Feedback vs. Assignment 02 Functional Specification  
**Deciders:** Lead Software Architect & AI Systems Engineer

---

## 1. Context and Problem Statement

The reviewer feedback recommended introducing new top-level outcome enumerations such as `RECOMMEND_DENIAL`, `REQUEST_INFO`, and `NEEDS_HUMAN` as alternatives to the assignment-mandated outcomes `APPROVE`, `REJECT`, and `ESCALATE`.

However, Assignment 02 explicitly establishes that the system must produce an `APPROVE`, `REJECT`, or `ESCALATE` business outcome. Conflating operational pipeline lifecycle states (e.g., waiting for an additional document) with legal business decisions (e.g., claim denied) creates competing state machines, confuses the Case Management UI, and breaks automated downstream billing integrations.

---

## 2. Decision

We **deliberately reject** the introduction of `RECOMMEND_DENIAL`, `REQUEST_INFO`, and `NEEDS_HUMAN` as top-level business decision enums.

Instead, we enforce a strict two-axis separation of concerns:

1. **Workflow State Axis (`WorkflowState`):**
   - `RECEIVED`, `SCANNING`, `SPLIT`, `CLASSIFYING`, `ANALYZING`, `AWAITING_INFO`, `AWAITING_HUMAN`, `COMPLETED`, `FAILED`.
2. **Business Decision Axis (`BusinessDecision`):**
   - `APPROVE`, `REJECT`, `ESCALATE`.
3. **Escalation Sub-Reason Axis (`EscalationSubReason`):**
   - `MISSING_INFORMATION`, `HUMAN_REVIEW`, `FRAUD_REVIEW`, `SECURITY_REVIEW`, `TECHNICAL_FAILURE`, `AMBIGUITY`.

### Semantic Mapping of Reviewer Concepts:
- **Reviewer's `REQUEST_INFO`** is mapped to:
  $$\text{BusinessDecision.ESCALATE} + \text{EscalationSubReason.MISSING_INFORMATION} + \text{WorkflowState.AWAITING_INFO}$$
- **Reviewer's `NEEDS_HUMAN`** is mapped to:
  $$\text{BusinessDecision.ESCALATE} + \text{EscalationSubReason.HUMAN_REVIEW} + \text{WorkflowState.AWAITING_HUMAN}$$
- **Reviewer's `RECOMMEND_DENIAL`:**
  - If a confirmed hard business rule fails (e.g., KYC failure, policy exclusion): **`BusinessDecision.REJECT`** (auto-reject).
  - If unconfirmed, ambiguous, or incomplete: **`BusinessDecision.ESCALATE`** (`HUMAN_REVIEW`).

---

## 3. Rationale & Consequences

### Positive Consequences
- **Strict Backward Compatibility:** Fully satisfies Assignment 02 core requirements.
- **Single Source of Truth:** The UI, API, and database possess exactly one unambiguous status field for pipeline tracking (`workflow_state`) and one for business adjudication (`current_decision`).
- **Audit Clarity:** Compliance auditors can distinguish between a claim that was rejected on the merits (`REJECT`) versus a claim paused because the member forgot to upload their ID card (`ESCALATE: MISSING_INFORMATION`).

### Negative / Mitigated Consequences
- Reviewers looking for literal `REQUEST_INFO` or `NEEDS_HUMAN` strings in the `decisions.decision` column must query `current_decision` combined with `workflow_state` and `escalation_sub_reason`. This is addressed via clear API response models and UI filtering tags.
