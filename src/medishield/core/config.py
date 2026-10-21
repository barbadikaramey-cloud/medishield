from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    ENVIRONMENT: Literal["development", "staging", "production", "test"] = "development"
    LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = Field(
        default="development-insecure-secret-key-32-chars-long",
        description="JWT and encryption secret key",
    )
    API_V1_STR: str = "/api/v1"

    # Database
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://medishield_user:medishield_password@localhost:5432/medishield",
        description="Async database connection string for SQLAlchemy",
    )
    DATABASE_SYNC_URL: str = Field(
        default="postgresql+psycopg2://medishield_user:medishield_password@localhost:5432/medishield",
        description="Sync database connection string for Alembic migrations",
    )
    SQLITE_DB_PATH: str = Field(
        default="data/medishield.db",
        description="File path for SQLite development database fallback (ADR-008)",
    )

    # Redis (Queues & Cache)
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )

    # Object Storage (MinIO / S3)
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY: str = "minioadmin"
    S3_SECRET_KEY: str = "minioadmin"
    S3_BUCKET_NAME: str = "medishield-storage"
    S3_REGION: str = "us-east-1"
    S3_USE_SSL: bool = False
    STORAGE_DIR: str = "data/storage"

    # Vector Storage
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_CLOUD_URL: str | None = None
    QDRANT_CLOUD_API_KEY: str | None = None
    QDRANT_COLLECTION_NAME: str = "authoritative_policy_chunks"
    QDRANT_VECTOR_NAME: str | None = None
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIMENSIONS: int = Field(default=512, ge=128, le=3072)
    QDRANT_PATH: str = "data/qdrant"
    STRICT_EVALUATION_MODE: bool = False
    EVALUATION_DATABASE_URL: str | None = None

    # Observability & Telemetry (Logfire)
    LOGFIRE_TOKEN: str | None = Field(
        default=None,
        description="Optional Logfire project write token for remote trace streaming",
    )
    LOGFIRE_SEND_TO_LOGFIRE: Literal["always", "if-token-present", "never"] = Field(
        default="if-token-present",
        description="Controls whether trace spans are sent to the Logfire cloud backend",
    )
    LOGFIRE_SERVICE_NAME: str = Field(
        default="medishield",
        description="Service identifier associated with trace spans",
    )

    # Observability & Tracing (LangSmith)
    LANGSMITH_TRACING: bool = Field(
        default=False,
        description="Enable LangSmith distributed agent and tool tracing",
    )
    LANGSMITH_API_KEY: str | None = Field(
        default=None,
        description="API key for LangSmith tracing authentication",
    )
    LANGSMITH_PROJECT: str = Field(
        default="medishield-local-dev",
        description="Target LangSmith project name for trace aggregation",
    )
    LANGSMITH_ENDPOINT: str | None = Field(
        default=None,
        description="Optional regional LangSmith endpoint (e.g. https://eu.api.smith.langchain.com)",
    )
    LANGSMITH_WORKSPACE_ID: str | None = Field(
        default=None,
        description="Optional workspace ID for workspace-scoped LangSmith API keys",
    )
    LANGSMITH_INCLUDE_CASE_PSEUDONYM: bool = Field(
        default=False,
        description=(
            "Whether to include keyed HMAC pseudonymous case identifiers in telemetry metadata. "
            "Disabled by default so traces rely solely on ephemeral root run UUIDs."
        ),
    )
    LANGSMITH_AGENTS_ONLY: bool = Field(
        default=False,
        description=(
            "When True, LangSmith distributed tracing is strictly restricted to agent and workflow nodes, "
            "filtering out low-level storage I/O, OCR preprocessor tools, and repository micro-spans."
        ),
    )
    FORCE_VLM_EXTRACTION: bool = Field(
        default=False,
        description="Force VLM extraction even on clean documents (primarily for testing and evaluation).",
    )
    CLINICAL_SUPPORT_APPROVAL_MIN_RATIO: float | None = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Fully supported billed-line ratio above which a clinical-only INDETERMINATE alignment no longer "
            "escalates. Unset to disable."
        ),
    )
    CLINICAL_UNCERTAINTY_APPROVAL_ENABLED: bool = Field(
        default=True,
        description=(
            "Allow P6 approval when clinical findings are indeterminate without verified contradiction, "
            "with discrepancy details preserved in the approval rationale."
        ),
    )

    # LLM Gateway (Groq)
    GROQ_API_KEY: str | None = Field(
        default=None,
        description="Groq API key for high-speed frontier LLM inference",
    )
    GROQ_MODEL: str = Field(
        default="openai/gpt-oss-120b",
        description="Groq model identifier (e.g. openai/gpt-oss-120b, openai/gpt-oss-20b)",
    )
    GROQ_FALLBACK_MODEL: str | None = Field(
        default="openai/gpt-oss-20b",
        description="Legacy fallback Groq model identifier when primary model fails, rate limits, or is unavailable",
    )
    GROQ_FALLBACK_MODELS: list[str] | str = Field(
        default_factory=lambda: [
            "openai/gpt-oss-20b",
            "qwen/qwen3.8-27b",
            "allam-2-7b",
        ],
        description="Ordered list of Groq fallback models when primary model (e.g. gpt-oss-120b) hits rate limits or errors",
    )
    GROQ_FALLBACK_API_KEYS: list[str] | str = Field(
        default_factory=list,
        description="Ordered list of fallback Groq API keys from different accounts for rate-limit failover",
    )
    GROQ_BASE_URL: str = Field(
        default="https://api.groq.com/openai/v1",
        description="Base URL for OpenAI-compatible Groq API endpoints",
    )
    GROQ_TIMEOUT_SECONDS: float = Field(
        default=10.0,
        description="HTTP client timeout in seconds for Groq API requests",
    )
    GROQ_MAX_RETRIES: int = Field(
        default=1,
        description="Maximum transient retry attempts for rate limits or network issues",
    )

    # LLM Fallback: Cerebras
    CEREBRAS_API_KEY: str | None = Field(
        default=None,
        description="Cerebras API key for ultra-fast Llama inference fallback",
    )
    CEREBRAS_MODEL: str = Field(
        default="gpt-oss-120b",
        description="Primary Cerebras model identifier",
    )
    CEREBRAS_FALLBACK_MODELS: list[str] | str = Field(
        default_factory=lambda: ["qwen-3.8-27b"],
        description="Ordered list of Cerebras fallback models",
    )
    CEREBRAS_BASE_URL: str = Field(
        default="https://api.cerebras.ai/v1",
        description="Base URL for OpenAI-compatible Cerebras API endpoints",
    )

    # LLM Fallback: Gemini (Text & JSON)
    GEMINI_LLM_MODEL: str = Field(
        default="gemini-3.5-flash",
        description="Gemini model identifier for structured text/JSON LLM generation fallback",
    )
    GEMINI_LLM_FALLBACK_MODELS: list[str] | str = Field(
        default_factory=lambda: [
            "gemini-3.5-flash-lite",
            "gemini-3.8-flash",
        ],
        description="Ordered list of Gemini LLM fallback models",
    )

    # VLM Extraction
    GEMINI_API_KEY: str | None = Field(
        default=None,
        description="Gemini API Key for VLM OCR extraction (local dev only)",
    )
    GEMINI_MODEL: str = Field(
        default="gemini-3.8-flash",
        description="Gemini model identifier for VLM extraction",
    )
    GEMINI_FALLBACK_MODELS: list[str] | str = Field(
        default_factory=lambda: [
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-flash-lite-latest",
        ],
        description="Ordered list of Gemini fallback models when primary model hits rate limits or errors",
    )
    HF_API_KEY: str | None = Field(
        default=None,
        description="Hugging Face API key for multi-provider VLM fallback",
    )
    HF_VLM_MODEL: str = Field(
        default="mistralai/Pixtral-12B-2409",
        description="Hugging Face VLM model identifier for OCR assistance",
    )
    HF_VLM_FALLBACK_MODELS: list[str] | str = Field(
        default_factory=lambda: [
            "Qwen/Qwen2-VL-7B-Instruct",
            "meta-llama/Llama-3.2-11B-Vision-Instruct",
        ],
        description="Ordered list of Hugging Face VLM fallback models when primary hits rate limits",
    )
    ENABLE_MODEL_FALLBACK: bool = Field(
        default=False,
        description="Explicit authorization gate for external model/VLM fallback (Spec 05)",
    )

    @field_validator(
        "GROQ_FALLBACK_MODELS",
        "GROQ_FALLBACK_API_KEYS",
        "CEREBRAS_FALLBACK_MODELS",
        "GEMINI_LLM_FALLBACK_MODELS",
        "GEMINI_FALLBACK_MODELS",
        "HF_VLM_FALLBACK_MODELS",
        mode="after",
    )
    @classmethod
    def _parse_models_list(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            if v.startswith("[") and v.endswith("]"):
                try:
                    import json

                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except (json.JSONDecodeError, ValueError):
                    pass
            return [part.strip() for part in v.split(",") if part.strip()]
        if isinstance(v, (list, tuple)):
            return [str(item).strip() for item in v if str(item).strip()]
        return []


settings = Settings()
