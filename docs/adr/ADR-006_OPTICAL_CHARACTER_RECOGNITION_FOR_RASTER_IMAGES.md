# Architecture Decision Record (ADR)
## ADR-006: Optical Character Recognition for Raster Document Images via RapidOCR

**Status:** Accepted  
**Date:** 2026-10-05  
**Context:** Intake Pipeline & Multimodal Evidence Grounding  
**Deciders:** Lead Software Architect & AI Systems Engineer  

---

## 1. Context and Problem Statement

Medical claim submissions often consist of raster document scans and photos (e.g., PNG, JPEG, TIFF) representing ID cards, hospital discharge summaries, itemized billing statements, and physician prescriptions.

Previously, the intake pipeline's `DefaultOCREngine` extracted text streams only from searchable PDFs via stream regex parsing. Raster images were treated as opaque binaries, returning zero text token spans and a degraded image quality score (`0.20`). This caused:
1. Downstream specialist agents (KYC, Claims Extraction) to fail extraction (`INDETERMINATE`).
2. Policy Coverage synchronization barriers (`JoinClaimReady`) to reject claims (`FAIL`) due to missing member IDs and dates of service.
3. Adjudication to escalate valid claims prematurely under `RULE-P3-AMBIGUITY`.

A local, deterministic, and accurate optical character recognition engine is required to extract text and physical coordinate spans from raster image pixels while preserving HIPAA/PHI privacy and zero recurring cloud costs.

---

## 2. Decision

We integrate **RapidOCR (`rapidocr-onnxruntime`) with Pillow** into `DefaultOCREngine`:

1. **Local Deep-Learning Architecture:**
   - RapidOCR utilizes PaddleOCR v4 (DBNet text detection + SVTR text recognition) executed entirely locally via ONNX Runtime on CPU.
   - Zero patient health information (PHI) is transmitted over external networks.

2. **Ground-Truth OCR Text Span Generation (Preserving ADR-004):**
   - Each detected text line or token produces an `OCRTextSpan` with:
     - `span_id`: Deterministically assigned (e.g. `span-p1-0001`).
     - `page_number`: 1-indexed page number.
     - `text`: Verbatim decoded string.
     - `confidence`: RapidOCR recognition confidence ($0.0 - 1.0$).
     - `bbox`: Normalized `OCRBoundingBox` $[x_{min}, y_{min}, x_{max}, y_{max}] \in [0.0, 1.0]$ derived from image pixel dimensions and RapidOCR 4-point polygon coordinates.

3. **Pluggable Fallback & Reliability:**
   - If RapidOCR is not available or encounters an unreadable/corrupted raster image, the preprocessor gracefully falls back to empty span reporting with quality assessment per `REQ-OCR-03` without crashing the coordinator.
   - Searchable PDF stream extraction remains preserved for electronic PDFs.

---

## 3. Rationale & Consequences

### Positive Consequences
- **High Document Accuracy:** Superior extraction of complex medical forms, itemized tables, and identity cards compared to traditional Tesseract OCR.
- **Zero OS-Level Installation Overhead:** Fully packaged in Python via `pip install rapidocr-onnxruntime Pillow`, eliminating external Windows binary dependencies.
- **Strict Provenance Adherence:** Bounding boxes and confidence scores directly populate `OCRPagePayload` and `OCRTextSpan`, preserving full auditability in accordance with ADR-004 and Spec 06.
- **Cost & Privacy:** 100% free, open-source (Apache 2.0), running locally without cloud API expenses.

### Mitigated Consequences
- Adds lightweight ONNX Runtime and OpenCV dependencies to the environment (~35MB total). Mitigated by declaring dependencies in `pyproject.toml`.

