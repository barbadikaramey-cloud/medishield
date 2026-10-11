# Technical Design Document (TTD)
## MediShield Insurance — Multi-Agent Document Intake System

**Document Type:** Technical Design Document  
**Status:** Proposed Production Baseline

---

# 1. Technical Goals

The implementation must provide:
- robust document intake;
- asynchronous processing;
- modular agents;
- structured outputs;
- durable state;
- policy RAG;
- deterministic decisioning;
- human-in-the-loop;
- auditable case history;
- future horizontal scale.

# 2. Logical Services

```text
Next.js UI
   |
   v
FastAPI
   |
   +---- PostgreSQL
   +---- Object Storage
   +---- Queue
             |
             v
          Workers
             |
             v
          LangGraph
     /       |       \
Classifier   OCR    Specialists
                    /  |  \
                   KYC Claims Policy
                         |
                       Fraud
                         |
                    Aggregator
                         |
                    Decision Engine
```

# 3. API Design

## POST /api/v1/cases

Multipart upload.

Request:
- `document`
- optional case metadata.

Response:
```json
{
  "case_id": "CASE-2026-000001",
  "status": "RECEIVED"
}
```

## GET /api/v1/cases

Query:
- status;
- document type;
- decision;
- page;
- page_size.

## GET /api/v1/cases/{case_id}

Returns case summary and status.

## GET /api/v1/cases/{case_id}/document

Authorized document access.

## GET /api/v1/cases/{case_id}/agents

Returns agent execution summaries.

## GET /api/v1/cases/{case_id}/audit

Returns chronological audit events.

## POST /api/v1/cases/{case_id}/process

Triggers/restarts processing.

## POST /api/v1/cases/{case_id}/review

Human review/override.

Example:
```json
{
  "action": "OVERRIDE",
  "decision": "APPROVE",
  "reason_code": "SOURCE_DOCUMENT_VERIFIED",
  "remarks": "Reviewer verified supporting evidence."
}
```

## GET /api/v1/health

Service health.

# 4. API Design Rules

- Version all public APIs.
- Validate request/response schemas.
- Reject unsupported MIME types.
- Use pagination.
- Avoid sensitive information in errors.
- Require authorization on case/document endpoints.
- Use an idempotency header for repeatable commands where appropriate.

# 5. Domain Model

## Case

```text
Case
 ├── case_id
 ├── status
 ├── workflow_version
 ├── created_at
 ├── updated_at
 └── final_decision
```

## Document

```text
Document
 ├── document_id
 ├── case_id
 ├── object_key
 ├── mime_type
 ├── checksum
 ├── size_bytes
 └── created_at
```

## Agent Run

```text
AgentRun
 ├── execution_id
 ├── case_id
 ├── agent_name
 ├── status
 ├── confidence
 ├── started_at
 ├── completed_at
 ├── output_json
 └── error_code
```

# 6. Recommended Database Schema

## cases

| Column | Type |
|---|---|
| id | UUID |
| case_id | VARCHAR |
| status | VARCHAR |
| workflow_version | VARCHAR |
| final_decision | VARCHAR |
| final_confidence | DECIMAL |
| created_at | TIMESTAMP |
| updated_at | TIMESTAMP |

## documents

| Column | Type |
|---|---|
| id | UUID |
| case_id | UUID |
| object_key | TEXT |
| mime_type | VARCHAR |
| checksum | VARCHAR |
| size_bytes | BIGINT |
| created_at | TIMESTAMP |

## agent_runs

| Column | Type |
|---|---|
| id | UUID |
| case_id | UUID |
| execution_id | UUID |
| agent_name | VARCHAR |
| status | VARCHAR |
| confidence | DECIMAL |
| output_json | JSONB |
| error_code | VARCHAR |
| started_at | TIMESTAMP |
| completed_at | TIMESTAMP |

## policy_documents

| Column | Type |
|---|---|
| id | UUID |
| policy_version | VARCHAR |
| object_key | TEXT |
| status | VARCHAR |
| created_at | TIMESTAMP |

## policy_chunks

| Column | Type |
|---|---|
| id | UUID |
| policy_document_id | UUID |
| chunk_text | TEXT |
| metadata | JSONB |
| vector_reference | TEXT |

## decisions

| Column | Type |
|---|---|
| id | UUID |
| case_id | UUID |
| decision | VARCHAR |
| confidence | DECIMAL |
| justification | TEXT |
| ruleset_version | VARCHAR |
| created_at | TIMESTAMP |

## human_reviews

| Column | Type |
|---|---|
| id | UUID |
| case_id | UUID |
| reviewer_id | VARCHAR |
| action | VARCHAR |
| decision | VARCHAR |
| reason_code | VARCHAR |
| remarks | TEXT |
| created_at | TIMESTAMP |

## audit_events

| Column | Type |
|---|---|
| id | UUID |
| case_id | UUID |
| execution_id | UUID |
| event_type | VARCHAR |
| actor_type | VARCHAR |
| actor_id | VARCHAR |
| event_payload | JSONB |
| created_at | TIMESTAMP |

# 7. LangGraph State

```python
class CaseState(TypedDict, total=False):
    case_id: str
    document_id: str
    workflow_version: str

    doc_type: str
    classification_confidence: float
    routing_tags: list[str]

    ocr_text: str
    fields: dict
    bounding_boxes: list

    kyc_result: dict
    claims_result: dict
    policy_result: dict
    fraud_result: dict

    decision: str
    decision_confidence: float
    justification: str
    agent_summaries: dict

    completed_nodes: list[str]
    errors: list[dict]
```

# 8. Workflow Graph

