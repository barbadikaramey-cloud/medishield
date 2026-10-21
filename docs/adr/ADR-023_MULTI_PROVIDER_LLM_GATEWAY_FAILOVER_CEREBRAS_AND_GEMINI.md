# ADR-023: Multi-Provider LLM Gateway Failover with Cerebras and Gemini

## Status
Accepted

## Date
2026-10-19

## Context
Production testing, automated benchmarks, and clinical explanation generation revealed persistent rate-limiting (`HTTP 429 Too Many Requests`) on the primary LLM provider (Groq). 
Groq's developer tier enforces an **8,000 Tokens Per Minute (TPM)** ceiling across primary models (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`). Because MediShield's 7-section clinical explanation generator compiles comprehensive factual summaries (KYC, Claims, Policy RAG, Fraud, Consistency, and Clinical Alignment line assessments), a single case explanation consumes 4,000–5,500 tokens. As a result, processing two cases or running test suites in succession immediately exhausts the 8,000 TPM quota.

To guarantee zero pipeline halts, high throughput, and fault isolation without modifying business rules, the LLM Gateway requires an automated multi-provider failover strategy across independent inference infrastructures.

## Decision
We extend `GroqLLMClient` into a resilient multi-provider gateway supporting an ordered cascade across three high-throughput providers:

1. **Tier 1 — Primary: Groq API (`api.groq.com/openai/v1`)**
   - Retains existing high-speed Groq inference for primary models (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`) and rotating key support.
   - If Groq encounters HTTP 429 or transport failure across active keys, the gateway does not abort; it cascades to Tier 2.

2. **Tier 2 — Secondary Fallback: Cerebras Cloud API (`api.cerebras.ai/v1`)**
   - Active when `CEREBRAS_API_KEY` is configured.
   - Uses OpenAI-compatible `/chat/completions` endpoint with ultra-fast wafer-scale Llama inference (`llama3.1-8b`, `llama-3.3-70b`).
   - If Cerebras encounters rate-limiting or is unconfigured, the gateway cascades to Tier 3.

3. **Tier 3 — Tertiary Fallback: Google Gemini API (`google.genai`)**
   - Active when `GEMINI_API_KEY` is configured (already provisioned in MediShield for VLM vision).
   - Utilizes `google.genai.Client` with fast structured JSON output mode (`response_mime_type="application/json"`).
   - Uses `gemini-3.5-flash-lite`, `gemini-3.5-flash`, and `gemini-3.8-flash`, offering a generous 1,000,000 TPM limit (125x Groq's capacity).

4. **Tier 4 — Deterministic Safety Fallback**
   - If all external API providers are exhausted or unavailable, the system safely falls back to deterministic rule synthesis (`generate_deterministic_explanation`, rule-based clinical catalogs), ensuring strict adherence to Rule 11 and Rule 14.

## Configuration Additions
In `src/medishield/core/config.py`:
- `CEREBRAS_API_KEY`: API key for Cerebras inference.
- `CEREBRAS_MODEL`: Primary Cerebras model (default: `llama3.1-8b`).
- `CEREBRAS_FALLBACK_MODELS`: Fallback Cerebras models (default: `["llama-3.3-70b", "llama3.1-70b"]`).
- `CEREBRAS_BASE_URL`: Base URL (default: `https://api.cerebras.ai/v1`).
- `GEMINI_LLM_MODEL`: Primary Gemini text model (default: `gemini-3.5-flash`).
- `GEMINI_LLM_FALLBACK_MODELS`: Fallback Gemini text models (default: `["gemini-3.5-flash-lite", "gemini-3.8-flash"]`).

## Consequences
- **Positive:**
  - Complete elimination of 429 rate limit pipeline crashes.
   - Multi-provider redundancy: if Groq experiences rate limits or outages, Cerebras is attempted next; Gemini remains the tertiary fallback if Cerebras is unavailable or exhausted.
  - Telemetry provenance records `last_used_provider`, `last_used_model`, and `fallback_used` for full transparency.
  - Zero changes to deterministic decision rules (P1–P8).
- **Negative / Risks:**
  - Slight latency variation when cascading from wafer-scale/LPU engines to cloud Gemini endpoints (mitigated by bounded timeouts).
