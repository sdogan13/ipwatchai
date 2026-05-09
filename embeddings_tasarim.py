"""Tasarım design embeddings generator.

Reads each ``bulletins/Tasarim/TS_*/metadata.json`` and writes embeddings
back into the same file:

  * **DINOv2 ViT-L/14** (1024-dim) — primary visual similarity. Per view +
    mean-pool per design.
  * **CLIP ViT-B/32** (laion2b_s34b_b79k, 512-dim) — secondary, category-aware.
    Per view + mean-pool per design.
  * **HSV histogram** (8x8x8 = 512 dim, normalized) — color, per view only.

OCR is intentionally NOT generated for designs (design photos rarely contain
text and EasyOCR cost is wasted).

Per-view embeddings live under ``view.embeddings``; per-design aggregates
under ``design.design_aggregates``. Records without images (Hague, deferred)
are skipped entirely.

CLI::

    python embeddings_tasarim.py                          # all issues missing aggregates
    python embeddings_tasarim.py --issue TS_483_2026-04-24
    python embeddings_tasarim.py --device cuda            # default: auto-detect
    python embeddings_tasarim.py --force                  # re-embed all views
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


_LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
_LOCAL_DEFAULT_BULLETINS_DIR = _LOCAL_PROJECT_ROOT / "bulletins" / "Tasarim"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [TASARIM-AI] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.tasarim_ai")

DINOV2_DIM = 1024
CLIP_DIM = 512
COLOR_DIM = 512  # 8 x 8 x 8 HSV bins
HSV_BINS = 8


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without GPU)
# ---------------------------------------------------------------------------

def mean_pool(vectors: Sequence[Sequence[float]]) -> List[float]:
    """Mean-pool a list of equal-length float vectors. Empty input → []."""
    if not vectors:
        return []
    width = len(vectors[0])
    if width == 0:
        return []
    if any(len(v) != width for v in vectors):
        raise ValueError("mean_pool requires equal-length vectors")
    out = [0.0] * width
    for v in vectors:
        for i, x in enumerate(v):
            out[i] += float(x)
    n = len(vectors)
    return [x / n for x in out]


def view_already_embedded(view: Dict[str, Any]) -> bool:
    """True when this view already has all three required embedding fields."""
    emb = view.get("embeddings")
    if not isinstance(emb, dict):
        return False
    required = ("dinov2_vitl14", "clip_vitb32", "color_hsv")
    return all(isinstance(emb.get(k), list) and emb[k] for k in required)


def design_already_aggregated(design: Dict[str, Any]) -> bool:
    """True when this design already has aggregated DINOv2 + CLIP means."""
    agg = design.get("design_aggregates")
    if not isinstance(agg, dict):
        return False
    required = ("dinov2_vitl14_mean", "clip_vitb32_mean")
    return all(isinstance(agg.get(k), list) and agg[k] for k in required)


def select_embeddable_records(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return records that are eligible for image embedding.

    Eligible: section in {tr_native, deferred_lifted, republished}.
    Skipped: hague (no images), deferred (metadata-only).
    """
    eligible_sections = {"tr_native", "deferred_lifted", "republished"}
    return [r for r in payload.get("records", []) if r.get("section") in eligible_sections]


def aggregate_design_embeddings(views: List[Dict[str, Any]], key: str) -> List[float]:
    """Mean-pool a per-view embedding key across the views that have it set."""
    vectors = [v["embeddings"][key] for v in views
               if isinstance(v.get("embeddings"), dict) and isinstance(v["embeddings"].get(key), list)]
    return mean_pool(vectors)


def resolve_view_image_path(
    issue_folder: Path, view: Dict[str, Any],
) -> Optional[Path]:
    """Resolve a view's ``image_path`` against its containing TS folder.

    Routing rules — driven by the ``image_source`` provenance tag added
    in stage B.1 of the canonical-folder push:

      - ``image_source == "cd"``  -> ``issue_folder / "cd_images" / image_path``
      - ``image_source == "pdf"`` -> ``issue_folder / "images" / image_path``
      - ``None`` (legacy data):    -> ``issue_folder / image_path`` —
        pre-stage-B.1 metadata.json files shipped image_path with the
        ``"images/..."`` prefix baked in, so resolving against the
        TS folder root works for those.

    Returns ``None`` when ``image_path`` is missing/empty (e.g. Hague
    views where no JPEG was located/persisted).

    Without this routing the embedder would look in ``TS_*/2016_01059/``
    instead of ``TS_*/cd_images/2016_01059/``, find no file, and mark
    every view as failed.
    """
    rel = view.get("image_path")
    if not rel:
        return None
    source = view.get("image_source")
    if source == "cd":
        return issue_folder / "cd_images" / rel
    if source == "pdf":
        return issue_folder / "images" / rel
    return issue_folder / rel  # legacy fallback


