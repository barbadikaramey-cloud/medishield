# MediShield Multi-Agent Document Intake System
## V2 Architecture & Design Revisions — Reviewer Response

**Document type:** Architecture / Design Change Note  
**Status:** Proposed V2 Baseline  
**Purpose:** Address reviewer feedback and document the agreed production-grade design changes for the MediShield AI-powered claims/document intake system.

---

## 1. Executive Summary

The first documentation package established the overall multi-agent architecture, LangGraph workflow, FastAPI ingestion layer, Policy RAG, case-management UI, audit logging, and evaluation framework.

The reviewer feedback identified several genuine architectural gaps and inconsistencies, especially around:

- policy amendment authority
- multi-document case handling
- dependency between claim extraction and policy evaluation
- uncertainty handling
- human-review boundaries
- AI decision vs. human decision history
- workflow state consistency
- database/API completeness
- healthcare-data security
- fraud model definition
- evaluation methodology

We accept these findings where they improve correctness, safety, traceability, and implementability.

At the same time, two design principles are being refined rather than blindly applying blanket escalation or eliminating parallelism:

1. **Policy processing will remain partially parallel.** Policy context resolution can run alongside KYC and Claims; final coverage evaluation will use validated claim information when available.
2. **Human review is an exception path, not the normal processing path.** The platform is explicitly optimized for straight-through processing (STP), while preserving a hard requirement for evidence, validation, and faithfulness.

The resulting architecture follows this principle:

> **Use the minimum sufficient AI computation required to produce a faithful, validated, evidence-backed result.**

Agentic does not mean LLM-everywhere. LLMs are used for document understanding, extraction, and interpretation where they add material value. Deterministic code, databases, retrieval, statistical models, and business rules are used wherever exactness is more appropriate.

---

# 2. Design Principles for V2

## 2.1 Faithfulness is a non-negotiable constraint

Cost and latency may be optimized, but not by removing evidence, validation, provenance, or policy grounding.

For every material conclusion, the system should be able to trace:

```text
Decision / Result
      ↓
Validated fact or interpretation
      ↓
Source evidence
      ↓
Document page / field / policy clause / database record
```

The objective is not to maximize the number of LLM calls. The objective is to maximize trustworthy automation.

## 2.2 LLMs are used selectively

The architecture intentionally distinguishes between:

**AI reasoning tasks**

- visual/document classification where needed
- messy document field extraction
- policy-language interpretation

**Deterministic / analytical tasks**

- OCR normalization
- member lookup
- identity matching
- expiry checks
- ICD/CPT/NPI validation
- claim arithmetic
- policy retrieval
- duplicate detection
- fraud feature calculation
- final decision rules

## 2.3 Human review is an exception path

Normal cases should be processed automatically when the evidence is sufficient and the risk is acceptable.

Human review is triggered by meaningful exceptions such as:

- unresolved uncertainty
- conflicting evidence
- missing required documents
- security/tamper concerns
- high fraud risk
- policy applicability ambiguity
- system/agent failure

Minor imperfections must not automatically force human intervention.

## 2.4 Uncertainty must not become a false negative

The system must distinguish:

```text
KNOWN POSITIVE
KNOWN NEGATIVE
INDETERMINATE
CONFLICTING
UNAVAILABLE
```

An inability to determine a fact is not equivalent to a negative finding.

## 2.5 Decision authority is separate from reasoning

LLMs produce structured evidence and recommendations. Business decisions are applied through versioned deterministic policy rules.

The production model is:

```text
LLM / AI
  = extraction + interpretation + reasoning

Services / Code
  = validation + data access + calculations

Decision Engine
  = business policy

Human Reviewer
  = exception resolution / authorized override
```

---

# 3. Reviewer Feedback Disposition

