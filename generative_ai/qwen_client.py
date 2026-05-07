"""Qwen multimodal JSON client for advisory risk reports."""

import asyncio
import json
import os
import re
import threading
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

_RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}
QWEN_MAX_OUTPUT_TOKENS = 8192


class QwenError(Exception):
    """Raised when Qwen API calls fail after retries."""

    def __init__(self, message: str, status_code: Optional[int] = None, retries_attempted: int = 0):
        self.status_code = status_code
        self.retries_attempted = retries_attempted
        super().__init__(message)


class QwenClient:
    """OpenAI SDK-backed Qwen-VL client for JSON text and multimodal generation."""

    provider_name = "qwen"

    def __init__(self, settings=None):
        if settings is None:
            from config.settings import settings as app_settings

            settings = app_settings.creative

        self.api_key: str = settings.qwen_api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url: str = settings.qwen_base_url
        self.text_model: str = settings.qwen_text_model
        self.vl_model: str = settings.qwen_vl_model
        self.timeout: int = settings.qwen_timeout
        self.max_retries: int = settings.qwen_max_retries

        self._client = None
        self._initialized = False

        if self.api_key:
            self._init_sdk()

    def _init_sdk(self) -> None:
        """Initialize the standard OpenAI SDK against the DashScope-compatible endpoint."""
        try:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=0,
            )
            self._initialized = True
            logger.info(
                "qwen_client_initialized",
                text_model=self.text_model,
                vl_model=self.vl_model,
                base_url=self.base_url,
            )
        except Exception as exc:
            logger.error("qwen_client_init_failed", error=str(exc))
            self._client = None
            self._initialized = False

    def is_available(self) -> bool:
        """Check whether the Qwen key and SDK client are configured."""
        return bool(self.api_key) and self._initialized and self._client is not None

    async def generate_json(
        self,
        prompt: str,
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
        system_prompt: str | None = None,
        user_prompt: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Generate a JSON object from text-only input."""
        if not self.is_available():
            raise QwenError("Qwen API key not configured", status_code=None)

        selected_model = model or self.text_model
        raw_text = await self._call_with_retry(
            model=selected_model,
            system_prompt=system_prompt or "Return only valid JSON.",
            user_content=user_prompt or prompt,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        return self._parse_json_object(raw_text)

    async def generate_multimodal_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        images: list[dict[str, Any]],
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        """Generate a JSON object from text plus labelled image data URLs."""
        if not self.is_available():
            raise QwenError("Qwen API key not configured", status_code=None)

        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image in images:
            label = str(image.get("label") or "image")
            data_url = image.get("data_url")
            if not data_url:
                continue
            content.append({"type": "text", "text": f"Attached image: {label}"})
            content.append({"type": "image_url", "image_url": {"url": data_url}})

        raw_text = await self._call_with_retry(
            model=self.vl_model,
            system_prompt=system_prompt,
            user_content=content,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        return self._parse_json_object(raw_text)

    async def _call_with_retry(
        self,
        *,
        model: str,
        system_prompt: str,
        user_content: Any,
        max_output_tokens: int,
        temperature: float,
    ) -> str:
        last_error = None
        provider_max_tokens = min(max_output_tokens, QWEN_MAX_OUTPUT_TOKENS)

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=temperature,
                    max_tokens=provider_max_tokens,
                    response_format={"type": "json_object"},
                    stream=False,
                    extra_body={"enable_thinking": False},
                )
                choice = response.choices[0] if response.choices else None
                content = choice.message.content if choice and choice.message else None
                if not content:
                    raise QwenError("Qwen returned an empty response")
                return content
            except Exception as exc:
                last_error = exc
                status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                if status_code in _RETRYABLE_CODES and attempt < self.max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "qwen_retry",
                        attempt=attempt + 1,
                        max_retries=self.max_retries,
                        status_code=status_code,
                        backoff_seconds=backoff,
                        error=str(exc),
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise QwenError(
                    message=f"Qwen API error: {exc}",
                    status_code=status_code,
                    retries_attempted=attempt,
                ) from exc

        raise QwenError(
            message=f"Qwen API failed after {self.max_retries} retries: {last_error}",
            retries_attempted=self.max_retries,
        )

    @staticmethod
    def _parse_json_object(raw_text: str) -> dict[str, Any]:
        if not raw_text:
            raise QwenError("Qwen returned an empty response")

        try:
            parsed = json.loads(raw_text.strip())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        raise QwenError("Qwen response was not a JSON object")


_instance: Optional[QwenClient] = None
_lock = threading.Lock()


def get_qwen_client(settings=None) -> QwenClient:
    """Get or create the shared Qwen client."""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = QwenClient(settings)
    return _instance


def reset_qwen_client() -> None:
    """Reset the singleton for tests."""
    global _instance
    with _lock:
        _instance = None
