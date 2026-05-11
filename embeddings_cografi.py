"""Coğrafi İşaret ve Geleneksel Ürün Adı embeddings generator.

Reads each ``bulletins/Cografi_Isaret_ve_Geleneksel_Urun_Adi/CI_*/metadata.json``
and writes embeddings back into the same file:

  * **Text embedding** (multilingual-e5-large, 1024-dim) — per record. Concats
    name + gi_type + product_group + geographical_boundary + usage_description
    + body_sections (product_description, production_method,
    boundary_processing, inspection) using E5's ``passage:`` prefix.
  * **DINOv2 ViT-L/14** (1024-dim) — per-figure visual similarity, plus a
    per-record mean pool stored as ``primary_figure_embedding`` (matches the
    patent convention).
  * **CLIP ViT-B/32** (laion2b_s34b_b79k, 512-dim) — per-figure secondary
    embedding, L2-normalised so cosine == dot product.

HSV histograms are intentionally NOT generated: defer to a follow-up if
downstream retrieval quality calls for color signal.

Per-figure embeddings live under ``record.figures[].embeddings``; per-
record aggregates live directly on the record
(``record.text_embedding`` and ``record.primary_figure_embedding``).
Records without figures still get the text embedding;
``primary_figure_embedding`` stays absent in that case.

CLI::

    python embeddings_cografi.py                      # all bulletins missing aggregates
    python embeddings_cografi.py --issue 220
    python embeddings_cografi.py --device cuda        # default: auto-detect
    python embeddings_cografi.py --force              # re-embed everything
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
_LOCAL_DEFAULT_BULLETINS_DIR = (
    _LOCAL_PROJECT_ROOT / "bulletins" / "Cografi_Isaret_ve_Geleneksel_Urun_Adi"
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [CI-AI] - %(levelname)s - %(message)s")
logger = logging.getLogger("turkpatent.cografi_ai")

DINOV2_DIM = 1024
CLIP_DIM = 512
TEXT_DIM = 1024  # multilingual-e5-large

TEXT_MODEL_NAME = "intfloat/multilingual-e5-large"

# Order matters: this is the order the fields are concatenated into the
# passage embedded by the text encoder. Header fields first, then body
# subsections in their natural reading order.
TEXT_HEADER_FIELDS: Sequence[str] = (
    "name",
    "gi_type",
    "product_group",
    "geographical_boundary",
    "usage_description",
)
TEXT_BODY_SECTION_KEYS: Sequence[str] = (
    "product_description",
    "production_method",
    "boundary_processing",
    "inspection",
)


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


def build_text_passage(record: Dict[str, Any]) -> str:
    """Build the E5 input passage from a cografi record.

    Concatenates header fields and body subsections in fixed order,
    skipping empty values. Prefixed with ``passage:`` per E5 convention
    (search-time queries get ``query:``). Returns ``""`` when no
    embeddable text exists; callers short-circuit to skipping.
    """
    parts: List[str] = []
    for key in TEXT_HEADER_FIELDS:
        val = record.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val.strip())
    body = record.get("body_sections")
    if isinstance(body, dict):
        for key in TEXT_BODY_SECTION_KEYS:
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                parts.append(val.strip())
    if not parts:
        return ""
    return "passage: " + ". ".join(parts)


def figure_already_embedded(figure: Dict[str, Any]) -> bool:
    """True when this figure already has both DINOv2 + CLIP embeddings."""
    emb = figure.get("embeddings")
    if not isinstance(emb, dict):
        return False
    required = ("dinov2_vitl14", "clip_vitb32")
    return all(isinstance(emb.get(k), list) and emb[k] for k in required)


def record_already_embedded(record: Dict[str, Any]) -> bool:
    """True when this record has the text embedding AND, if it has any
    embeddable figures, the per-record mean-pool DINOv2 aggregate."""
    text = record.get("text_embedding")
    if not (isinstance(text, list) and text):
        return False

    embeddable_figs = [
        f for f in (record.get("figures") or [])
        if isinstance(f.get("image_path"), str) and f["image_path"]
    ]
    if not embeddable_figs:
        # No figures → text embedding alone is the complete state.
        return True

    primary = record.get("primary_figure_embedding")
    if not (isinstance(primary, list) and primary):
        return False
    return all(figure_already_embedded(f) for f in embeddable_figs)


def select_embeddable_figures(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return figures whose image_path resolves to a non-empty string.

    Cografi extractor always emits a real path (figures with no on-disk
    file aren't written), but the same guard keeps the helper composable
    with future record shapes.
    """
    return [
        f for f in (record.get("figures") or [])
        if isinstance(f.get("image_path"), str) and f["image_path"]
    ]


