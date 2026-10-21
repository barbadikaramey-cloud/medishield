"""
Privacy-Safe LangSmith Distributed Tracing Module for MediShield.

PHI-Minimized Operational Telemetry & Boundary Protection:
1. PHI-Minimized Telemetry: Strictly operational summaries only. Prohibits CPT/HCPCS codes,
   ICD-10 diagnosis codes, service dates, patient-linked claim amounts, raw document text,
   images, clinical narratives, model prompts, completions, and unsanitized exception messages.
2. Value & Schema Validation: Enforces strict enums, range checks, and regex boundaries on all
   telemetry values (e.g. status, rule_id_matched, model, provider, duration_ms, retry_count).
3. Ephemeral Root Trace Correlation: Relies on ephemeral root run UUIDs by default.
   Notice: Pseudonymization alone does NOT establish legal de-identification under HIPAA Privacy
   Rule 45 CFR § 164.514 (neither Safe Harbor § 164.514(b) nor Expert Determination § 164.514(a)).
   Persistent case pseudonyms are disabled by default (LANGSMITH_INCLUDE_CASE_PSEUDONYM=False).
4. Local Boundary Sanitization: Both custom spans and automatic model tracers are filtered
   locally before transmission via client-level transform hooks.
5. Zero Overhead when Disabled: No-ops when LANGSMITH_TRACING=False.
6. Fault-Tolerance: Telemetry errors never disrupt business operations or alter decision outcomes.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import inspect
import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from medishield.core.config import settings

logger = logging.getLogger(__name__)

# ── 1. PHI & Secret Scrubbing Regexes ────────────────────────────────────────
SSN_REGEX = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
SSN_UNFORMATTED_REGEX = re.compile(r"\b\d{9}\b")
MEMBER_ID_REGEX = re.compile(r"\bMED-[A-Z0-9-]+\b", re.IGNORECASE)
DATE_REGEX = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})\b")
CURRENCY_REGEX = re.compile(r"(?:\$\s*\d+(?:,\d{3})*(?:\.\d{2})?|\b\d+\.\d{2}\b)")
CPT_HCPCS_REGEX = re.compile(r"\b(?:\d{5}|[A-Z]\d{4})\b")
ICD10_REGEX = re.compile(r"\b[A-TV-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?\b")
SECRET_KEY_REGEX = re.compile(
    r"\b(?:gsk_|sk-|Bearer\s+|AIza)[A-Za-z0-9_\-]{16,}\b", re.IGNORECASE
)
REDACTED_TEXT = "[REDACTED]"

# Explicitly prohibited sensitive keys that must never appear in trace payloads
BANNED_KEYS = {
    "ssn",
    "ssn_last4",
    "social_security_number",
    "dob",
    "date_of_birth",
    "patient_name",
    "first_name",
    "last_name",
    "member_id",
    "policy_id",
    "policy_number",
    "cpt",
    "cpt_code",
    "cpt_codes",
    "hcpcs",
    "icd10",
    "icd10_codes",
    "diagnosis_codes",
    "claim_amount",
    "total_charge",
    "billed_amount",
    "amount",
    "service_date",
    "date_of_service",
    "dos",
    "raw_text",
    "ocr_text",
    "content",
    "prompt",
    "messages",
    "completion",
    "choices",
    "image",
    "image_bytes",
    "images",
    "img",
    "image_data",
    "image_base64",
    "data_url",
    "raw_bytes",
    "content_bytes",
    "file_bytes",
    "picture",
    "photo",
    "file_name",
    "filename",
    "file_path",
    "storage_path",
    "secret_key",
    "api_key",
    "token",
    "password",
    "raw_case_id",
    "case_id",
    "clinical_summary",
    "discharge_summary",
    "notes",
}

# ── 2. Strict Telemetry Value Enums & Patterns ──────────────────────────────
STATUS_ENUM = {"SUCCESS", "FAILED", "SKIPPED"}
RUN_TYPE_ENUM = {"chain", "tool", "llm", "retriever", "prompt"}
VERDICT_ENUM = {"PASS", "FAIL", "INDETERMINATE", "CONFLICT", "UNAVAILABLE", "REVIEW"}
DECISION_ENUM = {"APPROVE", "REJECT", "ESCALATE"}
ESCALATION_SUBREASON_ENUM = {
    "MISSING_INFORMATION",
    "HUMAN_REVIEW",
    "FRAUD_REVIEW",
    "SECURITY_REVIEW",
    "TECHNICAL_FAILURE",
    "AMBIGUITY",
    "RECOMMEND_DENIAL",
}
RISK_LEVEL_ENUM = {"LOW", "REVIEW_REQUIRED", "MEDIUM", "HIGH"}
ERROR_CATEGORY_ENUM = {
    "RATE_LIMIT",
    "TIMEOUT",
    "AUTH_ERROR",
    "VALIDATION_ERROR",
    "DB_ERROR",
    "PROVIDER_ERROR",
    "INTERNAL_ERROR",
}
PROVIDER_ENUM = {"groq", "google", "huggingface", "local", "in-memory", "system"}
ALLOWED_MODELS = {
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "mistralai/Pixtral-12B-2409",
}

IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{1,64}$")
MODEL_REGEX = re.compile(r"^[a-zA-Z0-9_\-\./]{1,64}$")
RULE_ID_REGEX = re.compile(r"^RULE-[A-Z0-9_-]{1,32}$")
HMAC_PSEUDONYM_REGEX = re.compile(r"^[a-f0-9]{16}$")
DECISIVE_FACTORS_REGEX = re.compile(r"^[a-zA-Z0-9_,-]{1,64}$")
DIAGNOSTIC_REASON_REGEX = re.compile(r"^[A-Z0-9_]{1,64}$")

# Explicit allowlist of permitted operational telemetry fields
ALLOWED_TRACE_KEYS = {
    "component_name",
    "operation_name",
    "run_type",
    "status",
    "error_category",
    "error_type",
    "duration_ms",
    "retry_count",
    "document_count",
    "ocr_span_count",
    "service_line_count",
    "retrieved_chunk_count",
    "arithmetic_valid",
    "provenance_integrity_score",
    "verdict",
    "rule_id_matched",
    "decision",
    "pseudonymous_case_id",
    "token_usage",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "confidence",
    "member_matched",
    "is_complete",
    "classification_completed",
    "model",
    "provider",
    "error",
    # Step 3B Decision & Specialist Diagnostics
    "escalation_sub_reason",
    "risk_level",
    "fraud_score",
    "approval_safety_passed",
    "decision_summary",
    "decisive_factors",
    "blocking_condition_count",
    "tamper_detected",
    "id_expired",
    "identity_flag_count",
    "kyc_reason",
    "schema_valid",
    "validation_error_count",
    "ungrounded_line_count",
    "claims_reason",
    "duplicate_claim_detected",
    "covered",
    "is_exclusion",
    "policy_active_on_service_date",
    "requires_prior_auth",
    "cited_clause_count",
    "identified_exclusion_count",
    "policy_reason",
    "anomaly_count",
    "fraud_signals_count",
}


# ── 3. Keyed HMAC Case Pseudonymization ──────────────────────────────────────
def generate_case_pseudonym(case_id: str | None, secret_key: str | None = None) -> str:
    """
    Generates a cryptographically stable pseudonymous case correlation identifier
    using HMAC-SHA256 truncated to 16 hex characters.
    Never exposes raw case ID or secret key in trace payloads.
    Notice: Pseudonymization alone does NOT establish legal de-identification under HIPAA.
    """
    if not case_id or case_id.strip() in ("", "UNKNOWN", "NONE"):
        return "ANONYMOUS"
    key = (secret_key or settings.SECRET_KEY).encode("utf-8")
    digest = hmac.new(key, case_id.strip().encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:16]


# ── 4. Value Validation and Range Checking ───────────────────────────────────
def validate_telemetry_field(key: str, value: Any) -> tuple[bool, Any]:
    """
    Validates both the telemetry field name and its value.
    Enforces strict types, ranges, enums, and regexes to prevent arbitrary
    sensitive strings from being smuggled in permitted fields.
    """
    k_lower = key.lower()
    if k_lower not in ALLOWED_TRACE_KEYS:
        return False, None

    # Never allow raw bytes or base64 encoded images in telemetry
    if isinstance(value, (bytes, bytearray, memoryview)):
        return False, None

    if isinstance(value, str):
        val_lower = value.lower()
        if (
            val_lower.startswith("data:image/")
            or ";base64," in val_lower
            or "\x89png" in val_lower
            or "\xff\xd8\xff" in val_lower
            or "gif8" in val_lower
        ):
            return False, None

    if k_lower == "status":
        if isinstance(value, str) and value.upper() in STATUS_ENUM:
            return True, value.upper()
        return False, None

    if k_lower == "run_type":
        if isinstance(value, str) and value.lower() in RUN_TYPE_ENUM:
            return True, value.lower()
        return False, None

    if k_lower == "verdict":
        if isinstance(value, str) and value.upper() in VERDICT_ENUM:
            return True, value.upper()
        return False, None

    if k_lower == "decision":
        if isinstance(value, str) and value.upper() in DECISION_ENUM:
            return True, value.upper()
        return False, None

    if k_lower == "error_category":
        if isinstance(value, str) and value.upper() in ERROR_CATEGORY_ENUM:
            return True, value.upper()
        return False, None

    if k_lower == "error_type":
        if isinstance(value, str) and IDENTIFIER_REGEX.match(value):
            return True, value
        return False, None

    if k_lower == "provider":
        if isinstance(value, str) and value.lower() in PROVIDER_ENUM:
            return True, value.lower()
        return False, None

    if k_lower == "model":
        if isinstance(value, str):
            val_clean = value.strip()
            if val_clean in ALLOWED_MODELS or (
                MODEL_REGEX.match(val_clean)
                and not any(
                    p.search(val_clean)
                    for p in (SSN_REGEX, DATE_REGEX, CURRENCY_REGEX, MEMBER_ID_REGEX)
                )
            ):
                return True, val_clean
        return False, None

    if k_lower == "rule_id_matched":
        if isinstance(value, str) and RULE_ID_REGEX.match(value.strip()):
            return True, value.strip()
        return False, None

    if k_lower in ("component_name", "operation_name"):
        if isinstance(value, str) and IDENTIFIER_REGEX.match(value.strip()):
            return True, value.strip()
        return False, None

    if k_lower == "duration_ms":
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0.0 <= value <= 86_400_000.0
        ):
            return True, round(float(value), 2)
        return False, None

    if k_lower == "retry_count":
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 50:
            return True, value
        return False, None

    if k_lower in ("document_count", "service_line_count", "retrieved_chunk_count"):
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000:
            return True, value
        return False, None

    if k_lower == "ocr_span_count":
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and 0 <= value <= 100_000
        ):
            return True, value
        return False, None

    if k_lower in ("confidence", "provenance_integrity_score"):
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0.0 <= value <= 1.0
        ):
            return True, round(float(value), 4)
        return False, None

    if k_lower in ("arithmetic_valid", "member_matched", "is_complete", "classification_completed"):
        if isinstance(value, bool):
            return True, value
        return False, None

    if k_lower == "pseudonymous_case_id":
        if not settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM:
            return False, None
        if isinstance(value, str) and (
            value == "ANONYMOUS" or HMAC_PSEUDONYM_REGEX.match(value)
        ):
            return True, value
        return False, None

    if k_lower in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return True, value
        return False, None

    if k_lower == "token_usage":
        if isinstance(value, dict):
            valid_usage: dict[str, int] = {}
            for tk in ("prompt_tokens", "completion_tokens", "total_tokens"):
                if (
                    tk in value
                    and isinstance(value[tk], int)
                    and not isinstance(value[tk], bool)
                    and value[tk] >= 0
                ):
                    valid_usage[tk] = value[tk]
            if valid_usage:
                return True, valid_usage
        return False, None

    if k_lower == "error":
        if isinstance(value, str):
            cat = normalize_error_category(value)
            m = re.search(r"\b([A-Za-z0-9_]+(?:Error|Exception))\b", value)
            exc_type = m.group(1) if m else "Error"
            return True, f"{cat}: {exc_type}"
        return False, None

    if k_lower == "escalation_sub_reason":
        if isinstance(value, str) and value.upper() in ESCALATION_SUBREASON_ENUM:
            return True, value.upper()
        return False, None

    if k_lower == "risk_level":
        if isinstance(value, str) and value.upper() in RISK_LEVEL_ENUM:
            return True, value.upper()
        return False, None

    if k_lower == "fraud_score":
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0.0 <= value <= 1.0
        ):
            return True, round(float(value), 4)
        return False, None

    if k_lower in (
        "approval_safety_passed",
        "tamper_detected",
        "id_expired",
        "schema_valid",
        "duplicate_claim_detected",
        "fraud_review_required",
        "covered",
        "is_exclusion",
        "policy_active_on_service_date",
        "requires_prior_auth",
    ):
        if isinstance(value, bool):
            return True, value
        return False, None

    if k_lower in (
        "blocking_condition_count",
        "identity_flag_count",
        "validation_error_count",
        "ungrounded_line_count",
        "cited_clause_count",
        "identified_exclusion_count",
        "anomaly_count",
        "fraud_signals_count",
    ):
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000:
            return True, value
        return False, None

    if k_lower in ("kyc_reason", "claims_reason", "policy_reason"):
        if isinstance(value, str) and DIAGNOSTIC_REASON_REGEX.match(value.strip().upper()):
            return True, value.strip().upper()
        return False, None

    if k_lower == "decisive_factors":
        if isinstance(value, str) and DECISIVE_FACTORS_REGEX.match(value.strip()):
            return True, value.strip()
        if isinstance(value, (list, tuple, set)):
            clean_items = [str(x).strip().lower() for x in value if isinstance(x, str)]
            joined = ",".join(clean_items)
            if DECISIVE_FACTORS_REGEX.match(joined):
                return True, joined
        return False, None

    if k_lower == "decision_summary":
        if isinstance(value, str):
            clean_str = value.strip()
            if 0 < len(clean_str) <= 256:
                scrubbed = scrub_phi_text(clean_str)
                has_leak = any(
                    p.search(scrubbed)
                    for p in (SSN_REGEX, MEMBER_ID_REGEX, DATE_REGEX, CURRENCY_REGEX, SECRET_KEY_REGEX)
                )
                if not has_leak:
                    return True, scrubbed
        return False, None

    return False, None


# ── 5. Local Scrubbing & Boundary Sanitization ───────────────────────────────
def scrub_phi_text(text: str) -> str:
    """Scrub sensitive patterns (SSNs, dates, amounts, member IDs, secrets) from text."""
    if not isinstance(text, str):
        return text
    text = SECRET_KEY_REGEX.sub(REDACTED_TEXT, text)
    text = SSN_REGEX.sub(REDACTED_TEXT, text)
    text = SSN_UNFORMATTED_REGEX.sub(REDACTED_TEXT, text)
    text = MEMBER_ID_REGEX.sub(REDACTED_TEXT, text)
    text = DATE_REGEX.sub(REDACTED_TEXT, text)
    return CURRENCY_REGEX.sub(REDACTED_TEXT, text)


def scrub_phi_dict(data: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively scrub dictionary payloads: drop banned keys and sanitize string values.
    """
    if not isinstance(data, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in BANNED_KEYS:
            continue
        if isinstance(value, (bytes, bytearray, memoryview)):
            continue
        if isinstance(value, str):
            val_lower = value.lower()
            if val_lower.startswith("data:image/") or ";base64," in val_lower:
                continue
            sanitized[key] = scrub_phi_text(value)
        elif isinstance(value, dict):
            sanitized[key] = scrub_phi_dict(value)
        elif isinstance(value, list):
            sanitized[key] = [
                scrub_phi_dict(item)
                if isinstance(item, dict)
                else scrub_phi_text(item)
                if isinstance(item, str)
                else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


def sanitize_trace_payload(data: Any) -> dict[str, Any]:
    """
    Enforces PHI minimization and value validation on inbound or outbound trace payloads.
    Converts domain structures into operational counts and validates field contracts.
    """
    if not isinstance(data, dict):
        return {}

    sanitized: dict[str, Any] = {}

    # 1. Transform raw case_id to pseudonymous_case_id ONLY if enabled
    if settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM and "case_id" in data:
        pseudo = generate_case_pseudonym(str(data["case_id"]))
        is_valid, val = validate_telemetry_field("pseudonymous_case_id", pseudo)
        if is_valid:
            sanitized["pseudonymous_case_id"] = val

    # 2. Extract safe operational summaries from known domain structures
    if "documents" in data and isinstance(data["documents"], list):
        is_valid, val = validate_telemetry_field("document_count", len(data["documents"]))
        if is_valid:
            sanitized["document_count"] = val

    if "lines" in data and isinstance(data["lines"], list):
        is_valid, val = validate_telemetry_field("service_line_count", len(data["lines"]))
        if is_valid:
            sanitized["service_line_count"] = val
    elif "service_lines" in data and isinstance(data["service_lines"], list):
        is_valid, val = validate_telemetry_field(
            "service_line_count", len(data["service_lines"])
        )
        if is_valid:
            sanitized["service_line_count"] = val

    if "ocr_spans" in data and isinstance(data["ocr_spans"], list):
        is_valid, val = validate_telemetry_field("ocr_span_count", len(data["ocr_spans"]))
        if is_valid:
            sanitized["ocr_span_count"] = val

    # 3. Extract safe token usage
    if "usage" in data and isinstance(data["usage"], dict):
        is_valid, val = validate_telemetry_field("token_usage", data["usage"])
        if is_valid:
            sanitized["token_usage"] = val

    # 4. Validate and filter remaining keys against strict contracts
    for k, v in data.items():
        k_lower = k.lower()
        if k_lower in BANNED_KEYS:
            continue
        is_valid, validated_val = validate_telemetry_field(k, v)
        if is_valid:
            sanitized[k_lower] = validated_val

    return sanitized


def sanitize_trace_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Ensures metadata strictly follows allowlist and value validation."""
    if not metadata or not isinstance(metadata, dict):
        return {}
    return sanitize_trace_payload(metadata)


# ── 6. Normalized Error Classification ──────────────────────────────────────
def normalize_error_category(exc: BaseException | str) -> str:
    """Classify exceptions into normalized, non-sensitive operational categories."""
    msg = str(exc).lower()
    exc_type = (
        exc.__class__.__name__.lower()
        if isinstance(exc, BaseException)
        else "error"
    )

    if "429" in msg or "rate limit" in msg or "ratelimit" in msg:
        return "RATE_LIMIT"
    if "timeout" in msg or "timed out" in msg or "timeout" in exc_type:
        return "TIMEOUT"
    if any(code in msg for code in ("401", "403", "unauthorized", "forbidden")):
        return "AUTH_ERROR"
    if (
        "validation" in msg
        or "valueerror" in exc_type
        or "invalid" in msg
        or "assertion" in exc_type
    ):
        return "VALIDATION_ERROR"
    if "database" in msg or "sql" in msg or "postgres" in msg or "sqlite" in msg:
        return "DB_ERROR"
    if any(code in msg for code in ("500", "502", "503", "unavailable", "bad gateway")):
        return "PROVIDER_ERROR"
    return "INTERNAL_ERROR"


# ── 7. Safe Summary Container ────────────────────────────────────────────────
class SafeTraceSummary(BaseModel):
    """Container for operational span metrics conforming to the privacy contract."""

    component_name: str
    operation_name: str
    run_type: str = "chain"
    status: str = "SUCCESS"
    error_category: str | None = None
    error_type: str | None = None
    duration_ms: float = 0.0
    retry_count: int = 0
    document_count: int | None = None
    ocr_span_count: int | None = None
    service_line_count: int | None = None
    retrieved_chunk_count: int | None = None
    is_complete: bool | None = None
    member_matched: bool | None = None
    arithmetic_valid: bool | None = None
    provenance_integrity_score: float | None = None
    verdict: str | None = None
    rule_id_matched: str | None = None
    decision: str | None = None
    pseudonymous_case_id: str | None = None
    token_usage: dict[str, int] | None = None
    model: str | None = None
    provider: str | None = None
    # Step 3B Decision & Specialist Diagnostics
    escalation_sub_reason: str | None = None
    risk_level: str | None = None
    fraud_score: float | None = None
    approval_safety_passed: bool | None = None
    decision_summary: str | None = None
    decisive_factors: str | None = None
    blocking_condition_count: int | None = None
    tamper_detected: bool | None = None
    id_expired: bool | None = None
    identity_flag_count: int | None = None
    kyc_reason: str | None = None
    schema_valid: bool | None = None
    validation_error_count: int | None = None
    ungrounded_line_count: int | None = None
    claims_reason: str | None = None
    duplicate_claim_detected: bool | None = None
    covered: bool | None = None
    is_exclusion: bool | None = None
    policy_active_on_service_date: bool | None = None
    requires_prior_auth: bool | None = None
    cited_clause_count: int | None = None
    identified_exclusion_count: int | None = None
    policy_reason: str | None = None
    anomaly_count: int | None = None
    fraud_signals_count: int | None = None


# ── 8. Client Lifecycle & Global Registration ────────────────────────────────
_langsmith_client: Any | None = None
_client_initialized: bool = False


def get_langsmith_client() -> Any | None:
    """
    Returns the singleton LangSmith Client with client-side sanitization configured.
    Registers the sanitized client across global context and run tree factories.
    Returns None if tracing is disabled or package is unavailable.
    """
    global _langsmith_client, _client_initialized
    if _client_initialized:
        return _langsmith_client

    if not settings.LANGSMITH_TRACING:
        _client_initialized = True
        return None

    try:
        import langsmith.run_helpers as rh
        import langsmith.run_trees as rt
        from langsmith import Client

        client = Client(
            api_key=settings.LANGSMITH_API_KEY,
            api_url=settings.LANGSMITH_ENDPOINT,
            anonymizer=sanitize_trace_payload,
            hide_inputs=sanitize_trace_payload,
            hide_outputs=sanitize_trace_payload,
            hide_metadata=sanitize_trace_metadata,
        )
        # Register sanitized client across all global, context, and run tree registration points
        rt._CLIENT = client
        rh._context._CLIENT.set(client)
        rh._context._GLOBAL_CLIENT = client
        rh._context._GLOBAL_TRACING_ENABLED = True
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        try:
            from langsmith import utils
            if hasattr(utils.get_env_var, "cache_clear"):
                utils.get_env_var.cache_clear()
        except (ImportError, AttributeError):
            pass
        _langsmith_client = client
        _client_initialized = True
        logger.info(
            "LangSmith distributed tracing initialized (project=%s, endpoint=%s, pseudonym_enabled=%s)",
            settings.LANGSMITH_PROJECT,
            settings.LANGSMITH_ENDPOINT or "default",
            settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM,
        )
        return _langsmith_client
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to initialize LangSmith client: %s", exc)
        _client_initialized = True
        return None


def reset_tracing_client() -> None:
    """Reset tracing client state (used for testing)."""
    global _langsmith_client, _client_initialized
    _langsmith_client = None
    _client_initialized = False
    try:
        import langsmith.run_helpers as rh
        import langsmith.run_trees as rt
        from langsmith import utils

        rt._CLIENT = None
        rh._context._GLOBAL_CLIENT = None
        rh._context._CLIENT.set(None)
        rh._context._GLOBAL_TRACING_ENABLED = None
        if hasattr(utils.get_env_var, "cache_clear"):
            utils.get_env_var.cache_clear()
    except (ImportError, AttributeError):
        pass


# ── 9. Span Wrapper & Context Manager ────────────────────────────────────────
class SafeSpan:
    """Handle passed to context manager blocks allowing safe operational updates."""

    def __init__(
        self,
        name: str,
        run_type: str,
        pseudonymous_case_id: str | None = None,
        component: str | None = None,
        operation: str | None = None,
    ) -> None:
        self.name = name
        self.run_type = run_type
        self.pseudonymous_case_id = pseudonymous_case_id
        self.component = component or name
        self.operation = operation or name
        self._summary: dict[str, Any] = {
            "component_name": self.component,
            "operation_name": self.operation,
            "run_type": self.run_type,
            "status": "SUCCESS",
        }
        if pseudonymous_case_id and settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM:
            self._summary["pseudonymous_case_id"] = pseudonymous_case_id

    def set_summary(self, **kwargs: Any) -> None:
        """Record operational metrics conforming to the safe telemetry allowlist."""
        sanitized = sanitize_trace_payload(kwargs)
        self._summary.update(sanitized)

    def record_retry(self, count: int = 1) -> None:
        """Increment retry count safely."""
        current = self._summary.get("retry_count", 0)
        new_count = current + count
        is_valid, val = validate_telemetry_field("retry_count", new_count)
        if is_valid:
            self._summary["retry_count"] = val

    def get_summary(self) -> dict[str, Any]:
        """Return the collected sanitized summary."""
        return dict(self._summary)


# ── 9. Agent & Coordinator Trace Allowlist (LANGSMITH_AGENTS_ONLY) ──────────
AGENT_TRACE_COMPONENTS: set[str] = {
    # Workflow Nodes & Coordinators
    "CaseWorkflowCoordinator",
    "case_workflow_coordinator",
    "workflow_coordinator",
    "case_workflow_execute",
    "ocr_preprocessor_node",
    "split_group_node",
    "classifier_node",
    "case_completeness_node",
    "kyc_agent_node",
    "claims_extraction_node",
    "policy_context_node",
    "policy_coverage_rag_node",
    "fraud_agent_node",
    "clinical_alignment_node",
    "faithfulness_gate_node",
    "decision_engine_node",
    "orchestrator_node",
    # Agent Classes & Subsystems
    "KYCAgent",
    "kyc_agent",
    "ClaimsAgent",
    "claims_agent",
    "FraudAgent",
    "fraud_agent",
    "FraudEngine",
    "fraud_engine",
    "PolicyAgent",
    "policy_agent",
    "PolicyCoverageAgent",
    "PolicyCoverageRAGAgent",
    "PolicyContextAgent",
    "DocumentClassifierAgent",
    "document_classifier_agent",
    "Orchestrator",
    "orchestrator",
    "DecisionEngine",
    "decision_engine",
    "ExplanationGenerator",
    "explanation_agent",
    "FaithfulnessGate",
    "faithfulness_gate",
    # Frontier LLM invocations by agents
    "groq_llm_client",
    "agent_llm_call",
}


def is_agent_trace(name: str, component: str | None = None, run_type: str = "chain") -> bool:
    """
    Determines whether a trace span belongs to an agent or workflow coordination level.
    Used when LANGSMITH_AGENTS_ONLY=True to filter out micro-spans (e.g., storage I/O,
    OCR tool steps, raw DB repository lookups).
    """
    comp = (component or name).strip()
    if comp in AGENT_TRACE_COMPONENTS or name in AGENT_TRACE_COMPONENTS:
        return True
    comp_lower = comp.lower()
    name_lower = name.lower()
    if (
        comp_lower.endswith(("_node", "_agent"))
        or "coordinator" in comp_lower
        or "orchestrat" in comp_lower
        or "decision" in comp_lower
        or name_lower.endswith(("_node", "_agent"))
        or "coordinator" in name_lower
        or "orchestrat" in name_lower
    ):
        return True
    return run_type in ("chain", "llm")


class trace_span:
    """
    PHI-minimized distributed trace span context manager & decorator.

    Features:
    - Zero cost and no-op when LANGSMITH_TRACING=False.
    - Ephemeral root trace ID correlation by default (zero persistent case correlation).
    - Local boundary sanitization before upload.
    - Strict value validation on operational fields.
    - Never disrupts business operations on telemetry failures.
    - Preserves application exceptions without suppression.
    """

    def __init__(
        self,
        name: str,
        run_type: str = "chain",
        case_id: str | None = None,
        component: str | None = None,
        operation: str | None = None,
        inputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.name = scrub_phi_text(name)
        self.run_type = run_type
        self.raw_case_id = case_id
        self.pseudonymous_case_id = (
            generate_case_pseudonym(case_id)
            if case_id and settings.LANGSMITH_INCLUDE_CASE_PSEUDONYM
            else None
        )
        self.component = component or name
        self.operation = operation or name
        self.initial_inputs = sanitize_trace_payload(inputs) if inputs else {}
        if self.pseudonymous_case_id and "pseudonymous_case_id" not in self.initial_inputs:
            self.initial_inputs["pseudonymous_case_id"] = self.pseudonymous_case_id
        self.initial_metadata = sanitize_trace_metadata(metadata)
        if self.pseudonymous_case_id:
            self.initial_metadata["pseudonymous_case_id"] = self.pseudonymous_case_id
        self.tags = [scrub_phi_text(t) for t in (tags or ["medishield"])]

        self.span_handle = SafeSpan(
            name=self.name,
            run_type=self.run_type,
            pseudonymous_case_id=self.pseudonymous_case_id,
            component=self.component,
            operation=self.operation,
        )
        self._start_time: float = 0.0
        self._langsmith_context: Any | None = None
        self._run_tree: Any | None = None

    def __enter__(self) -> SafeSpan:
        self._start_time = time.perf_counter()
        if not settings.LANGSMITH_TRACING:
            return self.span_handle

        if getattr(settings, "LANGSMITH_AGENTS_ONLY", False) and not is_agent_trace(
            self.name, self.component, self.run_type
        ):
            return self.span_handle

        client = get_langsmith_client()
        if client is None:
            return self.span_handle

        try:
            from langsmith import trace

            self._langsmith_context = trace(
                name=self.name,
                run_type=self.run_type,  # type: ignore[arg-type]
                inputs=self.initial_inputs,
                metadata=self.initial_metadata,
                tags=self.tags,
                project_name=settings.LANGSMITH_PROJECT,
                client=client,
            )
            self._run_tree = self._langsmith_context.__enter__()
        except Exception as exc:  # noqa: BLE001
            logger.warning("LangSmith span activation failed for %s: %s", self.name, exc)
            self._langsmith_context = None
            self._run_tree = None

        return self.span_handle

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        elapsed_ms = (time.perf_counter() - self._start_time) * 1000.0
        self.span_handle.set_summary(duration_ms=round(elapsed_ms, 2))

        if exc_val is not None:
            cat = normalize_error_category(exc_val)
            self.span_handle.set_summary(
                status="FAILED",
                error_category=cat,
                error_type=exc_val.__class__.__name__,
            )

        if self.run_type == "llm":
            try:
                from medishield.core.llm import llm_runtime_telemetry

                stage = scrub_phi_text(f"{self.component}:{self.operation}")
                summary = self.span_handle.get_summary()
                llm_runtime_telemetry.record_trace_call(
                    stage=stage,
                    elapsed_ms=elapsed_ms,
                    success=summary.get("status") in ("SUCCESS", "SUCCEEDED"),
                    summary=summary,
                )
            except Exception as telemetry_exc:  # noqa: BLE001
                logger.debug("LLM runtime telemetry failed: %s", telemetry_exc)

        if self._langsmith_context is not None and self._run_tree is not None:
            try:
                outputs = self.span_handle.get_summary()
                # Ensure the run tree receives sanitized outputs
                if hasattr(self._run_tree, "end"):
                    error_str = (
                        f"{self.span_handle.get_summary().get('error_category')}: "
                        f"{self.span_handle.get_summary().get('error_type')}"
                        if exc_val
                        else None
                    )
                    self._run_tree.end(outputs=outputs, error=error_str)
                self._langsmith_context.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LangSmith span teardown failed for %s: %s", self.name, exc)

    async def __aenter__(self) -> SafeSpan:
        return self.__enter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Support decorating synchronous or asynchronous functions."""
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                case_id = kwargs.get("case_id") or (
                    args[0].get("case_id")
                    if args and isinstance(args[0], dict)
                    else None
                )
                async with trace_span(
                    name=self.name,
                    run_type=self.run_type,
                    case_id=case_id,
                    component=self.component,
                    operation=self.operation,
                ):
                    return await func(*args, **kwargs)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            case_id = kwargs.get("case_id") or (
                args[0].get("case_id")
                if args and isinstance(args[0], dict)
                else None
            )
            with trace_span(
                name=self.name,
                run_type=self.run_type,
                case_id=case_id,
                component=self.component,
                operation=self.operation,
            ):
                return func(*args, **kwargs)

        return sync_wrapper