| Review Topic | V2 Decision | Rationale |
|---|---|---|
| Policy amendment authority | **Accepted** | Customer amendment requests must not modify authoritative policy knowledge. |
| Multi-document case mismatch | **Accepted** | Case-level state must support 1:N documents and per-document results. |
| SPLIT / grouping step | **Accepted** | Required for multi-page and bundled submissions. |
| REQUEST_INFO outcome | **Accepted** | Missing-document handling must be a real workflow outcome. |
| Policy dependency | **Accepted with refinement** | Policy context can run in parallel; final coverage evaluation depends on claim information. |
| PASS/FAIL/INDETERMINATE | **Accepted as principle** | Exact vocabulary will vary by domain, but uncertainty must be explicit. |
| Remove autonomous reject | **Accepted for production mode, assignment behavior retained in compatibility mode** | Production workflow should distinguish recommendation from authorized final denial. |
| Decision priority order | **Accepted** | Decision rules need explicit first-match precedence. |
| AI vs human decision history | **Accepted** | Required for auditability and measuring overrides. |
| Human wait inside worker | **Accepted** | Automated run ends at escalation; human action is a separate event/resume path. |
| Large OCR payload in graph state | **Accepted** | Graph state will contain references rather than raw PHI-heavy payloads. |
| Full database schema | **Accepted** | Implementation requires concrete tables/columns, not only entity names. |
| API auth/error/pagination/idempotency | **Accepted** | Required for production API behavior. |
| KYC fuzzy matching / policy-active-date check | **Accepted** | Required for realistic identity and eligibility evaluation. |
| ELA/tamper detection | **Accepted as core security capability** | Visual tamper detection is not delegated solely to a vision LLM. |
| Claim service-line model | **Accepted** | Real claims can contain multiple service lines. |
| Policy hybrid retrieval | **Accepted** | Combine lexical and semantic retrieval. |
| Policy effective-date/versioning | **Accepted** | Correct policy version is mandatory for a decision. |
| Policy rider precedence | **Accepted** | Policy hierarchy must be explicit. |
| Fraud feature definitions | **Accepted** | Score must be explainable and reproducible. |
| HIPAA-oriented controls | **Accepted** | Security architecture must address healthcare-data handling and vendor controls. |
| Prompt-injection testing | **Accepted** | Document content is untrusted input and must never override system instructions. |
| 100+ evaluation cases | **Accepted as engineering target** | 20 remains the assignment minimum; broader evaluation is needed for meaningful metrics. |
| Early CI evaluation | **Accepted** | Golden tests should run early and continuously. |
| STP / latency / cost KPIs | **Accepted** | Automation effectiveness must be measured operationally, not just model accuracy. |

---

# 4. Revised End-to-End Architecture

```text
                           ┌────────────────────────────┐
                           │        Next.js UI           │
                           │ Dashboard / Case / Review  │
                           └─────────────┬──────────────┘
                                         │ HTTPS
                                         ▼
                           ┌────────────────────────────┐
                           │        FastAPI API          │
                           │ Auth / RBAC / Validation    │
                           │ Rate Limits / Idempotency   │
                           └─────────────┬──────────────┘
                                         │
                         ┌───────────────┼─────────────────┐
                         ▼                                 ▼
                ┌─────────────────┐              ┌─────────────────┐
                │ Object Storage  │              │   PostgreSQL    │
                │ S3 / MinIO      │              │ System of Record│
                └────────┬────────┘              └────────┬────────┘
                         │                                  │
                         └──────────────┬───────────────────┘
                                        ▼
                               ┌──────────────────┐
                               │ Redis / Job Queue │
                               │ Async Processing  │
                               └─────────┬────────┘
                                         ▼
                               ┌──────────────────┐
                               │    LangGraph     │
                               │ Workflow Engine  │
                               └─────────┬────────┘
                                         ▼
                                  PREPROCESS / OCR
                                         │
                                         ▼
                                     SPLIT / GROUP
                                         │
                                         ▼
                                     CLASSIFY
                                         │
                       ┌─────────────────┼─────────────────┐
                       ▼                 ▼                 ▼
                     KYC              CLAIMS         POLICY CONTEXT
                       │                 │                 │
                       │                 ▼                 │
                       │          Claim validation         │
                       │                 │                 │
                       └─────────────────┼─────────────────┘
                                         ▼
                                POLICY COVERAGE
                                RETRIEVAL + REASONING
                                         │
                                         ▼
                                   FRAUD ENGINE
                                   Rules / ML
                                         │
                                         ▼
                                FAITHFULNESS GATE
                                         │
                                         ▼
                                DECISION ENGINE
                                         │
                     ┌───────────────────┼─────────────────────┐
                     ▼                   ▼                     ▼
                  APPROVE         REQUEST_INFO             ESCALATE
                     │                   │                     │
                     │                   │                     ▼
                     │                   │               HUMAN REVIEW
                     │                   │                     │
                     │                   └───────┬─────────────┘
                     │                           ▼
                     └────────────────── FINAL CASE RESOLUTION

SEPARATE AUTHORIZED POLICY ADMINISTRATION PATH:

ADMIN / POLICY AUTHORITY
        ↓
Policy Admin API
        ↓
Policy Validation
        ↓
Version + Effective Dates
        ↓
Approval / Publication
        ↓
Policy RAG Index
```

