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
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
        ],
        description="Ordered list of Groq fallback models when primary model (e.g. gpt-oss-120b) hits rate limits or errors",
    )
    GROQ_BASE_URL: str = Field(
        default="https://api.groq.com/openai/v1",
        description="Base URL for OpenAI-compatible Groq API endpoints",
    )
    GROQ_TIMEOUT_SECONDS: float = Field(
        default=30.0,
        description="HTTP client timeout in seconds for Groq API requests",
    )
    GROQ_MAX_RETRIES: int = Field(
        default=2,
        description="Maximum transient retry attempts for rate limits or network issues",
    )

    # VLM Extraction
    GEMINI_API_KEY: str | None = Field(
        default=None,
        description="Gemini API Key for VLM OCR extraction (local dev only)",
    )
    GEMINI_MODEL: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model identifier for VLM extraction",
    )
    GEMINI_FALLBACK_MODELS: list[str] | str = Field(
        default_factory=lambda: [
            "gemini-2.0-flash",
            "gemini-1.5-flash",
            "gemini-2.0-flash-lite",
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