```text
START
  ↓
CLASSIFIER
  ├── UNKNOWN / LOW CONFIDENCE → HUMAN_REVIEW
  │
  └── ROUTABLE
       ↓
      OCR / PREPROCESS
       ↓
   ┌───┼───────────┐
   ▼   ▼           ▼
  KYC Claims      Policy
   └───┼───────────┘
       ▼
     FRAUD
       ↓
  AGGREGATION
       ↓
 DECISION ENGINE
   ├── APPROVE
   ├── REJECT
   └── ESCALATE → HUMAN_REVIEW
```

# 9. Agent Contracts

## Classifier

```json
{
  "doc_type": "CLAIM_FORM",
  "confidence": 0.94,
  "routing_tags": ["claims"]
}
```

## OCR

```json
{
  "fields": {},
  "raw_text": "...",
  "bounding_boxes": []
}
```

## KYC

```json
{
  "kyc_passed": true,
  "flags": [],
  "confidence": 0.91
}
```

## Claims

```json
{
  "extracted_fields": {
    "claim_amount": 12500,
    "icd10_codes": [],
    "cpt_codes": [],
    "provider_npi": "...",
    "service_date": "..."
  },
  "schema_valid": true,
  "validation_errors": []
}
```

## Policy

```json
{
  "covered": true,
  "coverage_percentage": 80,
  "policy_clause": "...",
  "exclusions": []
}
```

## Fraud

```json
{
  "fraud_score": 0.18,
  "anomalies": [],
  "risk_level": "LOW"
}
```

## Orchestrator

```json
{
  "decision": "APPROVE",
  "confidence": 0.89,
  "justification": "...",
  "agent_summaries": {}
}
```

# 10. Policy RAG Technical Flow

```text
Policy PDF
 ↓
Docling parsing
 ↓
Chunking
 ↓
Embedding
 ↓
ChromaDB / Qdrant
 ↓
Retriever
 ↓
Procedure-code query
 ↓
Relevant clauses
 ↓
Policy agent
```

Recommended metadata:
- policy version;
- source document ID;
- page number if available;
- chunk ID;
- effective date if present in source;
- ingestion timestamp.

# 11. Decision Engine

The business rules should be represented as code/configuration rather than hidden solely in a prompt.

Recommended structure:

```python
if not kyc_passed:
    return "REJECT"

if not schema_valid:
    return "REJECT"

if not covered:
    return "REJECT"

if fraud_score >= 0.3:
    return "ESCALATE"

if min_agent_confidence < 0.6:
    return "ESCALATE"

return "APPROVE"
```

The implementation must test precedence explicitly because the assignment presents separate reject and escalate conditions.

# 12. Idempotency

Use a stable processing key:

```text
case_id
+ document checksum
+ workflow version
+ processing command
```

Persist the key before execution and reject/return an existing successful result when the same logical operation is submitted again.

# 13. Retry Policy

Classify errors:

```text
TRANSIENT
PERMANENT
BUSINESS
MODEL
SECURITY
```

Only TRANSIENT and explicitly safe MODEL errors should automatically retry.

# 14. Queue Design

The assignment permits Redis or Celery.

Recommended logical queue types:

```text
document_intake
agent_processing
policy_ingestion
retry
human_review_notification
```

A first implementation may use a single queue with task types. Scale-specific separation can be introduced later.

# 15. Storage Design

## Object Storage

```text
bucket/
 ├── original/
 │    └── case_id/document_id
 ├── ocr/
 ├── derived/
 └── reports/
```

Original documents must remain separately identifiable from derived artifacts.

## Database

Use PostgreSQL for structured transactional state and audit metadata.

# 16. Security Design

## Authentication
Use an organization-approved identity provider.

## Authorization
Enforce role and case permissions server-side.

## Secrets
No credentials in repository or source code.

## Documents
No direct public bucket access.

## Logs
Avoid raw document content and unnecessary identifiers.

# 17. Testing

## Unit
Test each agent independently.

## Contract
Validate each JSON response schema.

## Integration
Test:
- API → queue;
- queue → worker;
- worker → DB;
- worker → object storage;
- policy ingestion → vector store.

## End-to-End
Execute complete LangGraph flow.

## Evaluation
Use ≥20 synthetic labelled documents.

# 18. Metrics Implementation

For the assignment:

### Classification Accuracy

```text
correctly classified documents / total documents
```

### Extraction Completeness

Measure required Claims fields successfully extracted.

### Policy Retrieval Quality

Manual or scripted relevance assessment of retrieved clauses.

### Decision Correctness

```text
correct final decisions / evaluated cases
```

### UI Functionality

Validate dashboard, detail and human override.

### Code Quality

Review module separation, type safety, configuration and credentials.

# 19. Configuration

Externalize:
- model name;
- model API endpoint;
- queue URL;
- database URL;
- object-store credentials;
- vector-store URL;
- confidence thresholds;
- fraud threshold;
- retry configuration;
- upload limits.

# 20. Recommended Repository Structure

```text
medishield/
├── backend/
│   ├── api/
│   ├── domain/
│   ├── db/
│   ├── services/
│   ├── security/
│   └── main.py
├── workflow/
│   ├── graph.py
│   ├── state.py
│   ├── nodes/
│   └── routing.py
├── agents/
│   ├── classifier/
│   ├── ocr/
│   ├── kyc/
│   ├── claims/
│   ├── policy/
│   ├── fraud/
│   └── orchestrator/
├── workers/
│   ├── tasks.py
│   └── worker.py
├── rag/
├── ui/
├── tests/
├── dataset/
├── scripts/
└── README.md
```