---

# 5. Revised Agent / Service Model

The assignment requires a multi-agent pipeline. V2 preserves the agent boundaries while avoiding unnecessary LLM calls.

## 5.1 Classifier Agent

Purpose:

- classify document type
- assign routing tags
- identify UNKNOWN/ambiguous input

Implementation strategy:

```text
fast/cheap classifier or vision model
            ↓
confidence / quality gate
       ┌────┴─────┐
       ▼          ▼
confident     uncertain
   ↓              ↓
route       vision LLM fallback
```

The classifier remains an agent boundary, but production inference may use an adaptive model path.

## 5.2 OCR / Preprocessor

Prefer deterministic document processing first:

```text
PDF/image
 ↓
render / normalize
 ↓
deskew / denoise
 ↓
OCR
 ↓
layout + field normalization
```

The full OCR payload is stored outside LangGraph state.

## 5.3 KYC Agent

Primary work:

- member lookup
- policy/enrollment lookup
- DOB matching
- policy number validation
- document expiration check
- fuzzy identity matching
- policy-active-on-service-date validation
- document tamper analysis

LLM usage is optional and targeted to ambiguous visual/document interpretation. Database lookups and exact validations are deterministic.

## 5.4 Claims Agent

Primary LLM use case:

- extract structured claim information from complex/varied documents

Output:

```text
Claim
 ├── claimant / member reference
 ├── provider
 ├── service dates
 ├── diagnosis codes
 ├── service lines[]
 │     ├── CPT
 │     ├── quantity
 │     ├── amount
 │     └── supporting evidence
 └── total amount
```

Deterministic validation follows:

- schema validation
- ICD validation
- CPT validation
- NPI validation
- dates
- line-total reconciliation
- duplicate/consistency checks

## 5.5 Policy Agent

The Policy Agent is intentionally split into two logical stages.

### Stage A — Policy Context Resolution

Can run in parallel with KYC and Claims.

```text
member
 ↓
enrollment
 ↓
policy_id
 ↓
policy_version
 ↓
effective_date
```

### Stage B — Coverage Evaluation

Runs when validated claim information is available.

```text
CPT/ICD/service line
 ↓
code → plain-language description
 ↓
hybrid search
 ↓
reranking
 ↓
relevant policy clauses
 ↓
one evidence-grounded LLM reasoning pass
 ↓
coverage result
```

This preserves useful parallelism without hiding the dependency between claim codes and coverage evaluation.

## 5.6 Fraud Agent

Fraud detection should primarily be statistical/deterministic rather than generative.

Potential features:

- duplicate claim key
- image similarity hash
- member claim frequency
- provider claim frequency
- amount anomaly
- same-day/rapid-repeat patterns
- provider outlier behavior
- historical claim patterns

