# MediShield Multi-Agent Autonomous Claims Adjudication Engine

**MediShield** is an enterprise-grade, multi-agent AI claims intake, fraud verification, and policy adjudication platform designed for healthcare payers and third-party administrators (TPAs).

The system automates the processing of complex medical claims (UB-04 hospital bills, CMS-1500 physician claims, government identity documents, pharmacy prescriptions, and discharge summaries) using an event-driven multi-agent architecture with deterministic decision gates, span-level OCR provenance, and strict compliance with healthcare regulatory standards.

---

## 1. System Architecture Overview

MediShield implements a **layered, decoupled enterprise architecture** governed by Canonical Architecture Specifications and Architecture Decision Records (ADRs).

```
                      ┌───────────────────────────────────────┐
                      │    Next.js 14 Web Console (UI)       │
                      │   - Intake Uploader & Queue Viewer    │
                      │   - Provenance Bounding-Box Overlay   │
                      │   - Human-in-the-Loop Review Console  │
                      └──────────────────┬────────────────────┘
                                         │ REST API / SSE
                      ┌──────────────────▼────────────────────┐
                      │       FastAPI Gateway (Port 8001)     │
                      │   - Sub-200ms Ingestion Handshake     │
                      │   - MIME Validation & SHA-256 Storage │
                      │   - RBAC Authentication (JWT / Test)  │
                      └──────────────────┬────────────────────┘
                                         │ Async Dispatch / Celery
                      ┌──────────────────▼────────────────────┐
                      │    LangGraph Adjudication Engine     │
                      └──────────────────┬────────────────────┘
                                         │
       ┌──────────────────┬──────────────┴─────┬──────────────────┐
       │                  │                    │                  │
┌──────▼──────┐    ┌──────▼──────┐      ┌──────▼──────┐    ┌──────▼──────┐
│  KYC Agent  │    │Claims Agent │      │ Policy RAG  │    │ Fraud Engine│
│- Member DB  │    │- CPT / ICD10│      │- Stage A:   │    │- Tamper ELA │
│- DOB Check  │    │- Arithmetic │      │  Candidates │    │- Duplicate  │
│- ID Expiry  │    │- Reconcile  │      │- Stage B:   │    │- Velocity   │
│- Tamper ELA │    │  Total/Lines│      │  Corpus RAG │    │- Charges Z  │
└──────┬──────┘    └──────┬──────┘      └──────┬──────┘    └──────┬──────┘
       │                  │                    │                  │
       └──────────────────┼────────────────────┴──────────────────┘
                          │
       ┌──────────────────▼────────────────────────────────────┐
       │     Deterministic Decision Engine (Rules P1 – P8)    │
       │   - Priority 1: Technical Failure                     │
       │   - Priority 2: Missing Required Documents            │
       │   - Priority 3: Ambiguity / Low OCR / Conflicts       │
       │   - Priority 4: High Fraud Score / Tampering          │
       │   - Priority 5: Policy Exclusion / Non-Coverage       │
       │   - Priority 6: Service Date Outside Enrollment       │
       │   - Priority 7: Line Item Sum Arithmetic Discrepancy  │
       │   - Priority 8: Straight-Through Autonomous Approval  │
       └──────────────────┬────────────────────────────────────┘
                          │
       ┌──────────────────▼────────────────────────────────────┐
       │             Persistent Storage & Ledger              │
       │   - PostgreSQL 16 / SQLite Local Fallback (ADR-008)  │
       │   - S3 / Local Storage (`data/storage/`)             │
       │   - Qdrant Vector DB (Policy Chunks)                 │
       │   - Append-Only SHA-256 Tamper-Evident Audit Log     │
       └───────────────────────────────────────────────────────┘
```

---

## 2. Core Architectural Pillars

1. **State Machine vs. Business Decision Decoupling (ADR-001)**:
   Operational workflow states (`RECEIVED`, `SCANNING`, `SPLIT`, `CLASSIFYING`, `ANALYZING`, `AWAITING_INFO`, `AWAITING_HUMAN`, `COMPLETED`, `FAILED`) are decoupled from business decisions (`APPROVE`, `REJECT`, `ESCALATE`). A case awaiting human review is always in state `AWAITING_HUMAN` with a formal escalation reason (`AMBIGUITY`, `FRAUD_REVIEW`, `MISSING_INFORMATION`).
2. **Selective AI Reasoning & Deterministic Supremacy (Rule 10 & 11)**:
   LLMs are restricted to unstructured entity normalization and complex policy clause interpretation. Critical operations (database queries, line-item arithmetic, ELA digital tamper scores, and final business decisions) are computed strictly by deterministic Python algorithms.
3. **Span-Level Provenance & Faithfulness Gate (ADR-004 & Spec 06)**:
   Every single extracted field links to an exact OCR text span ID, token confidence score, and 2D bounding box geometry. Values that cannot be proven directly from source documents are flagged as ungrounded and routed to human reviewers.
