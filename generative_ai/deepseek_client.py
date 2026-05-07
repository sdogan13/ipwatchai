"""DeepSeek JSON client for advisory risk reports."""

import asyncio
import json
import re
import threading
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

_RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}


class DeepSeekError(Exception):
    """Raised when DeepSeek API calls fail after retries."""

    def __init__(self, message: str, status_code: Optional[int] = None, retries_attempted: int = 0):
        self.status_code = status_code
        self.retries_attempted = retries_attempted
        super().__init__(message)


class DeepSeekClient:
    """OpenAI SDK-backed DeepSeek client for JSON text generation."""

    provider_name = "deepseek"

    def __init__(self, settings=None):
        if settings is None:
            from config.settings import settings as app_settings

            settings = app_settings.creative

        self.api_key: str = settings.deepseek_api_key
        self.base_url: str = settings.deepseek_base_url
        self.text_model: str = settings.deepseek_text_model
        self.timeout: int = settings.deepseek_timeout
        self.max_retries: int = settings.deepseek_max_retries

        self._client = None
        self._initialized = False

        if self.api_key:
            self._init_sdk()

    def _init_sdk(self) -> None:
        """Initialize the standard OpenAI SDK against the DeepSeek base URL."""
        try:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=0,
            )
            self._initialized = True
            logger.info("deepseek_client_initialized", text_model=self.text_model, base_url=self.base_url)
        except Exception as exc:
            logger.error("deepseek_client_init_failed", error=str(exc))
            self._client = None
            self._initialized = False

    def is_available(self) -> bool:
        """Check whether the DeepSeek key and SDK client are configured."""
        return bool(self.api_key) and self._initialized and self._client is not None

    async def generate_json(
        self,
        prompt: str,
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
        system_prompt: str | None = None,
        user_prompt: str | None = None,
    ) -> dict[str, Any]:
        """Generate a JSON object with DeepSeek native JSON mode."""
        if not self.is_available():
            raise DeepSeekError("DeepSeek API key not configured", status_code=None)

        raw_text = await self._call_with_retry(
            system_prompt=system_prompt or "Return only valid JSON.",
            user_prompt=user_prompt or prompt,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        return self._parse_json_object(raw_text)

    async def _call_with_retry(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        temperature: float,
    ) -> str:
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.chat.completions.create(
                    model=self.text_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_output_tokens,
                    response_format={"type": "json_object"},
                    stream=False,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                choice = response.choices[0] if response.choices else None
                content = choice.message.content if choice and choice.message else None
                if not content:
                    raise DeepSeekError("DeepSeek returned an empty response")
                return content
            except Exception as exc:
                last_error = exc
                status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
                if status_code in _RETRYABLE_CODES and attempt < self.max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "deepseek_retry",
                        attempt=attempt + 1,
                        max_retries=self.max_retries,
                        status_code=status_code,
                        backoff_seconds=backoff,
                        error=str(exc),
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise DeepSeekError(
                    message=f"DeepSeek API error: {exc}",
                    status_code=status_code,
                    retries_attempted=attempt,
                ) from exc

        raise DeepSeekError(
            message=f"DeepSeek API failed after {self.max_retries} retries: {last_error}",
            retries_attempted=self.max_retries,
        )

    @staticmethod
    def _parse_json_object(raw_text: str) -> dict[str, Any]:
        if not raw_text:
            raise DeepSeekError("DeepSeek returned an empty response")

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

        raise DeepSeekError("DeepSeek response was not a JSON object")


_instance: Optional[DeepSeekClient] = None
_lock = threading.Lock()


def get_deepseek_client(settings=None) -> DeepSeekClient:
    """Get or create the shared DeepSeek client."""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = DeepSeekClient(settings)
    return _instance


def reset_deepseek_client() -> None:
    """Reset the singleton for tests."""
    global _instance
    with _lock:
        _instance = None
