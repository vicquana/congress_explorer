"""
Resolve the Congressional Record Parquet corpus for local and cloud use.

Local development:
    uses data/processed/ when the full local corpus is present.

Deployment:
    downloads the pinned Hugging Face Dataset revision into the standard
    Hugging Face cache, then returns the snapshot's data/ directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import snapshot_download


HF_REPO_ID = "yeeder/congressional-record-parquet"
HF_REVISION = "f3352e5eddac0f4596ba68a0e5bfbcd225449b6c"
EXPECTED_FILE_COUNT = 72
CORPUS_START = 43
CORPUS_END = 114


def _canonical_files(path: Path) -> list[Path]:
    if not path.exists():
        return []

    files = []
    for congress in range(CORPUS_START, CORPUS_END + 1):
        candidate = path / f"congress_{congress:03d}.parquet"
        if candidate.is_file():
            files.append(candidate)
    return files


def resolve_processed_dir(project_root: Path) -> Path:
    """
    Return the directory containing the canonical Congress Parquet files.

    Resolution order:
      1. CONGRESS_DATA_DIR environment variable
      2. local project data/processed/ when all 72 files are present
      3. pinned Hugging Face Dataset snapshot
    """
    explicit = os.getenv("CONGRESS_DATA_DIR")
    if explicit:
        path = Path(explicit).expanduser().resolve()
        files = _canonical_files(path)
        if len(files) != EXPECTED_FILE_COUNT:
            raise RuntimeError(
                f"CONGRESS_DATA_DIR points to {path}, but found "
                f"{len(files)}/{EXPECTED_FILE_COUNT} canonical Parquet files."
            )
        return path

    local = project_root / "data" / "processed"
    local_files = _canonical_files(local)
    if len(local_files) == EXPECTED_FILE_COUNT:
        return local

    snapshot = Path(
        snapshot_download(
            repo_id=HF_REPO_ID,
            repo_type="dataset",
            revision=HF_REVISION,
            allow_patterns=[
                "data/*.parquet",
                "manifest.json",
            ],
        )
    )

    processed = snapshot / "data"
    remote_files = _canonical_files(processed)

    if len(remote_files) != EXPECTED_FILE_COUNT:
        raise RuntimeError(
            "Pinned Hugging Face snapshot is incomplete: "
            f"found {len(remote_files)}/{EXPECTED_FILE_COUNT} Parquet files."
        )

    return processed