4. **Preserved Parallelism (ADR-003)**:
   The KYC Agent, Claims Extraction Agent, and Policy Context Agent run concurrently during Stage A. The Fraud Engine executes in parallel with Policy Coverage RAG in Stage B.
5. **Conservative Safety & Zero Silent Failure (Rule 13 & 14)**:
   Uncertainty is never silently converted into failure. When evidence is incomplete or ambiguous, cases are safely routed to human claims examiners rather than being falsely rejected or arbitrarily hallucinated.

---

## 3. Component Deep Dive

### 3.1 OCR Preprocessor & Geometric Extractor
- **Engine**: Local deep-learning pipeline powered by **`RapidOCR`** (`rapidocr_onnxruntime`) with ONNX models for text detection, angle classification, and character recognition.
- **Geometric Box Pairing**: Standard medical claim forms (UB-04 / CMS-1500) do not use colon delimiters (`:`). The engine scans 2D coordinates to pair form box labels (`PATIENT NAME`, `BIRTHDATE`, `SEX`, `NPI`, `TOTAL CHARGES`) with corresponding data values directly beneath or to the right.
- **Caching**: OCR token spans and bounding boxes are cached in `data/storage/ocr/{case_id}/{document_id}.json` to enable instant subsequent re-runs and sub-second UI rendering.

### 3.2 Specialist Agents
- **KYC Agent (`kyc.py`)**: Resolves member ID against the master database, validates patient name and date of birth, checks government ID expiration, and inspects image digital tamper signals.
- **Claims Agent (`claims.py`)**: Extracts CPT/HCPCS procedure codes, ICD-10 diagnosis codes, dates of service, itemized line items, and provider NPIs. Reconciles itemized line amounts against total charges.
- **Policy Context Agent (`policy.py` - Stage A)**: Looks up enrolled policies and active coverage date windows using only member ID, avoiding dependencies on service date.
- **Policy Coverage Agent (`policy.py` - Stage B)**: Uses vector search (Qdrant) and LLM-assisted clause retrieval to match itemized procedures against covered benefits, exclusions, deductibles, co-pays, and prior authorization requirements. Enforces strict corpus verification.
- **Fraud Detection Engine (`fraud.py`)**: Computes a transparent, weighted 7-factor composite fraud risk score (duplicate claim detection, ELA image tampering, submission velocity, billing charge outliers, service date chronological consistency, arithmetic line drift, and 30-day frequency).

### 3.3 Deterministic Decision Engine (`decision.py`)
Applies priority rules **P1 through P8** deterministically. No LLM has write access to the business decision:
- **P1**: Infrastructure / Service Outage $\rightarrow$ `ESCALATE / TECHNICAL_FAILURE`
- **P2**: Missing Required Document Types $\rightarrow$ `ESCALATE / MISSING_INFORMATION`
- **P3**: Contradictory Data / Low OCR Confidence $\rightarrow$ `ESCALATE / AMBIGUITY`
- **P4**: Fraud Risk Score $\ge 0.70$ or Digital Tampering $\rightarrow$ `ESCALATE / FRAUD_REVIEW`
- **P5**: CPT Procedure Explicitly Excluded $\rightarrow$ `REJECT / COVERAGE_EXCLUSION`
- **P6**: Service Date Outside Policy Window $\rightarrow$ `REJECT / POLICY_INACTIVE`
- **P7**: Line Items Sum Discrepancy $\rightarrow$ `ESCALATE / AMBIGUITY`
- **P8**: All Specialist Checks Pass $\rightarrow$ `APPROVE` (Straight-Through Processing)

---

## 4. Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Backend API** | FastAPI, Pydantic v2, Uvicorn, Python 3.11 |
| **Workflow Orchestration** | LangGraph, Python asyncio |
| **OCR & Vision** | RapidOCR (`rapidocr_onnxruntime`), OpenCV, Pillow, Google Gemini VLM |
| **LLM Gateway** | Groq (Llama-3 / GPT-OSS), LangChain |
| **Database & ORM** | PostgreSQL 16 (AsyncPG / Psycopg2), SQLAlchemy 2.0, SQLite fallback (ADR-008) |
| **Vector Storage** | Qdrant (Hybrid Dense / BM25 Sparse Search) |
| **Object Storage** | MinIO / AWS S3, Local Filesystem Storage (`data/storage/`) |
| **Task Queue** | Celery 5, Redis 7 (In-process fallback when Redis is offline) |
| **Frontend UI** | Next.js 14 (App Router), React 18, Tailwind CSS, Lucide Icons |
| **Telemetry & Quality** | Pydantic Logfire, Pytest, Ruff |

---

## 5. Repository Directory Layout

