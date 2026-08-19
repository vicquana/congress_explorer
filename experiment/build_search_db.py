"""
Build a persistent DuckDB full-text-search database from the Phase 1
Congressional Record Parquet corpus.

Start with a one-Congress pilot:

    uv run python scripts/build_search_db.py --congress 77 --force

After the pilot is validated, build the full Phase 1 database:

    uv run python scripts/build_search_db.py --all --force

The source Parquet files remain the canonical processed corpus. This database
is a derived search artifact that can be deleted and rebuilt at any time.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time
from typing import Iterable

import duckdb


PHASE1_START = 77
PHASE1_END = 96

BENCHMARKS = [
    ("exact_phrase", "religious freedom"),
    ("exact_phrase", "freedom of worship"),
    ("all_terms", "religion communism"),
    ("any_terms", "Vatican Catholic"),
    ("all_terms", "chaplain"),
    ("all_terms", "sectarian"),
]


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def parquet_path(congress: int) -> Path:
    return project_root() / "data" / "processed" / f"congress_{congress:03d}.parquet"


def default_db_path(congresses: list[int]) -> Path:
    search_dir = project_root() / "data" / "search"
    if len(congresses) == 1:
        return search_dir / f"pilot_{congresses[0]:03d}.duckdb"
    return search_dir / f"phase1_{congresses[0]:03d}_{congresses[-1]:03d}.duckdb"


def sql_string(value: str | Path) -> str:
    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def human_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def database_size(path: Path) -> int:
    total = path.stat().st_size if path.exists() else 0
    wal = Path(str(path) + ".wal")
    if wal.exists():
        total += wal.stat().st_size
    return total


def validate_source_files(congresses: Iterable[int]) -> list[Path]:
    files: list[Path] = []
    for congress in congresses:
        path = parquet_path(congress)
        if not path.is_file():
            raise FileNotFoundError(
                f"Missing source Parquet for Congress {congress:03d}: {path}"
            )
        files.append(path)
    return files


def create_speeches_table(
    con: duckdb.DuckDBPyConnection,
    parquet_files: list[Path],
) -> None:
    file_list = ", ".join(sql_string(path.resolve()) for path in parquet_files)

    con.execute("DROP TABLE IF EXISTS speeches")
    con.execute(
        f"""
        CREATE TABLE speeches AS
        SELECT
            CAST(speech_id AS VARCHAR) AS doc_id,
            speech_id,
            congress,
            date,
            year,
            chamber,
            speaker_id,
            first_name,
            last_name,
            speaker,
            state,
            gender,
            party,
            district,
            nonvoting,
            char_count,
            word_count,
            speech_text,
            source
        FROM read_parquet(
            [{file_list}],
            union_by_name = true
        )
        """
    )


def validate_table(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    row = con.execute(
        """
        SELECT
            COUNT(*) AS total_rows,
            COUNT(DISTINCT doc_id) AS unique_doc_ids,
            COUNT(*) FILTER (WHERE doc_id IS NULL) AS null_doc_ids,
            COUNT(*) FILTER (
                WHERE speech_text IS NULL OR trim(speech_text) = ''
            ) AS empty_speeches,
            MIN(congress) AS congress_min,
            MAX(congress) AS congress_max,
            MIN(year) AS year_min,
            MAX(year) AS year_max
        FROM speeches
        """
    ).fetchone()

    if row is None:
        raise RuntimeError("Could not validate speeches table")

    stats = {
        "total_rows": int(row[0] or 0),
        "unique_doc_ids": int(row[1] or 0),
        "null_doc_ids": int(row[2] or 0),
        "empty_speeches": int(row[3] or 0),
        "congress_min": int(row[4] or 0),
        "congress_max": int(row[5] or 0),
        "year_min": int(row[6] or 0),
        "year_max": int(row[7] or 0),
    }

    if stats["total_rows"] == 0:
        raise RuntimeError("Search table contains zero rows")

    if stats["null_doc_ids"]:
        raise RuntimeError(
            f"Search table has {stats['null_doc_ids']:,} NULL document IDs"
        )

    if stats["unique_doc_ids"] != stats["total_rows"]:
        duplicates = stats["total_rows"] - stats["unique_doc_ids"]
        raise RuntimeError(
            f"Search table has {duplicates:,} duplicate document IDs"
        )

    return stats


def install_and_load_fts(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSTALL fts")
    con.execute("LOAD fts")


def build_fts_index(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        """
        PRAGMA create_fts_index(
            'speeches',
            'doc_id',
            'speech_text',
            stemmer = 'none',
            stopwords = 'none',
            lower = 1,
            overwrite = 1
        )
        """
    )


def split_terms(query: str) -> list[str]:
    return [term for term in query.split() if term]


def literal_count(
    con: duckdb.DuckDBPyConnection,
    mode: str,
    query: str,
) -> tuple[int, float]:
    started = time.perf_counter()

    if mode == "exact_phrase":
        row = con.execute(
            """
            SELECT COUNT(*)
            FROM speeches
            WHERE strpos(lower(coalesce(speech_text, '')), lower(?)) > 0
            """,
            [query],
        ).fetchone()

    elif mode == "all_terms":
        terms = split_terms(query)
        predicates = " AND ".join(
            "strpos(lower(coalesce(speech_text, '')), lower(?)) > 0"
            for _ in terms
        )
        row = con.execute(
            f"SELECT COUNT(*) FROM speeches WHERE {predicates}",
            terms,
        ).fetchone()

    elif mode == "any_terms":
        terms = split_terms(query)
        predicates = " OR ".join(
            "strpos(lower(coalesce(speech_text, '')), lower(?)) > 0"
            for _ in terms
        )
        row = con.execute(
            f"SELECT COUNT(*) FROM speeches WHERE {predicates}",
            terms,
        ).fetchone()

    else:
        raise ValueError(f"Unknown benchmark mode: {mode}")

    elapsed = time.perf_counter() - started
    return int(row[0] if row else 0), elapsed


def fts_count(
    con: duckdb.DuckDBPyConnection,
    mode: str,
    query: str,
) -> tuple[int, float]:
    started = time.perf_counter()

    if mode == "exact_phrase":
        row = con.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT
                    speech_text,
                    fts_main_speeches.match_bm25(
                        doc_id,
                        ?,
                        fields := 'speech_text',
                        conjunctive := 1
                    ) AS score
                FROM speeches
            ) candidate
            WHERE score IS NOT NULL
              AND strpos(
                    lower(coalesce(speech_text, '')),
                    lower(?)
                  ) > 0
            """,
            [query, query],
        ).fetchone()

    elif mode == "all_terms":
        row = con.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT
                    fts_main_speeches.match_bm25(
                        doc_id,
                        ?,
                        fields := 'speech_text',
                        conjunctive := 1
                    ) AS score
                FROM speeches
            ) candidate
            WHERE score IS NOT NULL
            """,
            [query],
        ).fetchone()

    elif mode == "any_terms":
        row = con.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT
                    fts_main_speeches.match_bm25(
                        doc_id,
                        ?,
                        fields := 'speech_text',
                        conjunctive := 0
                    ) AS score
                FROM speeches
            ) candidate
            WHERE score IS NOT NULL
            """,
            [query],
        ).fetchone()

    else:
        raise ValueError(f"Unknown benchmark mode: {mode}")

    elapsed = time.perf_counter() - started
    return int(row[0] if row else 0), elapsed


def top_fts_results(
    con: duckdb.DuckDBPyConnection,
    query: str,
    limit: int = 5,
) -> list[tuple]:
    return con.execute(
        """
        SELECT
            speech_id,
            congress,
            date,
            speaker,
            party,
            score
        FROM (
            SELECT
                speech_id,
                congress,
                date,
                speaker,
                party,
                fts_main_speeches.match_bm25(
                    doc_id,
                    ?,
                    fields := 'speech_text'
                ) AS score
            FROM speeches
        ) ranked
        WHERE score IS NOT NULL
        ORDER BY score DESC, date ASC, speech_id ASC
        LIMIT ?
        """,
        [query, limit],
    ).fetchall()


def run_benchmarks(con: duckdb.DuckDBPyConnection) -> None:
    print()
    print("SEARCH BENCHMARK")
    print("=" * 88)
    print(
        f"{'mode':<14} {'query':<24} "
        f"{'literal':>10} {'scan sec':>10} "
        f"{'fts':>10} {'fts sec':>10}"
    )
    print("-" * 88)

    for mode, query in BENCHMARKS:
        literal_matches, literal_seconds = literal_count(con, mode, query)
        fts_matches, fts_seconds = fts_count(con, mode, query)

        print(
            f"{mode:<14} {query:<24} "
            f"{literal_matches:>10,} {literal_seconds:>10.3f} "
            f"{fts_matches:>10,} {fts_seconds:>10.3f}"
        )

        if mode == "exact_phrase" and literal_matches != fts_matches:
            print(
                "  WARNING: exact-phrase counts differ. "
                "Do not adopt the FTS path until investigated."
            )

    print()
    print("Top BM25 results for 'religious freedom':")
    for speech_id, congress, date, speaker, party, score in top_fts_results(
        con, "religious freedom"
    ):
        print(
            f"  {date}  C{int(congress):03d}  "
            f"{speaker or 'Unknown'}  {party or '-'}  score={score:.4f}  "
            f"speech_id={speech_id}"
        )


def build_database(
    congresses: list[int],
    db_path: Path,
    *,
    force: bool,
    benchmark: bool,
) -> None:
    parquet_files = validate_source_files(congresses)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        if not force:
            raise FileExistsError(
                f"{db_path} already exists. Use --force to rebuild it."
            )
        db_path.unlink()

    wal_path = Path(str(db_path) + ".wal")
    if wal_path.exists():
        wal_path.unlink()

    print("SEARCH DATABASE BUILD")
    print("=" * 72)
    print(
        "Congresses:",
        f"{congresses[0]:03d}"
        if len(congresses) == 1
        else f"{congresses[0]:03d}-{congresses[-1]:03d}",
    )
    print("Parquet files:", len(parquet_files))
    print(
        "Parquet size:",
        human_bytes(sum(path.stat().st_size for path in parquet_files)),
    )
    print("Output:", db_path)
    print()

    con = duckdb.connect(str(db_path))

    try:
        cpu_count = os.cpu_count() or 1
        con.execute(f"SET threads = {max(1, cpu_count)}")

        print("1. Materializing speeches table...")
        started = time.perf_counter()
        create_speeches_table(con, parquet_files)
        con.execute("CHECKPOINT")
        table_seconds = time.perf_counter() - started
        print(
            f"   done in {table_seconds:.1f}s; "
            f"database size {human_bytes(database_size(db_path))}"
        )

        print("2. Validating table...")
        stats = validate_table(con)
        print(
            f"   {stats['total_rows']:,} rows; "
            f"{stats['unique_doc_ids']:,} unique IDs; "
            f"{stats['congress_min']:03d}-{stats['congress_max']:03d}; "
            f"{stats['year_min']}-{stats['year_max']}"
        )
        if stats["empty_speeches"]:
            print(
                f"   note: {stats['empty_speeches']:,} empty/null speech texts"
            )

        print("3. Installing/loading DuckDB FTS extension...")
        install_and_load_fts(con)
        print("   FTS ready")

        print(
            "4. Building lexical FTS index "
            "(stemming=none, stopwords=none, lowercase=yes)..."
        )
        started = time.perf_counter()
        build_fts_index(con)
        con.execute("CHECKPOINT")
        fts_seconds = time.perf_counter() - started
        print(
            f"   done in {fts_seconds:.1f}s; "
            f"database size {human_bytes(database_size(db_path))}"
        )

        if benchmark:
            run_benchmarks(con)

        print()
        print("BUILD COMPLETE")
        print(f"Database: {db_path}")
        print(f"Final size: {human_bytes(database_size(db_path))}")

    finally:
        con.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a persistent DuckDB lexical search database."
    )

    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--congress",
        type=int,
        default=77,
        help="Build a one-Congress pilot (default: 77)",
    )
    scope.add_argument(
        "--all",
        action="store_true",
        help="Build the complete Phase 1 database (Congresses 077-096)",
    )

    parser.add_argument(
        "--output",
        type=Path,
        help="Override the output .duckdb path",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Delete and rebuild an existing database",
    )
    parser.add_argument(
        "--no-benchmark",
        action="store_true",
        help="Skip post-build search benchmarks",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        if args.all:
            congresses = list(range(PHASE1_START, PHASE1_END + 1))
        else:
            congress = int(args.congress)
            if not PHASE1_START <= congress <= PHASE1_END:
                raise ValueError(
                    f"Pilot Congress must be within "
                    f"{PHASE1_START:03d}-{PHASE1_END:03d}"
                )
            congresses = [congress]

        db_path = (
            args.output.resolve()
            if args.output is not None
            else default_db_path(congresses)
        )

        build_database(
            congresses,
            db_path,
            force=args.force,
            benchmark=not args.no_benchmark,
        )
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
