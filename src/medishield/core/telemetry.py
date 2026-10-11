import re
import sys
from typing import Any

import logfire

from medishield.core.config import settings

# HIPAA Safeguards: Patterns for scrubbing Protected Health Information (PHI)
SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
SSN_UNFORMATTED_REGEX = re.compile(r"\b\d{9}\b")
PHI_REPLACEMENT = "[REDACTED_PHI]"

_is_configured = False


def scrub_phi(text: str) -> str:
    """Scrub sensitive PHI identifiers like SSNs from string values."""
    if not isinstance(text, str):
        return text
    text = SSN_REGEX.sub(PHI_REPLACEMENT, text)
    return SSN_UNFORMATTED_REGEX.sub(PHI_REPLACEMENT, text)


def scrub_dict_phi(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively scrub PHI patterns and sensitive keys from dictionary data."""
    sensitive_keys = {
        "ssn",
        "ssn_last4",
        "social_security_number",
        "date_of_birth",
        "dob",
        "patient_name",
        "first_name",
        "last_name",
        "medical_record_number",
        "mrn",
    }
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in sensitive_keys:
            sanitized[key] = PHI_REPLACEMENT
        elif isinstance(value, str):
            sanitized[key] = scrub_phi(value)
        elif isinstance(value, dict):
            sanitized[key] = scrub_dict_phi(value)
        elif isinstance(value, list):
            sanitized[key] = [
                scrub_dict_phi(item)
                if isinstance(item, dict)
                else scrub_phi(item)
                if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


def setup_telemetry() -> None:
    """
    Initialize Logfire observability with HIPAA-compliant defaults.
    Operates in offline console mode when no token is configured.
    """
    global _is_configured
    if _is_configured:
        return

    if not settings.LOGFIRE_TOKEN:
        # Avoid hanging/connecting if no token is provided in local dev
        _is_configured = True
        return

    logfire.configure(
        service_name=settings.LOGFIRE_SERVICE_NAME,
        environment=settings.ENVIRONMENT,
        token=settings.LOGFIRE_TOKEN,
        send_to_logfire=settings.LOGFIRE_SEND_TO_LOGFIRE,
    )
    _is_configured = True

def instrument_db(engine: Any) -> None:
    """Instrument a SQLAlchemy engine with Logfire tracing."""
    setup_telemetry()
    if not settings.LOGFIRE_TOKEN:
        return
    try:
        logfire.instrument_sqlalchemy(engine)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"Failed to instrument SQLAlchemy engine: {exc}\n")

def instrument_fastapi(app: Any) -> None:
    """Instrument a FastAPI application with Logfire tracing."""
    setup_telemetry()
    if not settings.LOGFIRE_TOKEN:
        return
    try:
        logfire.instrument_fastapi(app)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"Failed to instrument FastAPI app: {exc}\n")

