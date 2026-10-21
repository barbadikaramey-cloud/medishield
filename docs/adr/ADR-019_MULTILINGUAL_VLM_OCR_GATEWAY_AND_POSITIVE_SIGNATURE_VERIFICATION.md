# ADR-019: Multilingual VLM OCR Gateway and Positive Signature Verification

## Status
Accepted

## Date
2026-10-19

## Context
Production testing of multilingual claims (specifically Hindi case `CASE-20261018-EA1BE2` with Devanagari script and vernacular terms) revealed two critical gaps in the intake and adjudication pipelines:

1. **English-Only OCR Degradation on Non-Latin Scripts**:
   - The bundled local OCR engine (`RapidOCR`) uses an English/Latin ONNX character dictionary (`ch_PP-OCRv4_rec_infer.onnx` / mobile weights).
   - When encountering Devanagari script (Hindi Unicode `U+0900 - U+097F`) or complex diacritics, RapidOCR's character classifier collapses non-Latin glyphs into arbitrary pseudo-ASCII approximations (e.g. `fa Ri`, `Lb /k`, `Ll2`).
   - The degraded OCR text is passed to downstream clinical terminology extractors (`ClaimsAgent`), preventing identification of primary and secondary diagnoses (e.g. "घुटने का ऑस्टियोआर्थराइटिस" becomes "fa Ri", which fails terminology extraction).
   - Furthermore, the fallback vision model in `.env` was configured to `gemini-2.0-flash`, which is deprecated by Google GenAI (returning HTTP 404 with instruction to migrate to `gemini-3.8-flash`).

2. **Inverted Default in Regulatory Signature Verification**:
   - In `src/medishield/domain/faithfulness.py`, signature checks defaulted to `doctor_signature_verified = True` and `patient_signature_verified = True`.
   - The code only marked signatures as unverified if an explicit negative string marker (e.g., `[signature missing]`, `[blank]`) was found.
   - For documents lacking any signature block or containing an unexecuted blank line (`_______`), the system falsely certified both signatures as verified (`doctor_signature_verified: true`, `patient_signature_verified: true`).
   - In `src/medishield/domain/explanation.py`, this false positive was ingested by the explanation synthesizer, prompting the narrative to declare: `"Doctor and patient signatures are verified as present and authentic."`
   - This directly violated Rule 12 (*Preserve evidence and provenance*) and Rule 13 (*Never silently convert uncertainty into false success*).

## Decision
1. **Multilingual VLM OCR Gateway**:
   - Upgrade default Gemini Vision model from deprecated `gemini-2.0-flash` to `gemini-3.8-flash`.
   - In `src/medishield/ocr/preprocessor.py`, introduce automated script and degradation heuristics (`is_multilingual_or_degraded`).
   - When a document contains Devanagari or Spanish cues, or when local RapidOCR yields low-confidence character fragments (`fa Ri`, `Lb /k`, `Ll2`), route the page image through `GeminiVisionClient` / `ResilientVisionClient` with a multi-script transcription prompt.
   - The VLM extracts exact Unicode Devanagari script (`निदान`, `घुटने का ऑस्टियोआर्थराइटिस`, `रोहन पाटील`) and structured table spans, saving accurate OCR page payloads in storage (`ocr/{case_id}/{document_id}.json`).
   - Downstream clinical entities in `ClaimsAgent` can now extract canonical ICD-10 codes directly from vernacular narrative.

2. **Positive Evidence Verification Model for Doctor and Patient Signatures**:
   - Refactor `evaluate_case_consistency` in `src/medishield/domain/faithfulness.py`:
     - Discontinue the unsafe "innocent until proven guilty" default (`True`).
     - A signature is verified **only if positive evidence of execution or attestation is present**:
       - **Doctor Signature**: requires physician name/credentials, `Signed by Dr. ...`, `Physician Signature: Dr. ...`, `Attending Physician Signature: ...`, `/s/ Dr. ...`, or digital attestation statement. Blank signature lines (`_______`), explicit `[unsigned]` markers, or documents with no physician attestation evaluate to `doctor_signature_verified = False`.
       - **Patient Signature**: requires patient signature block with `Signed`, `Signature on file`, `Assignment of benefits authorized`, `Signed by patient`, or explicit patient signature statement. Blank signature lines, `None`, `No`, or documents with no patient signature evaluate to `patient_signature_verified = False`.
     - When signatures are unverified, record explicit conflict entries in `conflicts_detected` and `specific_escalation_reasons`.
     - Escalate claims with unverified signatures under Rule P3 (`EscalationSubReason.HUMAN_REVIEW`) and prevent auto-approval under Rule P6.

3. **Grounded Explainability Alignment**:
   - In `src/medishield/domain/explanation.py`:
     - Default signature lookups to `False` (`_safe_get(cr, "doctor_signature_verified", False)`).
     - Provide strict system prompt directives to the LLM explanation generator prohibiting claims of verified signatures when the consistency report flags them as unverified or absent.

## Consequences
- **Positive**:
  - Non-Latin Hindi and Spanish claims are transcribed with high-fidelity Unicode script, allowing automated clinical necessity verification for multilingual documents.
  - Zero false-positive signature verifications: ungrounded or unsigned documents are accurately flagged, protecting against compliance and audit failures.
  - Explanations strictly mirror ground truth provenance.
- **Risks & Mitigations**:
  - *Risk*: Dependency on VLM for non-Latin script transcription when local OCR lacks Devanagari models.
  - *Mitigation*: Graceful cascade across Gemini 3.8 Flash, HuggingFace Pixtral, and Groq Vision, with deterministic fallback to document text streams and offline catalog mappings.