def iter_records(metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten ``metadata.records`` into a single list across section_keys."""
    out: List[Dict[str, Any]] = []
    for items in (metadata.get("records") or {}).values():
        if isinstance(items, list):
            out.extend(items)
    return out


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
    text_encoder: Any


def detect_device(requested: Optional[str] = None) -> str:
    """Pick CUDA when available unless overridden."""
    if requested:
        return requested
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def load_models(device: str, *, load_vision: bool = True) -> LoadedModels:
    """Load DINOv2 ViT-L/14 + CLIP ViT-B/32 + multilingual-e5-large.

    Memory budget on a 16 GB GPU:
      DINOv2 ViT-L/14  ≈ 1.2 GB
      CLIP ViT-B/32    ≈ 0.6 GB
      E5-large         ≈ 2.2 GB
      transient batches≈ 0.5 GB
      ─────────────────  ──────
      total at peak    ≈ 4.5 GB

    ``load_vision=False`` is for text-only runs (e.g. when every record
    has zero figures) — saves ~1.8 GB on small / shared GPUs.
    """
    import torch
    from torchvision import transforms

    dinov2 = None
    dinov2_transform = None
    clip_model = None
    clip_preprocess = None

    if load_vision:
        logger.info("Loading DINOv2 ViT-L/14 (1024-dim) on %s...", device)
        # skip_validation=True: don't ping GitHub on every invocation;
        # the weights are cached locally after the first run.
        dinov2 = torch.hub.load(
            "facebookresearch/dinov2", "dinov2_vitl14",
            trust_repo=True, skip_validation=True,
        )
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


# ---------------------------------------------------------------------------
# Per-figure inference
# ---------------------------------------------------------------------------


def embed_image(image_path: Path, models: LoadedModels) -> Dict[str, List[float]]:
    """Generate DINOv2 + CLIP embeddings for one image file.

    Returns ``{"dinov2_vitl14": [...1024 floats...], "clip_vitb32":
    [...512 floats...]}``. On PIL open failure, returns zero vectors of
    the right dimensions and logs a warning — Stage 6 keeps moving
    through bad inputs rather than aborting the batch.
    """
    import torch
    from PIL import Image

    try:
        img = Image.open(image_path).convert("RGB")
    except Exception as e:
        logger.warning("PIL open failed for %s: %r", image_path, e)
        return {
            "dinov2_vitl14": [0.0] * DINOV2_DIM,
            "clip_vitb32":   [0.0] * CLIP_DIM,
        }

    out: Dict[str, List[float]] = {}
    with torch.no_grad():
        dino_input = models.dinov2_transform(img).unsqueeze(0).to(models.device)
        dino_feat = models.dinov2(dino_input)
        out["dinov2_vitl14"] = dino_feat.squeeze(0).cpu().float().tolist()

        clip_input = models.clip_transform(img).unsqueeze(0).to(models.device)
        clip_feat = models.clip.encode_image(clip_input)
        clip_feat = clip_feat / clip_feat.norm(dim=-1, keepdim=True).clamp(min=1e-9)
        out["clip_vitb32"] = clip_feat.squeeze(0).cpu().float().tolist()
    return out


# ---------------------------------------------------------------------------
# Per-record text inference
# ---------------------------------------------------------------------------


def embed_text(record: Dict[str, Any], models: LoadedModels) -> List[float]:
    """Encode the record's concatenated passage into a 1024-dim vector.

    Returns L2-normalised floats. Short-circuits to a zero vector when
    the record has no embeddable text (e.g. an art42 stub without
    structured fields).
    """
    passage = build_text_passage(record)
    if not passage:
        return [0.0] * TEXT_DIM
    vec = models.text_encoder.encode(
        passage,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [float(x) for x in vec]


# ---------------------------------------------------------------------------
# Per-bulletin orchestration
# ---------------------------------------------------------------------------


def _figure_files_path(bulletin_dir: Path, image_path: str) -> Path:
    """Resolve a figure's image_path against the bulletin subfolder."""
    return bulletin_dir / "figures" / image_path


def embed_metadata(
    metadata_path: Path,
    models: LoadedModels,
    *,
    force: bool = False,
) -> Dict[str, int]:
    """In-place embed one bulletin's metadata.json. Returns counters.

    Walks every record, adds text + per-figure + record-aggregate
    embeddings as needed. Idempotent: a record that already satisfies
    ``record_already_embedded`` is skipped unless ``force`` is true.
    """
    raw = metadata_path.read_text(encoding="utf-8")
    metadata = json.loads(raw)
    bulletin_dir = metadata_path.parent

    counters = {"text": 0, "figures": 0, "primary": 0, "skipped": 0}
    records = iter_records(metadata)

    for record in records:
        if not force and record_already_embedded(record):
            counters["skipped"] += 1
            continue

        # Text — always (idempotency above already handled the no-figs case).
        if force or not record.get("text_embedding"):
            record["text_embedding"] = embed_text(record, models)
            counters["text"] += 1

        # Figures — only when present and the vision branch is loaded.
        figs = select_embeddable_figures(record)
        if figs and models.dinov2 is not None and models.clip is not None:
            fig_dinos: List[List[float]] = []
            for fig in figs:
                if not force and figure_already_embedded(fig):
                    emb = fig.get("embeddings", {})
                    dino = emb.get("dinov2_vitl14")
                    if isinstance(dino, list) and dino:
                        fig_dinos.append(dino)
                    continue
                img_path = _figure_files_path(bulletin_dir, fig["image_path"])
                emb = embed_image(img_path, models)
                fig["embeddings"] = emb
                fig_dinos.append(emb["dinov2_vitl14"])
                counters["figures"] += 1
            if fig_dinos:
                record["primary_figure_embedding"] = mean_pool(fig_dinos)
                counters["primary"] += 1

    metadata["embeddings_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return counters


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _iter_metadata(bulletins_root: Path) -> List[Path]:
    out: List[Path] = []
    if not bulletins_root.is_dir():
        return out
    for entry in sorted(bulletins_root.iterdir()):
        if entry.is_dir() and entry.name.startswith("CI_"):
            md = entry / "metadata.json"
            if md.is_file():
                out.append(md)
    return out


def parse_argv(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="embeddings_cografi", add_help=True)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--issue", type=int, help="embed a single bulletin by issue number")
    src.add_argument("--all", action="store_true", help="embed every CI_*/metadata.json under --bulletins-root")
    parser.add_argument(
        "--bulletins-root", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR,
        help=f"bulletins root (default: {_LOCAL_DEFAULT_BULLETINS_DIR})",
    )
    parser.add_argument("--device", type=str, default=None, help="cpu|cuda (default: auto)")
    parser.add_argument("--force", action="store_true", help="re-embed even if already present")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_argv(argv)

    if args.issue is not None:
        matches = sorted(args.bulletins_root.glob(f"CI_{args.issue}_*/metadata.json"))
        if not matches:
            logger.error("no metadata.json found for issue %d", args.issue)
            return 1
        paths = matches[:1]
    else:
        paths = _iter_metadata(args.bulletins_root)
    if not paths:
        logger.warning("no metadata.json inputs found under %s", args.bulletins_root)
        return 0

    device = detect_device(args.device)
    # Decide whether to load vision models: if NO record across the
    # selected metadata.json has any figures, skip vision to save VRAM.
    any_figures = False
    for p in paths:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if any(select_embeddable_figures(r) for r in iter_records(d)):
                any_figures = True
                break
        except Exception:
            continue
    models = load_models(device, load_vision=any_figures)

    totals = {"text": 0, "figures": 0, "primary": 0, "skipped": 0}
    started = time.time()
    for p in paths:
        try:
            c = embed_metadata(p, models, force=args.force)
        except Exception as exc:
            logger.error("[!] %s: %r", p.relative_to(args.bulletins_root), exc)
            continue
        for k, v in c.items():
            totals[k] += v
        logger.info("[+] %s | text=%d figs=%d primary=%d skipped=%d",
                    p.parent.name, c["text"], c["figures"], c["primary"], c["skipped"])
    logger.info("done in %.1fs | totals: %s", time.time() - started, totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
