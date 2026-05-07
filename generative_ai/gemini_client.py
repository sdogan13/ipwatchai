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
import io
import json
import re
import threading
from typing import Any, Optional

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
- Brand concept or source name: {concept}
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

GEMINI_LOGO_PANEL_PROMPT = """\
Create one image containing exactly four distinct professional logo options for the brand "{brand_name}".

Layout requirements:
- Use a clean 2x2 grid: top-left, top-right, bottom-left, bottom-right
- Each panel must contain one complete standalone logo option
- Use equal-size panels with clear white gutters or spacing between panels
- Do not add panel labels, numbers, captions, mockups, packaging, devices, or presentation boards
- Do not add extra text beyond the brand text that belongs inside the logo

Logo requirements for every panel:
- The text "{brand_name}" MUST be clearly visible and legible
- Style: {style}
- Description: {description}
- Use clean, vector-style graphics suitable for trademark registration
- White or transparent background
- Professional quality, suitable for business use
- Each panel should use a different visual direction"""

LOGO_REVISION_PROMPT = """\
Revise the provided logo candidate for the brand "{brand_name}".

Keep:
- The text "{brand_name}" clearly visible and legible
- Style direction: {style}
- Original visual brief: {description}

Apply this user revision request:
{revision_prompt}

Requirements:
- Create one standalone logo candidate, not a grid, mockup, package, or presentation board
- Use clean vector-style graphics suitable for trademark registration
- White or transparent background
- Preserve the useful direction from the reference image while making a distinct revised option"""

GEMINI_LOGO_REVISION_PANEL_PROMPT = """\
Revise the provided logo candidate for the brand "{brand_name}" and create exactly four distinct revised logo options in one image.

Layout requirements:
- Use a clean 2x2 grid: top-left, top-right, bottom-left, bottom-right
- Each panel must contain one complete standalone revised logo option
- Use equal-size panels with clear white gutters or spacing between panels
- Do not add panel labels, numbers, captions, mockups, packaging, devices, or presentation boards
- Do not add extra text beyond the brand text that belongs inside the logo

Keep for every panel:
- The text "{brand_name}" clearly visible and legible
- Style direction: {style}
- Original visual brief: {description}

Apply this user revision request:
{revision_prompt}

Requirements:
- Preserve the useful direction from the reference image while making four distinct revised options
- Use clean vector-style graphics suitable for trademark registration
- White or transparent background"""


# Retryable HTTP status codes
_RETRYABLE_CODES = {429, 500, 503}


# ============================================================
# Client
# ============================================================