The fraud agent returns a score and evidence-backed anomaly list. The scoring model can evolve without changing the agent contract.

## 5.7 Orchestrator / Decision Engine

The orchestration controller is not an LLM decision-maker.

It performs:

- state coordination
- result consistency checks
- deterministic business rules
- final status transition

An LLM is not used to execute an IF/ELSE decision table.

---

# 6. LLM Call Optimization Without Compromising Faithfulness

## 6.1 Target architecture

The system should not implement "one LLM call per agent" by default.

Typical normal-case target:

```text
Classification             0–1 LLM call
Claims extraction            1 LLM call
Policy interpretation        0–1 LLM call
---------------------------------------
Typical total               1–3 LLM calls
```

The exact result will be measured using the evaluation harness.

## 6.2 What is intentionally not an LLM call

- OCR normalization
- member lookup
- exact identity-field comparison
- database validation
- ICD/CPT/NPI checks
- claim arithmetic
- policy retrieval
- effective-date filtering
- fraud feature calculation
- duplicate detection
- business decision rules

## 6.3 Adaptive escalation

Additional AI inference is triggered only when required.

```text
                First-pass result
                       ↓
                 evidence gate
                 /            \
             strong          uncertain
               ↓                 ↓
           continue       targeted second pass
                                  ↓
                             still uncertain?
                              /          \
                            no            yes
                            ↓              ↓
                        continue         human
```

This reduces cost and latency while preserving an explicit path for difficult cases.

## 6.4 No redundant re-reasoning

Do not build chains like:

```text
LLM extraction
 → LLM validation
 → LLM review
 → LLM summary
```

when deterministic validation can perform those checks more reliably.

## 6.5 Cache and retry selectively

- OCR result is cached.
- classification is cached.
- embeddings are cached.
- policy retrieval results are reusable where inputs/version are unchanged.
- failed nodes are retried independently.
- completed upstream nodes are not unnecessarily recomputed.

---

# 7. Faithfulness Architecture

## 7.1 Evidence-first output contract

Every material result should support provenance.

Example:

```json
{
  "field": "provider_npi",
  "value": "1234567890",
  "confidence": 0.96,
  "evidence": {
    "document_id": "DOC-123",
    "page": 2,
    "bbox": [120, 340, 280, 370],
    "text_span_id": "span-874"
  }
}
```

## 7.2 Policy grounding

Policy decisions must reference:

- applicable member enrollment
- correct policy version
- service date
- retrieved clause
- page/section metadata
- exclusions or conditions

The system should validate that every cited clause exists in the retrieved text.

## 7.3 Faithfulness Gate

Every agent result should be checked for:

```text
schema validity
source evidence
required fields
confidence
contradictions
policy applicability
```

Failure results in targeted retry or an explicit exception state.

The gate must never replace unsupported output with a guessed answer.

---

# 8. Revised Decision Model

The assignment defines APPROVE / REJECT / ESCALATE. V2 retains compatibility with the assignment while separating production recommendation from authorized final denial.

## 8.1 Assignment-compatible mode

```text
APPROVE
REJECT
ESCALATE
```

## 8.2 Production mode

```text
APPROVE
RECOMMEND_DENIAL
REQUEST_INFO
ESCALATE
NEEDS_HUMAN
```

`RECOMMEND_DENIAL` is not automatically equivalent to an authorized final denial where operating/legal policy requires human confirmation.

## 8.3 Decision precedence

First match wins.

| Priority | Condition | Outcome |
|---:|---|---|
| 1 | Agent/workflow failure or unrecoverable dependency failure | NEEDS_HUMAN |
| 2 | Required case documents missing | REQUEST_INFO |
| 3 | Indeterminate/conflicting material result | ESCALATE |
| 4 | Injection/tamper/security flag | ESCALATE |
| 5 | Fraud risk HIGH | ESCALATE |
| 6 | KYC failure / uncovered procedure / invalid claim after validation | RECOMMEND_DENIAL |
| 7 | KYC pass + valid claim + covered + acceptable fraud risk | APPROVE |
| 8 | Anything else | ESCALATE |

