# LangSmith Tracing Deferred Review Checklist

This checklist documents items deferred for a later architectural and compliance acceptance review during or after the staged LangSmith tracing implementation.

> [!IMPORTANT]
> Canonical specifications are the source of truth. Do not resolve these deferred items by silently altering canonical agent contracts, domain rules, or HIPAA boundaries.
> Real production Protected Health Information (PHI) must **never** be transmitted to an external tracing service unless the organization's security, privacy, legal, and compliance authorities explicitly execute a Business Associate Agreement (BAA) and validate appropriate administrative, physical, and technical safeguards.

---

### Deferred Review Items

1. **Adversarial Validation of Telemetry Values**
   - **Scope**: Further adversarial testing of allowable telemetry strings in fields such as `model`, `rule_id_matched`, `error_type`, `component_name`, and `operation_name`.
   - **Objective**: Ensure that malicious prompts, adversarial document payloads, or developer oversight cannot smuggle freeform clinical text, patient names, dates, or financial amounts through schema-compliant fields.
   - **Status in Step 2D**: In Step 2D decision and evaluation nodes (`decision_engine_node`, etc.), `rule_id_matched` is strictly omitted from telemetry span summaries to observe this deferral until adversarial validation is completed.
   - **Status in Step 2E-1**: In Step 2E-1 LLM and VLM client instrumentation (`GroqLLMClient`, `GeminiVisionClient`, `HuggingFaceVisionClient`, `GroqVisionClient`), the `model` parameter is strictly omitted from telemetry span summaries to observe this deferral until adversarial validation of model strings is completed.
   - **Status in Step 2E-2**: In Step 2E-2 OCR pipeline instrumentation (`OCRPreprocessorEngine.process_document` and `extract_service_lines_from_table`), all raw OCR texts, extracted tokens, bounding boxes, file paths, object storage keys, and document IDs are strictly omitted from inputs, outputs, and metadata, transmitting only non-sensitive operational integers (`ocr_span_count`, `service_line_count`, `retry_count`), status, and completion boolean.
   - **Status in Step 2E-3**: In Step 2E-3 Policy Retrieval and RAG instrumentation (`PolicyCorpusRepository.query_chunks`), all raw query strings, CPT/Rev procedure codes, policy IDs, version tags, chunk texts, clause titles, section headers, embeddings, and similarity scores are strictly omitted from inputs, outputs, and metadata, transmitting only non-sensitive operational integers (`retrieved_chunk_count`), execution status, and completion boolean.
   - **Status in Step 2E-4**: In Step 2E-4 Database and Storage instrumentation (`storage_get_bytes`, `storage_put_bytes`, `db_member_lookup`, `db_policy_enrollment_lookup`, `db_case_state_persistence`, `db_audit_trail_retrieval`), all raw SQL queries, query parameters, database connection strings, member/patient IDs, policy numbers, case IDs, filenames, storage keys, storage URIs, payload contents, document bytes, and cryptographic hashes are strictly omitted from inputs, outputs, and metadata, transmitting only fixed operational metrics (`status`, `duration_ms`, `is_complete`, `member_matched`, `retrieved_chunk_count`) with empty inputs (`inputs={}`).
   - **Target Phase**: Step 2D/2E/3 Security & Hardening Review.

2. **Alignment of Telemetry Verdicts with Canonical Agent Contracts**
   - **Scope**: Canonical agent contracts define specialist verdicts as:
     - `PASS`
     - `FAIL`
     - `INDETERMINATE`
     - `CONFLICT`
     - `UNAVAILABLE`
   - **Objective**: Ensure that specialist telemetry enum schemas (`VERDICT_ENUM`) strictly match the canonical domain contracts across all specialist agents (`KYCAgent`, `ClaimsAgent`, `PolicyCoverageAgent`, `DeterministicFraudEngine`) without data loss or semantic divergence.
   - **Status in Step 2C & 2D**: Per SDD rules, `VERDICT_ENUM` in `tracing.py` remains strictly restricted to `{"PASS", "FAIL", "CONFLICT", "REVIEW"}`. In Step 2C and Step 2D nodes (`policy_coverage_rag_node`, `fraud_agent_node`, `kyc_agent_node`, `claims_extraction_node`, `policy_context_node`), non-aligned canonical verdicts (`INDETERMINATE`, `UNAVAILABLE`) are safely omitted from telemetry span summaries to preserve canonical domain contracts and avoid validation failures. Formal alignment remains deferred.
   - **Target Phase**: Step 3 Security & Hardening Review.

3. **Public LangSmith Client & Context API Verification**
   - **Scope**: Telemetry client registration currently configures process-level context through `langsmith.run_trees._CLIENT` and `langsmith.run_helpers._context._CLIENT`.
   - **Objective**: Audit future minor releases of the `langsmith` SDK (`>=0.14.0,<0.15.0`) to confirm public API support for process-wide custom client factories and ensure no breakage occurs from private attribute deprecations.
   - **Target Phase**: Step 3 Pre-Production Packaging.

4. **Further End-to-End Serialized Wire-Payload Audits**
   - **Scope**: Continuous automated wire-payload testing using approved, sanitized representative test datasets across all workflow paths (clean claims, fraud escalations, exclusions, and multi-document packets).
   - **Objective**: Mathematically prove zero unscrubbed clinical tokens, ICD-10 diagnosis codes, CPT/HCPCS procedure codes, dates of service, or patient identifiers across all serialized multipart network payloads.
   - **Target Phase**: Step 2C Workflow Integration Tests.

5. **Review of Cross-Run Case Correlation Requirements**
   - **Scope**: Persistent case-level correlation (`pseudonymous_case_id`) is disabled by default (`LANGSMITH_INCLUDE_CASE_PSEUDONYM=False`).
   - **Objective**: Evaluate whether operational debugging or audit playback genuinely requires cross-run correlation across distinct pipeline invocations, or whether ephemeral root run UUIDs (`trace_id`) completely satisfy tracing needs without retaining stable pseudonym hashes.
   - **Target Phase**: Final SDD Operational Signoff.

