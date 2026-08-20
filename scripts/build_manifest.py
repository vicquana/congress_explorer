#!/usr/bin/env python3
"""Build manifest.json for the Congressional Record Parquet corpus."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pyarrow.parquet as pq


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT = PROJECT_ROOT / "manifest.json"
REPO_ID = "yeeder/congressional-record-parquet"


def congress_from_name(path: Path) -> int:
    return int(path.stem.split("_")[1])


def main() -> None:
    files = sorted(DATA_DIR.glob("congress_*.parquet"))

    if not files:
        raise SystemExit(f"No Parquet files found in {DATA_DIR}")

    con = duckdb.connect(database=":memory:")
    entries = []

    try:
        for path in files:
            congress = congress_from_name(path)
            parquet = pq.ParquetFile(path)

            quoted_path = str(path.resolve()).replace("'", "''")
            year_min, year_max = con.execute(
                f"""
                SELECT MIN(year), MAX(year)
                FROM read_parquet('{quoted_path}')
                """
            ).fetchone()

            entries.append(
                {
                    "congress": congress,
                    "path": f"data/{path.name}",
                    "rows": parquet.metadata.num_rows,
                    "size_bytes": path.stat().st_size,
                    "year_min": int(year_min) if year_min is not None else None,
                    "year_max": int(year_max) if year_max is not None else None,
                    "row_groups": parquet.metadata.num_row_groups,
                }
            )
    finally:
        con.close()

    years_min = [x["year_min"] for x in entries if x["year_min"] is not None]
    years_max = [x["year_max"] for x in entries if x["year_max"] is not None]

    manifest = {
        "dataset": REPO_ID,
        "format": "parquet",
        "file_count": len(entries),
        "congress_min": min(x["congress"] for x in entries),
        "congress_max": max(x["congress"] for x in entries),
        "year_min": min(years_min) if years_min else None,
        "year_max": max(years_max) if years_max else None,
        "total_rows": sum(x["rows"] for x in entries),
        "total_size_bytes": sum(x["size_bytes"] for x in entries),
        "source": {
            "title": (
                "Congressional Record for the 43rd-114th Congresses: "
                "Parsed Speeches and Phrase Counts"
            ),
            "authors": [
                "Matthew Gentzkow",
                "Jesse M. Shapiro",
                "Matt Taddy",
            ],
            "publisher": "Stanford Libraries",
            "year": 2018,
            "url": "https://data.stanford.edu/congress_text",
            "license": "ODC-BY-1.0",
        },
        "files": entries,
    }

    OUTPUT.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Wrote: {OUTPUT}")
    print(f"Files: {manifest['file_count']}")
    print(
        f"Congresses: {manifest['congress_min']}–"
        f"{manifest['congress_max']}"
    )
    print(f"Years: {manifest['year_min']}–{manifest['year_max']}")
    print(f"Rows: {manifest['total_rows']:,}")
    print(
        "Size: "
        f"{manifest['total_size_bytes'] / (1024**3):.2f} GiB"
    )


if __name__ == "__main__":
    main()