Every decision must have one or more versioned reason codes.

---

# 9. Case-Level State and Multi-Document Handling

## 9.1 Case vs document separation

A case can contain multiple documents.

```text
CASE
 ├── DOCUMENT
 │    ├── classification
 │    ├── OCR
 │    └── extracted evidence
 ├── DOCUMENT
 │    └── ...
 └── CASE-LEVEL RESULTS
      ├── KYC
      ├── Claims
      ├── Policy
      └── Fraud
```

## 9.2 LangGraph state principle

Graph state stores references rather than large raw payloads.

Example:

```python
ClaimState(
    case_id="C-1001",
    document_ids=["D1", "D2", "D3"],
    classification_ids=["CL1", "CL2", "CL3"],
    ocr_result_ids=["OCR1", "OCR2", "OCR3"],
    agent_run_ids=[],
    decision_id=None,
    trace_id="TRACE-123"
)
```

Full OCR/document data remains in the appropriate persistence layer.

## 9.3 Case completeness

Before final decision:

```text
Required documents for this case
        ↓
all present?
  ┌────┴────┐
  │         │
 YES       NO
  │         │
continue  REQUEST_INFO
```

The required-document set should be configurable by claim/case type rather than globally hardcoded.

---

# 10. Human Review Philosophy

Human review is not a default validation loop.

## Automatically complete when:

- required evidence exists
- material fields are validated
- policy is deterministically identified
- coverage is evidence-backed
- fraud risk is acceptable
- no critical contradictions exist
- security checks pass

## Request information when:

- required documents are missing
- a required field cannot be established from available evidence
- a source document is unreadable and another source is reasonably expected

## Escalate when:

- evidence conflicts
- material ambiguity remains after targeted verification
- fraud risk is high
- tamper/injection is detected
- policy applicability cannot be established
- the workflow/dependency fails

The objective is to maximize **straight-through processing (STP)** while keeping high-risk cases under controlled review.

---

# 11. Revised Database Model

Minimum production-oriented entities:

```text
users
roles
user_roles

cases
documents
document_pages
document_hashes

members
enrollments
policies
policy_versions
policy_riders
policy_chunks

claims
claim_lines
providers

agent_runs
agent_results
agent_errors

kyc_results
claims_results
policy_results
fraud_results

fraud_features
fraud_signals

decisions
human_reviews
review_locks

reason_codes
audit_events
idempotency_keys

model_versions
prompt_versions
evaluation_runs
```

Key design choices:

- `cases` is the business aggregate.
- `documents` is 1:N from cases.
- `decisions` is 1:N so AI and human decisions can coexist historically.
- AI and human decisions are explicitly distinct.
- audit events are append-only at the application/database privilege level.
- model/prompt/rule versions are persisted for reproducibility.

---

# 12. Revised API Requirements

All production APIs should support:

- authentication
- authorization
- validation
- pagination where list endpoints are used
- standard error envelope
- request correlation ID
- idempotency for side-effecting operations
- optimistic concurrency on review/override actions

Example override contract:

```json
{
  "new_decision": "APPROVE",
  "reason_code": "HUMAN_REVIEW_CONFIRMED",
  "reason": "Supporting hospital documentation reviewed.",
  "expected_version": 7
}
```

The service rejects stale updates when the case version has changed.

Operational endpoints:

```text
GET /health
GET /ready
```

Audit export and real-time event delivery should be represented explicitly in the API design.

---

# 13. Security and Healthcare-Data Controls

The platform processes highly sensitive insurance/health-related documents. V2 therefore treats healthcare-data security as a first-class architecture concern.

The design must explicitly address:

- TLS in transit
- encryption at rest
- private object-storage access
- short-lived signed document URLs
- SSO/MFA-ready authentication
- RBAC
- least privilege
- secret management
- upload file-size/page limits
- MIME/content validation
- malware scanning
- rate limiting
- retention/deletion controls
- audit logging
- vendor data-processing controls
- applicable healthcare/privacy obligations
- LLM vendor BAA/contractual requirements where applicable
- vendor zero-retention/data-use configuration where available

The architecture should be described as **HIPAA-oriented / healthcare-data security compliant by design**, while actual production compliance requires organizational, legal, privacy, and security validation.

---

# 14. Prompt Injection and Untrusted Document Content

All document-derived text is untrusted content.

A document containing instruction-like text must never override system or agent instructions.

Controls:

```text
System instructions
      +
Structured task
      +
Untrusted document content
```

must remain logically separated.

Agents return typed fields only.

The decision engine never reads raw document text to make business decisions.

Instruction-like document content may itself become a security flag.

The evaluation dataset must contain prompt-injection cases.

---

# 15. Revised Policy RAG Architecture

```text
             AUTHORIZED POLICY ADMIN
                       │
                       ▼
                Policy Validation
                       │
                       ▼
             Version + Effective Date
                       │
                       ▼
                Docling / Parsing
                       │
                       ▼
                   Chunking
                       │
                       ▼
                Embedding Index
                       │
                       ▼
                 Qdrant / Chroma

QUERY PATH

Member
  ↓
Enrollment
  ↓
Applicable Policy Version
  ↓
Service Date
  ↓
CPT/ICD/service-line description
  ↓
Hybrid lexical + semantic retrieval
  ↓
Reranking
  ↓
Relevant clauses
  ↓
Evidence-grounded LLM interpretation
  ↓
Clause verification
  ↓
Coverage Result
```

Policy result should contain:

```json
{
  "coverage_status": "COVERED",
  "coverage_percentage": 80,
  "policy_id": "POL-001",
  "policy_version": "v3",
  "effective_on_service_date": true,
  "evidence": [
    {
      "page": 17,
      "section": "4.2",
      "text_span_id": "SPAN-112"
    }
  ],
  "exclusions": [],
  "prior_authorization_required": false
}
```

`INDETERMINATE` is returned when the applicable policy version or adequate evidence cannot be established.

---

# 16. Revised Fraud Architecture

Fraud analysis is primarily a numerical/analytical workload.

```text
Member history
Provider history
Claim history
Current claim
Document similarity
      ↓
Feature engineering
      ↓
Rules / anomaly model
      ↓
fraud_score
      ↓
risk_level
      ↓
evidence/signals
```

Example features:

```text
same_claim_key
image_similarity_score
member_claim_count_30d
provider_claim_count_30d
amount_z_score
same_day_service_count
rapid_repeat_count
provider_outlier_score
historical_pattern_score
```

The score range, feature weights, thresholds, and calibration must be explicitly defined and evaluated rather than using arbitrary constants.

---

# 17. Revised Decision Confidence

Final decision confidence must not be a simple average of agent confidence values.

Confidence should consider:

```text
field extraction quality
+ validation results
+ evidence completeness
+ policy evidence quality
+ consistency between sources
+ fraud evidence quality
+ unresolved ambiguity
```

The system should separately record:

1. **Model confidence** — how confident the model was.
2. **Evidence confidence/quality** — how strong and verifiable the source evidence is.
3. **Decision confidence** — how strongly the validated evidence supports the resulting business outcome.

---

# 18. AI Decision vs Human Decision

A case may have multiple decision records.

Example:

```text
AI Decision
RECOMMEND_DENIAL
       ↓
Human Review
       ↓
Human Decision
APPROVE
       ↓
Current Case Decision
APPROVE
```

Store:

- decision ID
- case ID
- source type (`AI` / `HUMAN`)
- outcome
- reason codes
- evidence references
- actor
- model/rule version where applicable
- timestamp
- case version
- superseded/current status

This enables measurement of human override rate and AI performance without losing history.

---

