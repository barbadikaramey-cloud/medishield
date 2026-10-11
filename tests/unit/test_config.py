from medishield.core.config import Settings


def test_default_settings():
    settings = Settings()
    assert settings.ENVIRONMENT in ["development", "staging", "production", "test"]
    assert settings.API_V1_STR == "/api/v1"
    assert "postgresql" in settings.DATABASE_URL
    assert "postgresql" in settings.DATABASE_SYNC_URL
    assert settings.S3_BUCKET_NAME == "medishield-storage"


def test_custom_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("S3_BUCKET_NAME", "custom-bucket")

    settings = Settings()
    assert settings.ENVIRONMENT == "test"
    assert settings.LOG_LEVEL == "DEBUG"
    assert settings.S3_BUCKET_NAME == "custom-bucket"
