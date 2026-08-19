"""
Process Stanford Congressional Record ZIP archives directly into Parquet files.

The processor reads the three core Stanford files for each Congress directly
from the source ZIP archive:

    speeches_XXX.txt
    descr_XXX.txt
    XXX_SpeakerMap.txt

ZIP member reading is delegated to ``archive_reader.py`` so the Stanford 4 GiB
offset quirk is handled in one place. Speech records are written to Parquet in
bounded batches rather than accumulating an entire Congress in memory.

Phase 1 research scope defaults to Congresses 077-096 (approximately
1941-1981), all from ``hein-bound.zip``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pyarrow as pa
import pyarrow.parquet as pq

try:
    # Works when imported as ``scripts.process_corpus``.
    from .archive_reader import (
        StanfordArchive,
        congress_member_names,
        get_archive_for_congress,
    )
except ImportError:
    # Works when run directly: ``python scripts/process_corpus.py``.
    from archive_reader import (  # type: ignore
        StanfordArchive,
        congress_member_names,
        get_archive_for_congress,
    )


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("process_corpus")

PHASE1_START = 77
PHASE1_END = 96
DEFAULT_BATCH_SIZE = 10_000


PARQUET_SCHEMA = pa.schema(
    [
        pa.field("speech_id", pa.int64()),
        pa.field("congress", pa.int32(), nullable=False),
        pa.field("date", pa.string()),
        pa.field("year", pa.int32()),
        pa.field("chamber", pa.string()),
        pa.field("speaker_id", pa.int64()),
        pa.field("first_name", pa.string()),
        pa.field("last_name", pa.string()),
        pa.field("speaker", pa.string()),
        pa.field("state", pa.string()),
        pa.field("gender", pa.string()),
        pa.field("party", pa.string()),
        pa.field("district", pa.string()),
        pa.field("nonvoting", pa.string()),
        pa.field("char_count", pa.int32()),
        pa.field("word_count", pa.int32()),
        pa.field("speech_text", pa.string()),
        pa.field("source", pa.string(), nullable=False),
    ]
)


@dataclass
class CongressStats:
    congress: int
    source: str
    total_speeches: int = 0
    matched_descr: int = 0
    matched_smap: int = 0
    malformed_speech_lines: int = 0
    duplicate_descr_ids: int = 0
    duplicate_smap_ids: int = 0


class BatchBuffer:
    """Column-oriented in-memory buffer for one Parquet write batch."""

    def __init__(self) -> None:
        self.columns: dict[str, list] = {field.name: [] for field in PARQUET_SCHEMA}

    def __len__(self) -> int:
        return len(self.columns["speech_id"])

    def append(self, record: dict[str, object]) -> None:
        for name in self.columns:
            self.columns[name].append(record[name])

    def to_table(self) -> pa.Table:
        return pa.Table.from_pydict(self.columns, schema=PARQUET_SCHEMA)

    def clear(self) -> None:
        for values in self.columns.values():
            values.clear()


def _clean_unknown(value: str) -> str:
    """Normalize Stanford placeholder strings used for missing metadata."""
    if value in {"", "Unknown", "None"}:
        return ""
    return value


def _safe_int(value: str) -> Optional[int]:
    value = value.strip()
    if value.isdigit():
        return int(value)
    return None


def _load_descr_map(
    archive: StanfordArchive,
    member_name: str,
    stats: CongressStats,
) -> dict[str, tuple[str, ...]]:
    """Load speech-level description metadata keyed by speech_id."""
    descr_map: dict[str, tuple[str, ...]] = {}

    try:
        lines = archive.iter_member_lines(member_name, validate=True)
        header = next(lines, None)
        if header is None:
            logger.warning("Empty description file: %s", member_name)
            return descr_map

        for line_number, line in enumerate(lines, start=2):
            parts = line.rstrip("\r\n").split("|")
            if len(parts) < 14:
                logger.warning(
                    "Skipping malformed descr line %s:%d (%d fields)",
                    member_name,
                    line_number,
                    len(parts),
                )
                continue

            speech_id = parts[0]
            if speech_id in descr_map:
                stats.duplicate_descr_ids += 1

            # chamber, date, raw speaker, first, last, state, gender,
            # char_count, word_count
            descr_map[speech_id] = (
                parts[1],
                parts[2],
                parts[4],
                parts[5],
                parts[6],
                parts[7],
                parts[8],
                parts[12],
                parts[13],
            )

    except KeyError:
        logger.warning("Description file not found: %s", member_name)

    return descr_map


def _load_speakermap(
    archive: StanfordArchive,
    member_name: str,
    stats: CongressStats,
) -> dict[str, tuple[str, ...]]:
    """Load normalized speaker metadata keyed by speech_id."""
    smap_map: dict[str, tuple[str, ...]] = {}

    try:
        lines = archive.iter_member_lines(member_name, validate=True)
        header = next(lines, None)
        if header is None:
            logger.warning("Empty SpeakerMap file: %s", member_name)
            return smap_map

        for line_number, line in enumerate(lines, start=2):
            parts = line.rstrip("\r\n").split("|")
            if len(parts) < 10:
                logger.warning(
                    "Skipping malformed SpeakerMap line %s:%d (%d fields)",
                    member_name,
                    line_number,
                    len(parts),
                )
                continue

            speech_id = parts[1]
            if speech_id in smap_map:
                stats.duplicate_smap_ids += 1

            # speakerid, lastname, firstname, chamber, state, gender,
            # party, district, nonvoting
            smap_map[speech_id] = (
                parts[0],
                parts[2],
                parts[3],
                parts[4],
                parts[5],
                parts[6],
                parts[7],
                parts[8],
                parts[9],
            )

    except KeyError:
        logger.warning("SpeakerMap file not found: %s", member_name)

    return smap_map


def _build_record(
    *,
    congress_num: int,
    source: str,
    speech_id_raw: str,
    text: str,
    descr: tuple[str, ...] | None,
    speaker_meta: tuple[str, ...] | None,
) -> dict[str, object]:
    """Resolve one speech plus optional metadata into the Parquet schema."""
    speech_id = _safe_int(speech_id_raw)

    date_str = descr[1] if descr else ""
    year = int(date_str[:4]) if len(date_str) >= 4 and date_str[:4].isdigit() else None

    chamber = ""
    if speaker_meta and speaker_meta[3]:
        chamber = _clean_unknown(speaker_meta[3])
    elif descr:
        chamber = _clean_unknown(descr[0])

    speaker_id = _safe_int(speaker_meta[0]) if speaker_meta else None

    first_name = ""
    last_name = ""
    state = ""
    gender = ""

    if speaker_meta:
        first_name = _clean_unknown(speaker_meta[2])
        last_name = _clean_unknown(speaker_meta[1])
        state = _clean_unknown(speaker_meta[4])
        gender = _clean_unknown(speaker_meta[5])

    if descr:
        if not first_name:
            first_name = _clean_unknown(descr[3])
        if not last_name:
            last_name = _clean_unknown(descr[4])
        if not state:
            state = _clean_unknown(descr[5])
        if not gender:
            gender = _clean_unknown(descr[6])

    raw_speaker = _clean_unknown(descr[2]) if descr else ""
    party = _clean_unknown(speaker_meta[6]) if speaker_meta else ""
    district = _clean_unknown(speaker_meta[7]) if speaker_meta else ""
    nonvoting = _clean_unknown(speaker_meta[8]) if speaker_meta else ""

    char_count = _safe_int(descr[7]) if descr else None
    if char_count is None:
        char_count = len(text)

    word_count = _safe_int(descr[8]) if descr else None
    if word_count is None:
        word_count = len(text.split())

    return {
        "speech_id": speech_id,
        "congress": congress_num,
        "date": date_str,
        "year": year,
        "chamber": chamber,
        "speaker_id": speaker_id,
        "first_name": first_name,
        "last_name": last_name,
        "speaker": raw_speaker,
        "state": state,
        "gender": gender,
        "party": party,
        "district": district,
        "nonvoting": nonvoting,
        "char_count": char_count,
        "word_count": word_count,
        "speech_text": text,
        "source": source,
    }


def _flush_batch(
    writer: pq.ParquetWriter,
    batch: BatchBuffer,
) -> int:
    """Write one in-memory batch and return the number of rows written."""
    if len(batch) == 0:
        return 0

    rows = len(batch)
    table = batch.to_table()
    writer.write_table(table)
    batch.clear()
    return rows


def _validate_parquet_file(path: Path, expected_rows: int) -> None:
    """Perform a lightweight structural check before publishing the file."""
    parquet_file = pq.ParquetFile(path)
    actual_rows = parquet_file.metadata.num_rows

    if actual_rows != expected_rows:
        raise RuntimeError(
            f"Parquet row-count mismatch for {path.name}: "
            f"expected {expected_rows:,}, got {actual_rows:,}"
        )

    if parquet_file.schema_arrow != PARQUET_SCHEMA:
        raise RuntimeError(
            f"Parquet schema mismatch for {path.name}:\n"
            f"expected: {PARQUET_SCHEMA}\n"
            f"actual:   {parquet_file.schema_arrow}"
        )


def process_single_congress(
    congress_num: int,
    out_dir: Path,
    *,
    force: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> Path:
    """
    Process one Congress into a compressed Parquet file.

    The final filename appears only after the ZIP members have been fully read,
    archive integrity validation has succeeded, Parquet writing has completed,
    and the output row count/schema have been validated.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than zero")

    c_str = f"{congress_num:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / f"congress_{c_str}.parquet"
    tmp_file = out_dir / f"congress_{c_str}.parquet.tmp"

    if out_file.exists() and not force:
        logger.info(
            "Congress %s already processed at %s. Skipping (use --force to overwrite).",
            c_str,
            out_file.name,
        )
        return out_file

    if tmp_file.exists():
        logger.warning("Removing stale temporary file: %s", tmp_file.name)
        tmp_file.unlink()

    spec = get_archive_for_congress(congress_num)
    names = congress_member_names(congress_num, spec.prefix)
    stats = CongressStats(congress=congress_num, source=spec.prefix)

    logger.info(
        "Processing Congress %s from %s -> %s",
        c_str,
        spec.path.name,
        out_file.name,
    )

    started = time.time()
    writer: pq.ParquetWriter | None = None

    try:
        with StanfordArchive(spec.path) as archive:
            logger.info("Loading descr metadata for Congress %s...", c_str)
            descr_map = _load_descr_map(archive, names["descr"], stats)
            logger.info("Loaded %s descr records", f"{len(descr_map):,}")

            logger.info("Loading SpeakerMap metadata for Congress %s...", c_str)
            smap_map = _load_speakermap(archive, names["speakermap"], stats)
            logger.info("Loaded %s SpeakerMap records", f"{len(smap_map):,}")

            writer = pq.ParquetWriter(
                tmp_file,
                PARQUET_SCHEMA,
                compression="zstd",
                compression_level=3,
                use_dictionary=True,
            )

            batch = BatchBuffer()
            lines = archive.iter_member_lines(
                names["speeches"],
                validate=True,
            )

            header = next(lines, None)
            if header is None:
                raise RuntimeError(f"Empty speeches file: {names['speeches']}")

            for line_number, line in enumerate(lines, start=2):
                raw = line.rstrip("\r\n")
                parts = raw.split("|", 1)

                if len(parts) != 2:
                    stats.malformed_speech_lines += 1
                    raise RuntimeError(
                        f"Malformed speech line {names['speeches']}:{line_number}; "
                        "refusing to silently drop a research record"
                    )

                speech_id_raw, text = parts
                descr = descr_map.get(speech_id_raw)
                speaker_meta = smap_map.get(speech_id_raw)

                if descr is not None:
                    stats.matched_descr += 1
                if speaker_meta is not None:
                    stats.matched_smap += 1

                batch.append(
                    _build_record(
                        congress_num=congress_num,
                        source=spec.prefix,
                        speech_id_raw=speech_id_raw,
                        text=text,
                        descr=descr,
                        speaker_meta=speaker_meta,
                    )
                )
                stats.total_speeches += 1

                if len(batch) >= batch_size:
                    _flush_batch(writer, batch)

            _flush_batch(writer, batch)

        # Closing the writer writes the Parquet footer. Do this before validation.
        writer.close()
        writer = None

        if stats.total_speeches == 0:
            raise RuntimeError(f"Congress {c_str} produced zero speech records")

        _validate_parquet_file(tmp_file, stats.total_speeches)

        # Publish only a complete, validated file.
        tmp_file.replace(out_file)

    except Exception:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                logger.exception("Error while closing failed Parquet writer")

        if tmp_file.exists():
            tmp_file.unlink()
        raise

    elapsed = time.time() - started
    file_size_mb = out_file.stat().st_size / (1024 * 1024)

    descr_rate = (
        100.0 * stats.matched_descr / stats.total_speeches
        if stats.total_speeches
        else 0.0
    )
    smap_rate = (
        100.0 * stats.matched_smap / stats.total_speeches
        if stats.total_speeches
        else 0.0
    )

    logger.info(
        "Done Congress %s: %s speeches -> %.1f MB in %.1fs",
        c_str,
        f"{stats.total_speeches:,}",
        file_size_mb,
        elapsed,
    )
    logger.info(
        "Metadata matches: descr %s (%.1f%%), SpeakerMap %s (%.1f%%)",
        f"{stats.matched_descr:,}",
        descr_rate,
        f"{stats.matched_smap:,}",
        smap_rate,
    )

    if stats.malformed_speech_lines:
        logger.warning(
            "Congress %s had %s malformed speech lines",
            c_str,
            f"{stats.malformed_speech_lines:,}",
        )
    if stats.duplicate_descr_ids:
        logger.warning(
            "Congress %s had %s duplicate descr speech IDs",
            c_str,
            f"{stats.duplicate_descr_ids:,}",
        )
    if stats.duplicate_smap_ids:
        logger.warning(
            "Congress %s had %s duplicate SpeakerMap speech IDs",
            c_str,
            f"{stats.duplicate_smap_ids:,}",
        )

    return out_file


