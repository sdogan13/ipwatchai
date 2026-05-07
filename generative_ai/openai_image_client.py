"""OpenAI image client for Logo Studio."""

import asyncio
import base64
import io
import os
import threading
from typing import Optional

import structlog

from generative_ai.gemini_client import LOGO_GENERATION_PROMPT, LOGO_REVISION_PROMPT

logger = structlog.get_logger(__name__)

_RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}
_SAFETY_ERROR_TERMS = (
    "content_policy",
    "content policy",
    "policy_violation",
    "safety",
    "unsafe",
    "moderation",
    "violat",
)


class OpenAIImageError(Exception):
    """Raised when OpenAI image generation fails."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        retries_attempted: int = 0,
        fallback_allowed: bool = True,
    ):
        self.status_code = status_code
        self.retries_attempted = retries_attempted
        self.fallback_allowed = fallback_allowed
        super().__init__(message)


def _looks_like_safety_error(exc: Exception) -> bool:
    text = str(exc).lower()
    code = str(getattr(exc, "code", "") or "").lower()
    error_type = str(getattr(exc, "type", "") or "").lower()
    return any(term in text or term in code or term in error_type for term in _SAFETY_ERROR_TERMS)


class OpenAIImageClient:
    """OpenAI GPT Image client with the Logo Studio image provider interface."""

    provider_name = "openai"

    def __init__(self, settings=None):
        if settings is None:
            from config.settings import settings as app_settings

            settings = app_settings.creative

        self.api_key: str = settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.image_model: str = settings.openai_image_model
        self.image_size: str = settings.openai_image_size
        self.image_quality: str = settings.openai_image_quality
        self.image_revision_quality: str = getattr(
            settings, "openai_image_revision_quality", settings.openai_image_quality
        )
        self.image_background: str = settings.openai_image_background
        self.image_output_format: str = settings.openai_image_output_format
        self.timeout: int = settings.openai_timeout
        self.max_retries: int = settings.openai_max_retries
        self.source_layout: str | None = None
        self.provider_call_count: int = 0

        self._client = None
        self._initialized = False

        if self.api_key:
            self._init_sdk()

    def _init_sdk(self) -> None:
        """Initialize the OpenAI SDK client."""
        try:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.api_key,
                timeout=self.timeout,
                max_retries=0,
            )
            self._initialized = True
            logger.info("openai_image_client_initialized", image_model=self.image_model)
        except Exception as exc:
            logger.error("openai_image_client_init_failed", error=str(exc))
            self._client = None
            self._initialized = False

    def is_available(self) -> bool:
        """Check whether an API key and SDK client are configured."""
        return bool(self.api_key) and self._initialized and self._client is not None

    async def generate_logos(
        self,
        brand_name: str,
        description: str,
        style: str = "modern",
        count: int = 4,
    ) -> list[bytes]:
        """Generate logo image candidates using GPT Image."""
        if not self.is_available():
            raise OpenAIImageError("OpenAI API key not configured", status_code=None)

        prompt = LOGO_GENERATION_PROMPT.format(
            brand_name=brand_name,
            style=style,
            description=description or f"Professional logo for {brand_name}",
        )
        return await self._generate_images(prompt=prompt, count=count)

    async def generate_logo_revisions(
        self,
        brand_name: str,
        description: str,
        style: str = "modern",
        revision_prompt: str = "",
        reference_image_bytes: Optional[bytes] = None,
        count: int = 4,
    ) -> list[bytes]:
        """Generate revised logo candidates, using image edits when a reference exists."""
        if not self.is_available():
            raise OpenAIImageError("OpenAI API key not configured", status_code=None)

        prompt = LOGO_REVISION_PROMPT.format(
            brand_name=brand_name,
            style=style,
            description=description or f"Professional logo for {brand_name}",
            revision_prompt=revision_prompt or "Create a refined alternative that improves distinctiveness.",
        )

        if not reference_image_bytes:
            return await self._generate_images(prompt=prompt, count=count)

        reference_file = io.BytesIO(reference_image_bytes)
        reference_file.name = "reference.png"
        return await self._edit_images(prompt=prompt, image=reference_file, count=count)

    async def _generate_images(self, *, prompt: str, count: int) -> list[bytes]:
        requested_count = max(1, int(count or 1))
        response = await self._call_with_retry(
            "generate",
            model=self.image_model,
            prompt=prompt,
            n=requested_count,
            size=self.image_size,
            quality=self.image_quality,
            background=self.image_background,
            output_format=self.image_output_format,
        )
        images = self._extract_images(response)

        if not images:
            raise OpenAIImageError("OpenAI returned no logo images", fallback_allowed=True)
        self.source_layout = "native_multi_image"
        self.provider_call_count = 1
        return images[:requested_count]

    async def _edit_images(self, *, prompt: str, image: io.BytesIO, count: int) -> list[bytes]:
        requested_count = max(1, int(count or 1))
        response = await self._call_with_retry(
            "edit",
            model=self.image_model,
            image=image,
            prompt=prompt,
            n=requested_count,
            size=self.image_size,
            quality=self.image_revision_quality,
            background=self.image_background,
            output_format=self.image_output_format,
        )
        images = self._extract_images(response)

        if not images:
            raise OpenAIImageError("OpenAI returned no revised logo images", fallback_allowed=True)
        self.source_layout = "native_multi_image"
        self.provider_call_count = 1
        return images[:requested_count]

    async def _call_with_retry(self, method_name: str, **kwargs):
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                image_arg = kwargs.get("image")
                if hasattr(image_arg, "seek"):
                    image_arg.seek(0)
                method = getattr(self._client.images, method_name)
                return await method(**kwargs)
            except Exception as exc:
                last_error = exc
                status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                safety_error = _looks_like_safety_error(exc)

                if not safety_error and status_code in _RETRYABLE_CODES and attempt < self.max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "openai_image_retry",
                        attempt=attempt + 1,
                        max_retries=self.max_retries,
                        status_code=status_code,
                        backoff_seconds=backoff,
                        error=str(exc),
                    )
                    await asyncio.sleep(backoff)
                    continue

                raise OpenAIImageError(
                    message=f"OpenAI image API error: {exc}",
                    status_code=status_code,
                    retries_attempted=attempt,
                    fallback_allowed=not safety_error,
                ) from exc

        raise OpenAIImageError(
            message=f"OpenAI image API failed after {self.max_retries} retries: {last_error}",
            retries_attempted=self.max_retries,
            fallback_allowed=True,
        )

    @staticmethod
    def _extract_images(response) -> list[bytes]:
        images: list[bytes] = []
        for item in getattr(response, "data", None) or []:
            b64_json = getattr(item, "b64_json", None)
            if b64_json is None and isinstance(item, dict):
                b64_json = item.get("b64_json")
            if not b64_json:
                continue
            if "," in b64_json and b64_json.strip().lower().startswith("data:"):
                b64_json = b64_json.split(",", 1)[1]
            try:
                images.append(base64.b64decode(b64_json))
            except Exception as exc:
                raise OpenAIImageError(
                    message=f"OpenAI returned invalid base64 image data: {exc}",
                    fallback_allowed=True,
                ) from exc
        return images


_instance: Optional[OpenAIImageClient] = None
_lock = threading.Lock()


def get_openai_image_client(settings=None) -> OpenAIImageClient:
    """Get or create the shared OpenAI image client."""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = OpenAIImageClient(settings)
    return _instance


def reset_openai_image_client() -> None:
    """Reset the singleton for tests."""
    global _instance
    with _lock:
        _instance = None
