# ADR-020: Claims Agent Latency Optimization and Adaptive Fallback

## Status
Accepted

## Context
During claim processing, the Claims Agent exhibited high latency (frequently 10–25 seconds per claim), primarily driven by:
1. **Overly Eager Fallback Triggers**: `ClaimsAgent.execute()` triggered heavy multimodal VLM (Gemini) and LLM (Groq) fallbacks whenever any service line lacked a CPT code (`has_uncoded_charged_lines`), even when:
   - Total arithmetic check already passed (`ArithmeticCheckStatus.PASS`).
   - All procedure codes from the table were accounted for.
   - Uncoded lines were non-procedural items (pharmacy, consumables, room and board) with valid revenue codes (`rev_code`).
2. **Cascading Rate-Limit Multiplier on 429**: In `GroqLLMClient`, when keys encountered rate-limits (HTTP 429), both keys entered a 10s cooldown. Because the loop checked `if not active_keys: active_keys = keys_to_try`, it repeatedly reset the list and hammered cooled-down keys across every candidate model, generating up to 6 sequential failing requests.
3. **Excessive Timeouts**: Default 30.0s timeout and transport retries caused stalled connections to wait unnecessarily.

## Decision
1. **Refined Fallback Trigger**:
   - In `ClaimsAgent.execute()`, an unresolved issue is only declared if:
     - Arithmetic fails (`arithmetic_status != ArithmeticCheckStatus.PASS`), OR
     - Procedural CPT codes detected in the table are missing from extracted service lines (`has_table_missing_cpts`), OR
     - An uncoded line lacks *both* a CPT code and an institutional Revenue code (`rev_code`), AND arithmetic is not verified.
   - Mathematically consistent claims with legitimate revenue codes (e.g. pharmacy line `0250` or bed charge `0120`) execute deterministically in milliseconds without calling remote vision or LLM endpoints.
2. **Smart Cooldown & Bounded Attempt Budget**:
   - In `GroqLLMClient`, if all active keys are currently under cooldown, immediately abort failover and raise `GroqRateLimitError` or switch to alternate provider, instead of repeatedly hammering throttled keys across all models.
   - Enforce an upper bound on total attempts (`max_attempts = min(len(keys_to_try) * len(models_to_try), 3)`).
3. **Tuned Timeouts**:
   - Configure responsive bounded timeouts (10.0s) for fallback LLM operations, ensuring the orchestrator never blocks.

## Consequences
- **Positive**:
  - Deterministic claim processing latency drops from >10s to <300ms for claims with complete or revenue-coded lines.
  - Eliminates redundant 429 cascades and avoids key burn.
  - Full compliance with Rule 11 (code handles deterministic rules) and Rule 12 (do not invoke LLM when deterministic logic is sufficient).
- **Negative / Risks**:
  - Claims with genuinely ambiguous CPTs when all Groq keys are throttled will fail over directly to human review/escalation rather than waiting for cooldowns. This aligns with Rule 14 (never silently convert uncertainty into failure).

