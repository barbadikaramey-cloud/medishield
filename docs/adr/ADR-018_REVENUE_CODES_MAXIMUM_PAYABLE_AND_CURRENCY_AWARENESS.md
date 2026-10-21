# ADR-018: Revenue Code Adjudication, Maximum Payable Allowable Caps, Multilingual Diagnosis Linking, and Currency Awareness

## Status
Accepted

## Date
2026-10-19

## Context
MediShield processes both professional claims (CMS-1500) and institutional hospital claims (UB-04, facility invoices, itemized bills) across domestic and international contexts. Real-world claim evaluation (such as case `CASE-20261018-EA1BE2`) revealed three major systemic billing discrepancies:

1. **Revenue Code Admissibility False Conflicts:** Institutional claims bill facility accommodations (e.g. Room and Board `0100`–`0120`, ICU `0200`) and ancillaries (`0250` Pharmacy, `0270` Supplies) using Revenue Codes rather than physician CPT codes. The consistency engine in `faithfulness.py` previously treated any line lacking a 5-digit CPT code as an *"unvalidated billed charge with missing CPT/HCPCS code"*, falsely escalating valid hospital bills.
2. **Maximum Payable Allowable Caps & Chargemaster Reconciliation:** Hospitals bill gross chargemaster rates (e.g. $13,000/day for 5 days = $65,000), but health plans enforce maximum allowable rates per diem (e.g. $1,500/day in the standard medical catalog). When the claim form submits the net agreed payable amount while itemized lines list gross chargemaster charges, naive arithmetic validation flags a false `ARITHMETIC_DRIFT` discrepancy.
3. **Currency Blindness in Processing & Reporting:** Medical documents often state amounts in regional currencies (e.g. `₹79,400 INR`, `€2,500 EUR`). When the OCR pipeline strips currency symbols, downstream fraud models and explanation generators blindly assume USD (`$`), distorting statistical outlier scoring by ~85x and displaying misleading currency symbols to human adjusters.
4. **Multilingual Diagnosis Normalization:** In non-English discharge summaries (e.g. Hindi `मुख्य निदान: घुटने का ऑस्टियोआर्थराइटिस`), diagnostic clinical text was omitted or unmapped to ICD-10, resulting in false diagnosis-procedure clinical misalignment (`INDETERMINATE`).

## Decision

### 1. Revenue Code Admissibility in Faithfulness Gate
- Update `src/medishield/domain/faithfulness.py`: If a service line has an authoritative institutional Revenue Code (`rev_code`) matching `^\d{3,4}$` or active in `CodingSystem.REV_CODE`, it is recognized as a validly coded institutional line item.
- Lines with valid Revenue Codes are NOT flagged as *"missing CPT/HCPCS code"*.

### 2. Revenue Code Maximum Allowable Reconciler
- When a revenue code has standard allowable charges / per-diem limits in `MedicalCode` or catalog (e.g. Room & Board $1,500/day), allowable charge calculation is supported:
  $$\text{allowable\_amount} = \text{standard\_charge} \times \text{units}$$
- If gross chargemaster charges exceed the allowable cap, the excess is categorized as a contractual rate adjustment rather than an uncoded line error.

### 3. Currency Awareness in Contracts, Extraction, and Explanations
- Extend `ClaimsAgentOutput` and `ServiceLine` in `src/medishield/domain/contracts.py` with `currency: str = "USD"` and `currency_symbol: str = "$"`.
- In `src/medishield/ocr/ocr_reconcile.py` and `src/medishield/agents/claims.py`, detect currency markers (`₹`, `INR`, `Rs.`, `€`, `EUR`, `£`, `GBP`, `$`, `USD`) and preserve the detected currency code and symbol in the claim payload.
- In `src/medishield/domain/explanation.py`, render monetary amounts using the claim's actual currency symbol (`₹`, `€`, `$`) instead of hardcoding `$`.
- In `frontend/src/components/ClinicalExplanationViewer.tsx`, support dynamic currency display.

### 4. Multilingual Clinical Diagnosis Linking
- Extend `src/medishield/ocr/multilingual.py` and `src/medishield/domain/clinical_align.py` with localized clinical mappings (e.g. `मुख्य निदान` / `निदान` for Primary Diagnosis, and `घुटने का ऑस्टियोआर्थराइटिस` / `संधिवात` / `Gonartrosis` mapped to `M17.11` Knee Osteoarthritis).
- Ensure that bilingual/multilingual clinical documentation can satisfy medical necessity for matching surgical procedures (e.g. CPT 27447 TKA).

### 5. Hybrid Multilingual LLM Clinical Extraction Gateway (Rule 11)
- Static in-code terminology registries are inherently bounded and cannot generalize to the open-ended lexicon of Hindi and Spanish clinical narratives, regional dialects, colloquial idioms, and rare morbidities in production.
- In accordance with **Rule 11 ("Do not use an LLM when deterministic logic is sufficient")**, deterministic regex and catalog matching are executed first. When deterministic logic is insufficient to extract or interpret vernacular diagnoses, the system invokes `GroqLLMClient` to translate and ground multilingual clinical entities into candidate ICD-10 codes.
- **Architectural Guardrails:**
  - **Rule 10 & 14:** The prompt is strictly constrained to medical entity extraction and translation; it contains zero business rules and is forbidden from emitting business verdicts (APPROVE/REJECT/ESCALATE).
  - **Rule 11:** Extracted ICD-10 codes are deterministically validated against ICD-10 format specifications and catalog validity before admission.
  - **Rule 12:** Source text spans, language script, and confidence scores are preserved as provenance.
  - **Rule 13:** If the LLM gateway is offline, unconfigured, or rate-limited, the system gracefully falls back to the deterministic ontology registry without crashing or silently converting uncertainty into failure.

## Consequences
- Institutional hospital claims with Revenue Codes no longer trigger false "missing CPT" conflicts.
- Currency metadata is preserved with provenance across all specialist agent findings and UI views.
- Vernacular Hindi and Spanish diagnosis terms can be dynamically translated and linked to canonical ICD-10 codes via hybrid LLM assistance, preventing clinical alignment blind spots on arbitrary foreign language discharge summaries while strictly enforcing deterministic adjudication rules.

