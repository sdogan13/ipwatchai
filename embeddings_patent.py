"""Patent / Faydalı Model embeddings generator.

Reads each ``bulletins/Patent__Faydali_Model/PT_*/metadata.json`` and
writes embeddings back into the same file:

  * **Title + abstract text embedding** (multilingual-e5-large, 1024-dim) —
    new for patents; Tasarım skipped this since designs are mostly visual.
  * **DINOv2 ViT-L/14** (1024-dim) — per-figure visual similarity, plus
    a per-record mean pool stored as ``primary_figure_embedding``.
  * **CLIP ViT-B/32** (laion2b_s34b_b79k, 512-dim) — per-figure
    secondary embedding.

HSV histograms (used by Tasarım) are intentionally NOT generated:
patent figures are mostly line drawings where colour carries no signal.

Per-figure embeddings live under each ``record.figures[].embeddings``
(matches the Tasarım convention). Per-record aggregates live directly
on the record (``title_abstract_embedding`` + ``primary_figure_embedding``).
Records without any image_path-resolvable figures still get the text
embedding; ``primary_figure_embedding`` stays absent in that case.

CLI::

    python embeddings_patent.py                      # all bulletins missing aggregates
    python embeddings_patent.py --bulletin PT_2025_8_2025-08-21
    python embeddings_patent.py --device cuda        # default: auto-detect
    python embeddings_patent.py --force              # re-embed everything
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
_LOCAL_DEFAULT_BULLETINS_DIR = _LOCAL_PROJECT_ROOT / "bulletins" / "Patent__Faydali_Model"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [PATENT-AI] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.patent_ai")

DINOV2_DIM = 1024
CLIP_DIM = 512
TEXT_DIM = 1024  # multilingual-e5-large

TEXT_MODEL_NAME = "intfloat/multilingual-e5-large"


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


def figure_already_embedded(figure: Dict[str, Any]) -> bool:
    """True when this figure already has both DINOv2 + CLIP embeddings."""
    emb = figure.get("embeddings")
    if not isinstance(emb, dict):
        return False
    required = ("dinov2_vitl14", "clip_vitb32")
    return all(isinstance(emb.get(k), list) and emb[k] for k in required)


def record_already_embedded(record: Dict[str, Any]) -> bool:
    """True when this record has the text embedding AND, if it has any
    embeddable figures, the per-record figure mean-pool aggregate."""
    text = record.get("title_abstract_embedding")
    if not (isinstance(text, list) and text):
        return False

    embeddable_figs = [
        f for f in record.get("figures", [])
        if isinstance(f.get("image_path"), str) and f["image_path"]
    ]
    if not embeddable_figs:
        # No figures with a resolvable image_path → text embedding alone is
        # the complete state for this record.
        return True

    primary = record.get("primary_figure_embedding")
    if not (isinstance(primary, list) and primary):
        return False
    return all(figure_already_embedded(f) for f in embeddable_figs)


def select_embeddable_figures(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return figures whose image_path resolves to a real file path
    (excludes figures dedup'd against CD TIFFs whose image_path is None
    — those keep their page/xref metadata but no on-disk image)."""
    return [
        f for f in record.get("figures", [])
        if isinstance(f.get("image_path"), str) and f["image_path"]
    ]


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
    text_encoder: Any   # SentenceTransformer wrapping multilingual-e5-large


def detect_device(requested: Optional[str] = None) -> str:
    """Pick CUDA when available unless overridden."""
    if requested:
        return requested
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def load_models(device: str) -> LoadedModels:
    """Load DINOv2 ViT-L/14 + CLIP ViT-B/32 + multilingual-E5-large.

    Memory budget on the user's RTX 4070 (16 GB):
      DINOv2 ViT-L/14   ≈ 1.2 GB
      CLIP ViT-B/32     ≈ 0.6 GB
      E5-large          ≈ 2.2 GB
      transient batches ≈ 0.5 GB
      ─────────────────  ──────
      total at peak     ≈ 4.5 GB  (well under 16 GB)
    """
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
        "ViT-B-32", pretrained="laion2b_s34b_b79k",
    )
    clip_model = clip_model.to(device).eval()

    logger.info("Loading %s (1024-dim) on %s...", TEXT_MODEL_NAME, device)
    from sentence_transformers import SentenceTransformer
    text_encoder = SentenceTransformer(TEXT_MODEL_NAME, device=device)

    return LoadedModels(
        device=device,
        dinov2=dinov2,
        dinov2_transform=dinov2_transform,
        clip=clip_model,
        clip_transform=clip_preprocess,
        text_encoder=text_encoder,
    )
