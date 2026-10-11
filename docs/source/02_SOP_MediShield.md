# Standard Operating Procedure (SOP)
## MediShield Insurance — Multi-Agent Document Intake and Claims Processing

**Document Type:** Standard Operating Procedure  
**Status:** Proposed Production SOP

---

# 1. Purpose

This SOP defines the operational method for receiving, processing, reviewing, deciding, monitoring and recovering document-intake cases.

# 2. Operating Model

```text
INTAKE
 ↓
CASE CREATION
 ↓
DOCUMENT STORAGE
 ↓
QUEUE
 ↓
CLASSIFICATION
 ↓
OCR / PREPROCESSING
 ↓
KYC + CLAIMS + POLICY
 ↓
FRAUD CHECK
 ↓
AGGREGATION
 ↓
DECISION
 ↓
HUMAN REVIEW when required
 ↓
CLOSE
```

# 3. Roles and Responsibilities

| Role | Responsibility |
|---|---|
| Intake/API Service | Validate and register submissions |
| Processing Worker | Execute workflow |
| AI/ML Team | Agent/model quality |
| Operations Reviewer | Resolve escalated cases |
| Operations Manager | Monitor queues and outcomes |
| Platform Team | Infrastructure and deployment |
| QA | Regression and evaluation |
| Security/Compliance | Access and audit controls |

# 4. Daily System Start-Up

Perform:
1. API health check.
2. Database connectivity check.
3. Queue connectivity check.
4. Worker heartbeat check.
5. Object-storage check.
6. Vector-store check.
7. Model-provider connectivity check.
8. Failed-job review.
9. Queue-depth review.
10. Verify no abnormal error spike.

# 5. Document Intake Procedure

## Step 1 — Receive
Accept only configured document types:
`JPEG, PNG, PDF, TIFF`.

## Step 2 — Validate
Validate:
- filename;
- MIME type;
- size limit;
- readable content.

## Step 3 — Create Case
Generate:
- `case_id`;
- document ID;
- checksum;
- submission timestamp.

## Step 4 — Store
Persist original document in private object storage.

## Step 5 — Queue
Create a processing job.

The HTTP intake request should return the case reference rather than blocking for the full AI workflow.

# 6. Classification Procedure

The Classifier Agent inspects the submitted document and returns:

```json
{
  "doc_type": "CLAIM_FORM",
  "confidence": 0.94,
  "routing_tags": ["claims"]
}
```

Decision:
- recognized and sufficiently confident → continue;
- unknown or low confidence → Human Review.

Record classifier output and execution metadata.

# 7. OCR / Preprocessing Procedure

For image-based inputs:

```text
Image
 ↓
Normalization
 ↓
Orientation / quality handling
 ↓
OCR / vision extraction
 ↓
Normalized JSON
```

Store:
- raw text;
- structured fields;
- bounding boxes;
- preprocessing status.

Do not replace the original document with a processed version.

# 8. KYC Procedure

KYC Agent checks:
- member ID;
- date of birth;
- policy number;
- expiry;
- visual anomalies.

Return:

```json
{
  "kyc_passed": true,
  "flags": [],
  "confidence": 0.91
}
```

Any suspicious or uncertain outcome should remain visible to downstream decisioning and human review.

# 9. Claims Procedure

Claims Agent extracts:
- claim amount;
- ICD-10 codes;
- CPT codes;
- provider NPI;
- service date.

Then validate against the claims schema.

Example outcome:

```json
{
  "extracted_fields": {},
  "schema_valid": true,
  "validation_errors": []
}
```

# 10. Policy RAG Procedure

Policy PDF lifecycle:

```text
PDF
 ↓
Docling
 ↓
Parse
 ↓
Chunk
 ↓
Embed
 ↓
Vector Store
 ↓
Retrieve by procedure context
 ↓
Return policy evidence
```

The agent returns:
- covered;
- coverage percentage;
- policy clause;
- exclusions.

Recommended operational control: record policy document version and retrieval source metadata with every case.

# 11. Fraud Procedure

Fraud Agent reads:
- current claim characteristics;
- patient history;
- duplicate patterns;
- frequency;
- provider patterns.

