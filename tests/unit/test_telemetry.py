import logfire

from medishield.core.telemetry import (
    PHI_REPLACEMENT,
    scrub_dict_phi,
    scrub_phi,
    setup_telemetry,
)


def test_setup_telemetry_offline():
    setup_telemetry()
    with logfire.span("test_offline_span", case_id="CASE-123"):
        logfire.info("Processing case", step="OCR")


def test_scrub_phi_ssn_formats():
    raw_formatted = "Patient SSN is 123-45-6789 in records"
    scrubbed_formatted = scrub_phi(raw_formatted)
    assert PHI_REPLACEMENT in scrubbed_formatted
    assert "123-45-6789" not in scrubbed_formatted

    raw_unformatted = "ID number 987654321 attached"
    scrubbed_unformatted = scrub_phi(raw_unformatted)
    assert PHI_REPLACEMENT in scrubbed_unformatted
    assert "987654321" not in scrubbed_unformatted


def test_scrub_dict_phi_nested():
    data = {
        "case_id": "CASE-20261002-001",
        "first_name": "Jane",
        "last_name": "Doe",
        "ssn": "123-45-6789",
        "nested": {
            "dob": "1980-05-12",
            "notes": "Patient 111-22-3333 had knee surgery",
        },
        "items": [
            {"service": "CPT-99213"},
            {"patient_name": "Jane Doe"},
        ],
    }

    sanitized = scrub_dict_phi(data)
    # Identifiers allowed
    assert sanitized["case_id"] == "CASE-20261002-001"
    # Sensitive keys redacted
    assert sanitized["first_name"] == PHI_REPLACEMENT
    assert sanitized["last_name"] == PHI_REPLACEMENT
    assert sanitized["ssn"] == PHI_REPLACEMENT
    assert sanitized["nested"]["dob"] == PHI_REPLACEMENT
    # Embedded regex match redacted
    assert PHI_REPLACEMENT in sanitized["nested"]["notes"]
    assert "111-22-3333" not in sanitized["nested"]["notes"]
    # List items redacted
    assert sanitized["items"][1]["patient_name"] == PHI_REPLACEMENT