class GeminiClient:
    """Unified client for Gemini text and image generation."""

    provider_name = "gemini"

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
        self.source_layout: str | None = None
        self.provider_call_count: int = 0

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

    async def generate_json(
        self,
        prompt: str,
        max_output_tokens: int = 4096,
        temperature: float = 0.2,
        system_prompt: str | None = None,
        user_prompt: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Generate a JSON object using the text model."""
        if not self.is_available():
            raise GeminiError("Gemini API key not configured", status_code=None)

        from google.genai import types

        if system_prompt or user_prompt:
            user_content = user_prompt if user_prompt is not None else prompt
            prompt = "\n\n".join(part for part in [system_prompt, user_content] if part)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
        )

        raw_text = await self._call_with_retry(
            model=model or self.text_model,
            contents=prompt,
            config=config,
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
        """Generate a JSON object using the text model with labelled image parts."""
        if not self.is_available():
            raise GeminiError("Gemini API key not configured", status_code=None)

        from google.genai import types

        contents: list[Any] = ["\n\n".join(part for part in [system_prompt, user_prompt] if part)]
        for image in images:
            label = str(image.get("label") or "image")
            image_bytes = image.get("bytes")
            mime_type = image.get("mime_type") or "image/jpeg"
            if not image_bytes:
                continue
            contents.append(f"Attached image: {label}")
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
        )

        raw_text = await self._call_with_retry(
            model=self.text_model,
            contents=contents,
            config=config,
        )

        return self._parse_json_object(raw_text)

    def build_name_prompt(
        self,
        concept: str = "",
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
            concept=concept or "Not specified",
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

        prompt = GEMINI_LOGO_PANEL_PROMPT.format(
            brand_name=brand_name,
            style=style,
            description=description or f"Professional logo for {brand_name}",
        )

        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        )

        panel_bytes = await self._generate_single_logo(prompt, config)
        if not panel_bytes:
            raise GeminiError("No logo panel image was generated", retries_attempted=self.max_retries)
        images = self._split_logo_panel(panel_bytes, count=count)
        self.source_layout = "panel_2x2_split"
        self.provider_call_count = 1
        return images

    async def generate_logo_revisions(
        self,
        brand_name: str,
        description: str,
        style: str = "modern",
        revision_prompt: str = "",
        reference_image_bytes: Optional[bytes] = None,
        count: int = 4,
    ) -> list[bytes]:
        """Generate revised logo candidates from a selected prior candidate."""
        if not self.is_available():
            raise GeminiError("Gemini API key not configured", status_code=None)

        from google.genai import types

        prompt = GEMINI_LOGO_REVISION_PANEL_PROMPT.format(
            brand_name=brand_name,
            style=style,
            description=description or f"Professional logo for {brand_name}",
            revision_prompt=revision_prompt or "Create a refined alternative that improves distinctiveness.",
        )

        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        )

        contents = prompt
        if reference_image_bytes:
            try:
                contents = [
                    prompt,
                    types.Part.from_bytes(
                        data=reference_image_bytes,
                        mime_type="image/png",
                    ),
                ]
            except Exception:
                logger.warning("logo_revision_reference_part_failed")
                contents = prompt

        panel_bytes = await self._generate_single_logo(contents, config)
        if not panel_bytes:
            raise GeminiError("No revised logo panel image was generated", retries_attempted=self.max_retries)
        images = self._split_logo_panel(panel_bytes, count=count)
        self.source_layout = "panel_2x2_split"
        self.provider_call_count = 1
        return images

    async def _generate_single_logo(self, prompt, config) -> Optional[bytes]:
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

    @staticmethod
    def _split_logo_panel(panel_bytes: bytes, count: int = 4) -> list[bytes]:
        """Split a Gemini 2x2 logo panel into standalone square PNG images."""
        try:
            from PIL import Image, ImageChops
        except Exception as exc:  # pragma: no cover - dependency is present in runtime/tests
            raise GeminiError(f"Logo panel splitting requires Pillow: {exc}") from exc

        requested_count = max(1, int(count or 1))
        if requested_count > 4:
            raise GeminiError("Gemini panel splitting supports up to 4 logo options")

        try:
            panel = Image.open(io.BytesIO(panel_bytes))
            panel.load()
        except Exception as exc:
            raise GeminiError(f"Gemini returned an invalid logo panel image: {exc}") from exc

        if panel.width < 256 or panel.height < 256:
            raise GeminiError("Gemini logo panel image is too small to split")

        panel = panel.convert("RGBA")
        mid_x = panel.width // 2
        mid_y = panel.height // 2
        boxes = [
            (0, 0, mid_x, mid_y),
            (mid_x, 0, panel.width, mid_y),
            (0, mid_y, mid_x, panel.height),
            (mid_x, mid_y, panel.width, panel.height),
        ]

        outputs: list[bytes] = []
        for index, box in enumerate(boxes[:requested_count], start=1):
            crop = panel.crop(box)
            if crop.width < 128 or crop.height < 128:
                raise GeminiError(f"Gemini logo panel crop {index} is too small")
            normalized = GeminiClient._normalize_logo_crop(crop)
            if GeminiClient._looks_blank(normalized):
                raise GeminiError(f"Gemini logo panel crop {index} appears blank")
            output = io.BytesIO()
            normalized.save(output, format="PNG")
            outputs.append(output.getvalue())

        if len(set(outputs)) != len(outputs):
            raise GeminiError("Gemini logo panel contained duplicate logo crops")
        return outputs

    @staticmethod
    def _normalize_logo_crop(crop) -> Any:
        """Trim white gutters and center a crop on a square white canvas."""
        from PIL import Image, ImageChops

        rgba = crop.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        flattened = Image.alpha_composite(white, rgba)

        diff = ImageChops.difference(flattened.convert("RGB"), Image.new("RGB", flattened.size, "white"))
        mask = diff.convert("L").point(lambda value: 255 if value > 12 else 0)
        bbox = mask.getbbox()
        if bbox:
            content_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            crop_area = max(1, flattened.width * flattened.height)
            touches_edge = (
                bbox[0] <= 2
                or bbox[1] <= 2
                or bbox[2] >= flattened.width - 2
                or bbox[3] >= flattened.height - 2
            )
            if content_area >= crop_area * 0.01 and not (touches_edge and content_area < crop_area * 0.08):
                flattened = flattened.crop(bbox)

        side = max(flattened.width, flattened.height)
        padding = max(12, int(side * 0.12))
        canvas_side = side + padding * 2
        canvas = Image.new("RGBA", (canvas_side, canvas_side), (255, 255, 255, 255))
        x = (canvas_side - flattened.width) // 2
        y = (canvas_side - flattened.height) // 2
        canvas.alpha_composite(flattened.convert("RGBA"), (x, y))
        return canvas.convert("RGB")

    @staticmethod
    def _looks_blank(image) -> bool:
        from PIL import Image, ImageChops

        rgb = image.convert("RGB")
        width, height = rgb.size
        border = max(4, int(min(width, height) * 0.16))
        if width > border * 2 and height > border * 2:
            rgb = rgb.crop((border, border, width - border, height - border))

        diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white"))
        mask = diff.convert("L").point(lambda value: 255 if value > 16 else 0)
        non_white = mask.histogram()[255]
        return (non_white / max(1, rgb.width * rgb.height)) < 0.005

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

    @staticmethod
    def _parse_json_object(raw_text: str) -> dict[str, Any]:
        """Parse a JSON object from a Gemini text response."""
        if not raw_text:
            raise GeminiError("Gemini returned an empty response")

        try:
            parsed = json.loads(raw_text.strip())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        raise GeminiError("Gemini response was not a JSON object")


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
