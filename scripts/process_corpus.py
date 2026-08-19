"""
Process Stanford Congressional Record ZIP archives directly into Parquet files.

Reads hein-bound.zip (Congresses 43-111) and hein-daily.zip (Congresses 112-114)
directly in memory without extracting entire archives to disk.
"""

import argparse
import io
import logging
from pathlib import Path
import struct
import sys
import time
from typing import Dict, List, Optional, Tuple
import zipfile
import zlib

import pyarrow as pa
import pyarrow.parquet as pq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("process_corpus")


def find_zip_path(filename: str) -> Path:
    """Locate zip file across possible candidate directories."""
    project_root = Path(__file__).resolve().parent.parent
    candidates = [
        project_root / "data" / filename,
        project_root / "data" / "raw" / filename,
        project_root / "raw" / filename,
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"Could not find {filename} in data/, data/raw/, or raw/"
    )


def get_entry_stream(
    raw_fp: io.BufferedReader,
    zinfo: zipfile.ZipInfo,
) -> io.BytesIO:
    """
    Directly read and decompress a zip entry using its raw offset.
    Accurately handles zip64 offset quirks and prevents overlap/zip-bomb checks.
    """
    offset = zinfo.header_offset
    raw_fp.seek(offset)
    sig = raw_fp.read(4)

    # Check if offset is shifted by 4GB
    if sig != b"PK\x03\x04" and offset >= 4294967296:
        offset -= 4294967296
        raw_fp.seek(offset)
        sig = raw_fp.read(4)

    if sig != b"PK\x03\x04":
        raise ValueError(
            f"Invalid zip local header signature for {zinfo.filename} at offset {offset}"
        )

    raw_fp.seek(offset)
    header = raw_fp.read(30)
    (
        _,
        _,
        _,
        ctype,
        _,
        _,
        _,
        c_size_hdr,
        u_size_hdr,
        fname_len,
        extra_len,
    ) = struct.unpack("<4sHHHHHIIIHH", header)

    data_offset = offset + 30 + fname_len + extra_len
    raw_fp.seek(data_offset)

    read_size = (
        zinfo.compress_size
        if zinfo.compress_size > 0
        else (c_size_hdr if c_size_hdr > 0 else zinfo.file_size)
    )

    if ctype == 8:  # Deflate
        raw_compressed = raw_fp.read(read_size)
        decompressed = zlib.decompress(raw_compressed, -15)
        return io.BytesIO(decompressed)
    elif ctype == 0:  # Stored
        raw_data = raw_fp.read(read_size)
        return io.BytesIO(raw_data)
    else:
        raise ValueError(
            f"Unsupported compression type {ctype} for {zinfo.filename}"
        )


def get_archive_for_congress(congress_num: int) -> Tuple[Path, str]:
    """Return the zip file path and internal prefix for a given Congress number."""
    if 43 <= congress_num <= 111:
        return find_zip_path("hein-bound.zip"), "hein-bound"
    elif 112 <= congress_num <= 114:
        return find_zip_path("hein-daily.zip"), "hein-daily"
    else:
        raise ValueError(
            f"Congress {congress_num} is outside canonical range (43-114)."
        )


