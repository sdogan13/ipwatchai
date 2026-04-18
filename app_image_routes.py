"""Trademark image lookup and route helpers for the legacy FastAPI app."""

import logging
import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse


MODULE_LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent
LOGOS_DIR = PROJECT_ROOT / "bulletins" / "Marka" / "LOGOS"
IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".bmp", ".tif", ".gif", ".webp"]
MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".webp": "image/webp",
}

_IMAGE_INDEX: dict[str, str] = {}
_IMAGE_INDEX_BUILT = False


def _build_image_index(logger=None):
    """Build a basename-to-path index across bulletin image folders."""
    global _IMAGE_INDEX, _IMAGE_INDEX_BUILT
    if _IMAGE_INDEX_BUILT:
        return

    import glob as glob_mod

    marka_root = PROJECT_ROOT / "bulletins" / "Marka"
    count = 0
    for images_dir in glob_mod.glob(str(marka_root / "*/images")):
        for file_name in os.listdir(images_dir):
            basename_no_ext = os.path.splitext(file_name)[0]
            if basename_no_ext not in _IMAGE_INDEX:
                _IMAGE_INDEX[basename_no_ext] = os.path.join(images_dir, file_name)
                count += 1

    if LOGOS_DIR.is_dir():
        for file_name in os.listdir(LOGOS_DIR):
            basename_no_ext = os.path.splitext(file_name)[0]
            if basename_no_ext not in _IMAGE_INDEX:
                _IMAGE_INDEX[basename_no_ext] = str(LOGOS_DIR / file_name)
                count += 1

    _IMAGE_INDEX_BUILT = True
    (logger or MODULE_LOGGER).info("Image index built: %s images across bulletin folders", count)


def _find_in_logos(basename: str, logger=None) -> str | None:
    """Try to find an image by bare filename across bulletin folders."""
    if not basename:
        return None

    basename_no_ext = os.path.splitext(basename)[0] if "." in basename else basename
    for ext in IMAGE_EXTENSIONS:
        full_path = LOGOS_DIR / f"{basename_no_ext}{ext}"
        if full_path.is_file():
            return str(full_path)

    full_path = LOGOS_DIR / basename
    if full_path.is_file():
        return str(full_path)

    _build_image_index(logger)
    found = _IMAGE_INDEX.get(basename_no_ext)
    if found and os.path.isfile(found):
        return found
    return None


def find_trademark_image(image_path: str, logger=None) -> str | None:
    """Resolve an image path to an absolute filesystem path."""
    if not image_path:
        return None

    if ".." in image_path:
        return None

    if "/" in image_path:
        full_path = PROJECT_ROOT / image_path.replace("/", os.sep)
        if full_path.is_file():
            return str(full_path)

        if "/images/" in image_path:
            root_path = image_path.replace("/images/", "/")
            root_full = PROJECT_ROOT / root_path.replace("/", os.sep)
            if root_full.is_file():
                return str(root_full)

        basename = os.path.splitext(os.path.basename(image_path))[0]
        return _find_in_logos(basename, logger)

    return _find_in_logos(image_path, logger)


def register_trademark_image_routes(app, logger=None):
    """Register the trademark image file-serving endpoint."""

    @app.get("/api/trademark-image/{image_path:path}", tags=["Images"])
    async def serve_trademark_image(image_path: str):
        """
        Serve a trademark logo image.

        Accepts both formats:
        - New: /api/trademark-image/bulletins/Marka/BLT_253/images/2011_41714.jpg
        - Legacy: /api/trademark-image/2005_28311
        """
        file_path = find_trademark_image(image_path, logger)
        if not file_path:
            raise HTTPException(status_code=404, detail="Image not found")

        ext = os.path.splitext(file_path)[1].lower()
        media_type = MEDIA_TYPES.get(ext, "image/jpeg")

        return FileResponse(
            path=file_path,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )
