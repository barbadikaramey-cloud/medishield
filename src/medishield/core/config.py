from typing import Literal

from pydantic import Field
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

    # LLM Gateway (Groq)
    GROQ_API_KEY: str | None = Field(
        default=None,
        description="Groq API key for high-speed frontier LLM inference",
    )
    GROQ_MODEL: str = Field(
        default="openai/gpt-oss-120b",
        description="Groq model identifier (e.g. openai/gpt-oss-120b, openai/gpt-oss-20b)",
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
        default="gemini-3.8-flash",
        description="Gemini model identifier for VLM extraction",
    )


settings = Settings()
