"""Unit tests for Multi-Provider LLM Gateway Failover across Groq, Cerebras, and Gemini (ADR-023)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from medishield.core.llm import (
    GroqLLMClient,
    GroqLLMError,
    GroqUnavailableError,
)


def test_groq_primary_success() -> None:
    """When Groq responds with 200 OK, Cerebras and Gemini are not invoked."""
    client = GroqLLMClient(
        api_key="gsk_test_key",
        cerebras_api_key="csk_test_key",
        gemini_api_key="gem_test_key",
    )
    fake_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"status": "PASS", "confidence": 0.98}),
                }
            }
        ]
    }
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = fake_response

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        result = client.generate_json(prompt="Test prompt", system_prompt="Test system")
        assert result == {"status": "PASS", "confidence": 0.98}
        assert client.last_used_provider == "groq"
        assert client.fallback_used is False
        assert mock_post.call_count == 1


def test_cerebras_fallback_on_groq_429() -> None:
    """Cerebras is the immediate fallback after Groq model quotas are exhausted."""
    client = GroqLLMClient(
        api_key="gsk_test_key",
        cerebras_api_key="csk_test_key",
        gemini_api_key="gem_test_key",
    )

    groq_429_resp = MagicMock(spec=httpx.Response)
    groq_429_resp.status_code = 429
    groq_429_resp.text = "Rate limit reached (TPM exhausted)"

    cerebras_200_resp = MagicMock(spec=httpx.Response)
    cerebras_200_resp.status_code = 200
    cerebras_200_resp.json.return_value = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"provider": "cerebras", "processed": True}),
                }
            }
        ]
    }

    providers: list[str] = []

    def side_effect(endpoint: str, *args, **kwargs) -> MagicMock:
        if "groq.com" in endpoint:
            providers.append("groq")
            err = httpx.HTTPStatusError("Rate limited", request=MagicMock(), response=groq_429_resp)
            raise err
        if "cerebras.ai" in endpoint:
            providers.append("cerebras")
            return cerebras_200_resp
        raise ValueError(f"Unexpected endpoint: {endpoint}")

    mock_genai_client = MagicMock()

    def gemini_quota_error(*args, **kwargs):
        providers.append("gemini")
        raise RuntimeError("Gemini 429 quota exhausted")

    mock_genai_client.models.generate_content.side_effect = gemini_quota_error

    with (
        patch("httpx.Client.post", side_effect=side_effect),
        patch("google.genai.Client", return_value=mock_genai_client),
    ):
        result = client.generate_json(prompt="Summarize case", system_prompt="System instructions")
        assert result == {"provider": "cerebras", "processed": True}
        assert client.last_used_provider == "cerebras"
        assert client.fallback_used is True
        assert client.last_used_model == "llama3.1-8b"
        assert providers == ["groq"] * 3 + ["cerebras"]
        mock_genai_client.models.generate_content.assert_not_called()


def test_gemini_fallback_on_groq_and_cerebras_exhaustion() -> None:
    """When Groq hits 429 and Cerebras is unconfigured or errors, gateway cascades to Gemini."""
    client = GroqLLMClient(
        api_key="gsk_test_key",
        cerebras_api_key="",  # unconfigured Cerebras
        gemini_api_key="gem_test_key",
    )

    groq_429_resp = MagicMock(spec=httpx.Response)
    groq_429_resp.status_code = 429
    groq_429_resp.text = "429 Too Many Requests"

    mock_gemini_resp = MagicMock()
    mock_gemini_resp.text = json.dumps({"source": "gemini_flash", "adjudication": "APPROVED"})

    mock_genai_client = MagicMock()
    mock_genai_client.models.generate_content.return_value = mock_gemini_resp

    with (
        patch("httpx.Client.post", side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=groq_429_resp)),
        patch("google.genai.Client", return_value=mock_genai_client),
    ):
        result = client.generate_json(prompt="Generate explanation", system_prompt="Lead auditor")
        assert result == {"source": "gemini_flash", "adjudication": "APPROVED"}
        assert client.last_used_provider == "gemini"
        assert client.fallback_used is True
        assert client.last_used_model == "gemini-3.5-flash"


def test_all_providers_exhausted_raises_error() -> None:
    """When all configured providers fail, GroqLLMError is raised for deterministic fallback."""
    client = GroqLLMClient(
        api_key="gsk_test_key",
        cerebras_api_key="csk_test_key",
        gemini_api_key="gem_test_key",
    )

    failing_resp = MagicMock(spec=httpx.Response)
    failing_resp.status_code = 500
    failing_resp.text = "Internal Server Error"

    mock_genai_client = MagicMock()
    mock_genai_client.models.generate_content.side_effect = RuntimeError("Gemini 503 high demand")

    with (
        patch("httpx.Client.post", side_effect=httpx.HTTPStatusError("Error", request=MagicMock(), response=failing_resp)),
        patch("google.genai.Client", return_value=mock_genai_client),
        pytest.raises(GroqLLMError),
    ):
        client.generate_json("Test prompt")


def test_unconfigured_client_availability() -> None:
    """A client with empty keys is unavailable and immediately raises GroqUnavailableError."""
    client = GroqLLMClient(api_key="", cerebras_api_key="", gemini_api_key="")
    assert client.is_available() is False
    with pytest.raises(GroqUnavailableError, match="not configured"):
        client.generate_json("Test prompt")


@pytest.mark.asyncio
async def test_async_gemini_fallback() -> None:
    """Async generation cascades to Gemini async SDK when Groq fails."""
    client = GroqLLMClient(
        api_key="gsk_test_key",
        cerebras_api_key="",
        gemini_api_key="gem_test_key",
    )

    groq_429_resp = MagicMock(spec=httpx.Response)
    groq_429_resp.status_code = 429
    groq_429_resp.text = "429 Too Many Requests"

    mock_gemini_resp = MagicMock()
    mock_gemini_resp.text = json.dumps({"async_processed": True, "provider": "gemini"})

    mock_genai_client = MagicMock()
    mock_genai_client.aio.models.generate_content = AsyncMock(return_value=mock_gemini_resp)

    with (
        patch("httpx.AsyncClient.post", side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=groq_429_resp)),
        patch("google.genai.Client", return_value=mock_genai_client),
    ):
        result = await client.generate_json_async("Async prompt", system_prompt="Async system")
        assert result == {"async_processed": True, "provider": "gemini"}
        assert client.last_used_provider == "gemini"
        assert client.fallback_used is True


@pytest.mark.asyncio
async def test_async_cerebras_fallback_on_groq_429() -> None:
    """Async path also uses Cerebras immediately after Groq quota failures."""
    client = GroqLLMClient(
        api_key="gsk_test_key",
        cerebras_api_key="csk_test_key",
        gemini_api_key="gem_test_key",
    )
    groq_429 = MagicMock(spec=httpx.Response)
    groq_429.status_code = 429
    groq_429.text = "Groq quota exhausted"
    cerebras_response = MagicMock(spec=httpx.Response)
    cerebras_response.status_code = 200
    cerebras_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"provider": "cerebras"})}}]
    }
    providers: list[str] = []

    async def post_side_effect(endpoint: str, *args, **kwargs):
        if "groq.com" in endpoint:
            providers.append("groq")
            raise httpx.HTTPStatusError("429", request=MagicMock(), response=groq_429)
        if "cerebras.ai" in endpoint:
            providers.append("cerebras")
            return cerebras_response
        raise ValueError(f"Unexpected endpoint: {endpoint}")

    mock_genai_client = MagicMock()

    async def gemini_quota_error(*args, **kwargs):
        providers.append("gemini")
        raise RuntimeError("Gemini 429 quota exhausted")

    mock_genai_client.aio.models.generate_content = AsyncMock(side_effect=gemini_quota_error)

    with (
        patch("httpx.AsyncClient.post", side_effect=post_side_effect),
        patch("google.genai.Client", return_value=mock_genai_client),
    ):
        result = await client.generate_json_async("quota fallback test")

    assert result == {"provider": "cerebras"}
    assert client.last_used_provider == "cerebras"
    assert providers == ["groq"] * 3 + ["cerebras"]
    mock_genai_client.aio.models.generate_content.assert_not_awaited()
