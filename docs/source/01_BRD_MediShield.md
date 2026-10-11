# Business Requirements Document (BRD)
## MediShield Insurance — Multi-Agent Document Intake System

**Document Type:** Business Requirements Document  
**Status:** Proposed Production Baseline  
**Source:** Assignment 02 — Multi-Agent Document Intake System

---

# 1. Executive Summary

MediShield Health Insurance Ltd. operates a high-volume document-processing workflow covering claim forms, discharge summaries, prescriptions, identity documents and policy amendments.

The supplied business context states that MediShield manages more than 2 million active policyholders across 14 states and receives approximately 85,000 document submissions per month through email, mobile uploads and scanning stations at partner hospitals.

The current operation depends on 120 document reviewers and averages 4.2 days per claim. The assignment states an annual processing overhead of $18.4M, a 12% re-processing rate caused by classification errors, delayed reimbursements, and three ongoing regulatory audits associated with KYC non-compliance.

The target platform introduces an AI-powered multi-agent intake system capable of:
- classifying submitted documents;
- extracting structured information;
- validating identity and claims information;
- retrieving relevant policy clauses;
- identifying suspicious patterns;
- producing an Approve / Reject / Escalate outcome;
- routing uncertainty and high-risk cases to human reviewers;
- presenting full case evidence through an operations UI.

The target experience is processing within minutes of document receipt, subject to model, OCR, retrieval and infrastructure performance validation.

# 2. Business Problem

The current process has four principal business challenges:

## 2.1 Manual classification and routing
Reviewers manually identify document type and decide which processing path applies.

## 2.2 Manual extraction and validation
Required claim, member, provider and policy information must be manually read, entered and checked.

## 2.3 Cross-system verification
KYC, policy and fraud checks require data from multiple sources and create additional operational effort.

## 2.4 Inconsistent handling and auditability
Manual review can produce different processing patterns and increases the difficulty of reconstructing exactly how a case was handled.

# 3. Business Objectives

| Objective | Business Need |
|---|---|
| Automate document classification | Reduce repetitive sorting and routing |
| Automate structured extraction | Reduce manual keying and omissions |
| Automate policy retrieval | Accelerate coverage validation |
| Automate anomaly checks | Surface suspicious claims earlier |
| Reduce processing time | Move from multi-day processing toward minutes |
| Improve traceability | Support operations and audit investigations |
| Preserve human control | Route uncertain/high-risk cases to reviewers |
| Enable measurable evaluation | Use a labelled synthetic dataset |

# 4. Stakeholders

| Stakeholder | Primary Need |
|---|---|
| VP Operations | Throughput, processing time and operational visibility |
| Operations Manager | Queue monitoring, exceptions and team workload |
| Operations Reviewer | Review evidence and resolve escalated cases |
| Compliance / Audit | Traceable decisions and KYC evidence |
| Internal AI Team | Agent quality, model behavior and evaluation |
| Backend Engineering | APIs, data integrity and integrations |
| Platform / DevOps | Reliability, deployment and scaling |
| QA | Functional and evaluation validation |

# 5. User Roles

### Operations Reviewer
Can view assigned/authorized cases, inspect documents and agent outputs, and perform an authorized human decision or override.

### Operations Manager
Can view broader operational dashboards, queues, processing metrics and reviewer activity.

### Administrator
Manages operational configuration and authorized access.

Exact permissions are to be finalized during implementation.

# 6. Scope

## 6.1 In Scope

- Multipart intake for JPEG, PNG, PDF and TIFF.
- Unique case ID creation.
- Raw document storage in S3 or MinIO.
- Queue submission through Redis or Celery.
- Vision-based classification.
- OCR / preprocessing.
- KYC validation.
- Claims extraction and schema validation.
- Policy PDF ingestion through Docling.
- Policy vector retrieval using ChromaDB or Qdrant.
- Fraud/anomaly scoring.
- Orchestrated decisioning using LangGraph.
- Approve / Reject / Escalate outcomes.
- Next.js operations dashboard.
- Case detail and agent evidence views.
- Human review queue and override.
- Timestamped audit history.
- Minimum 20 synthetic documents for evaluation.
- Architecture diagram.
- Setup/readme and end-to-end walkthrough.

## 6.2 Explicitly Not Specified by the Source

The source does not define:
- the enterprise identity provider;
- exact production cloud vendor;
- exact latency SLO;
- exact backup RPO/RTO;
- exact retention period;
- exact service-level availability;
- exact human-review SLA;
- exact compliance framework implementation;
- exact model/provider selected for production.

These remain implementation decisions.

# 7. End-to-End Business Flow

```text
Submission
   ↓
Case Created
   ↓
Original Document Stored
   ↓
Queued
   ↓
Document Classified
   ↓
OCR / Preprocessing when applicable
   ↓
Specialist Processing
   ├── KYC
   ├── Claims
   └── Policy
   ↓
Fraud Check
   ↓
Evidence Aggregation
   ↓
Decision
   ├── APPROVE
   ├── REJECT
   └── ESCALATE
           ↓
      Human Review
```

Source state machine:

```text
RECEIVED → CLASSIFIED → [PARALLEL: KYC + CLAIMS + POLICY]
→ FRAUD_CHECK → AGGREGATED → DECIDED
```

# 8. Functional Requirements

## FR-001 — Intake
The system shall accept multipart uploads for JPEG, PNG, PDF and TIFF.

## FR-002 — Case Creation
The system shall assign a unique `case_id` to each submission.

## FR-003 — Original Document Persistence
The system shall store the raw document in object storage.

## FR-004 — Queue Submission
The system shall publish processing work to Redis or Celery.