# ---------------------------------------------------------------------------
# Image processing (HSV color histogram — pure, unit-testable)
# ---------------------------------------------------------------------------

def hsv_histogram(image_path: Path, bins: int = HSV_BINS) -> List[float]:
    """Compute an 8x8x8 normalized HSV histogram for the image at ``image_path``.

    Returns a flat ``bins**3`` length list of floats summing to 1.0 (or all
    zeros if the image can't be loaded).
    """
    import cv2
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        return [0.0] * (bins ** 3)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [bins, bins, bins],
                        [0, 180, 0, 256, 0, 256])
    total = float(hist.sum())
    if total > 0:
        hist = hist / total
    return hist.flatten().astype("float32").tolist()


# ---------------------------------------------------------------------------
# Model loaders (lazy, GPU-aware)
# ---------------------------------------------------------------------------

@dataclass
class LoadedModels:
    device: str
    dinov2: Any
    dinov2_transform: Any
    clip: Any
    clip_transform: Any


def detect_device(requested: Optional[str] = None) -> str:
    if requested:
        return requested
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def load_models(device: str) -> LoadedModels:
    """Load DINOv2 ViT-L/14 + CLIP ViT-B/32 once. ~3 GB GPU memory at load."""
    import torch
    from torchvision import transforms

    logger.info("Loading DINOv2 ViT-L/14 (1024-dim) on %s...", device)
    dinov2 = torch.hub.load("facebookresearch/dinov2", "dinov2_vitl14", trust_repo=True)
    dinov2 = dinov2.to(device).eval()
    dinov2_transform = transforms.Compose([
        transforms.Resize(256, interpolation=transforms.InterpolationMode.BICUBIC),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])

    logger.info("Loading CLIP ViT-B/32 (512-dim) on %s...", device)
    import open_clip
    clip_model, _, clip_preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k"
    )
    clip_model = clip_model.to(device).eval()

    return LoadedModels(
        device=device,
        dinov2=dinov2,
        dinov2_transform=dinov2_transform,
        clip=clip_model,
        clip_transform=clip_preprocess,
    )


# ---------------------------------------------------------------------------
# Per-image inference
# ---------------------------------------------------------------------------

def embed_image(image_path: Path, models: LoadedModels) -> Dict[str, List[float]]:
    """Generate all three embeddings (DINOv2, CLIP, HSV) for one image."""
    import torch
    from PIL import Image

    out: Dict[str, List[float]] = {}

    try:
        img = Image.open(image_path).convert("RGB")
    except Exception as e:
        logger.warning("PIL open failed for %s: %r", image_path, e)
        return {
            "dinov2_vitl14": [0.0] * DINOV2_DIM,
            "clip_vitb32": [0.0] * CLIP_DIM,
            "color_hsv": [0.0] * COLOR_DIM,
        }

    with torch.no_grad():
        dino_input = models.dinov2_transform(img).unsqueeze(0).to(models.device)
        dino_feat = models.dinov2(dino_input)
        out["dinov2_vitl14"] = dino_feat.squeeze(0).cpu().float().tolist()

        clip_input = models.clip_transform(img).unsqueeze(0).to(models.device)
        clip_feat = models.clip.encode_image(clip_input)
        clip_feat = clip_feat / clip_feat.norm(dim=-1, keepdim=True).clamp(min=1e-9)
        out["clip_vitb32"] = clip_feat.squeeze(0).cpu().float().tolist()

    out["color_hsv"] = hsv_histogram(image_path)
    return out


# ---------------------------------------------------------------------------
# Issue-level orchestration
# ---------------------------------------------------------------------------

