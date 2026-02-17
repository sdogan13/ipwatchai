"""
Gemini API Client for Creative Suite
=====================================
Unified client for text (Name Generator) and image (Logo Studio) generation
using the Google Gen AI SDK (google-genai).

Usage:
    from generative_ai.gemini_client import get_gemini_client

    client = get_gemini_client()
    if client.is_available():
        names = await client.generate_names(prompt, count=25)
        logos = await client.generate_logos("BrandX", "modern tech logo", count=4)
"""
import asyncio
import json
import re
import threading
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


# ============================================================
# Exception
# ============================================================

class GeminiError(Exception):
    """Raised when Gemini API calls fail after retries."""

    def __init__(self, message: str, status_code: Optional[int] = None,
                 retries_attempted: int = 0):
        self.status_code = status_code
        self.retries_attempted = retries_attempted
        super().__init__(message)


# ============================================================
# Prompt Templates
# ============================================================

NAME_GENERATION_PROMPT = """\
You are a creative brand naming expert specializing in trademark law.

Generate {count} unique brand name suggestions for:
- Industry/Category: {industry}
- Nice Classes: {nice_classes}
- Style: {style}
- Language preference: {language}
- Avoid similarity to: {avoid_names}

Requirements:
- Each name must be distinctive and memorable
- Use techniques: neologisms, portmanteaus, Latin/Greek roots, metaphors, compound words
- Names should work internationally (easy to pronounce in Turkish and English)
- Avoid generic/descriptive terms that can't be trademarked
- Each name should be 1-3 words maximum

Return ONLY a JSON array of strings, no explanations:
["Name1", "Name2", "Name3", ...]"""

LOGO_GENERATION_PROMPT = """\
Create a professional logo design for the brand "{brand_name}".

Requirements:
- The text "{brand_name}" MUST be clearly visible and legible in the logo
- Style: {style}
- Description: {description}
- Use clean, vector-style graphics suitable for trademark registration
- White or transparent background
- Professional quality, suitable for business use
- The design should be distinctive and unique"""


# Retryable HTTP status codes
_RETRYABLE_CODES = {429, 500, 503}


# ============================================================
# Client
# ============================================================