def process_range(
    start_c: int,
    end_c: int,
    out_dir: Path,
    *,
    force: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[int]:
    """
    Process an inclusive Congress range.

    Returns a list of Congress numbers that failed. Individual failures are
    logged and processing continues so one bad Congress does not discard a long
    batch run.
    """
    if start_c > end_c:
        raise ValueError("--start must be less than or equal to --end")

    logger.info(
        "Starting batch processing for Congresses %03d to %03d...",
        start_c,
        end_c,
    )

    started = time.time()
    succeeded = 0
    failures: list[int] = []

    for congress_num in range(start_c, end_c + 1):
        try:
            process_single_congress(
                congress_num,
                out_dir,
                force=force,
                batch_size=batch_size,
            )
            succeeded += 1
        except Exception:
            failures.append(congress_num)
            logger.exception(
                "Error processing Congress %03d",
                congress_num,
            )

    elapsed = time.time() - started
    logger.info(
        "Batch finished: %d succeeded, %d failed, %.1fs total",
        succeeded,
        len(failures),
        elapsed,
    )

    if failures:
        logger.error(
            "Failed Congresses: %s",
            ", ".join(f"{c:03d}" for c in failures),
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Process Stanford Congressional Record ZIP files into compressed "
            "Parquet. Phase 1 defaults to Congresses 077-096."
        )
    )
    parser.add_argument(
        "--congress",
        "-c",
        type=int,
        help="Process one Congress number",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=PHASE1_START,
        help=f"Start Congress number (default: {PHASE1_START})",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=PHASE1_END,
        help=f"End Congress number (default: {PHASE1_END})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Process the complete Phase 1 research corpus "
            f"({PHASE1_START:03d}-{PHASE1_END:03d})"
        ),
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing Parquet files",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=(
            "Number of speech records per Parquet write batch "
            f"(default: {DEFAULT_BATCH_SIZE:,})"
        ),
    )
    parser.add_argument(
        "--out-dir",
        "-o",
        type=str,
        default="data/processed",
        help="Output directory (default: data/processed)",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    out_dir = project_root / args.out_dir

    try:
        if args.congress is not None:
            process_single_congress(
                args.congress,
                out_dir,
                force=args.force,
                batch_size=args.batch_size,
            )
            return 0

        if args.all:
            start_c, end_c = PHASE1_START, PHASE1_END
        else:
            start_c, end_c = args.start, args.end

        failures = process_range(
            start_c,
            end_c,
            out_dir,
            force=args.force,
            batch_size=args.batch_size,
        )
        return 1 if failures else 0

    except Exception:
        logger.exception("Corpus processing failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