## FR-005 — Classification
The Classifier Agent shall return `doc_type`, `confidence` and `routing_tags`.

Supported document types:
- CLAIM_FORM
- ID_DOCUMENT
- DISCHARGE_SUMMARY
- PRESCRIPTION
- POLICY_AMENDMENT
- UNKNOWN

## FR-006 — Low-Confidence Routing
Unknown or low-confidence classification shall be routed to human review.

## FR-007 — OCR / Preprocessing
Image-based inputs shall produce normalized fields, raw text and bounding boxes.

## FR-008 — KYC
The KYC Agent shall:
- validate member ID;
- validate DOB;
- validate policy number;
- check document expiry;
- identify visual anomalies indicating possible tampering.

## FR-009 — Claims Extraction
The Claims Agent shall extract:
- claim amount;
- ICD-10 diagnosis codes;
- CPT procedure codes;
- provider NPI;
- service date.

## FR-010 — Claims Schema Validation
The Claims Agent shall validate extracted data against MediShield's claim submission standard.

## FR-011 — Policy RAG
The Policy Agent shall ingest policy PDFs through Docling, chunk and vectorize them, and retrieve relevant clauses based on procedure codes.

## FR-012 — Policy Result
The Policy Agent shall return:
- covered;
- coverage_percentage;
- policy_clause;
- exclusions.

## FR-013 — Fraud Analysis
The Fraud Agent shall inspect patient history and score:
- duplicate submissions;
- frequency anomalies;
- provider billing patterns.

## FR-014 — Fraud Result
The Fraud Agent shall return:
- fraud_score;
- anomalies;
- risk_level.

## FR-015 — Decisioning
The Orchestrator shall aggregate upstream results and produce decision, confidence, justification and agent summaries.

## FR-016 — Case UI
The case UI shall contain:
- dashboard;
- case detail;
- document viewer;
- agent result panels;
- decision panel;
- human-review queue;
- audit log.

## FR-017 — Human Override
The human-review queue shall support an authorized override operation.

## FR-018 — Audit
Agent decisions and case activity shall be timestamped and traceable.

# 9. Business Rules

### BR-001 Classification Routing
Unknown or low-confidence documents are routed to human review.

### BR-002 Approval
Approval requires:

```text
KYC passed
AND claim valid
AND covered
AND fraud score < 0.3
```

### BR-003 Rejection
Reject when:

```text
KYC failed
OR procedure not covered
OR schema invalid
```

### BR-004 Escalation
Escalate when:

```text
fraud score >= 0.3
OR any agent confidence < 0.6
```

### BR-005 Orchestration Gate
The orchestrator executes only after required upstream outputs are available.

# 10. Recommended Production Rules

These are architecture recommendations, not explicit source requirements:

- Preserve an immutable original document.
- Assign a processing execution ID to each attempt.
- Persist workflow checkpoints.
- Add idempotency protection.
- Bound retries to transient errors.
- Record model, prompt, workflow and policy versions.
- Require a reason when a reviewer overrides the automated outcome.
- Prevent UI clients from accessing object-storage credentials directly.
- Ensure every case/document request passes server-side authorization.

# 11. Non-Functional Requirements

## Security
- TLS for application traffic.
- Authentication and role-based authorization.
- Secrets outside source control.
- Private object storage.
- Minimum required access between services.
- Audit logging.
- Sensitive data minimization.

## Reliability
- Queue-backed asynchronous execution.
- Recoverable worker failures.
- Durable case state.
- Retry only transient faults.

## Scalability
- Support multiple workers.
- Keep document storage independent from worker instances.
- Avoid processing inside synchronous API request threads.

## Maintainability
- Separate agent modules.
- Typed contracts.
- Unit and integration tests.
- Configuration externalization.

## Observability
- case status;
- job state;
- queue depth;
- agent latency;
- model errors;
- retry counts;
- human overrides.

# 12. UI Requirements

## Dashboard
Display:
- case ID;
- document type;
- processing status;
- decision status;
- confidence;
- created/updated time.

## Case Detail
Display:
- document;
- classification;
- OCR;
- KYC;
- Claims;
- Policy;
- Fraud;
- final decision;
- justification;
- audit trail.

## Human Review
Display:
- case evidence;
- escalation reason;
- agent outputs;
- authorized decision/override controls.

# 13. Acceptance Criteria

A release meets the assignment baseline when:

1. End-to-end workflow executes.
2. All seven named processing nodes/components are connected.
3. Required file types can be submitted.
4. Unique case IDs are generated.
5. Documents are persisted.
6. Jobs are queued.
7. Policy RAG works with at least two sample policy documents.
8. UI dashboard, detail and human review flows work end-to-end.
9. At least 20 synthetic document images are available with ground truth.
10. Final decisions are persisted and auditable.
11. No hardcoded credentials exist.
12. Weighted score is at least 70%.
13. Decision Correctness is at least 60%.

# 14. Evaluation Requirements

| Criterion | Weight |
|---|---:|
| Classification Accuracy | 20% |
| Extraction Completeness | 20% |
| Policy Retrieval Quality | 15% |
| Decision Correctness | 25% |
| UI Functionality | 10% |
| Code Quality & Structure | 10% |

Minimum:
- overall ≥ 70%;
- Decision Correctness ≥ 60%.

# 15. Success Measures

Primary business measures to track during pilot:
- average processing time;
- automated-processing percentage;
- escalation rate;
- re-processing rate;
- manual-review workload;
- decision correctness;
- classification accuracy;
- extraction completeness;
- policy retrieval quality.

The source provides no target values for these operational measures beyond the evaluation thresholds, so production targets should be baselined during pilot testing.