Output:

```json
{
  "fraud_score": 0.18,
  "anomalies": [],
  "risk_level": "LOW"
}
```

# 12. Decision Procedure

Apply the supplied business rules:

```text
REJECT:
KYC failed
OR procedure not covered
OR schema invalid

ESCALATE:
fraud score >= 0.3
OR any agent confidence < 0.6

APPROVE:
KYC passed
AND claim valid
AND covered
AND fraud score < 0.3
```

The technical implementation should make precedence explicit and test it against edge cases.

# 13. Human Review Procedure

Cases enter human review when:
- document type is UNKNOWN;
- classification is low confidence;
- fraud threshold is met;
- agent confidence falls below the configured threshold;
- processing fails in a non-retryable manner.

Reviewer procedure:
1. Open case.
2. Inspect original document.
3. Inspect extracted data.
4. Inspect KYC output.
5. Inspect Claims output.
6. Inspect Policy evidence.
7. Inspect Fraud anomalies.
8. Confirm or override decision.
9. Record reason.
10. Submit final action.

# 14. Retry Procedure

## Retryable examples
- transient database timeout;
- temporary object storage error;
- temporary queue failure;
- model-provider timeout.

## Non-retryable examples
- corrupt file;
- unsupported format;
- invalid case reference;
- invalid configuration.

Recommended retry strategy:

```text
Attempt 1
  ↓ wait
Attempt 2
  ↓ wait longer
Attempt 3
  ↓
Operational Exception Queue
```

Do not retry business-rule failures indefinitely.

# 15. Worker Crash Recovery

Recommended production controls:
- execution lease;
- heartbeat timestamp;
- workflow checkpoint;
- idempotency key.

Example:

```text
Worker A starts
 ↓
checkpoint saved
 ↓
Worker A crashes
 ↓
lease expires
 ↓
Worker B resumes/retries
```

# 16. Monitoring Procedure

Monitor:

### Intake
- submission volume;
- failed uploads;
- file-type distribution.

### Processing
- queue depth;
- active jobs;
- completed jobs;
- failed jobs;
- retries.

### AI
- model latency;
- classifier confidence;
- extraction completeness;
- policy retrieval failures;
- fraud scoring failures.

### Business
- approve;
- reject;
- escalate;
- human override rate.

# 17. Incident Handling

## Sev-1
Examples:
- widespread processing outage;
- suspected sensitive-data exposure;
- major service outage.

Actions:
1. Stop affected processing path where needed.
2. Preserve logs and evidence.
3. Notify on-call/service owner.
4. Investigate.
5. Recover from last known-good state.
6. Document incident.

## Sev-2
Major workflow degradation requiring engineering intervention.

## Sev-3
Localized defect with workaround.

## Sev-4
Non-critical reporting/UI issue.

# 18. Security Operations

- Keep documents in private object storage.
- Do not expose raw object-storage credentials to browser clients.
- Use TLS.
- Enforce server-side authorization.
- Mask sensitive information in logs.
- Keep audit events separate from mutable UI records.
- Store secrets outside source control.

# 19. Release Procedure

1. Unit tests pass.
2. Contract tests pass.
3. Integration tests pass.
4. 20-case evaluation set passes.
5. Policy RAG tested against at least two sample policies.
6. UI end-to-end tests pass.
7. Security/dependency review completed.
8. Configuration reviewed.
9. Staging deployment completed.
10. Smoke test completed.
11. Production deployment authorized.
12. Post-release monitoring enabled.

# 20. Rollback

Rollback to last known-good release when:
- processing failures spike;
- decision correctness degrades;
- critical dependency is unavailable;
- security defect is discovered.

Retain existing case and audit data during rollback.

# 21. Case Closure Checklist

A case can be marked closed when:
- final decision exists;
- required agent outputs are stored;
- audit events are complete;
- human review, if required, is complete;
- no unresolved processing error remains.

# 22. Operational Records

Recommended records:
- case record;
- document metadata;
- agent execution;
- decision;
- human review;
- audit event;
- failure/retry event;
- configuration/model/policy versions.
