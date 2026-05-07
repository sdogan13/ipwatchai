"""Provider chain for Logo Studio image generation."""

from typing import Any

import structlog

from generative_ai.gemini_client import get_gemini_client
from generative_ai.openai_image_client import get_openai_image_client

logger = structlog.get_logger(__name__)


class LogoImageProviderError(Exception):
    """Raised when every configured Logo Studio image provider fails."""

    def __init__(self, message: str, provider_errors: list[dict[str, Any]] | None = None):
        self.provider_errors = provider_errors or []
        super().__init__(message)


def _provider_label(provider) -> str:
    provider_name = getattr(provider, "provider_name", provider.__class__.__name__.lower())
    image_model = getattr(provider, "image_model", None) or getattr(provider, "model", "unknown")
    return f"{provider_name}:{image_model}"


def _provider_metadata(provider) -> dict[str, Any]:
    return {
        "provider": getattr(provider, "provider_name", provider.__class__.__name__.lower()),
        "model": getattr(provider, "image_model", None) or getattr(provider, "model", None),
    }


class LogoImageProviderChain:
    """OpenAI-first image provider chain with Gemini fallback."""

    provider_name = "logo_image_provider_chain"

    def __init__(self, providers=None):
        self.providers = list(providers) if providers is not None else [
            get_openai_image_client(),
            get_gemini_client(),
        ]
        self.selected_provider_name: str | None = None
        self.selected_model: str | None = None
        self.selected_source_layout: str | None = None
        self.selected_provider_call_count: int | None = None
        self.image_model = "unavailable"
        self.attempts: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        return any(provider.is_available() for provider in self.providers)

    async def generate_logos(
        self,
        brand_name: str,
        description: str,
        style: str = "modern",
        count: int = 4,
    ) -> list[bytes]:
        return await self._run_provider_method(
            "generate_logos",
            brand_name=brand_name,
            description=description,
            style=style,
            count=count,
        )

    async def generate_logo_revisions(
        self,
        brand_name: str,
        description: str,
        style: str = "modern",
        revision_prompt: str = "",
        reference_image_bytes: bytes | None = None,
        count: int = 4,
    ) -> list[bytes]:
        return await self._run_provider_method(
            "generate_logo_revisions",
            brand_name=brand_name,
            description=description,
            style=style,
            revision_prompt=revision_prompt,
            reference_image_bytes=reference_image_bytes,
            count=count,
        )

    def selected_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.selected_provider_name,
            "model": self.selected_model,
            "source_layout": self.selected_source_layout,
            "provider_call_count": self.selected_provider_call_count,
            "attempts": self.attempts,
        }

    async def _run_provider_method(self, method_name: str, **kwargs) -> list[bytes]:
        self.selected_provider_name = None
        self.selected_model = None
        self.selected_source_layout = None
        self.selected_provider_call_count = None
        self.image_model = "unavailable"
        self.attempts = []
        last_error = None

        for provider in self.providers:
            metadata = _provider_metadata(provider)
            attempt = {
                **metadata,
                "available": False,
                "used": False,
                "error": None,
                "fallback_allowed": True,
            }

            if not provider.is_available():
                attempt["error"] = "unavailable"
                self.attempts.append(attempt)
                logger.info("logo_image_provider_unavailable", provider=_provider_label(provider))
                continue

            attempt["available"] = True
            self.attempts.append(attempt)

            try:
                if not hasattr(provider, method_name):
                    raise RuntimeError(f"{_provider_label(provider)} does not support {method_name}")
                images = await getattr(provider, method_name)(**kwargs)
                if not images:
                    raise RuntimeError(f"{_provider_label(provider)} returned no logo images")
                expected_count = max(1, int(kwargs.get("count") or 1))
                if len(images) != expected_count:
                    raise RuntimeError(
                        f"{_provider_label(provider)} returned {len(images)}/{expected_count} logo images"
                    )

                attempt["used"] = True
                self.selected_provider_name = metadata["provider"]
                self.selected_model = metadata["model"]
                self.selected_source_layout = getattr(provider, "source_layout", None) or "native_multi_image"
                self.selected_provider_call_count = int(getattr(provider, "provider_call_count", None) or 1)
                self.image_model = _provider_label(provider)
                return images
            except Exception as exc:
                last_error = exc
                fallback_allowed = getattr(exc, "fallback_allowed", True)
                attempt["error"] = str(exc)[:500]
                attempt["fallback_allowed"] = fallback_allowed
                logger.warning(
                    "logo_image_provider_failed",
                    provider=_provider_label(provider),
                    error_type=type(exc).__name__,
                    fallback_allowed=fallback_allowed,
                    error=str(exc)[:320],
                )
                if not fallback_allowed:
                    raise

        if last_error is not None:
            raise LogoImageProviderError("All logo image providers failed", self.attempts) from last_error
        raise LogoImageProviderError("No logo image provider configured", self.attempts)


def get_logo_image_provider_chain() -> LogoImageProviderChain:
    """Create a per-request provider chain so selected provider metadata stays local."""
    return LogoImageProviderChain()
