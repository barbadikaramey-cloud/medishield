# Architecture Decision Record (ADR)
## ADR-004: Provenance Verification via OCR Text Spans Over Model-Generated Bounding Boxes

**Status:** Accepted  
**Date:** 2026-10-02  
**Context:** Faithfulness Verification & Evidence Grounding  
**Deciders:** Lead Software Architect & AI Systems Engineer

---

## 1. Context and Problem Statement

Document AI architectures often rely on vision-language models (VLMs) to output normalized bounding box coordinates (e.g., `[ymin, xmin, ymax, xmax]`) alongside extracted values to demonstrate provenance.

However, extensive empirical testing demonstrates that model-generated bounding boxes suffer from:
1. **Coordinate Hallucination:** Models frequently output plausible-looking box coordinates that do not actually enclose the target text.
2. **Resolution & Aspect Ratio Drift:** Small differences in image rasterization, DPI, and browser viewport scaling cause bounding box overlays to miss physical words on the UI canvas.
3. **Lack of Text Grounding:** A model can hallucinate a member ID value while pointing a bounding box at an empty table cell or irrelevant label.

---

## 2. Decision

We **deliberately reject relying on model-generated bounding boxes as the primary proof of provenance**.

Instead, we mandate:
1. **OCR Text Span IDs as Authoritative Provenance:**
   - The OCR preprocessor breaks each page into discrete, immutable text spans (`span_id`, e.g., `'span-p1-042'`), each retaining its verbatim text string, OCR confidence, and ground-truth OCR bounding box.
   - When an extraction agent extracts a fact (e.g., Member ID `'MEM-84920'`), it must cite the exact `span_id`.
2. **Deterministic Text Existence Check:**
   - The Faithfulness Gate verifies that the extracted value actually exists as a substring or exact token match within the cited span's raw text.
   - If the text does not exist in the cited span, the evidence is flagged as ungrounded (`evidence_quality_score = 0.0`), and the agent verdict is forced to `INDETERMINATE`.
3. **Page-Level Fallback with Quality Penalty:**
   - If a span ID is missing or misaligned but the value exists elsewhere on the physical page, the system downgrades `provenance_type` from `SPAN` to `PAGE` and assesses a 50% penalty (`evidence_quality_score = 0.5`).
4. **Bounding Boxes as Secondary UI Rendering Properties:**
   - Bounding boxes are derived from the verified OCR span geometry, guaranteeing that UI highlight boxes always frame the actual physical words on the document.

---

## 3. Rationale & Consequences

### Positive Consequences
- **Eliminates False Provenance:** Prevents LLMs from fabricating facts while gesturing at random areas of a document.
- **Auditable Traceability:** Reviewers in the Next.js UI can click an extracted field and jump directly to the exact verified OCR text span.
- **Calibrated Decision Confidence:** Decision Engine can mathematically incorporate `evidence_quality_score` into final adjudication confidence.

### Negative / Mitigated Consequences
- Requires OCR preprocessors (Docling/Tesseract) to generate structured span token lists. Mitigated by standardized OCR schema definitions in `03_MULTI_DOCUMENT_INTAKE_AND_SPLIT_CONTRACT.md`.
