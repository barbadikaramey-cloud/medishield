# Feature Specification: FEAT-013 Evaluation & Synthetic Data Framework

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-013`
- **Feature Name:** Evaluation & Synthetic Data Framework
- **Phase:** Phase 10 (Evaluation & Benchmark Framework)
- **Status:** Implemented & Verified
- **Authoritative References:**
  - `docs/canonical/12_EVALUATION_AND_SYNTHETIC_DATA_FRAMEWORK.md` (Sections 1–6)
  - `docs/canonical/06_FAITHFULNESS_PROVENANCE_AND_VERIFICATION.md` (Grounding & provenance thresholds)
  - `docs/canonical/10_FRAUD_DETECTION_ENGINE_SPECIFICATION.md` (CAT-07 duplicate, CAT-10 tampering)
  - `docs/canonical/11_SECURITY_HIPAA_AND_PROMPT_INJECTION_DEFENSES.md` (CAT-09 prompt injection defenses)
  - `AGENTS.md` (Rules 1–15)

---

## 2. Purpose
Establishes the authoritative benchmarking, synthetic data generation, and evaluation harness for MediShield:
1. **Unified Seed Relational Data Generation (Spec 12 § 2):**
   - Implements `RelationalSyntheticGenerator` propagating random seed to generate synchronized synthetic entities across members, policy catalog, enrollments, claim history, and current claim files.
   - Eliminates relational drift between claims and member/policy registries.
2. **Dual-Tier Evaluation Strategy (Spec 12 § 1, § 6):**
   - **Tier 1 (Assignment Core Minimum):** 20 canonical cases with ground truth labels covering all 6 document types.
   - **Tier 2 (Scalable Benchmark):** 100+ cases with 60% Development / 40% Blind Holdout Test split across all 11 adversarial stress categories.
3. **11 Hard Adversarial & Stress Categories (Spec 12 § 3):**
   - `CAT-01`: Clean Standard Pass (`APPROVE`).
   - `CAT-02`: Missing Document (`ESCALATE(MISSING_INFORMATION)`, State: `AWAITING_INFO`).
   - `CAT-03`: Multi-Page Bundled PDF (Split & Adjudicate).
   - `CAT-04`: Degraded Image Quality (`ESCALATE(AMBIGUITY)`).
   - `CAT-05`: Expired Identity Document (`REJECT` via KYC Agent).
   - `CAT-06`: Policy Date Window Mismatch (`REJECT` via Policy Agent).
   - `CAT-07`: Duplicate Submission (`ESCALATE(FRAUD_REVIEW)` via Fraud Engine).
   - `CAT-08`: Conflicting Identity Data (`ESCALATE(AMBIGUITY)`).
   - `CAT-09`: Prompt Injection Attack (`ESCALATE(SECURITY_REVIEW)` via Security Firewall).
   - `CAT-10`: Digital Image Tampering (`ESCALATE(FRAUD_REVIEW)` via Tamper Detector).
   - `CAT-11`: Dependency Failure (`ESCALATE(TECHNICAL_FAILURE)`).
4. **Machine-Readable Ground Truth Contract (Spec 12 § 4):**
   - Emits canonical manifest JSONs with document lists, expected field extractions, bounding boxes, policy clauses, fraud risks, and expected business decisions.
5. **Multi-Dimensional Weighted Rubric & KPIs (Spec 12 § 5):**
   - Computes Classification Accuracy (20%), Extraction Completeness (20%), Policy Retrieval Quality Recall@5 (15%), Decision Correctness (25%), UI Functionality (10%), Code Quality (10%), and Overall Weighted Pass Gate ($\ge 70.0\%$).
   - Calculates operational KPIs: STP rate, False Approval Rate (FAR $\le 1\%$), False Denial Rate (FDR $\le 2\%$), Latency, and Rework Rate.

---

## 3. Requirement References
- **REQ-EVAL-01 (Spec 12 § 1):** Support Tier 1 (20 cases) and Tier 2 (100+ cases) dataset generation.
- **REQ-EVAL-02 (Spec 12 § 2):** Relational seed propagation with zero relational drift between entities.
- **REQ-EVAL-03 (Spec 12 § 3):** Exact generation of 11 stress/adversarial categories (`CAT-01` to `CAT-11`).
- **REQ-EVAL-04 (Spec 12 § 4):** Ground truth JSON manifest serialization and validation.
- **REQ-EVAL-05 (Spec 12 § 5.1):** Weighted assignment rubric scoring with $\ge 60\%$ decision gate and $\ge 70\%$ overall composite gate.
- **REQ-EVAL-06 (Spec 12 § 5.2):** Operational KPI reporting (STP rate, FAR, FDR, Latency, Rework Rate).
- **REQ-EVAL-07 (Spec 12 § 6):** Deterministic 60/40 train/test dataset partition with seed repeatability.

---

## 4. Architectural Scope & Affected Components
- **Package:** `src/medishield/eval/`
  - `src/medishield/eval/contracts.py`: Dataclasses and Pydantic models for ground truth cases, documents, expected fields, evaluation results, and scoring summaries.
  - `src/medishield/eval/generator.py`: `RelationalSyntheticGenerator` creating relational seed data, claim items, OCR mocks, and test cases for `CAT-01` through `CAT-11`.
  - `src/medishield/eval/metrics.py`: Evaluation metrics engine computing classification accuracy, extraction completeness, recall@K, decision correctness, STP rate, and weighted rubric composite.
  - `src/medishield/eval/harness.py`: `BenchmarkHarness` runner that executes cases through workflow/evaluator, compares predictions against ground truth, and outputs formatted evaluation reports.
  - `src/medishield/eval/__init__.py`: Evaluation framework entry point.
- **Unit Test Suite:**
  - `tests/unit/test_evaluation_framework.py`: Comprehensive test suite verifying generator, 11 categories, metrics engine, and benchmark harness.

---

## 5. Acceptance Criteria (Verification Matrix)
1. **Relational Consistency:** Generator produces matching `member_id`, `policy_id`, service dates, and line amounts across all generated tables and files for any given seed.
2. **Category Coverage:** All 11 adversarial categories (`CAT-01` through `CAT-11`) can be synthesized deterministically with valid expected decisions and failure reasons.
3. **Dataset Splits:** Generates Tier 1 (20 cases) and Tier 2 (100+ cases) with 60/40 development/test split without duplicate seeds or cross-leakage.
4. **Scoring Accuracy:** Weighted composite score correctly reflects weights: 20% classification, 20% extraction, 15% retrieval, 25% decision, 10% UI, 10% code quality.
5. **KPI Computation:** STP, FAR, FDR, latency, and rework rates are accurately evaluated.
6. **Zero Regressions & Clean Lint:** 100% test pass rate (171/171 passing) and clean `ruff check`.

