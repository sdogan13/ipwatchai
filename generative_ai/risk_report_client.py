"""Provider chain for search risk report JSON generation."""

from typing import Any

import structlog

from generative_ai.deepseek_client import get_deepseek_client
from generative_ai.gemini_client import get_gemini_client
from generative_ai.qwen_client import get_qwen_client

logger = structlog.get_logger(__name__)


class RiskReportProviderError(Exception):
    """Raised when every configured risk report provider fails."""


def _provider_label(provider, model_attr: str = "text_model") -> str:
    provider_name = getattr(provider, "provider_name", provider.__class__.__name__.lower())
    text_model = getattr(provider, model_attr, None) or getattr(provider, "text_model", "unknown")
    return f"{provider_name}:{text_model}"


class RiskReportJsonClient:
    """Qwen-first text JSON provider chain with DeepSeek and Gemini fallbacks."""

    provider_name = "risk_report_provider_chain"

    def __init__(self, providers=None):
        self.providers = list(providers) if providers is not None else [
            get_qwen_client(),
            get_deepseek_client(),
            get_gemini_client(),
        ]
        self.text_model = "unavailable"

    def is_available(self) -> bool:
        return any(provider.is_available() for provider in self.providers)

    async def generate_json(
        self,
        prompt: str,
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
        system_prompt: str | None = None,
        user_prompt: str | None = None,
    ) -> dict[str, Any]:
        last_error = None
        for provider in self.providers:
            provider_label = _provider_label(provider)
            if not provider.is_available():
                logger.info("risk_report_provider_unavailable", provider=provider_label)
                continue

            try:
                result = await provider.generate_json(
                    prompt=prompt,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                self.text_model = provider_label
                return result
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "risk_report_provider_failed",
                    provider=provider_label,
                    error_type=type(exc).__name__,
                    error=str(exc)[:320],
                )

        if last_error is not None:
            raise RiskReportProviderError("All risk report providers failed") from last_error
        raise RiskReportProviderError("No risk report provider configured")


class RiskReportMultimodalJsonClient:
    """Qwen-first multimodal JSON provider chain with Gemini fallback."""

    provider_name = "risk_report_multimodal_provider_chain"

    def __init__(self, providers=None):
        self.providers = list(providers) if providers is not None else [
            get_qwen_client(),
            get_gemini_client(),
        ]
        self.text_model = "unavailable"

    def is_available(self) -> bool:
        return any(provider.is_available() for provider in self.providers)

    async def generate_multimodal_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: list[dict[str, Any]],
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        last_error = None
        for provider in self.providers:
            provider_label = _provider_label(provider, "vl_model")
            if not provider.is_available():
                logger.info("risk_report_multimodal_provider_unavailable", provider=provider_label)
                continue
            if not hasattr(provider, "generate_multimodal_json"):
                logger.info("risk_report_multimodal_provider_unsupported", provider=provider_label)
                continue

            try:
                result = await provider.generate_multimodal_json(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    images=images,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                )
                self.text_model = provider_label
                return result
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "risk_report_multimodal_provider_failed",
                    provider=provider_label,
                    error_type=type(exc).__name__,
                    error=str(exc)[:320],
                )

        if last_error is not None:
            raise RiskReportProviderError("All multimodal risk report providers failed") from last_error
        raise RiskReportProviderError("No multimodal risk report provider configured")


def get_risk_report_json_client() -> RiskReportJsonClient:
    """Create a per-request provider chain so selected model metadata stays local."""
    return RiskReportJsonClient()


def get_risk_report_multimodal_json_client() -> RiskReportMultimodalJsonClient:
    """Create a per-request multimodal provider chain so selected model metadata stays local."""
    return RiskReportMultimodalJsonClient()
