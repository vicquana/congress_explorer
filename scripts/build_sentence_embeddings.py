"""
Build a sentence-level semantic-search index for one Congress (pilot scope).

Splits each speech into sentences, embeds every sentence with a local
sentence-transformers model, and writes the sentences + embeddings to
``data/embeddings/pilot_<congress>/``. This is a derived, rebuildable
artifact — the canonical corpus remains the Congress-level Parquet files.

Start with a smoke test on a handful of speeches:

    uv run python scripts/build_sentence_embeddings.py --congress 77 --limit 500 --force

Then build the full pilot Congress:

    uv run python scripts/build_sentence_embeddings.py --congress 77 --force
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pysbd

DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def parquet_path(congress: int) -> Path:
    return project_root() / "data" / "processed" / f"congress_{congress:03d}.parquet"


def default_output_dir(congress: int) -> Path:
    return project_root() / "data" / "embeddings" / f"pilot_{congress:03d}"


def human_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)}m{secs:04.1f}s"
    hours, mins = divmod(minutes, 60)
    return f"{int(hours)}h{int(mins)}m"


def load_speeches(path: Path, limit: int | None) -> tuple[list[int], list[str]]:
    table = pq.read_table(path, columns=["speech_id", "speech_text"])
    if limit is not None:
        table = table.slice(0, limit)

    speech_ids = table.column("speech_id").to_pylist()
    speech_texts = table.column("speech_text").to_pylist()
    return speech_ids, speech_texts


def split_into_sentences(
    speech_ids: list[int],
    speech_texts: list[str],
    *,
    min_words: int,
) -> tuple[list[int], list[int], list[str]]:
    segmenter = pysbd.Segmenter(language="en", clean=False)

    out_speech_ids: list[int] = []
    out_sentence_index: list[int] = []
    out_sentence_text: list[str] = []

    for speech_id, text in zip(speech_ids, speech_texts):
        if not text or not text.strip():
            continue

        try:
            sentences = segmenter.segment(text)
        except Exception:
            continue

        index = 0
        for raw_sentence in sentences:
            sentence = raw_sentence.strip()
            if not sentence:
                continue
            if len(sentence.split()) < min_words:
                continue

            out_speech_ids.append(speech_id)
            out_sentence_index.append(index)
            out_sentence_text.append(sentence)
            index += 1

    return out_speech_ids, out_sentence_index, out_sentence_text


def embed_sentences(
    sentences: list[str],
    *,
    model_name: str,
    batch_size: int,
) -> np.ndarray:
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name, device="cpu")
    embeddings = model.encode(
        sentences,
        batch_size=batch_size,
        show_progress_bar=True,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    return embeddings.astype(np.float32)


def validate_outputs(
    sentence_count: int,
    embeddings: np.ndarray,
    dim: int,
) -> None:
    if embeddings.shape[0] != sentence_count:
        raise RuntimeError(
            f"Embedding row count ({embeddings.shape[0]:,}) does not match "
            f"sentence count ({sentence_count:,})"
        )
    if embeddings.shape[1] != dim:
        raise RuntimeError(
            f"Embedding dimension ({embeddings.shape[1]}) does not match "
            f"model dimension ({dim})"
        )
    if not np.isfinite(embeddings).all():
        raise RuntimeError("Embeddings contain NaN or infinite values")


def build(
    congress: int,
    output_dir: Path,
    *,
    model_name: str,
    batch_size: int,
    min_words: int,
    limit: int | None,
    force: bool,
) -> None:
    source_path = parquet_path(congress)
    if not source_path.is_file():
        raise FileNotFoundError(f"Missing processed Parquet: {source_path}")

    if output_dir.exists():
        if not force:
            raise FileExistsError(
                f"{output_dir} already exists. Use --force to rebuild it."
            )
        shutil.rmtree(output_dir)

    tmp_dir = output_dir.parent / f"{output_dir.name}.tmp"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    print("SENTENCE EMBEDDING BUILD (pilot)")
    print("=" * 72)
    print(f"Congress:   {congress:03d}")
    print(f"Source:     {source_path}")
    print(f"Model:      {model_name}")
    print(f"Batch size: {batch_size}")
    print(f"Min words:  {min_words}")
    if limit is not None:
        print(f"Limit:      {limit:,} speeches (smoke test)")
    print(f"Output:     {output_dir}")
    print()

    started = time.perf_counter()

    print("1. Loading speeches...")
    speech_ids, speech_texts = load_speeches(source_path, limit)
    print(f"   {len(speech_ids):,} speeches loaded")

    print("2. Splitting into sentences...")
    step_started = time.perf_counter()
    out_speech_ids, out_sentence_index, out_sentence_text = split_into_sentences(
        speech_ids, speech_texts, min_words=min_words
    )
    sentence_count = len(out_sentence_text)
    print(
        f"   {sentence_count:,} sentences "
        f"({human_seconds(time.perf_counter() - step_started)})"
    )

    if sentence_count == 0:
        raise RuntimeError("No sentences produced — nothing to embed")

    print("3. Embedding sentences (CPU)...")
    step_started = time.perf_counter()
    embeddings = embed_sentences(
        out_sentence_text, model_name=model_name, batch_size=batch_size
    )
    embed_seconds = time.perf_counter() - step_started
    dim = int(embeddings.shape[1])
    rate = sentence_count / embed_seconds if embed_seconds > 0 else 0.0
    print(
        f"   done in {human_seconds(embed_seconds)} "
        f"({rate:,.0f} sentences/sec, dim={dim})"
    )

    print("4. Validating outputs...")
    validate_outputs(sentence_count, embeddings, dim)
    print("   OK")

    print("5. Writing artifacts...")
    sentences_table = pa.table(
        {
            "sentence_id": pa.array(range(sentence_count), type=pa.int64()),
            "speech_id": pa.array(out_speech_ids),
            "sentence_index": pa.array(out_sentence_index, type=pa.int32()),
            "sentence_text": pa.array(out_sentence_text, type=pa.string()),
        }
    )
    pq.write_table(
        sentences_table,
        tmp_dir / "sentences.parquet",
        compression="zstd",
    )
    np.save(tmp_dir / "embeddings.npy", embeddings)

    source_stat = source_path.stat()
    meta = {
        "congress": congress,
        "model": model_name,
        "dim": dim,
        "sentence_count": sentence_count,
        "min_words": min_words,
        "limit": limit,
        "source_file": source_path.name,
        "source_size": source_stat.st_size,
        "source_mtime_ns": source_stat.st_mtime_ns,
    }
    (tmp_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    tmp_dir.replace(output_dir)

    total_seconds = time.perf_counter() - started
    npy_size = (output_dir / "embeddings.npy").stat().st_size
    print(f"   {output_dir}")
    print()
    print("BUILD COMPLETE")
    print(f"Sentences:  {sentence_count:,}")
    print(f"Embeddings: {npy_size / (1024 * 1024):.1f} MB")
    print(f"Total time: {human_seconds(total_seconds)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a sentence-level semantic-search index for one Congress."
    )
    parser.add_argument(
        "--congress", "-c", type=int, default=77, help="Congress number (43-114)"
    )
    parser.add_argument("--output", type=Path, help="Override the output directory")
    parser.add_argument(
        "--model", type=str, default=DEFAULT_MODEL, help="sentence-transformers model"
    )
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument(
        "--min-words",
        type=int,
        default=3,
        help="Skip sentences shorter than this many whitespace tokens",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N speeches (smoke testing)",
    )
    parser.add_argument(
        "--force", "-f", action="store_true", help="Overwrite an existing output dir"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        output_dir = (
            args.output.resolve()
            if args.output is not None
            else default_output_dir(args.congress)
        )
        build(
            args.congress,
            output_dir,
            model_name=args.model,
            batch_size=args.batch_size,
            min_words=args.min_words,
            limit=args.limit,
            force=args.force,
        )
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
