"""
DuckDB search backend for the Stanford Congressional Record Parquet corpus.

The canonical research corpus remains the Congress-level Parquet files.
Literal searches scan the corpus once, then materialize only matching IDs and
sort keys into a small derived search-cache Parquet. Pagination reads that
cache instead of rescanning every speech.

This keeps retrieval semantics transparent while making Next/Previous fast.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import json
import os
from pathlib import Path
import re
from typing import Any, Optional

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from app.data_loader import resolve_processed_dir


CORPUS_START = 43
CORPUS_END = 114

# DuckDB's regexp_matches() compiles patterns with RE2, which guarantees
# linear-time matching and has no catastrophic-backtracking (ReDoS) failure
# mode. The length cap below is defense-in-depth against pathologically large
# patterns (compile time/memory), not a ReDoS mitigation by itself.
MAX_REGEX_PATTERN_LENGTH = 200

DEFAULT_CSV_EXPORT_MAX_ROWS = int(os.getenv("CONGRESS_CSV_EXPORT_MAX_ROWS", "50000"))

RESULT_COLUMNS = [
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
]


@dataclass
class SearchFilter:
    query: str = ""
    search_mode: str = "all_words"
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    congress_list: Optional[list[int]] = None
    chambers: Optional[list[str]] = None
    parties: Optional[list[str]] = None
    states: Optional[list[str]] = None
    speaker_query: str = ""
    limit: int = 25
    offset: int = 0
    sort_by: str = "date_asc"


class SearchEngine:
    """Literal-search backend with disk-backed result caching."""

    def __init__(
        self,
        processed_dir: Optional[Path | str] = None,
        cache_dir: Optional[Path | str] = None,
    ):
        self.project_root = Path(__file__).resolve().parent.parent

        if processed_dir is None:
            self.processed_dir = resolve_processed_dir(self.project_root)
        else:
            self.processed_dir = Path(processed_dir)

        if cache_dir is None:
            env_cache_dir = os.getenv("CONGRESS_SEARCH_CACHE_DIR")
            self.cache_dir = (
                Path(env_cache_dir)
                if env_cache_dir
                else self.project_root / "data" / "search_cache"
            )
        else:
            self.cache_dir = Path(cache_dir)

        self.con = duckdb.connect(database=":memory:")
        self.con.execute("SET preserve_insertion_order = false")

        self._view_initialized = False
        self._view_files: tuple[Path, ...] = ()

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass

    def get_parquet_files(self) -> list[Path]:
        """Return canonical Congress 043-114 Parquet files."""
        if not self.processed_dir.exists():
            return []

        files: list[Path] = []

        for path in self.processed_dir.glob("congress_*.parquet"):
            match = re.fullmatch(r"congress_(\d{3})\.parquet", path.name)
            if not match:
                continue

            congress = int(match.group(1))
            if CORPUS_START <= congress <= CORPUS_END:
                files.append(path)

        return sorted(files)

    @staticmethod
    def _quote_path(path: Path) -> str:
        return "'" + str(path.resolve()).replace("'", "''") + "'"

    def _ensure_view(self) -> bool:
        files = tuple(self.get_parquet_files())

        if not files:
            self._view_initialized = False
            self._view_files = ()
            return False

        if self._view_initialized and files == self._view_files:
            return True

        file_list_sql = ", ".join(self._quote_path(path) for path in files)

        self.con.execute("DROP VIEW IF EXISTS speeches")
        self.con.execute(
            f"""
            CREATE VIEW speeches AS
            SELECT *
            FROM read_parquet([{file_list_sql}], union_by_name=true)
            """
        )

        self._view_initialized = True
        self._view_files = files
        return True

    def corpus_signature(self) -> str:
        """
        Fingerprint the current corpus using file names, sizes, and mtimes.

        Search-cache keys include this signature, so replacing a corpus Parquet
        automatically produces a new cache rather than silently reusing stale
        search results.
        """
        parts: list[str] = []

        for path in self.get_parquet_files():
            stat = path.stat()
            parts.append(
                f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"
            )

        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    def get_corpus_stats(self) -> dict[str, Any]:
        files = self.get_parquet_files()

        if not files or not self._ensure_view():
            return {
                "total_congresses": 0,
                "congress_min": 0,
                "congress_max": 0,
                "total_speeches": 0,
                "year_min": 0,
                "year_max": 0,
                "total_size_mb": 0.0,
            }

        total_bytes = sum(path.stat().st_size for path in files)

        row = self.con.execute(
            """
            SELECT
                COUNT(DISTINCT congress),
                MIN(congress),
                MAX(congress),
                COUNT(*),
                MIN(year),
                MAX(year)
            FROM speeches
            """
        ).fetchone()

        if row is None:
            return {
                "total_congresses": 0,
                "congress_min": 0,
                "congress_max": 0,
                "total_speeches": 0,
                "year_min": 0,
                "year_max": 0,
                "total_size_mb": round(total_bytes / (1024 * 1024), 1),
            }

        return {
            "total_congresses": int(row[0] or 0),
            "congress_min": int(row[1] or 0),
            "congress_max": int(row[2] or 0),
            "total_speeches": int(row[3] or 0),
            "year_min": int(row[4] or 0),
            "year_max": int(row[5] or 0),
            "total_size_mb": round(total_bytes / (1024 * 1024), 1),
        }

    @staticmethod
    def _split_terms(query: str) -> list[str]:
        return [term for term in re.split(r"\s+", query.strip()) if term]

    def _validate_regex(self, pattern: str) -> None:
        """
        Reject regex patterns that are too long or fail to compile under RE2.

        Raises ValueError with a message safe to show directly to end users.
        """
        if len(pattern) > MAX_REGEX_PATTERN_LENGTH:
            raise ValueError(
                f"Regex pattern is too long (max {MAX_REGEX_PATTERN_LENGTH} "
                "characters)."
            )

        try:
            self.con.execute("SELECT regexp_matches('', ?)", [pattern])
        except duckdb.Error as exc:
            raise ValueError(f"Invalid regular expression: {exc}") from exc

    def _build_where_clause(
        self,
        sf: SearchFilter,
    ) -> tuple[str, list[Any], list[str]]:
        conditions: list[str] = []
        params: list[Any] = []
        highlight_terms: list[str] = []

        query = sf.query.strip()

        if query:
            if sf.search_mode == "exact_phrase":
                conditions.append(
                    "strpos(lower(coalesce(speech_text, '')), lower(?)) > 0"
                )
                params.append(query)
                highlight_terms.append(query)

            elif sf.search_mode == "any_word":
                terms = self._split_terms(query)
                if terms:
                    subconditions = [
                        "strpos(lower(coalesce(speech_text, '')), lower(?)) > 0"
                        for _ in terms
                    ]
                    conditions.append("(" + " OR ".join(subconditions) + ")")
                    params.extend(terms)
                    highlight_terms.extend(terms)

            elif sf.search_mode == "regex":
                self._validate_regex(query)
                conditions.append(
                    "regexp_matches(coalesce(speech_text, ''), ?, 'i')"
                )
                params.append(query)
                highlight_terms.append(query)

            else:  # all_words
                terms = self._split_terms(query)
                for term in terms:
                    conditions.append(
                        "strpos(lower(coalesce(speech_text, '')), lower(?)) > 0"
                    )
                    params.append(term)
                highlight_terms.extend(terms)

        if sf.year_min is not None:
            conditions.append("year >= ?")
            params.append(int(sf.year_min))

        if sf.year_max is not None:
            conditions.append("year <= ?")
            params.append(int(sf.year_max))

        if sf.congress_list:
            congresses = [
                int(c)
                for c in sf.congress_list
                if CORPUS_START <= int(c) <= CORPUS_END
            ]

            if not congresses:
                conditions.append("FALSE")
            else:
                placeholders = ", ".join("?" for _ in congresses)
                conditions.append(f"congress IN ({placeholders})")
                params.extend(congresses)

        if sf.chambers:
            chambers = [str(c).upper() for c in sf.chambers]
            placeholders = ", ".join("?" for _ in chambers)
            conditions.append(
                f"upper(coalesce(chamber, '')) IN ({placeholders})"
            )
            params.extend(chambers)

        if sf.parties:
            party_conditions: list[str] = []

            for party in sf.parties:
                if party == "Other":
                    party_conditions.append(
                        "(coalesce(party, '') NOT IN ('R', 'D'))"
                    )
                else:
                    party_conditions.append("party = ?")
                    params.append(party)

            if party_conditions:
                conditions.append("(" + " OR ".join(party_conditions) + ")")

        if sf.states:
            states = [str(state).upper() for state in sf.states]
            placeholders = ", ".join("?" for _ in states)
            conditions.append(
                f"upper(trim(coalesce(state, ''))) IN ({placeholders})"
            )
            params.extend(states)

        speaker_query = sf.speaker_query.strip()
        if speaker_query:
            conditions.append(
                """
                (
                    strpos(lower(coalesce(speaker, '')), lower(?)) > 0
                    OR strpos(lower(coalesce(first_name, '')), lower(?)) > 0
                    OR strpos(lower(coalesce(last_name, '')), lower(?)) > 0
                    OR strpos(
                        lower(
                            trim(
                                coalesce(first_name, '') || ' ' ||
                                coalesce(last_name, '')
                            )
                        ),
                        lower(?)
                    ) > 0
                )
                """
            )
            params.extend([speaker_query] * 4)

        if not conditions:
            return "", params, highlight_terms

        return "WHERE " + " AND ".join(conditions), params, highlight_terms

    @staticmethod
    def _order_expression(sort_by: str) -> str:
        if sort_by == "date_desc":
            return "date DESC NULLS LAST, speech_id DESC"
        if sort_by == "word_count_desc":
            return (
                "word_count DESC NULLS LAST, "
                "date ASC NULLS LAST, speech_id ASC"
            )
        if sort_by == "word_count_asc":
            return (
                "word_count ASC NULLS LAST, "
                "date ASC NULLS LAST, speech_id ASC"
            )
        return "date ASC NULLS LAST, speech_id ASC"

    def _search_cache_key(self, sf: SearchFilter) -> str:
        """
        Build a stable key that intentionally ignores limit/offset.

        All pages of the same search therefore share one materialized result.
        """
        payload = {
            "corpus": self.corpus_signature(),
            "query": sf.query.strip(),
            "search_mode": sf.search_mode,
            "year_min": sf.year_min,
            "year_max": sf.year_max,
            "congress_list": sorted(sf.congress_list or []),
            "chambers": sorted(sf.chambers or []),
            "parties": sorted(sf.parties or []),
            "states": sorted(sf.states or []),
            "speaker_query": sf.speaker_query.strip(),
            "sort_by": sf.sort_by,
            "cache_version": 1,
        }

        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _prune_search_cache(self, protected: Optional[Path] = None) -> None:
        """Bound derived search-cache disk usage for small deployment hosts."""
        if not self.cache_dir.exists():
            return

        max_mb = int(os.getenv("CONGRESS_SEARCH_CACHE_MAX_MB", "512"))
        max_files = int(os.getenv("CONGRESS_SEARCH_CACHE_MAX_FILES", "100"))
        max_bytes = max_mb * 1024 * 1024

        files = [
            path
            for path in self.cache_dir.glob("search_*.parquet")
            if path.is_file() and path != protected
        ]
        files.sort(key=lambda path: path.stat().st_mtime)

        def total_size() -> int:
            return sum(path.stat().st_size for path in files if path.exists())

        while files and (
            len(files) + (1 if protected and protected.exists() else 0) > max_files
            or total_size()
            + (
                protected.stat().st_size
                if protected and protected.exists()
                else 0
            )
            > max_bytes
        ):
            oldest = files.pop(0)
            try:
                oldest.unlink()
            except FileNotFoundError:
                pass

    def _materialize_search(
        self,
        sf: SearchFilter,
    ) -> tuple[Path, int, list[str], bool]:
        """
        Scan the corpus once and persist matching IDs/sort keys.

        Returns:
            (cache_path, total_matches, highlight_terms, cache_hit)
        """
        if not self._ensure_view():
            raise RuntimeError("No Congressional Record Parquet corpus found")

        where_clause, params, highlight_terms = self._build_where_clause(sf)
        order_expression = self._order_expression(sf.sort_by)

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        key = self._search_cache_key(sf)
        cache_path = self.cache_dir / f"search_{key}.parquet"

        if cache_path.is_file():
            try:
                os.utime(cache_path, None)
            except OSError:
                pass
            self._prune_search_cache(protected=cache_path)
            total = pq.ParquetFile(cache_path).metadata.num_rows
            return cache_path, int(total), highlight_terms, True

        tmp_path = self.cache_dir / f"search_{key}.parquet.tmp"
        if tmp_path.exists():
            tmp_path.unlink()

        sql = f"""
            COPY (
                SELECT
                    speech_id,
                    congress,
                    date,
                    year,
                    word_count,
                    row_number() OVER (
                        ORDER BY {order_expression}
                    ) AS result_rank
                FROM speeches
                {where_clause}
                ORDER BY {order_expression}
            )
            TO {self._quote_path(tmp_path)}
            (
                FORMAT PARQUET,
                COMPRESSION ZSTD
            )
        """

        try:
            self.con.execute(sql, params)
            tmp_path.replace(cache_path)
        except Exception:
            if tmp_path.exists():
                tmp_path.unlink()
            raise

        self._prune_search_cache(protected=cache_path)
        total = pq.ParquetFile(cache_path).metadata.num_rows
        return cache_path, int(total), highlight_terms, False

    def _fetch_page(
        self,
        cache_path: Path,
        *,
        limit: int,
        offset: int,
    ) -> pd.DataFrame:
        """Fetch one page without rescanning speech_text across the corpus."""
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if offset < 0:
            raise ValueError("offset cannot be negative")

        hits = self.con.execute(
            f"""
            SELECT speech_id, congress, result_rank
            FROM read_parquet({self._quote_path(cache_path)})
            ORDER BY result_rank
            LIMIT ? OFFSET ?
            """,
            [int(limit), int(offset)],
        ).df()

        if hits.empty:
            return pd.DataFrame(columns=RESULT_COLUMNS)

        congresses = sorted(
            {int(value) for value in hits["congress"].dropna().tolist()}
        )

        source_files: list[Path] = []
        for congress in congresses:
            path = self.processed_dir / f"congress_{congress:03d}.parquet"
            if not path.is_file():
                raise FileNotFoundError(
                    f"Missing source Parquet needed for cached result: {path}"
                )
            source_files.append(path)

        file_list_sql = ", ".join(
            self._quote_path(path) for path in source_files
        )

        self.con.register("_page_hits", hits)

        try:
            column_sql = ",\n                ".join(
                f"s.{column}" for column in RESULT_COLUMNS
            )

            page = self.con.execute(
                f"""
                SELECT
                    {column_sql}
                FROM read_parquet(
                    [{file_list_sql}],
                    union_by_name=true
                ) AS s
                INNER JOIN _page_hits AS h
                    ON s.speech_id = h.speech_id
                ORDER BY h.result_rank
                """
            ).df()
        finally:
            try:
                self.con.unregister("_page_hits")
            except Exception:
                pass

        return page

    def search(
        self,
        sf: SearchFilter,
    ) -> tuple[pd.DataFrame, int, list[str]]:
        """
        Search with transparent literal semantics and cached pagination.

        First request for a query/filter combination scans the corpus and writes
        a compact result cache. Later pages reuse that cache.
        """
        if not self.get_parquet_files():
            return pd.DataFrame(columns=RESULT_COLUMNS), 0, []

        cache_path, total, highlight_terms, _ = self._materialize_search(sf)

        if total == 0:
            return pd.DataFrame(columns=RESULT_COLUMNS), 0, highlight_terms

        page = self._fetch_page(
            cache_path,
            limit=sf.limit,
            offset=sf.offset,
        )

        return page, total, highlight_terms

    def export_csv(
        self,
        sf: SearchFilter,
        max_rows: int = DEFAULT_CSV_EXPORT_MAX_ROWS,
    ) -> tuple[bytes, int, int]:
        """
        Export matching speeches for a search as CSV bytes.

        Reuses the same materialized result cache as ``search()``, so this
        does not rescan the corpus if the user already ran the search. The
        export is capped at ``max_rows`` (highest-ranked rows for the
        search's current sort order) to bound memory on small hosts.

        Returns (csv_bytes, exported_rows, total_matches).
        """
        if max_rows <= 0:
            raise ValueError("max_rows must be greater than zero")

        if not self.get_parquet_files():
            return (
                pd.DataFrame(columns=RESULT_COLUMNS).to_csv(index=False).encode("utf-8"),
                0,
                0,
            )

        cache_path, total, _highlight_terms, _cache_hit = self._materialize_search(sf)

        if total == 0:
            empty_csv = pd.DataFrame(columns=RESULT_COLUMNS).to_csv(index=False)
            return empty_csv.encode("utf-8"), 0, 0

        export_limit = min(total, max_rows)
        rows = self._fetch_page(cache_path, limit=export_limit, offset=0)
        csv_bytes = rows.to_csv(index=False).encode("utf-8")

        return csv_bytes, len(rows), total

    def get_filter_options(self) -> dict[str, list[Any]]:
        if not self._ensure_view():
            return {
                "congresses": [],
                "years": [],
                "chambers": [],
                "parties": [],
                "states": [],
            }

        def one_column(sql: str) -> list[Any]:
            return [row[0] for row in self.con.execute(sql).fetchall()]

        return {
            "congresses": one_column(
                "SELECT DISTINCT congress FROM speeches ORDER BY congress"
            ),
            "years": one_column(
                "SELECT DISTINCT year FROM speeches "
                "WHERE year IS NOT NULL ORDER BY year"
            ),
            "chambers": one_column(
                "SELECT DISTINCT chamber FROM speeches "
                "WHERE chamber IS NOT NULL AND chamber <> '' "
                "ORDER BY chamber"
            ),
            "parties": one_column(
                "SELECT DISTINCT party FROM speeches "
                "WHERE party IS NOT NULL AND party <> '' "
                "ORDER BY party"
            ),
            "states": one_column(
                """
                SELECT DISTINCT upper(trim(state)) AS state
                FROM speeches
                WHERE state IS NOT NULL
                  AND regexp_matches(
                      upper(trim(state)),
                      '^[A-Z]{2}$'
                  )
                ORDER BY state
                """
            ),
        }

    @staticmethod
    def extract_snippet(
        text: str,
        terms: list[str],
        window_chars: int = 180,
        max_snippets: int = 2,
    ) -> str:
        if not text:
            return ""

        if window_chars < 0:
            raise ValueError("window_chars cannot be negative")
        if max_snippets <= 0:
            raise ValueError("max_snippets must be greater than zero")

        literal_terms = [term.strip() for term in terms if term and term.strip()]

        if not literal_terms:
            preview = text[:360]
            escaped = html.escape(preview)
            return escaped + ("..." if len(text) > len(preview) else "")

        pattern = re.compile(
            "|".join(
                sorted(
                    (re.escape(term) for term in literal_terms),
                    key=len,
                    reverse=True,
                )
            ),
            flags=re.IGNORECASE,
        )

        matches = list(pattern.finditer(text))

        if not matches:
            preview = text[:360]
            escaped = html.escape(preview)
            return escaped + ("..." if len(text) > len(preview) else "")

        regions: list[tuple[int, int]] = []

        for match in matches:
            start = max(0, match.start() - window_chars)
            end = min(len(text), match.end() + window_chars)

            overlaps = any(
                start <= old_end and end >= old_start
                for old_start, old_end in regions
            )
            if overlaps:
                continue

            regions.append((start, end))
            if len(regions) >= max_snippets:
                break

        snippets: list[str] = []

        for start, end in regions:
            chunk = text[start:end]
            rendered: list[str] = []
            cursor = 0

            for match in pattern.finditer(chunk):
                rendered.append(html.escape(chunk[cursor:match.start()]))
                rendered.append(
                    "<mark class='kw-match'>"
                    + html.escape(match.group(0))
                    + "</mark>"
                )
                cursor = match.end()

            rendered.append(html.escape(chunk[cursor:]))

            prefix = "..." if start > 0 else ""
            suffix = "..." if end < len(text) else ""
            snippets.append(prefix + "".join(rendered) + suffix)

        return (
            " <span class='kw-separator'>&bull;&bull;&bull;</span> "
        ).join(snippets)