# 19. Evaluation Strategy V2

The assignment minimum of 20 synthetic document images remains the minimum demonstration requirement. V2 adds a larger engineering evaluation set.

Recommended target:

```text
20+     assignment minimum
100+    engineering baseline
```

Suggested distribution:

```text
20 basic valid
20 invalid / malformed
20 fraud/anomaly
20 multi-document / multi-page
10 low-quality images
10 adversarial/security
10 edge cases
```

Evaluation dimensions:

- classification accuracy
- extraction completeness
- field-level exact/normalized accuracy
- policy retrieval Recall@K
- policy evidence grounding
- decision correctness
- false-approve rate
- escalation rate
- STP rate
- human override rate
- p95 processing time
- cost per case
- unsupported assertion rate
- provenance completeness

Ground truth should be represented as machine-readable JSON.

Example:

```json
{
  "document_id": "DOC-001",
  "expected_doc_type": "CLAIM_FORM",
  "expected_fields": {
    "member_id": "M123",
    "provider_npi": "1234567890",
    "service_date": "2026-09-21"
  },
  "expected_policy_clause_ids": ["POL1-P17-S4.2"],
  "expected_decision": "APPROVE"
}
```

Evaluation should run in CI on representative regression cases.

---

# 20. New Operational KPIs

Accuracy alone is insufficient to determine whether the automation is valuable.

## Straight-Through Processing Rate

```text
STP Rate =
Automatically completed eligible cases
---------------------------------------
Total eligible cases
```

## Escalation Rate

```text
Escalation Rate =
Human-review cases
-----------------
Total cases
```

## False-Approve Rate

```text
False-Approve Rate =
Incorrect automatic approvals
-----------------------------
Total automatic approvals
```

## Human Override Rate

```text
Human Override Rate =
Human decisions different from AI recommendation
--------------------------------------------------
Human-reviewed cases
```

## Other production targets

- p95 case-processing latency
- average LLM calls per case
- LLM tokens per case
- cost per 1,000 cases
- agent error rate
- retry rate
- Policy RAG Recall@K
- evidence/provenance completeness

Targets should be finalized after baseline evaluation rather than invented without measurement.

---

# 21. Failure and Degraded-Mode Strategy

Every external dependency must have defined failure behavior.

```text
Primary model fails
       ↓
Fallback model / controlled retry
       ↓
Still unavailable
       ↓
Exception path
```

```text
Policy vector DB unavailable
       ↓
No reliable policy evidence
       ↓
INDETERMINATE
       ↓
ESCALATE
```

The system must never silently convert infrastructure failure into a business negative.

Similarly, failed nodes are retried independently rather than replaying the entire graph unnecessarily.

---

# 22. LangGraph V2 Workflow

```text
START
  ↓
RECEIVED
  ↓
PREPROCESS
  ↓
SPLIT / GROUP
  ↓
CLASSIFY_DOCUMENTS
  ↓
CASE_COMPLETENESS
  ├── missing required docs → REQUEST_INFO / END
  └── complete
        ↓
      ┌───────────────────────────────┐
      │ KYC                            │
      │ CLAIMS                          │   parallel where independent
      │ POLICY CONTEXT                 │
      └───────────────┬───────────────┘
                      ↓
             CLAIM / CONTEXT READY
                      ↓
             POLICY COVERAGE
                      ↓
                FRAUD ANALYSIS
                      ↓
              FAITHFULNESS GATE
                      │
               ┌──────┴───────┐
               ▼              ▼
           valid evidence   unresolved
               │              │
               ▼              ▼
        DECISION ENGINE    ESCALATE
               │
      ┌────────┼──────────┐
      ▼        ▼          ▼
   APPROVE  RECOMMEND   REQUEST_INFO
              DENIAL
```

Human review is a separate event-driven continuation rather than a worker blocked inside the graph.

---

# 23. Repository / Implementation Alignment

The implementation should mirror these boundaries:

```text
backend/
  agents/
  workflow/
  decision_engine/
  rag/
  services/
  repositories/
  security/
  schemas/
  api/
  workers/

frontend/

database/
migrations/

prompts/
  classifier/
  claims/
  policy/

policies/
sample_data/
evaluation/
tests/
docs/
```

Each technical component must map to a documented interface and at least one automated test family.

---

# 24. Traceability Model

Every requirement should map to:

```text
Requirement
    ↓
Component
    ↓
Implementation artifact
    ↓
Test case
    ↓
Evaluation metric
```

Example:

```text
FR-Claims-Extraction
      ↓
Claims Agent
      ↓
claims/schema.py
      ↓
test_claim_extraction.py
      ↓
Extraction Completeness
```

This traceability matrix should be included in the final documentation set.

---

# 25. MVP vs Production Scope

## MVP required for assignment demonstration

- document ingestion
- OCR/preprocessing
- classification
- KYC
- claims extraction
- policy RAG
- fraud analysis
- orchestration
- case UI
- human review
- 20+ synthetic documents
- architecture documentation

## Production-hardening layer

- full RBAC/SSO/MFA integration
- advanced fraud model
- expanded synthetic/real-world validation dataset
- enterprise malware scanning
- immutable audit controls
- production secret management
- vendor/privacy agreements
- HA deployment
- distributed tracing
- capacity/load testing
- state-specific regulatory/business configuration
- formal model governance

The MVP can demonstrate the architecture without pretending that every production dependency has already been enterprise-certified.

---

# 26. Final Architecture Position

The V2 design intentionally avoids two extremes.

### Extreme A — LLM everywhere

```text
7 agents
= 7+ LLM calls
= unnecessary cost/latency
= duplicated reasoning
= higher error propagation risk
```

### Extreme B — Over-escalation

```text
Any uncertainty
→ Human

Result:
AI becomes an expensive pre-review tool rather than an automation platform.
```

### V2 target

```text
Strong evidence + deterministic validation
                ↓
Selective LLM reasoning
                ↓
Faithfulness gate
                ↓
Risk-aware decision engine
                ↓
Straight-through automation
                ↓
Human exception handling
```

The design target is therefore:

> **High-faithfulness automation with the minimum sufficient number of AI inference calls.**

---

# 27. Reviewer Summary

The reviewer feedback is accepted as a valuable correction to the initial architecture. The V2 design specifically resolves the major issues by:

1. separating customer policy-amendment submissions from authoritative policy administration
2. making the case/document relationship explicit
3. adding document split/grouping and case completeness
4. introducing REQUEST_INFO
5. retaining parallel Policy context work while respecting claim-to-coverage dependency
6. making uncertainty explicit rather than encoding it as false
7. separating production denial recommendation from authorized final denial where required
8. defining deterministic decision precedence
9. separating AI and human decisions in persistence
10. removing long-lived human waits from workers
11. keeping large document/OCR payloads outside LangGraph state
12. expanding database/API/security requirements
13. using service-line claim structures
14. strengthening policy version/effective-date/RAG grounding
15. making fraud scoring explainable and measurable
16. adding healthcare-data security and prompt-injection controls
17. expanding evaluation and moving regression testing earlier
18. measuring STP, false approvals, cost and latency in addition to accuracy
19. reducing redundant LLM calls without reducing evidence, validation or faithfulness

This V2 should be treated as the canonical baseline for subsequent BRD, TTD, solution architecture, agent contracts, database design, API specifications, implementation and testing documentation.

---

## 28. Reference Basis

This change note is based on:

- the original MediShield Assignment 02 — Multi-Agent Document Intake System
- the reviewer feedback on the first 14-document package
- subsequent architecture discussions concerning Policy parallelism, human escalation, LLM cost/latency, and faithfulness

The assignment-defined baseline remains important for compatibility, including the multi-agent pipeline, LangGraph workflow, Policy RAG, case-management UI, sample dataset, and evaluation criteria.
