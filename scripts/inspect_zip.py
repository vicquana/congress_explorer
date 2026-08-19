"""
Inspect Stanford Congressional Record files inside ZIP archives without uncompressing.
"""

import argparse
import io
from pathlib import Path
import struct
from typing import Optional
import zipfile
import zlib


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


def inspect_congress(
    congress_num: int, num_lines: int = 5, show_sample: bool = True
):
    c_str = f"{congress_num:03d}"
    if congress_num <= 111:
        zip_name = "hein-bound.zip"
        prefix = "hein-bound"
    else:
        zip_name = "hein-daily.zip"
        prefix = "hein-daily"

    zip_path = find_zip_path(zip_name)
    print(f"=== Inspecting Congress {congress_num} in {zip_path.name} ===")

    targets = [
        f"{prefix}/speeches_{c_str}.txt",
        f"{prefix}/descr_{c_str}.txt",
        f"{prefix}/{c_str}_SpeakerMap.txt",
    ]

    with open(zip_path, "rb") as raw_fp:
        with zipfile.ZipFile(zip_path, "r") as z:
            for name in targets:
                try:
                    info = z.getinfo(name)
                    print()
                    print(f"File: {name}")
                    print(f"  Uncompressed size: {info.file_size:,} bytes")
                    print(f"  Compressed size:   {info.compress_size:,} bytes")

                    if show_sample:
                        stream = get_entry_stream(raw_fp, info)
                        print(f"  First {num_lines} lines:")
                        for i in range(num_lines):
                            line = (
                                stream.readline()
                                .decode("utf-8", errors="replace")
                                .rstrip("\r\n")
                            )
                            if not line and i > 0:
                                break
                            print(f"    [{i+1}] {line[:140]}")
                except KeyError:
                    print(f"File {name} not found in archive.")


def main():
    parser = argparse.ArgumentParser(
        description="Inspect Stanford Congressional Record ZIP files without extraction."
    )
    parser.add_argument(
        "--congress",
        "-c",
        type=int,
        default=43,
        help="Congress number to inspect (e.g. 43 to 114, default 43)",
    )
    parser.add_argument(
        "--lines",
        "-n",
        type=int,
        default=5,
        help="Number of lines to preview (default 5)",
    )
    args = parser.parse_args()

    inspect_congress(args.congress, args.lines)


if __name__ == "__main__":
    main()