def embed_issue(
    issue_folder: Path,
    *,
    force: bool = False,
    models: Optional[LoadedModels] = None,
    strict: bool = True,
) -> Dict[str, Any]:
    metadata_path = issue_folder / "metadata.json"
    if not metadata_path.is_file():
        if strict:
            raise FileNotFoundError(f"missing metadata.json in {issue_folder}")
        logger.info("[~] %s has no metadata.json yet, skipping", issue_folder.name)
        return {"status": "not_ready", "issue": issue_folder.name, "embedded": 0}

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    eligible = select_embeddable_records(payload)
    if not eligible:
        logger.info("[=] %s has no image-bearing records, nothing to embed", issue_folder.name)
        return {"status": "skipped", "issue": issue_folder.name, "embedded": 0}

    if models is None:
        models = load_models(detect_device())

    started = time.time()
    embedded_views = 0
    skipped_views = 0
    failed_views = 0
    embedded_aggregates = 0

    for record in eligible:
        record_dirty = False
        for design in record.get("designs", []):
            views = design.get("views", [])
            for view in views:
                image_path = resolve_view_image_path(issue_folder, view)
                if image_path is None:
                    continue
                if not force and view_already_embedded(view):
                    skipped_views += 1
                    continue
                if not image_path.is_file():
                    failed_views += 1
                    continue
                try:
                    view["embeddings"] = embed_image(image_path, models)
                    embedded_views += 1
                    record_dirty = True
                except Exception as e:
                    logger.warning("embed failed for %s: %r", image_path, e)
                    failed_views += 1

            # Per-design aggregates: only compute if design has at least one
            # embedded view, and either force or aggregate is missing.
            if not force and design_already_aggregated(design):
                continue
            valid_views = [v for v in views if isinstance(v.get("embeddings"), dict)]
            if not valid_views:
                continue
            agg = {
                "dinov2_vitl14_mean": aggregate_design_embeddings(valid_views, "dinov2_vitl14"),
                "clip_vitb32_mean": aggregate_design_embeddings(valid_views, "clip_vitb32"),
            }
            if any(agg.values()):
                design["design_aggregates"] = agg
                embedded_aggregates += 1
                record_dirty = True

        if record_dirty:
            # Saving inside the loop is overkill; we save once at the end.
            pass

    payload["embeddings_extracted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    payload["embeddings_duration_seconds"] = round(time.time() - started, 1)
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "[+] %s: embedded=%d skipped=%d failed=%d designs_aggregated=%d in %.1fs",
        issue_folder.name, embedded_views, skipped_views, failed_views,
        embedded_aggregates, payload["embeddings_duration_seconds"],
    )
    return {
        "status": "ok",
        "issue": issue_folder.name,
        "embedded": embedded_views,
        "skipped": skipped_views,
        "failed": failed_views,
        "designs_aggregated": embedded_aggregates,
        "duration_seconds": payload["embeddings_duration_seconds"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def find_issue_folders(bulletins_root: Path) -> List[Path]:
    if not bulletins_root.is_dir():
        return []
    return sorted(p for p in bulletins_root.iterdir() if p.is_dir() and p.name.startswith("TS_"))


def parse_argv(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="embeddings_tasarim", add_help=True)
    parser.add_argument("--issue", type=str, default=None,
                        help="single issue folder name (e.g. TS_483_2026-04-24)")
    parser.add_argument("--bulletins-root", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR)
    parser.add_argument("--force", action="store_true",
                        help="re-embed even when fields are present")
    parser.add_argument("--device", type=str, default=None,
                        help="cuda or cpu (default: auto-detect)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_argv(argv)
    device = detect_device(args.device)
    if device == "cpu":
        logger.warning("Running on CPU — DINOv2 ViT-L/14 inference will be very slow.")

    if args.issue:
        target = args.bulletins_root / args.issue
        result = embed_issue(target, force=args.force)
        return 0 if result.get("status") in {"ok", "skipped"} else 1

    folders = find_issue_folders(args.bulletins_root)
    if not folders:
        logger.warning("no TS_* folders under %s", args.bulletins_root)
        return 0

    models = load_models(device)
    logger.info("scanning %d issue folder(s) under %s", len(folders), args.bulletins_root)
    failed = 0
    for folder in folders:
        try:
            # Batch mode: missing metadata.json is "not yet ready", not an error.
            embed_issue(folder, force=args.force, models=models, strict=False)
        except Exception as e:
            logger.exception("issue %s failed: %r", folder.name, e)
            failed += 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
