"""
Multilingual Document OCR, Script Recognition, and Localized Parsing Engine.
Specification Reference: ADR-017 § Feature 4.

Provides Devanagari (Hindi) and Spanish script identification, Devanagari numeral
translation (०-९ to 0-9), Spanish diacritic normalization, and localized Indian / European
date and currency parsing.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

# Devanagari script Unicode block: U+0900 - U+097F
RE_DEVANAGARI = re.compile(r"[\u0900-\u097F]")
RE_DEVANAGARI_DIGITS = re.compile(r"[\u0966-\u096F]")

# Mapping from Devanagari numerals to standard ASCII digits
DEVANAGARI_DIGIT_MAP = {
    "०": "0",
    "१": "1",
    "२": "2",
    "३": "3",
    "४": "4",
    "५": "5",
    "६": "6",
    "७": "7",
    "८": "8",
    "९": "9",
}

# Spanish diacritics and inverted punctuation marks
RE_SPANISH_CHARS = re.compile(r"[áéíóúñüÁÉÍÓÚÑÜ¿¡]")

# Spanish month taxonomy
SPANISH_MONTHS: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

RE_SPANISH_DATE_VERBAL = re.compile(
    r"\b(\d{1,2})\s+de\s+([a-zA-ZáéíóúñÁÉÍÓÚÑ]+)\s+(?:del?\s+)?(\d{4})\b",
    re.IGNORECASE,
)

# Currency and number patterns
RE_CURRENCY_SYMBOLS = re.compile(r"[₹€$\xa3\xa5]|(?:Rs\.?|INR|रु|EUR|USD)\s*", re.IGNORECASE)


def has_devanagari(text: str) -> bool:
    """Returns True if the text contains characters in the Devanagari Unicode block."""
    return bool(RE_DEVANAGARI.search(text))


def translate_devanagari_numerals(text: str) -> str:
    """
    Translates Devanagari numerals (०, १, २, ३, ४, ५, ६, ७, ८, ९)
    to standard ASCII digits (0-9) deterministically.
    """
    if not text:
        return text
    return "".join(DEVANAGARI_DIGIT_MAP.get(ch, ch) for ch in text)


def normalize_spanish_text(text: str) -> str:
    """
    Normalizes Spanish text using Unicode NFC canonical composition.
    Preserves all accent marks and diacritics (á, é, í, ó, ú, ñ, ü).
    """
    if not text:
        return text
    return unicodedata.normalize("NFC", text)


def detect_script(text: str) -> tuple[str, dict[str, float]]:
    """
    Classifies the dominant and secondary scripts of the given text string.
    Returns:
        (dominant_script, script_distribution)
        where dominant_script is one of: 'LATIN', 'DEVANAGARI', 'MIXED', 'NEUTRAL'
    """
    if not text:
        return "NEUTRAL", {"latin": 0.0, "devanagari": 0.0, "other": 0.0}

    devanagari_count = 0
    latin_count = 0
    total_letters = 0

    for ch in text:
        if ch.isalpha():
            total_letters += 1
            code = ord(ch)
            if 0x0900 <= code <= 0x097F:
                devanagari_count += 1
            elif (0x0041 <= code <= 0x005A) or (0x0061 <= code <= 0x007A) or (0x00C0 <= code <= 0x024F):
                latin_count += 1

    if total_letters == 0:
        return "NEUTRAL", {"latin": 0.0, "devanagari": 0.0, "other": 0.0}

    dev_frac = round(devanagari_count / total_letters, 3)
    lat_frac = round(latin_count / total_letters, 3)
    other_frac = round(max(0.0, 1.0 - (dev_frac + lat_frac)), 3)

    distribution = {"latin": lat_frac, "devanagari": dev_frac, "other": other_frac}

    if dev_frac >= 0.70:
        return "DEVANAGARI", distribution
    if lat_frac >= 0.70:
        return "LATIN", distribution
    if dev_frac > 0.15 and lat_frac > 0.15:
        return "MIXED", distribution

    return "LATIN" if lat_frac >= dev_frac else "DEVANAGARI", distribution


def parse_multilingual_date(text: str) -> date | None:
    """
    Extracts and standardizes dates across Hindi (Devanagari numerals),
    Spanish verbal date formats, and Indian/European conventions.
    Returns standard datetime.date or None.
    """
    if not text:
        return None

    # Step 1: Normalize Spanish NFC and translate Devanagari numerals
    clean = normalize_spanish_text(text)
    clean = translate_devanagari_numerals(clean)

    # Step 2: Spanish verbal date: "14 de octubre de 2026", "5 de mayo 2025"
    m_es = RE_SPANISH_DATE_VERBAL.search(clean)
    if m_es:
        day_str, month_str, year_str = m_es.groups()
        month_lower = month_str.lower()
        if month_lower in SPANISH_MONTHS:
            try:
                d = int(day_str)
                m = SPANISH_MONTHS[month_lower]
                y = int(year_str)
                return date(y, m, d)
            except ValueError:
                pass

    # Step 3: Slash / Dash / Dot Separated: DD/MM/YYYY, YYYY-MM-DD
    # Matches dates with optional surrounding label
    m_num = re.search(r"\b(\d{1,4})[./-](\d{1,2})[./-](\d{2,4})\b", clean)
    if m_num:
        p1, p2, p3 = m_num.groups()
        # Case A: YYYY-MM-DD
        if len(p1) == 4:
            try:
                return date(int(p1), int(p2), int(p3))
            except ValueError:
                pass

        # Case B: DD/MM/YYYY (Standard Indian, Spanish, and European format)
        if len(p3) == 4:
            y = int(p3)
            # Differentiate DD/MM vs MM/DD: if p1 > 12, p1 is definitely day
            try:
                d = int(p1)
                m = int(p2)
                if 1 <= m <= 12 and 1 <= d <= 31:
                    return date(y, m, d)
            except ValueError:
                pass

        # Case C: 2-digit year DD/MM/YY
        if len(p3) == 2:
            y = 2000 + int(p3)
            try:
                d = int(p1)
                m = int(p2)
                if 1 <= m <= 12 and 1 <= d <= 31:
                    return date(y, m, d)
            except ValueError:
                pass

    return None


def parse_multilingual_amount(text: str) -> Decimal | None:
    """
    Parses and normalizes currency and numeric values across:
    - Indian numbering convention (Lakh / Crore with Devanagari or ASCII digits)
      e.g. '₹ 1,50,000.00', 'Rs. 1,00,000', 'रु १५००००'
    - European / Spanish comma-decimal formatting
      e.g. '1.250,50 €', '$ 1.250,50', '250,75'
    - Standard US format
      e.g. '$1,250.50'
    Returns Decimal with 2 decimal places, or None.
    """
    if not text:
        return None

    # Step 1: Translate Devanagari numerals to ASCII
    clean = translate_devanagari_numerals(text)
    clean = normalize_spanish_text(clean)

    # Step 2: Strip currency symbols, currency codes, and whitespace
    stripped = RE_CURRENCY_SYMBOLS.sub("", clean).strip()

    # Find the numeric chunk with commas, periods, or digits
    m = re.search(r"[0-9][0-9.,\s]*[0-9]|[0-9]", stripped)
    if not m:
        return None

    raw_num = m.group(0).replace(" ", "")

    # Step 3: Handle European/Spanish comma decimal separator
    # e.g., '1.250,50' or '250,50' (dot as thousands, comma as decimal)
    if "," in raw_num and "." in raw_num:
        last_comma = raw_num.rfind(",")
        last_dot = raw_num.rfind(".")
        if last_comma > last_dot:
            # European format: 1.250,50 -> 1250.50
            standardized = raw_num.replace(".", "").replace(",", ".")
        else:
            # US / Indian format: 1,250.50 -> 1250.50
            standardized = raw_num.replace(",", "")
    elif "," in raw_num:
        # Check if comma is decimal separator (e.g. '250,50' -> 2 fractional digits)
        parts = raw_num.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            standardized = f"{parts[0]}.{parts[1]}"
        else:
            # Thousands separator (e.g. Indian lakh '1,50,000')
            standardized = raw_num.replace(",", "")
    else:
        standardized = raw_num

    try:
        dec = Decimal(standardized)
        return dec.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def enrich_text_multilingual(text: str) -> dict[str, Any]:
    """
    Enriches raw text with script classification, Devanagari numeral translation,
    and extracted date/currency values.
    """
    dom_script, dist = detect_script(text)
    ascii_num_text = translate_devanagari_numerals(text)
    parsed_date = parse_multilingual_date(text)
    parsed_amt = parse_multilingual_amount(text)
    curr_code, curr_sym = detect_currency(text)

    return {
        "raw_text": text,
        "normalized_text": normalize_spanish_text(ascii_num_text),
        "dominant_script": dom_script,
        "script_distribution": dist,
        "extracted_date": parsed_date.isoformat() if parsed_date else None,
        "extracted_amount": float(parsed_amt) if parsed_amt is not None else None,
        "currency_code": curr_code,
        "currency_symbol": curr_sym,
    }


# ── Multilingual Currency Detection ──────────────────────────────────────────

RE_CURRENCY_INR = re.compile(r"(?:₹|\bINR\b|\bRs\.?\b|रु|\bRUPAY\b|\bRUPEES?\b)", re.IGNORECASE)
RE_CURRENCY_EUR = re.compile(r"(?:€|\bEUR\b|\bEUROS?\b)", re.IGNORECASE)
RE_CURRENCY_GBP = re.compile(r"(?:£|\bGBP\b|\bPOUNDS?\b|\bLIBRAS?\b)", re.IGNORECASE)
RE_CURRENCY_JPY = re.compile(r"(?:¥|\bJPY\b|\bCNY\b|\bYEN\b|\bYUAN\b)", re.IGNORECASE)
RE_CURRENCY_USD = re.compile(r"(?:\$|\bUSD\b|\bDOLLARS?\b)", re.IGNORECASE)


def detect_currency(text: str) -> tuple[str, str]:
    """
    Detects ISO-4217 currency code and symbol from document text or token (ADR-018).
    Returns (iso_code, symbol), e.g. ('INR', '₹'), ('EUR', '€'), ('USD', '$'), ('GBP', '£').
    Defaults to ('USD', '$') when no currency indicator is detected.
    """
    if not text:
        return "USD", "$"

    clean = normalize_spanish_text(text)

    # Priority matching: INR, EUR, GBP, JPY, USD
    if RE_CURRENCY_INR.search(clean):
        return "INR", "₹"
    if RE_CURRENCY_EUR.search(clean):
        return "EUR", "€"
    if RE_CURRENCY_GBP.search(clean):
        return "GBP", "£"
    if RE_CURRENCY_JPY.search(clean):
        return "JPY", "¥"
    if RE_CURRENCY_USD.search(clean):
        return "USD", "$"

    return "USD", "$"


# ── Multilingual Clinical Terminology & Entity Linking ────────────────────────

CLINICAL_TERMINOLOGY_REGISTRY: list[dict[str, Any]] = [
    {
        "icd10": None,
        "standard_name": "Osteoarthritis of unspecified knee",
        "code_specificity": "LATERALITY_UNSPECIFIED",
        "condition_group": "Knee Osteoarthritis",
        "anatomy": "Knee Joint",
        "cpt_codes": ["27447", "27446", "29881"],
        "terms": [
            # Hindi Devanagari
            "घुटने का ऑस्टियोआर्थराइटिस",
            "घुटने की ऑस्टियोआर्थराइटिस",
            "घुटने का गठिया",
            "घुटने की संधि विकृति",
            "घुटने का संधिवात",
            "संधिवात",
            "ऑस्टियोआर्थराइटिस",
            "संधिशोथ",
            "घुटने का दर्द",
            # Spanish
            "artrosis de rodilla",
            "gonartrosis",
            "gonartrosis de rodilla",
            "osteoartritis de rodilla",
            "gonartrosis primaria",
            "gonartrosis bilateral",
            "gonartrosis unilateral",
            "desgaste de rodilla",
            # English
            "knee osteoarthritis",
            "osteoarthritis of knee",
            "degenerative joint disease of knee",
            "gonarthrosis",
        ],
    },
    {
        "icd10": None,
        "standard_name": "Osteoarthritis of unspecified hip",
        "code_specificity": "LATERALITY_UNSPECIFIED",
        "condition_group": "Hip Osteoarthritis",
        "anatomy": "Hip Joint",
        "cpt_codes": ["27130"],
        "terms": [
            "कूल्हे का ऑस्टियोआर्थराइटिस",
            "कूल्हे का गठिया",
            "coxartrosis",
            "artrosis de cadera",
            "osteoartritis de cadera",
            "hip osteoarthritis",
        ],
    },
    {
        "icd10": "H25.9",
        "standard_name": "Age-related cataract, unspecified",
        "condition_group": "Cataract",
        "anatomy": "Eye / Lens",
        "cpt_codes": ["66984", "66982"],
        "terms": [
            "मोतियाबिंद",
            "सफेद मोतिया",
            "catarata",
            "catarata senil",
            "cataract",
            "senile cataract",
        ],
    },
    {
        "icd10": "K80.00",
        "standard_name": "Calculus of gallbladder with acute cholecystitis without obstruction",
        "condition_group": "Gallbladder / Cholelithiasis",
        "anatomy": "Gallbladder / Biliary Tract",
        "cpt_codes": ["47562", "47563"],
        "terms": [
            "पित्ताशय की पथरी",
            "पित्त की पथरी",
            "पित्त पथरी",
            "पित्ताशय शोथ",
            "colelitiasis",
            "litiasis biliar",
            "colecistitis",
            "cholelithiasis",
            "cholecystitis",
            "gallstones",
        ],
    },
    {
        "icd10": "K35.80",
        "standard_name": "Unspecified acute appendicitis",
        "condition_group": "Appendicitis",
        "anatomy": "Appendix / Lower Right Abdomen",
        "cpt_codes": ["44970", "44950"],
        "terms": [
            "अपेंडिसाइटिस",
            "आंत्रपुच्छ शोथ",
            "apendicitis",
            "apendicitis aguda",
            "acute appendicitis",
            "appendicitis",
        ],
    },
    {
        "icd10": "K40.90",
        "standard_name": "Unilateral inguinal hernia, without obstruction or gangrene, not specified as recurrent",
        "condition_group": "Inguinal Hernia",
        "anatomy": "Groin / Inguinal",
        "cpt_codes": ["49505"],
        "terms": [
            "हर्निया",
            "हर्निया का रोग",
            "hernia inguinal",
            "inguinal hernia",
        ],
    },
    {
        "icd10": "M51.26",
        "standard_name": "Other intervertebral disc displacement, lumbar region",
        "condition_group": "Lumbar Disc Displacement",
        "anatomy": "Lumbar Spine",
        "cpt_codes": ["63030"],
        "terms": [
            "स्लिप डिस्क",
            "कमर दर्द",
            "साइटिका",
            "hernia discal lumbar",
            "hernia de disco",
            "lumbar disc displacement",
            "herniated disc",
        ],
    },
    {
        "icd10": "I25.10",
        "standard_name": "Atherosclerotic heart disease of native coronary artery without angina pectoris",
        "condition_group": "Coronary Artery Disease",
        "anatomy": "Coronary Arteries / Cardiovascular",
        "cpt_codes": ["33533", "33510", "92928"],
        "terms": [
            "हृदय रोग",
            "कोरोनरी धमनी रोग",
            "दिल का दौरा",
            "enfermedad coronaria",
            "cardiopatía isquémica",
            "coronary artery disease",
            "ischemic heart disease",
        ],
    },
    {
        "icd10": "I10",
        "standard_name": "Essential (primary) hypertension",
        "condition_group": "Hypertension",
        "anatomy": "Cardiovascular / Systemic",
        "cpt_codes": ["99213", "99214"],
        "terms": [
            "उच्च रक्तचाप",
            "हाई ब्लड प्रेशर",
            "hipertensión",
            "hipertensión arterial",
            "hypertension",
            "high blood pressure",
        ],
    },
    {
        "icd10": "E11.9",
        "standard_name": "Type 2 diabetes mellitus without complications",
        "condition_group": "Type 2 Diabetes",
        "anatomy": "Endocrine / Metabolic",
        "cpt_codes": ["99213", "99214"],
        "terms": [
            "मधुमेह",
            "डायबिटीज",
            "diabetes",
            "diabetes tipo 2",
            "diabetes mellitus tipo 2",
            "type 2 diabetes",
        ],
    },
]


def _extract_clinical_indications_with_llm(
    text: str,
    client: Any,
) -> list[dict[str, Any]]:
    """
    Invokes Groq LLM to translate and extract clinical diagnostic indications
    from open-ended multilingual medical documentation (Hindi, Spanish, etc.)
    strictly adhering to Rules 10, 11, 12, 14.
    """
    if not text or not client:
        return []

    is_avail_fn = getattr(client, "is_available", None)
    if callable(is_avail_fn) and not is_avail_fn():
        return []

    try:
        from medishield.core.llm import wrap_untrusted_content
    except ImportError:
        def wrap_untrusted_content(s: str) -> str:
            return s

    system_prompt = (
        "You are an authoritative multilingual clinical coding and medical translation specialist for MediShield.\n"
        "Your task is to identify all diagnostic medical conditions, pathologies, or clinical symptoms "
        "mentioned in the text (which may be in Hindi, Spanish, English, or other languages), translate each into standard clinical English, "
        "and determine its canonical ICD-10 diagnostic code (e.g. M17.11, K80.00, I10, M51.26, S82.001A).\n\n"
        "STRICT CONSTRAINTS (Rules 10 & 14):\n"
        "- Do NOT make business decisions (no APPROVE, REJECT, or ESCALATE).\n"
        "- Only extract diagnoses grounded in the text. Do NOT hallucinate.\n"
        "- Return ONLY a JSON object conforming to this schema:\n"
        "{\n"
        '  "clinical_entities": [\n'
        "    {\n"
        '      "source_term": "<exact term or phrase as written in document>",\n'
        '      "standard_name": "<clinical English condition name>",\n'
        '      "condition_group": "<condition category, e.g. Knee Osteoarthritis, Cholecystitis>",\n'
        '      "icd10_code": "<valid 3-7 character ICD-10 code>",\n'
        '      "anatomy": "<affected body part or anatomical system>",\n'
        '      "confidence": <float 0.0 to 1.0>\n'
        "    }\n"
        "  ]\n"
        "}"
    )
    user_prompt = f"Clinical Document Excerpt:\n{wrap_untrusted_content(text[:4000])}"

    try:
        res = client.generate_json(prompt=user_prompt, system_prompt=system_prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Multilingual LLM clinical extraction encountered error: %s", exc)
        return []

    entities = res.get("clinical_entities", []) if isinstance(res, dict) else []
    extracted: list[dict[str, Any]] = []
    text_lower = text.lower()

    for item in entities:
        if not isinstance(item, dict):
            continue
        icd = str(item.get("icd10_code", "")).strip().upper()
        # Rule 11: Deterministic format validation on LLM output
        if not re.fullmatch(r"^[A-TV-Z][0-9]{2}(?:\.[0-9]{1,4})?$", icd):
            continue

        source_term = str(item.get("source_term", "")).strip()
        # Rule 12: Grounding & Provenance check - verify source term appears in text
        if source_term and source_term.lower() not in text_lower:
            words = [w for w in re.split(r"\s+", source_term.lower()) if len(w) > 2]
            if words and not any(w in text_lower for w in words):
                continue

        script, _ = detect_script(source_term or text)
        conf = float(item.get("confidence", 0.90))
        conf = max(0.0, min(1.0, conf))

        extracted.append({
            "icd10_code": icd,
            "matched_term": source_term or item.get("standard_name", ""),
            "standard_name": item.get("standard_name", ""),
            "condition_group": item.get("condition_group", "General Pathology"),
            "anatomy": item.get("anatomy", "Unspecified"),
            "cpt_codes": [],
            "script": script,
            "confidence": conf,
            "extraction_method": "LLM_MULTILINGUAL_TRANSLATION",
        })

    return extracted


def extract_multilingual_clinical_indications(
    text: str,
    llm_client: Any | None = None,
    enable_llm_fallback: bool = True,
) -> list[dict[str, Any]]:
    """
    Extracts clinical diagnostic indications and mapped ICD-10 codes from multilingual
    clinical narrative or discharge summary text (Devanagari, Spanish, English) (ADR-018).

    Architecture (Rules 10, 11, 12, 14):
    - Tier 1: Fast deterministic matching against CLINICAL_TERMINOLOGY_REGISTRY.
    - Tier 2: Hybrid LLM-assisted clinical translation and ICD-10 extraction when
      deterministic logic is insufficient (e.g. open-vocabulary vernacular text).
    - Preserves provenance of the exact matching text span.
    """
    if not text:
        return []

    norm_text = normalize_spanish_text(text)
    lower_text = norm_text.lower()

    extracted: list[dict[str, Any]] = []
    seen_concepts: set[str] = set()

    # Tier 1: Deterministic matching against ontology registry
    for entry in CLINICAL_TERMINOLOGY_REGISTRY:
        icd = entry.get("icd10")
        concept = str(entry["condition_group"])
        for term in entry["terms"]:
            term_clean = term.strip().lower()
            if term_clean in lower_text:
                if concept not in seen_concepts:
                    seen_concepts.add(concept)
                    script, _ = detect_script(term)
                    extracted.append({
                        "icd10_code": icd,
                        "code_specificity": entry.get("code_specificity"),
                        "code_validation_required": bool(icd),
                        "matched_term": term,
                        "standard_name": entry["standard_name"],
                        "condition_group": entry["condition_group"],
                        "anatomy": entry["anatomy"],
                        "cpt_codes": entry["cpt_codes"],
                        "script": script,
                        "confidence": 0.96 if script == "DEVANAGARI" else 0.95,
                        "extraction_method": "DETERMINISTIC_REGISTRY",
                    })
                break

    # Tier 2: LLM Multilingual Extraction Gateway (Rule 11: where deterministic logic is not sufficient)
    # Triggered when non-English script, Spanish clinical vocabulary, or explicit clinical narrative is present
    should_call_llm = enable_llm_fallback and (
        has_devanagari(text)
        or any(k in lower_text for k in ("diagnóstico", "diagnostico", "enfermedad", "dolor", "cirugía", "artrosis", "alta", "ingreso", "patología"))
        or (len(extracted) == 0 and any(k in lower_text for k in ("diagnos", "assessment", "impression", "discharge summary")) and len(text.strip()) > 30)
    )

    if should_call_llm:
        client = llm_client
        if client is None and enable_llm_fallback:
            try:
                from medishield.core.llm import default_groq_client
                if default_groq_client and default_groq_client.is_available():
                    client = default_groq_client
            except Exception:  # noqa: BLE001
                client = None

        if client is not None:
            llm_items = _extract_clinical_indications_with_llm(text, client)
            for item in llm_items:
                c = item["icd10_code"]
                if c and c not in seen_concepts:
                    seen_concepts.add(c)
                    extracted.append(item)

    return extracted


def extract_multilingual_icd_codes(
    text: str,
    llm_client: Any | None = None,
    enable_llm_fallback: bool = True,
) -> list[str]:
    """
    Extracts ICD-10 codes from text using alphanumeric patterns,
    multilingual terminology entity linking, and hybrid LLM translation (ADR-018).
    """
    if not text:
        return []

    codes: list[str] = []
    # 1. Alphanumeric ICD regex (translating Devanagari numerals if present)
    translated = translate_devanagari_numerals(text)
    for m in re.finditer(r"\b([A-TV-Z][0-9]{2}(?:\.[0-9]{1,4})?)\b", translated):
        c = m.group(1).upper()
        if c not in codes:
            codes.append(c)

    # 2. Multilingual terminology & hybrid LLM linking
    for item in extract_multilingual_clinical_indications(
        text,
        llm_client=llm_client,
        enable_llm_fallback=enable_llm_fallback,
    ):
        icd = item["icd10_code"]
        if icd and icd not in codes:
            codes.append(icd)

    return codes


