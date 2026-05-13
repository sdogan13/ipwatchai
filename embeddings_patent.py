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
    # skip_validation=True: don't ping GitHub for ref freshness on every
    # invocation. The repo source + weights are cached locally after the
    # first download, and a transient GitHub 502 used to kill long --all
    # runs even though the cache was healthy.
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


def embed_image(
    image_path: Path,
    models: LoadedModels,
) -> Dict[str, List[float]]:
    """Generate DINOv2 + CLIP embeddings for one image file.

    Returns ``{"dinov2_vitl14": [...1024 floats...],
              "clip_vitb32":   [...512 floats...]}``.

    On PIL open failure (corrupt file, missing path), returns
    zero-vectors of the right dimensions and logs a warning rather
    than raising — Stage 6 should keep going through bad inputs.
    Stage 4's CD-first dedup already nulled image_paths for files
    we knew were absent, so PIL failures here mean genuinely-
    corrupt-on-disk images.
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
        # L2-normalise CLIP features so cosine similarity == dot product
        # (matches Tasarım convention; the halfvec_cosine_ops index
        # works either way but normalised vectors are easier to reason
        # about across providers).
        clip_feat = clip_feat / clip_feat.norm(dim=-1, keepdim=True).clamp(min=1e-9)
        out["clip_vitb32"] = clip_feat.squeeze(0).cpu().float().tolist()

    return out


# ---------------------------------------------------------------------------
# Text embedding (E5-large; multilingual incl. Turkish)
# ---------------------------------------------------------------------------


def _build_text_prompt(title: Optional[str], abstract: Optional[str]) -> str:
    """Build the input prompt for the E5 text encoder.

    E5 models expect a task prefix: ``passage:`` for documents being
    indexed, ``query:`` for retrieval-time queries. Stage 6 always
    embeds passages — search-time query encoding is a separate
    concern (a future search API would prepend ``query:``).

    Concatenation joins title + abstract with ``. ``. Empty parts
    are dropped. If both are missing/blank, returns ``""`` (caller
    short-circuits to a zero vector).
    """
    parts: List[str] = []
    if title and title.strip():
        parts.append(title.strip())
    if abstract and abstract.strip():
        parts.append(abstract.strip())
    if not parts:
        return ""
    return "passage: " + ". ".join(parts)


def embed_text(
    title: Optional[str],
    abstract: Optional[str],
    models: LoadedModels,
) -> List[float]:
    """Encode title + abstract into a normalised 1024-dim vector.

    Uses ``models.text_encoder`` (a SentenceTransformer wrapping
    ``intfloat/multilingual-e5-large``). ``normalize_embeddings=True``
    L2-normalises so cosine == dot product, matching the CLIP path.

    Empty / blank input returns a zero vector of the right shape so
    downstream code never has to special-case ``None``.
    """
    prompt = _build_text_prompt(title, abstract)
    if not prompt:
        return [0.0] * TEXT_DIM
    vec = models.text_encoder.encode(
        prompt, normalize_embeddings=True, show_progress_bar=False,
    )
    return vec.tolist()


# ---------------------------------------------------------------------------
# Per-record orchestration
# ---------------------------------------------------------------------------


def embed_record(
    record: Dict[str, Any],
    bulletin_folder: Path,
    models: LoadedModels,
    *,
    force: bool = False,
) -> Dict[str, Any]:
    """Compute and attach all embeddings for one record, in place.

    Mutates the input dict:
      - sets ``title_abstract_embedding`` (1024-dim)
      - sets ``embeddings`` on each embeddable figure (dinov2 + clip)
      - sets ``primary_figure_embedding`` (mean-pool of per-figure
        DINOv2; only when there's at least one embeddable figure)

    Skips re-embedding fields that already have valid embeddings,
    unless ``force=True``. ``record_already_embedded`` is the
    short-circuit check at the top.

    ``bulletin_folder`` is the parent ``PT_*`` directory; figure
    ``image_path`` values are resolved as ``bulletin_folder /
    image_path``.

    Returns ``{"text_embedded": 0|1, "figures_embedded": N,
    "primary_aggregated": 0|1, "skipped": bool}`` for caller stats.
    """
    if not force and record_already_embedded(record):
        return {
            "text_embedded": 0, "figures_embedded": 0,
            "primary_aggregated": 0, "skipped": True,
        }

    text_embedded = 0
    if force or not (
        isinstance(record.get("title_abstract_embedding"), list)
        and record["title_abstract_embedding"]
    ):
        record["title_abstract_embedding"] = embed_text(
            record.get("title"), record.get("abstract"), models,
        )
        text_embedded = 1

    figs_embedded = 0
    embeddable = select_embeddable_figures(record)
    for fig in embeddable:
        if not force and figure_already_embedded(fig):
            continue
        full_path = bulletin_folder / fig["image_path"]
        fig["embeddings"] = embed_image(full_path, models)
        figs_embedded += 1

    primary_aggregated = 0
    if embeddable:
        dino_vectors = [
            fig["embeddings"]["dinov2_vitl14"] for fig in embeddable
            if isinstance(fig.get("embeddings"), dict)
            and isinstance(fig["embeddings"].get("dinov2_vitl14"), list)
        ]
        if dino_vectors:
            record["primary_figure_embedding"] = mean_pool(dino_vectors)
            primary_aggregated = 1

    return {
        "text_embedded": text_embedded,
        "figures_embedded": figs_embedded,
        "primary_aggregated": primary_aggregated,
        "skipped": False,
    }


# ---------------------------------------------------------------------------
# Per-bulletin orchestration
# ---------------------------------------------------------------------------


def embed_bulletin(
    bulletin_folder: Path,
    models: LoadedModels,
    *,
    force: bool = False,
    limit: Optional[int] = None,
) -> Dict[str, Any]:
    """Embed every record in one bulletin's metadata.json.

    Reads ``bulletin_folder/metadata.json`` and writes it back with
    per-record + per-figure embeddings attached. Single write at the
    end of the bulletin (each metadata.json is several MB; rewriting
    after every record would dominate runtime).

    ``limit`` caps the records processed (useful for live smoke tests).
    Returns a small stats dict for the CLI summary log.
    """
    metadata_path = bulletin_folder / "metadata.json"
    if not metadata_path.is_file():
        return {"status": "no_metadata", "bulletin": bulletin_folder.name}

    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    if not records:
        return {"status": "empty", "bulletin": bulletin_folder.name}

    started = time.time()
    text_total = figs_total = aggregated_total = skipped_total = 0
    target = records if limit is None else records[:limit]

    for record in target:
        summary = embed_record(record, bulletin_folder, models, force=force)
        text_total += summary["text_embedded"]
        figs_total += summary["figures_embedded"]
        aggregated_total += summary["primary_aggregated"]
        if summary.get("skipped"):
            skipped_total += 1

    metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    duration = time.time() - started
    return {
        "status": "ok",
        "bulletin": bulletin_folder.name,
        "records_processed": len(target),
        "text_embedded": text_total,
        "figures_embedded": figs_total,
        "primary_aggregated": aggregated_total,
        "skipped": skipped_total,
        "duration_seconds": round(duration, 1),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@dataclass
class CLIArgs:
    bulletins_dir: Path
    bulletin_names: List[str]
    all_mode: bool
    device: Optional[str]
    force: bool
    limit: Optional[int]


def parse_argv(argv: Optional[Sequence[str]] = None) -> CLIArgs:
    parser = argparse.ArgumentParser(
        prog="embeddings_patent",
        description="Generate text + figure embeddings for patent bulletins.",
    )
    parser.add_argument(
        "--bulletins-dir", type=Path, default=_LOCAL_DEFAULT_BULLETINS_DIR,
        help="Root containing PT_{Y}_{M}_{date}/ folders.",
    )
    parser.add_argument(
        "--bulletin", action="append", default=[],
        help="Specific PT_{Y}_{M}_{date} folder name. Repeat for multiple.",
    )
    parser.add_argument(
        "--all", action="store_true", dest="all_mode",
        help="Process every PT_*/metadata.json under --bulletins-dir.",
    )
    parser.add_argument(
        "--device", default=None,
        help="Force device (cuda|cpu). Default: auto-detect.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-embed everything, even records already embedded.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most N records per bulletin (smoke testing).",
    )
    ns = parser.parse_args(argv)

    if ns.all_mode and ns.bulletin:
        parser.error("--all is mutually exclusive with --bulletin")
    if not ns.all_mode and not ns.bulletin:
        parser.error("provide --bulletin (one or more) or --all")

    return CLIArgs(
        bulletins_dir=ns.bulletins_dir,
        bulletin_names=list(ns.bulletin),
        all_mode=ns.all_mode,
        device=ns.device,
        force=ns.force,
        limit=ns.limit,
    )


def find_bulletin_folders(args: CLIArgs) -> List[Path]:
    if args.all_mode:
        return sorted(
            p for p in args.bulletins_dir.iterdir()
            if p.is_dir() and p.name.startswith("PT_")
        )
    return [args.bulletins_dir / name for name in args.bulletin_names]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_argv(argv)
    folders = find_bulletin_folders(args)
    if not folders:
        logger.warning("no bulletin folders to process")
        return 1

    device = detect_device(args.device)
    models = load_models(device)

    started = time.time()
    succeeded: List[str] = []
    failed: List[str] = []

    for folder in folders:
        try:
            result = embed_bulletin(
                folder, models, force=args.force, limit=args.limit,
            )
        except Exception as exc:
            logger.error("[!] %s: %r", folder.name, exc)
            failed.append(folder.name)
            continue

        if result["status"] == "ok":
            succeeded.append(folder.name)
            logger.info(
                "[+] %s: %d records (text=%d, figures=%d, primary=%d, "
                "skipped=%d) in %.1fs",
                result["bulletin"], result["records_processed"],
                result["text_embedded"], result["figures_embedded"],
                result["primary_aggregated"], result["skipped"],
                result["duration_seconds"],
            )
        else:
            logger.warning("[~] %s: %s", folder.name, result["status"])

    duration = time.time() - started
    logger.info(
        "Done in %.1fs: %d succeeded, %d failed",
        duration, len(succeeded), len(failed),
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
