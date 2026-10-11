# Requirements Traceability Matrix
## MediShield Insurance — Assignment 02

| Req ID | Source Requirement | Component | Primary API/Data | Test Evidence |
|---|---|---|---|---|
| FR-001 | Multipart JPEG/PNG/PDF/TIFF upload | FastAPI Intake | POST /api/v1/cases | Upload tests |
| FR-002 | Unique case ID | Case Service | cases.case_id | Case creation test |
| FR-003 | Raw object storage | Object Store | documents.object_key | Storage integration test |
| FR-004 | Queue job | Queue/Worker | job execution record | Queue test |
| FR-005 | Classification output | Classifier | agent_runs | Classification evaluation |
| FR-006 | Unknown/low confidence review | Workflow/UI | human_reviews | Routing test |
| FR-007 | OCR normalized JSON | OCR Node | agent_runs.output_json | OCR tests |
| FR-008 | KYC checks | KYC Agent | kyc result | KYC integration test |
| FR-009 | Claim fields | Claims Agent | claims result | Extraction evaluation |
| FR-010 | Claim schema validation | Claims Agent | validation_errors | Contract tests |
| FR-011 | Policy PDF RAG | Policy Agent | policy_documents/policy_chunks | Two-policy test |
| FR-012 | Coverage output | Policy Agent | policy result | Retrieval test |
| FR-013 | Fraud anomaly checks | Fraud Agent | fraud result | Fraud test cases |
| FR-014 | fraud_score/risk_level | Fraud Agent | agent_runs | Schema test |
| FR-015 | Final decision | Orchestrator/Decision Engine | decisions | Decision evaluation |
| FR-016 | Case UI | Next.js | case APIs | UI E2E tests |
| FR-017 | Human override | Review API/UI | human_reviews | Override E2E |
| FR-018 | Full audit trail | Audit Service | audit_events | Audit test |
| NFR-SEC | Secure credentials and access | Security | IAM/config | Security review |
| NFR-REL | Recoverable processing | Queue/Workers | job lease/checkpoints | Failure injection |
| NFR-SCALE | Multiple workers | Worker Pool | Queue | Load test |
| EVAL-01 | Classification 20% | Classifier | dataset labels | Accuracy report |
| EVAL-02 | Extraction 20% | Claims | dataset labels | Completeness report |
| EVAL-03 | Retrieval 15% | Policy | policy test set | Retrieval review |
| EVAL-04 | Decision 25% | Decision Engine | ground truth | Decision report |
| EVAL-05 | UI 10% | Next.js | case APIs | E2E evidence |
| EVAL-06 | Code quality 10% | Repository | CI/code review | Review report |

## Traceability Principle

A requirement is considered implemented only when all of the following can be linked:
1. business requirement;
2. technical component;
3. persisted state/API;
4. automated/manual test;
5. acceptance evidence.

## Evaluation Gate

```text
Overall Weighted Score >= 70%
AND
Decision Correctness >= 60%
```
