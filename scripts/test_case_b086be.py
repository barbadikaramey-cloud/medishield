import json
from pathlib import Path
from medishield.agents.claims import ClaimsAgent
from medishield.agents.kyc import KYCAgent, InMemoryMemberRepository
from medishield.domain.contracts import OCRDocumentPayload, OCRPagePayload, OCRTextSpan

repo = Path(__file__).resolve().parent.parent
b086_ocr_path = repo / "data/storage/ocr/CASE-20261008-B086BE/DOC-B086BE-01.json"

with open(b086_ocr_path, encoding="utf-8") as f:
    data = json.load(f)

# Reconstruct OCRDocumentPayload
page_data = data["pages"][0]
spans = [
    OCRTextSpan(
        span_id=s["span_id"],
        page_number=s["page_number"],
        text=s["text"],
        confidence=s["confidence"],
        bbox=s["bbox"],
    )
    for s in page_data["spans"]
]
page_obj = OCRPagePayload(
    page_number=page_data["page_number"],
    width_px=page_data["width_px"],
    height_px=page_data["height_px"],
    raw_text=page_data["raw_text"],
    spans=spans,
    key_value_pairs=page_data.get("key_value_pairs", []),
)
doc_payload = OCRDocumentPayload(
    document_id="DOC-B086BE-01",
    case_id="CASE-20261008-B086BE",
    ocr_engine="Tesseract-5.3/Surya",
    total_pages=1,
    pages=[page_obj],
)

claims_agent = ClaimsAgent()
claims_out = claims_agent.execute("CASE-20261008-B086BE", [doc_payload])

print("=== CLAIMS AGENT OUTPUT FOR B086BE ===")
print("Verdict               :", claims_out.verdict)
print("Confidence            :", claims_out.confidence)
print("Claim Amount          :", claims_out.claim_amount)
print("Service Date          :", claims_out.service_date)
print("Provider NPI          :", claims_out.provider_npi)
print("CPT Codes             :", [c.value for c in claims_out.cpt_codes])
print("ICD-10 Codes          :", [c.value for c in claims_out.icd10_codes])
print("Service Lines Count   :", len(claims_out.service_lines))
print("Schema Valid          :", claims_out.schema_valid)
print("Line Sum Valid        :", claims_out.line_items_sum_valid)
print("Validation Errors     :", claims_out.validation_errors)

kyc_agent = KYCAgent()
kyc_out = kyc_agent.execute("CASE-20261008-B086BE", [doc_payload])
print("\n=== KYC AGENT OUTPUT FOR B086BE ===")
print("Verdict               :", kyc_out.verdict)
print("Confidence            :", kyc_out.confidence)
print("Member ID             :", kyc_out.member_id)
print("First Name            :", kyc_out.first_name)
print("Last Name             :", kyc_out.last_name)
print("DOB                   :", kyc_out.date_of_birth)
print("Member DB Matched     :", kyc_out.member_db_matched)
print("Tamper Score          :", kyc_out.tamper_score)

