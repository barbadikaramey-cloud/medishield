# Architecture Decision Record (ADR)
## ADR-011: LLM-Aided Explainability Over Deterministic Adjudication Decisions

**Status:** Accepted  
**Date:** 2026-10-11  
**Context:** Need for deep, human-understandable clinical and financial claim decision explanations on the operator frontend without violating autonomous deterministic authority  
**Deciders:** Lead Software Architect & AI Systems Engineer

---

## 1. Context and Problem Statement

MediShield's autonomous claims adjudication pipeline evaluates multi-document submissions against identity databases, claims arithmetic rules, policy RAG knowledge bases, and fraud scoring algorithms.

However, displaying only raw rule identifiers (e.g., `RULE-P6-CLEAN-PASS`, `RULE-P3-AMBIGUITY`) and one-sentence template justifications leaves human operators and compliance auditors with limited visibility into the clinical, financial, and policy reasoning underlying the decision:
1. **Explainability Deficit:** Claims adjusters need to know *which* specific line items failed arithmetic reconciliation, *why* a policy exclusion applies, or *how* fraud risk was calculated.
2. **Deterministic Governance Mandate (Rules 10, 11, 14):**
   - **Rule 10:** Business rules belong in code, not prompts.
   - **Rule 11:** Deterministic logic is sufficient for the business verdict; LLMs must not be relied upon to make the business decision.
   - **Rule 14:** Never let the LLM directly determine the final business decision.

We need a solution that generates comprehensive, structured, clinical-grade explanations for the frontend while preserving 100% deterministic authority over the business outcome.

---

## 2. Decision

We mandate that **Adjudication Explanations are strictly explanatory and decoupled from Decision Authority**:

1. **Deterministic Authority:**
   - The final adjudication verdict (`APPROVE`, `REJECT`, `ESCALATE`), rule ID (`RULE-P1` through `RULE-P8`), escalation sub-reason, and calibrated confidence are computed strictly by `DeterministicDecisionEngine`.
   - The LLM has zero authority to alter, override, or influence the verdict or confidence.

2. **LLM-Aided Clinical & Financial Explanation:**
   - After the deterministic engine reaches a decision, the system compiles the full specialist evidence snapshot (KYC verification, claims line items and arithmetic, policy grounding, fraud feature scores).
   - If Groq LLM (`openai/gpt-oss-120b`) is available, it synthesizes a comprehensive explanation covering:
     - **Executive Summary:** Plain-language explanation of the decision and matched rule.
     - **Member Identity & KYC Verification:** Matched member status, DOB/SSN validation, and tampering checks.
     - **Claims & Financial Reconciliation:** Total billed vs. sum of itemized charges, service dates, CPT/ICD-10 codes.
     - **Policy Coverage & Benefit Grounding:** Policy tier, deductible/coinsurance, specific clauses or exclusions cited.
     - **Fraud & Anomaly Assessment:** Deterministic fraud score, feature breakdown, statistical anomaly analysis.
     - **Actionable Guidance:** Clear, concrete steps for human review (if escalated) or audit notes (if auto-approved).

3. **Graceful Deterministic Fallback (Rule 11 & Rule 13):**
   - If Groq LLM is offline, unconfigured, or encounters a timeout/rate-limit, a deterministic rule-based generator synthesizes the exact same structured markdown explanation directly from the specialist summaries.
   - Adjudication never fails or pauses due to an explanation synthesis error.

4. **API and Storage Integration:**
   - The explanation is stored in `Decision.justification` and preserved in `agent_summary_snapshot["detailed_explanation"]`.
   - Exposed via `GET /api/v1/cases/{case_id}` in `detailed_explanation` and `justification`.
   - Rendered on the frontend in a dedicated "AI Decision Analysis & Clinical Rationale" component.

---

## 3. Rationale & Consequences

### Positive Consequences
- **Complete Audit Traceability:** Reviewers see comprehensive rationale for every decision.
- **Strict Compliance:** Obey Rules 10, 11, and 14 by keeping the business verdict deterministic while leveraging LLMs for natural language synthesis.
- **Zero Downtime:** Works identically online (LLM) and offline (deterministic fallback).
- **Reduced Operator Fatigue:** Claims adjusters can understand complex multi-document claims in seconds.

### Negative / Mitigated Consequences
- *Token usage and minor latency:* Mitigated by setting bounded timeouts (3.0s) and asynchronous execution in background tasks.