def process_single_congress(
    congress_num: int,
    out_dir: Path,
    force: bool = False,
) -> Optional[Path]:
    """
    Process a single Congress from its source ZIP into a compressed Parquet file.
    Returns the path to the generated parquet file, or None if skipped/error.
    """
    c_str = f"{congress_num:03d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"congress_{c_str}.parquet"

    if out_file.exists() and not force:
        logger.info(
            f"Congress {congress_num:03d} already processed at {out_file.name}. Skipping (use --force to overwrite)."
        )
        return out_file

    t0 = time.time()
    zip_path, prefix = get_archive_for_congress(congress_num)
    logger.info(
        f"Processing Congress {congress_num:03d} from {zip_path.name} -> {out_file.name}..."
    )

    with open(zip_path, "rb") as raw_fp:
        with zipfile.ZipFile(zip_path, "r") as z:
            # 1. Load description metadata (speech_id -> metadata tuple)
            descr_name = f"{prefix}/descr_{c_str}.txt"
            descr_map: Dict[str, Tuple] = {}
            try:
                descr_info = z.getinfo(descr_name)
                stream = get_entry_stream(raw_fp, descr_info)
                stream.readline()  # skip header
                for line in stream:
                    parts = (
                        line.decode("utf-8", errors="replace")
                        .rstrip("\r\n")
                        .split("|")
                    )
                    if parts and len(parts) >= 14:
                        # 0: speech_id, 1: chamber, 2: date, 4: speaker, 5: first_name, 6: last_name, 7: state, 8: gender, 12: char_count, 13: word_count
                        descr_map[parts[0]] = (
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
                logger.warning(
                    f"Description file {descr_name} not found in {zip_path.name}"
                )

            # 2. Load SpeakerMap (speech_id -> speaker details tuple)
            smap_name = f"{prefix}/{c_str}_SpeakerMap.txt"
            smap_map: Dict[str, Tuple] = {}
            try:
                smap_info = z.getinfo(smap_name)
                stream = get_entry_stream(raw_fp, smap_info)
                stream.readline()  # skip header
                for line in stream:
                    parts = (
                        line.decode("utf-8", errors="replace")
                        .rstrip("\r\n")
                        .split("|")
                    )
                    if parts and len(parts) >= 10:
                        # 0: speakerid, 1: speech_id, 2: lastname, 3: firstname, 4: chamber, 5: state, 6: gender, 7: party, 8: district, 9: nonvoting
                        smap_map[parts[1]] = (
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
                logger.warning(
                    f"SpeakerMap file {smap_name} not found in {zip_path.name}"
                )

            # 3. Stream speeches and assemble record arrays
            speeches_name = f"{prefix}/speeches_{c_str}.txt"
            speech_ids: List[Optional[int]] = []
            congresses: List[int] = []
            dates: List[str] = []
            years: List[Optional[int]] = []
            chambers: List[str] = []
            speaker_ids: List[Optional[int]] = []
            first_names: List[str] = []
            last_names: List[str] = []
            speakers: List[str] = []
            states: List[str] = []
            genders: List[str] = []
            parties: List[str] = []
            districts: List[str] = []
            nonvotings: List[str] = []
            char_counts: List[int] = []
            word_counts: List[int] = []
            speeches_text: List[str] = []
            sources: List[str] = []

            matched_descr = 0
            matched_smap = 0

            sp_info = z.getinfo(speeches_name)
            stream = get_entry_stream(raw_fp, sp_info)
            stream.readline()  # skip header

            for line in stream:
                parts = (
                    line.decode("utf-8", errors="replace")
                    .rstrip("\r\n")
                    .split("|", 1)
                )
                if len(parts) == 2:
                    sid, text = parts
                    d = descr_map.get(sid)
                    sm = smap_map.get(sid)

                    if d:
                        matched_descr += 1
                    if sm:
                        matched_smap += 1

                    speech_ids.append(int(sid) if sid.isdigit() else None)
                    congresses.append(congress_num)

                    date_str = d[1] if d else ""
                    dates.append(date_str)
                    yr = (
                        int(date_str[:4])
                        if (
                            date_str
                            and len(date_str) >= 4
                            and date_str[:4].isdigit()
                        )
                        else None
                    )
                    years.append(yr)

                    # Chamber resolution
                    chamber = (
                        sm[3]
                        if sm and sm[3]
                        else (d[0] if d and d[0] and d[0] != "None" else "")
                    )
                    chambers.append(chamber)

                    # Speaker metadata resolution
                    sp_id = sm[0] if sm else ""
                    speaker_ids.append(int(sp_id) if sp_id.isdigit() else None)

                    fn = (
                        sm[2]
                        if sm and sm[2]
                        else (d[3] if d and d[3] != "Unknown" else "")
                    )
                    ln = (
                        sm[1]
                        if sm and sm[1]
                        else (d[4] if d and d[4] != "Unknown" else "")
                    )
                    first_names.append(fn)
                    last_names.append(ln)

                    raw_sp = d[2] if d else ""
                    speakers.append(raw_sp)

                    st = (
                        sm[4]
                        if sm and sm[4]
                        else (d[5] if d and d[5] != "Unknown" else "")
                    )
                    states.append(st)

                    gen = (
                        sm[5]
                        if sm and sm[5]
                        else (d[6] if d and d[6] != "Unknown" else "")
                    )
                    genders.append(gen)

                    pty = sm[6] if sm else ""
                    parties.append(pty)

                    dist = sm[7] if sm else ""
                    districts.append(dist)

                    nv = sm[8] if sm else ""
                    nonvotings.append(nv)

                    cc = (
                        int(d[7])
                        if d and d[7].isdigit()
                        else (len(text) if text else 0)
                    )
                    wc = (
                        int(d[8])
                        if d and d[8].isdigit()
                        else (len(text.split()) if text else 0)
                    )
                    char_counts.append(cc)
                    word_counts.append(wc)

                    speeches_text.append(text)
                    sources.append(prefix)

        # Build PyArrow table with compact types
        table = pa.Table.from_arrays(
            [
                pa.array(speech_ids, type=pa.int64()),
                pa.array(congresses, type=pa.int32()),
                pa.array(dates, type=pa.string()),
                pa.array(years, type=pa.int32()),
                pa.array(chambers, type=pa.string()),
                pa.array(speaker_ids, type=pa.int64()),
                pa.array(first_names, type=pa.string()),
                pa.array(last_names, type=pa.string()),
                pa.array(speakers, type=pa.string()),
                pa.array(states, type=pa.string()),
                pa.array(genders, type=pa.string()),
                pa.array(parties, type=pa.string()),
                pa.array(districts, type=pa.string()),
                pa.array(nonvotings, type=pa.string()),
                pa.array(char_counts, type=pa.int32()),
                pa.array(word_counts, type=pa.int32()),
                pa.array(speeches_text, type=pa.string()),
                pa.array(sources, type=pa.string()),
            ],
            names=[
                "speech_id",
                "congress",
                "date",
                "year",
                "chamber",
                "speaker_id",
                "first_name",
                "last_name",
                "speaker",
                "state",
                "gender",
                "party",
                "district",
                "nonvoting",
                "char_count",
                "word_count",
                "speech_text",
                "source",
            ],
        )

        # Write Parquet with zstd compression
        pq.write_table(
            table,
            out_file,
            compression="zstd",
            compression_level=3,
            use_dictionary=True,
        )

        elapsed = time.time() - t0
        file_size_mb = out_file.stat().st_size / (1024 * 1024)
        total_speeches = len(table)
        logger.info(
            f"Done Congress {congress_num:03d}: {total_speeches:,} speeches ({matched_smap:,} identified speakers) -> {file_size_mb:.1f} MB in {elapsed:.2f}s"
        )
        return out_file


def process_range(
    start_c: int,
    end_c: int,
    out_dir: Path,
    force: bool = False,
):
    """Process a range of Congresses inclusive."""
    logger.info(
        f"Starting batch processing for Congresses {start_c:03d} to {end_c:03d}..."
    )
    total_start = time.time()
    count = 0

    for c in range(start_c, end_c + 1):
        try:
            res = process_single_congress(c, out_dir, force=force)
            if res:
                count += 1
        except Exception as e:
            logger.error(f"Error processing Congress {c}: {e}", exc_info=True)

    elapsed = time.time() - total_start
    logger.info(
        f"Completed batch of {count} Congresses in {elapsed:.1f}s ({elapsed/max(1, count):.1f}s/congress avg)."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Process Stanford Congressional Record ZIP files into Parquet."
    )
    parser.add_argument(
        "--congress",
        "-c",
        type=int,
        help="Process a single Congress number (43 to 114)",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=43,
        help="Start Congress number (default: 43)",
    )
    parser.add_argument(
        "--end",
        type=int,
        default=114,
        help="End Congress number (default: 114)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process all 72 canonical Congresses (43 to 114)",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite existing parquet files",
    )
    parser.add_argument(
        "--out-dir",
        "-o",
        type=str,
        default="data/processed",
        help="Output directory for parquet files (default: data/processed)",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    out_dir = project_root / args.out_dir

    if args.congress:
        process_single_congress(args.congress, out_dir, force=args.force)
    elif args.all:
        process_range(43, 114, out_dir, force=args.force)
    else:
        process_range(args.start, args.end, out_dir, force=args.force)


if __name__ == "__main__":
    main()