class GeminiClient:
    """Unified client for Gemini text and image generation."""

    def __init__(self, settings=None):
        """
        Initialize the Gemini client.

        Args:
            settings: CreativeSettings instance (from config.settings).
                      If None, loads from config.settings.settings.creative.
        """
        if settings is None:
            from config.settings import settings as app_settings
            settings = app_settings.creative

        self.api_key: str = settings.google_api_key
        self.text_model: str = settings.gemini_text_model
        self.image_model: str = settings.gemini_image_model
        self.timeout: int = settings.gemini_timeout
        self.max_retries: int = settings.gemini_max_retries

        self._client = None
        self._initialized = False

        if self.api_key:
            self._init_sdk()

    def _init_sdk(self) -> None:
        """Initialize the Google Gen AI SDK client."""
        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            self._initialized = True
            logger.info("gemini_client_initialized",
                        text_model=self.text_model,
                        image_model=self.image_model)
        except Exception as e:
            logger.error("gemini_client_init_failed", error=str(e))
            self._client = None
            self._initialized = False

    def is_available(self) -> bool:
        """Check if Gemini API key is configured and SDK initialized."""
        return bool(self.api_key) and self._initialized and self._client is not None

    # ----------------------------------------------------------
    # Text generation (Name Generator)
    # ----------------------------------------------------------

    async def generate_names(self, prompt: str, count: int = 25) -> list[str]:
        """
        Generate brand name suggestions using Gemini text model.

        Args:
            prompt: Structured prompt with brand context, industry, style preferences.
            count: Number of names to request.

        Returns:
            List of generated name strings (deduplicated, cleaned).

        Raises:
            GeminiError: On API failure after retries.
        """
        if not self.is_available():
            raise GeminiError("Gemini API key not configured", status_code=None)

        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=1.0,
            max_output_tokens=2048,
            response_mime_type="application/json",
        )

        raw_text = await self._call_with_retry(
            model=self.text_model,
            contents=prompt,
            config=config,
        )

        return self._parse_name_list(raw_text, count)

    def build_name_prompt(
        self,
        industry: str = "",
        nice_classes: str = "",
        style: str = "modern",
        language: str = "Turkish and English",
        avoid_names: str = "",
        count: int = 25,
    ) -> str:
        """Build a name generation prompt from parameters."""
        return NAME_GENERATION_PROMPT.format(
            count=count,
            industry=industry or "General",
            nice_classes=nice_classes or "Not specified",
            style=style,
            language=language,
            avoid_names=avoid_names or "None",
        )

    # ----------------------------------------------------------
    # Image generation (Logo Studio)
    # ----------------------------------------------------------

    async def generate_logos(
        self,
        brand_name: str,
        description: str,
        style: str = "modern",
        count: int = 4,
    ) -> list[bytes]:
        """
        Generate logo images using Gemini image model.

        Args:
            brand_name: The brand name to render in the logo.
            description: Visual description / style guide.
            style: Style preset (modern, classic, minimal, bold).
            count: Number of variations (default 4).

        Returns:
            List of image bytes (PNG format).

        Raises:
            GeminiError: On API failure after retries.
        """
        if not self.is_available():
            raise GeminiError("Gemini API key not configured", status_code=None)

        from google.genai import types

        prompt = LOGO_GENERATION_PROMPT.format(
            brand_name=brand_name,
            style=style,
            description=description or f"Professional logo for {brand_name}",
        )

        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        )

        images: list[bytes] = []
        # Generate one at a time since image generation typically returns 1 image
        for i in range(count):
            try:
                image_bytes = await self._generate_single_logo(prompt, config)
                if image_bytes:
                    images.append(image_bytes)
            except GeminiError:
                # If a single variation fails, continue with others
                if i == 0:
                    raise  # If the first one fails, propagate
                logger.warning("logo_variation_failed", variation=i + 1, total=count)
                continue

        if not images:
            raise GeminiError("No logo images were generated", retries_attempted=self.max_retries)

        return images

    async def _generate_single_logo(self, prompt: str, config) -> Optional[bytes]:
        """Generate a single logo image and return its bytes."""
        from google.genai import types

        response = await self._call_with_retry_raw(
            model=self.image_model,
            contents=prompt,
            config=config,
        )

        # Extract image bytes from response parts
        if response and response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.inline_data and part.inline_data.data:
                            return part.inline_data.data

        return None

    # ----------------------------------------------------------
    # Retry logic
    # ----------------------------------------------------------

    async def _call_with_retry(self, model: str, contents, config) -> str:
        """
        Call generate_content with retry logic, return text response.

        Retries on 429 (rate limit) and 500/503 (server errors)
        with exponential backoff: 1s, 2s, 4s.
        """
        response = await self._call_with_retry_raw(model, contents, config)
        return response.text if response else ""

    async def _call_with_retry_raw(self, model: str, contents, config):
        """
        Call generate_content with retry logic, return raw response object.
        """
        from google.genai import errors as genai_errors

        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
                return response

            except genai_errors.APIError as e:
                last_error = e
                status_code = getattr(e, 'code', None) or getattr(e, 'status_code', None)

                if status_code in _RETRYABLE_CODES and attempt < self.max_retries:
                    backoff = 2 ** attempt  # 1s, 2s, 4s
                    logger.warning(
                        "gemini_retry",
                        attempt=attempt + 1,
                        max_retries=self.max_retries,
                        status_code=status_code,
                        backoff_seconds=backoff,
                        error=str(e),
                    )
                    await asyncio.sleep(backoff)
                    continue

                raise GeminiError(
                    message=f"Gemini API error: {e}",
                    status_code=status_code,
                    retries_attempted=attempt,
                ) from e

            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    backoff = 2 ** attempt
                    logger.warning(
                        "gemini_retry_unexpected",
                        attempt=attempt + 1,
                        error=str(e),
                        backoff_seconds=backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue

                raise GeminiError(
                    message=f"Unexpected error calling Gemini: {e}",
                    retries_attempted=attempt,
                ) from e

        # Should not reach here, but safety net
        raise GeminiError(
            message=f"Gemini API failed after {self.max_retries} retries: {last_error}",
            retries_attempted=self.max_retries,
        )

    # ----------------------------------------------------------
    # Response parsing
    # ----------------------------------------------------------

    @staticmethod
    def _parse_name_list(raw_text: str, max_count: int) -> list[str]:
        """
        Parse generated names from Gemini response text.
        Handles JSON arrays, numbered lists, bullet lists, and plain lines.
        Returns deduplicated, cleaned list.
        """
        if not raw_text:
            return []

        names: list[str] = []

        # Try JSON parse first (preferred - we request application/json)
        try:
            parsed = json.loads(raw_text.strip())
            if isinstance(parsed, list):
                names = [str(n).strip() for n in parsed if n]
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: try to extract JSON array from within markdown/text
        if not names:
            json_match = re.search(r'\[.*?\]', raw_text, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                    if isinstance(parsed, list):
                        names = [str(n).strip() for n in parsed if n]
                except (json.JSONDecodeError, ValueError):
                    pass

        # Fallback: line-by-line parsing
        if not names:
            for line in raw_text.split('\n'):
                line = line.strip()
                if not line:
                    continue
                # Remove numbering: "1. ", "1) ", "- ", "* "
                cleaned = re.sub(r'^[\d]+[.)]\s*', '', line)
                cleaned = re.sub(r'^[-*]\s*', '', cleaned)
                # Remove quotes
                cleaned = cleaned.strip('"\'`')
                cleaned = cleaned.strip()
                if cleaned and len(cleaned) <= 60:
                    names.append(cleaned)

        # Deduplicate preserving order, limit count
        seen = set()
        unique_names = []
        for name in names:
            name_lower = name.lower().strip()
            if name_lower and name_lower not in seen:
                seen.add(name_lower)
                unique_names.append(name)

        return unique_names[:max_count]


# ============================================================
# Singleton access
# ============================================================

_instance: Optional[GeminiClient] = None
_lock = threading.Lock()


def get_gemini_client(settings=None) -> GeminiClient:
    """
    Get or create the singleton GeminiClient instance.

    Args:
        settings: Optional CreativeSettings. Only used on first call.

    Returns:
        The shared GeminiClient instance.
    """
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = GeminiClient(settings)
    return _instance


def reset_gemini_client() -> None:
    """Reset the singleton (for testing)."""
    global _instance
    with _lock:
        _instance = None
