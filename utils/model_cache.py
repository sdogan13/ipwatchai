from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    ordered: list[Path] = []
    for path in paths:
        resolved = str(path.expanduser())
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(path.expanduser())
    return ordered


def iter_hf_hub_roots() -> list[Path]:
    home = Path.home()
    roots = _dedupe_paths(
        [
            Path(os.environ["HUGGINGFACE_HUB_CACHE"])
            for _ in [0]
            if os.environ.get("HUGGINGFACE_HUB_CACHE")
        ]
        + [
            Path(os.environ["HF_HOME"]) / "hub"
            for _ in [0]
            if os.environ.get("HF_HOME")
        ]
        + [
            home / ".cache" / "huggingface" / "hub",
            home / "AppData" / "Local" / "huggingface" / "hub",
            home / "AppData" / "Roaming" / "huggingface" / "hub",
        ]
    )
    return [root for root in roots if root.exists()]


def find_hf_snapshot_dir(repo_id: str, required_files: Iterable[str] | None = None) -> Path | None:
    required = list(required_files or [])
    repo_dir_name = f"models--{repo_id.strip('/').replace('/', '--')}"

    for root in iter_hf_hub_roots():
        snapshot_root = root / repo_dir_name / "snapshots"
        if not snapshot_root.exists():
            continue

        candidates: list[Path] = []
        for snapshot in snapshot_root.iterdir():
            if not snapshot.is_dir():
                continue
            if required and any(not (snapshot / name).exists() for name in required):
                continue
            candidates.append(snapshot)

        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime)

    return None


def find_hf_snapshot_file(repo_id: str, filename: str) -> Path | None:
    snapshot = find_hf_snapshot_dir(repo_id, required_files=[filename])
    if snapshot is None:
        return None
    return snapshot / filename


def iter_torch_hub_roots() -> list[Path]:
    home = Path.home()
    roots = _dedupe_paths(
        [
            Path(os.environ["TORCH_HOME"]) / "hub"
            for _ in [0]
            if os.environ.get("TORCH_HOME")
        ]
        + [
            home / ".cache" / "torch" / "hub",
            home / "AppData" / "Local" / "torch" / "hub",
            home / "AppData" / "Roaming" / "torch" / "hub",
        ]
    )
    return [root for root in roots if root.exists()]


def find_torch_hub_repo(repo_id: str) -> Path | None:
    slug = repo_id.strip("/").replace("/", "_").replace("-", "_").lower()
    preferred_suffixes = ("_main", "_master", "")

    for root in iter_torch_hub_roots():
        matches = [
            path for path in root.iterdir()
            if path.is_dir() and path.name.lower().startswith(slug)
        ]
        if not matches:
            continue

        ranked = sorted(
            matches,
            key=lambda path: (
                next(
                    (idx for idx, suffix in enumerate(preferred_suffixes) if path.name.lower().endswith(suffix)),
                    len(preferred_suffixes),
                ),
                -path.stat().st_mtime,
            ),
        )
        return ranked[0]

    return None