```text
medishield/
├── data/
│   ├── medishield.db                # SQLite development database
│   └── storage/                     # Local object storage root
│       ├── cases/{case_id}/raw/     # Raw intake documents (PNG, PDF)
│       └── ocr/{case_id}/           # Cached OCR token payloads & bounding boxes
├── dataset/                         # 30 canonical benchmark patient clusters
│   ├── claim_forms/                 # CMS-1500 & UB-04 sample images
│   ├── discharge_summaries/         # Clinical encounter notes
│   ├── id_documents/                # Driver licenses & state IDs
│   ├── policy_amendments/           # Rider and amendment documentation
│   └── prescriptions/               # Itemized Rx orders
├── docs/
│   ├── canonical/                   # 12 Canonical Architecture Specifications
│   ├── adr/                         # Architecture Decision Records (ADR-001 to ADR-008)
│   └── source/                      # Business Requirements & Source Contracts
├── frontend/                        # Next.js 14 Operator Review Console
│   ├── src/app/cases/               # Case list & review queue
│   └── src/app/cases/[id]/          # Interactive case adjudication & visual overlay
├── src/medishield/
│   ├── agents/                      # Specialist agents (KYC, Claims, Policy, Fraud)
│   ├── api/                         # FastAPI REST routers, schemas, and dependencies
│   ├── core/                        # Global configuration, settings, security
│   ├── db/                          # SQLAlchemy models, enums, seeder, session
│   ├── domain/                      # Pydantic data contracts (Decision, Claims, KYC)
│   ├── eval/                        # Synthetic benchmark generator, harness, metrics
│   ├── intake/                      # Magic bytes validation, file sanitization, S3 storage
│   ├── ocr/                         # RapidOCR preprocessor, geometric extractor, reconciler
│   ├── worker/                      # Celery workflow execution tasks
│   └── workflows/                   # LangGraph nodes, state, and coordinator
├── tests/
│   └── unit/                        # 208 comprehensive unit tests
├── pyproject.toml                   # Project dependencies and tool configurations
└── README.md                        # Primary project documentation
```

---

## 6. Getting Started

### 6.1 Prerequisites
- Python 3.11+
- Node.js 18+ and `npm`
- (Optional for full production mode) Docker, PostgreSQL 16, Redis 7, Qdrant

### 6.2 Backend Installation & Setup
1. Clone the repository and navigate to the project root:
   ```bash
   cd d:\Projects\medishield
   ```
2. Activate your virtual environment and install dependencies:
   ```bash
   .venv\Scripts\activate
   pip install -e .
   ```
3. Configure environment variables in `.env`:
   ```env
   ENVIRONMENT=development
   DATABASE_URL=postgresql+asyncpg://medishield_user:medishield_password@localhost:5432/medishield
   STORAGE_DIR=data/storage
   GROQ_API_KEY=your_groq_api_key
   GEMINI_API_KEY=your_gemini_api_key
   ```
   *(Note: In local development, if PostgreSQL or Redis are offline, MediShield automatically activates ADR-008 SQLite fallback and in-process task execution).*

4. Launch the Backend API:
   ```bash
   uvicorn medishield.api.main:app --host 0.0.0.0 --port 8001 --reload
   ```

### 6.3 Frontend Installation & Setup
1. Navigate to the `frontend/` directory:
   ```bash
   cd frontend
   npm install
   ```
2. Launch the development server:
   ```bash
   npm run dev
   ```
3. Open [http://localhost:3000](http://localhost:3000) in your browser.

---

## 7. API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Service health status and environment check |
| `POST` | `/api/v1/cases` | Multipart intake handshake; validates and saves documents |
| `GET` | `/api/v1/cases` | Paginated case list with workflow state and decision filters |
| `GET` | `/api/v1/cases/{case_id}` | Detailed case adjudication, specialist findings, and provenance |
| `POST` | `/api/v1/cases/{case_id}/rerun` | Re-evaluates claims through the multi-agent pipeline |
| `GET` | `/api/v1/cases/{case_id}/documents/{doc_id}/ocr` | Returns OCR token spans, bounding boxes, and key-value pairs |
| `POST` | `/api/v1/cases/{case_id}/review` | Human reviewer manual decision override (Approve / Reject) |
| `GET` | `/api/v1/audit/cases/{case_id}` | Cryptographically verifiable SHA-256 audit event ledger |

---

## 8. Verification & Testing

Run the full automated test suite:
```bash
pytest tests/unit/
```
Run code quality and style linter:
```bash
ruff check src/ tests/
```
Execute synthetic benchmark evaluation:
```bash
python -m pytest tests/unit/test_evaluation_framework.py
```

---

## 9. Governing Engineering Rules

Every change to the codebase adheres to [`AGENTS.md`](file:///d:/Projects/medishield/AGENTS.md):
- Canonical specifications are the source of truth.
- Do not modify architecture without an ADR.
- Implement only requested features without silently introducing dependencies.
- Every production feature requires tests.
- Never silently convert uncertainty into failure.
- Never let the LLM directly determine the final business decision.
