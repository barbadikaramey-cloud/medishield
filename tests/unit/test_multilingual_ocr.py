"""
Unit Tests for Multilingual Document OCR (Hindi and Spanish).
Specification Reference: ADR-017 § Feature 4.
"""

from datetime import date
from decimal import Decimal

from medishield.ocr.multilingual import (
    detect_script,
    enrich_text_multilingual,
    has_devanagari,
    normalize_spanish_text,
    parse_multilingual_amount,
    parse_multilingual_date,
    translate_devanagari_numerals,
)


def test_devanagari_script_detection() -> None:
    """Verifies identification of Devanagari and Latin scripts."""
    hindi_text = "अस्पताल डिस्चार्ज सारांश: मरीज का नाम राहुल शर्मा"
    latin_text = "Hospital Discharge Summary: Patient Name John Doe"
    mixed_text = "Hospital Bill: मरीज का नाम राहुल शर्मा (ID: PT_9012)"

    assert has_devanagari(hindi_text) is True
    assert has_devanagari(latin_text) is False
    assert has_devanagari(mixed_text) is True

    dom_hi, _ = detect_script(hindi_text)
    assert dom_hi == "DEVANAGARI"

    dom_lat, _ = detect_script(latin_text)
    assert dom_lat == "LATIN"

    dom_mix, dist = detect_script(mixed_text)
    assert dom_mix == "MIXED"
    assert dist["devanagari"] > 0.15
    assert dist["latin"] > 0.15


def test_devanagari_numeral_translation() -> None:
    """Verifies deterministic translation of Devanagari numerals (०-९) to ASCII digits (0-9)."""
    devanagari_date = "दिनांक: १५/१०/२०२६"
    translated_date = translate_devanagari_numerals(devanagari_date)
    assert translated_date == "दिनांक: 15/10/2026"

    devanagari_amount = "कुल बिल: ₹ १,५०,०००.५०"
    translated_amount = translate_devanagari_numerals(devanagari_amount)
    assert translated_amount == "कुल बिल: ₹ 1,50,000.50"


def test_spanish_normalization_and_diacritics() -> None:
    """Verifies NFC normalization and preservation of Spanish diacritics and inverted marks."""
    raw_spanish = "Formulario de Reclamación Médica: ¿Autorización previa aprobada? ¡Sí, con éxito!"
    normalized = normalize_spanish_text(raw_spanish)

    assert "Reclamación" in normalized
    assert "Médica" in normalized
    assert "¿" in normalized
    assert "¡" in normalized
    assert "éxito" in normalized


def test_multilingual_date_parsing() -> None:
    """Verifies parsing of Spanish verbal dates, Devanagari dates, and Indian/European formats."""
    # 1. Spanish verbal format
    d1 = parse_multilingual_date("Fecha de admisión: 14 de octubre de 2026")
    assert d1 == date(2026, 10, 14)

    d2 = parse_multilingual_date("Tratamiento finalizado el 5 de mayo del 2025")
    assert d2 == date(2025, 5, 5)

    d3 = parse_multilingual_date("28 de febrero de 2024")
    assert d3 == date(2024, 2, 28)

    # 2. Devanagari numerals date
    d4 = parse_multilingual_date("प्रवेश तिथि: २५/१२/२०२५")
    assert d4 == date(2025, 12, 25)

    # 3. Standard Indian DD/MM/YYYY
    d5 = parse_multilingual_date("Admission Date: 19/08/2026")
    assert d5 == date(2026, 8, 19)


def test_multilingual_amount_parsing() -> None:
    """Verifies parsing of Indian Rupee (Lakh grouping), Devanagari digits, and Spanish comma-decimals."""
    # 1. Indian Rupee with Devanagari numerals & Lakh grouping: ₹ 1,50,000.50
    amt1 = parse_multilingual_amount("कुल अस्पताल शुल्क: ₹ १,५०,०००.५०")
    assert amt1 == Decimal("150000.50")

    # 2. Indian Rupee with Lakh grouping and Rs. symbol
    amt2 = parse_multilingual_amount("Total Billed Amount: Rs. 2,75,000.00")
    assert amt2 == Decimal("275000.00")

    # 3. Spanish European comma-decimal separator with Euro symbol: 1.250,75 €
    amt3 = parse_multilingual_amount("Importe total facturado: 1.250,75 €")
    assert amt3 == Decimal("1250.75")

    # 4. Spanish comma-decimal without thousands dot: 450,50
    amt4 = parse_multilingual_amount("Cargos por consulta: $ 450,50")
    assert amt4 == Decimal("450.50")

    # 5. Standard US format: $15,400.25
    amt5 = parse_multilingual_amount("Total Charges: $15,400.25")
    assert amt5 == Decimal("15400.25")


def test_enrich_text_multilingual() -> None:
    """Verifies full text enrichment pipeline."""
    text = "अस्पताल बिल (Hospital Invoice) कुल राशि: ₹ १,५०,०००.०० दिनांक: १५/१०/२०२६"
    enrichment = enrich_text_multilingual(text)

    assert enrichment["dominant_script"] == "MIXED"
    assert enrichment["extracted_amount"] == 150000.00
    assert enrichment["extracted_date"] == "2026-10-15"
    assert "1,50,000.00" in enrichment["normalized_text"]
