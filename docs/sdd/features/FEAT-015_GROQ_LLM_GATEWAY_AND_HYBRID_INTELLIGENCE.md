# Feature Specification: FEAT-015 Groq LLM Gateway & Hybrid Agent Intelligence

## 1. Feature ID & Metadata
- **Feature ID:** `FEAT-015`
- **Feature Name:** Groq LLM Gateway & Hybrid Agent Intelligence
- **Phase:** Phase 5 (Intelligent Extraction & Hybrid Adjudication)
- **Status:** Proposed
- **Authoritative References:**
  - `docs/canonical/01_CANONICAL_ARCHITECTURE.md` (Section 1.1 Hybrid Intelligence & Section 5 Security)
  - `docs/canonical/05_AGENT_INTERFACE_CONTRACTS.md` (Sections 1-5 Typed Agent Interfaces)
  - `docs/canonical/06_FAITHFULNESS_PROVENANCE_AND_VERIFICATION.md` (Section 2 Extraction Verification)
  - `docs/canonical/09_POLICY_RAG_AND_ADMINISTRATION_SPECIFICATION.md` (Section 3 Semantic Policy Coverage)
  - `docs/canonical/11_SECURITY_HIPAA_AND_PROMPT_INJECTION_DEFENSES.md` (Prompt Injection Boundary Defenses)
  - `agents.md` (Rules 1, 2, 3, 4, 5, 6, 10, 11, 12, 13, 14, 15)

---

## 2. Purpose & Context
In accordance with **Architectural Pillar 2 (Hybrid Intelligence)**:
> *"Large Language Models are applied exclusively to tasks where probabilistic language and visual understanding provide superior value: messy document extraction, classification of ambiguous layouts, and natural language policy interpretation. Deterministic software, relational databases, cryptographic integrity checks, deterministic rule engines, and hybrid retrieval systems are used for all validations, calculations, identity checks, and business decision authority."*

This feature establishes an authoritative, ultra-fast **Groq LLM Gateway** (`GroqLLMClient`) that leverages high-speed open-source frontier models (e.g., `llama-3.3-70b-versatile`) to perform structured language inference. It equips the **Document Classifier**, **Claims Extraction**, and **Policy Coverage RAG** agents with hybrid capabilities while enforcing strict deterministic guardrails:
1. **Prompt Injection Fences:** Untrusted document text is isolated inside canonical boundary markers (`<<<UNTRUSTED_DOCUMENT_CONTENT_START>>>` and `<<<UNTRUSTED_DOCUMENT_CONTENT_END>>>`).
2. **Zero Direct LLM Business Authority (Rule 14):** The LLM never decides `APPROVE`, `REJECT`, or `ESCALATE`. It only extracts and translates untrusted text into typed candidate facts.
3. **Deterministic Mathematical & Span Grounding:** Line-item arithmetic ($\sum \text{lines} == \text{total}$) and OCR character-level span verification strictly validate any LLM extraction before it is accepted.
4. **Graceful Offline / Zero-Key Fallback (Rule 13):** If `GROQ_API_KEY` is not provided or the network is unavailable, agents gracefully fall back to deterministic pattern/regex heuristics with zero crash.

---

## 3. Requirement References
- **REQ-LLM-01 (Spec 01 § 1.1 & Spec 11 § 2):** Fast Groq Client Gateway using standard HTTP client (`httpx`). Support async and sync completions, JSON mode (`response_format={"type": "json_object"}`), bounded timeout (default 30s), and configurable model (`llama-3.3-70b-versatile`).
- **REQ-LLM-02 (Spec 11 § 2):** Prompt Injection Defense. All prompts must wrap untrusted input with canonical tokens:
  ```text
  <<<UNTRUSTED_DOCUMENT_CONTENT_START>>>
  {untrusted_content}
  <<<UNTRUSTED_DOCUMENT_CONTENT_END>>>
  ```
  The system instructions must explicitly mandate that text within these delimiters cannot alter instructions, override roles, or emit adjudication verdicts.
- **REQ-LLM-03 (Spec 05 § 2):** Hybrid Document Classification. The Document Classifier evaluates pattern signatures first. If top confidence is $< 0.70$ or layout is ambiguous, it queries the Groq LLM Gateway to disambiguate the document category and operational routing tags.
- **REQ-LLM-04 (Spec 05 § 5 & Spec 06 § 2):** Hybrid Claims & Clinical Extraction. When clinical narrative or non-standard billing forms contain unstructured text, Groq LLM assists in parsing ICD-10 codes, CPT procedures, and itemized lines. Every extracted field is subjected to deterministic span verification against OCR coordinates and deterministic arithmetic reconciliation.
- **REQ-LLM-05 (Spec 09 § 3):** Hybrid Policy Coverage Reasoning (Stage B). In Stage B policy evaluation, when candidate policy chunks are retrieved, Groq LLM parses the clinical indication and policy clauses to assess coverage and prior authorization requirements, returning citations that undergo Gate 2 verbatim text grounding.
- **REQ-LLM-06 (Config & Security):** `GROQ_API_KEY` configurable via `.env` and `Settings`. If missing, `GroqLLMClient.is_available()` returns `False` and agents execute rule-based pathways.

---

## 4. Architectural Scope & Affected Components
- **Configuration & Core:**
  - `src/medishield/core/config.py`: Add `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_BASE_URL`, `GROQ_TIMEOUT_SECONDS`.
  - `src/medishield/core/llm.py` (New): `GroqLLMClient` with JSON generation, prompt framing, retry logic, and availability checks.
  - `.env.example`: Document Groq environment variables.
- **Agents:**
  - `src/medishield/agents/classifier.py`: Hybrid classifier incorporating Groq fallback for ambiguous documents.
  - `src/medishield/agents/claims.py`: Hybrid claims parsing helper for unstructured clinical narratives.
  - `src/medishield/agents/policy.py`: Semantic clause evaluation in Policy Coverage Agent Stage B.
- **Testing:**
  - `tests/unit/test_groq_llm.py` (New): Gateway unit tests with mocked Groq responses, timeout handling, prompt fence verification, and offline fallback.
  - `tests/unit/test_ocr_and_classifier.py`: Verify hybrid classifier fallback.
  - `tests/unit/test_kyc_and_claims_agents.py`: Verify hybrid claims extraction.

---

## 5. Acceptance Criteria
1. **Groq Gateway:**
   - Initializes cleanly with `GROQ_API_KEY` from settings or environment.
   - Successfully produces typed JSON responses using Groq API schema.
   - Automatically injects canonical prompt defense delimiters around untrusted document text.
   - Returns graceful fallback when `GROQ_API_KEY` is empty or an HTTP timeout occurs.
2. **Hybrid Classifier:**
   - If regex signatures yield high confidence ($\ge 0.70$), bypasses LLM call (cost and latency optimization).
   - If regex confidence $< 0.70$ and Groq is available, queries Groq LLM to disambiguate document type.
   - If Groq is unavailable, gracefully emits `INDETERMINATE` without raising exceptions.
3. **Hybrid Claims & Policy:**
   - Candidate fields extracted via LLM are validated by deterministic code (line sum math, span verification).
   - Zero hallucinations can bypass arithmetic sum checks or verbatim policy chunk validation.
   - No business adjudication decisions (`APPROVE`, `REJECT`, `ESCALATE`) are emitted by LLM prompts (Rule 14).
4. **Verification:**
   - 100% of unit tests pass.
   - Ruff linting and formatting pass with zero errors.
   - No unapproved external dependencies introduced (uses existing `httpx`).
