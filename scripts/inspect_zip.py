"""
Inspect Stanford Congressional Record files inside ZIP archives without uncompressing.
"""

import argparse
from itertools import islice

try:
    # Works when imported as ``scripts.inspect_zip``.
    from .archive_reader import (
        StanfordArchive,
        congress_member_names,
        get_archive_for_congress,
    )
except ImportError:
    # Works when run directly: ``python scripts/inspect_zip.py``.
    from archive_reader import (  # type: ignore
        StanfordArchive,
        congress_member_names,
        get_archive_for_congress,
    )


def inspect_congress(
    congress_num: int, num_lines: int = 5, show_sample: bool = True
):
    spec = get_archive_for_congress(congress_num)
    print(f"=== Inspecting Congress {congress_num} in {spec.path.name} ===")

    members = congress_member_names(congress_num, spec.prefix)

    with StanfordArchive(spec.path) as archive:
        for name in members.values():
            try:
                info = archive.get_info(name)
            except KeyError:
                print(f"File {name} not found in archive.")
                continue

            print()
            print(f"File: {name}")
            print(f"  Uncompressed size: {info.file_size:,} bytes")
            print(f"  Compressed size:   {info.compress_size:,} bytes")

            if show_sample:
                print(f"  First {num_lines} lines:")
                # Preview only: iteration stops early, so full-member
                # CRC/size validation is skipped (see iter_member_lines).
                lines = archive.iter_member_lines(name, validate=False)
                for i, line in enumerate(islice(lines, num_lines)):
                    print(f"    [{i + 1}] {line.rstrip(chr(13) + chr(10))[:140]}")


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
