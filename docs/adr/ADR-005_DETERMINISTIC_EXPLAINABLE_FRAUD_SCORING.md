# Architecture Decision Record (ADR)
## ADR-005: Deterministic Explainable Fraud Scoring Over Synthetic ML or Generative AI

**Status:** Accepted  
**Date:** 2026-10-02  
**Context:** Reviewer Feedback on Advanced Fraud Scoring vs. Healthcare Compliance Reality  
**Deciders:** Lead Software Architect & AI Systems Engineer

---

## 1. Context and Problem Statement

The reviewer feedback recommended upgrading the Fraud Detection Agent to an "advanced machine learning model." 

However, in this project:
1. **Synthetic Data Fallacy:** The system is evaluated on a synthetic dataset. Training an ML model (e.g., XGBoost or Random Forest) purely on synthetic samples creates an illusion of predictive power that completely fails in real-world clinical claims environments.
2. **Generative LLM Fraud Hallucination:** Using an LLM to "reason about whether this claim is fraudulent" results in non-deterministic, uncalibrated fraud scores that vary wildly across prompt runs and cannot withstand insurance fraud litigation or regulatory audits.
3. **Regulatory Non-Discrimination:** Federal healthcare regulations (ACA Section 1557, HIPAA) strictly forbid fraud algorithms from utilizing protected personal characteristics. An unconstrained ML model risks picking up spurious demographic correlations present in training data.

---

## 2. Decision

We **deliberately reject using generative LLMs or synthetic-only ML classifiers as the primary fraud engine for MVP**.

We mandate:
1. **Deterministic, Transparent Feature Suite:**
   - Fraud scoring is computed strictly from 7 explicit, auditable features: duplicate claim collision, digital image tampering (ELA), rapid resubmission velocity, provider charge $Z$-score outliers, future service dates, line-item arithmetic drift, and 30-day member claim frequency.
2. **Explainable Linear Scoring:**
   - Fraud score is a normalized weighted sum $\in [0.0, 1.0]$. Every flag is accompanied by the raw calculated feature value, its weight, and a plain-text audit explanation.
3. **Strict Prohibition on Protected Demographic Attributes:**
   - The fraud engine has zero access to race, gender, age, religion, national origin, or disability status.
4. **Calibrated Escalation Tiers:**
   - Scores $< 0.30$ map to `LOW` risk (auto-eligible).
   - Scores $\ge 0.70$ map to `HIGH` risk (mandatory human escalation).
   - Scores between $0.30$ and $0.70$ map to `MEDIUM` risk, but are **not** blanket escalated unless accompanied by secondary uncertainty, protecting straight-through processing rates.

---

## 3. Rationale & Consequences

### Positive Consequences
- **100% Audit Traceability:** Every fraud flag can be explained to a state insurance commissioner or hospital billing director with exact numbers and dates.
- **Repeatable & Deterministic:** Identical inputs yield identical fraud scores across every test run.
- **Legal Compliance:** Immune to algorithmic demographic bias claims.

### Negative / Mitigated Consequences
- Advanced non-linear fraud patterns (e.g., complex multi-hospital collusion rings) are not captured by single-claim features. This is accepted for MVP and documented as an enterprise roadmap expansion item once real historical de-identified claims datasets are available.
