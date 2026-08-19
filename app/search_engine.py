"""
DuckDB search backend for the full Stanford Congressional Record Parquet corpus.

The backend searches every canonical Stanford Congress Parquet currently
available in data/processed/, limited to Congresses 043-114.

Search semantics in this V1 are deliberately simple and transparent:

* exact_phrase  -> literal case-insensitive substring search
* all_words     -> every whitespace-separated term must occur somewhere
* any_word      -> at least one whitespace-separated term must occur
* regex         -> case-insensitive regular expression (kept for compatibility)

"all_words" and "any_word" are substring searches, not linguistic token
searches. The researcher-facing UI should describe them as "Contains all
terms" / "Contains any term" rather than implying stemming or word boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
import html
from pathlib import Path
import re
from typing import Any, Optional

import duckdb
import pandas as pd


CORPUS_START = 43
CORPUS_END = 114

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
    """Search the canonical Congress 043-114 Parquet corpus with DuckDB."""

    def __init__(self, processed_dir: Optional[Path | str] = None):
        self.project_root = Path(__file__).resolve().parent.parent

        if processed_dir is None:
            self.processed_dir = self.project_root / "data" / "processed"
        else:
            self.processed_dir = Path(processed_dir)

        self.con = duckdb.connect(database=":memory:")
        self._view_initialized = False
        self._view_files: tuple[Path, ...] = ()

        # Keep temporary work bounded and avoid surprising disk-heavy behavior.
        self.con.execute("SET preserve_insertion_order = false")

    def close(self) -> None:
        """Close the in-memory DuckDB connection."""
        try:
            self.con.close()
        except Exception:
            pass

    def get_parquet_files(self) -> list[Path]:
        """
        Return canonical Stanford Parquet files for Congresses 043-114.

        Files outside this range are ignored.
        """
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
        """Quote a local path for a DuckDB SQL string literal."""
        return "'" + str(path.resolve()).replace("'", "''") + "'"

    def _ensure_view(self) -> bool:
        """Create/recreate the DuckDB view over the current canonical corpus files."""
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

    def get_corpus_stats(self) -> dict[str, Any]:
        """Return lightweight summary statistics for the indexed canonical corpus."""
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
                COUNT(DISTINCT congress) AS congress_count,
                MIN(congress) AS congress_min,
                MAX(congress) AS congress_max,
                COUNT(*) AS total_speeches,
                MIN(year) AS year_min,
                MAX(year) AS year_max
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
        """Split a simple query on whitespace, preserving no empty terms."""
        return [term for term in re.split(r"\s+", query.strip()) if term]

    def _build_where_clause(
        self,
        sf: SearchFilter,
    ) -> tuple[str, list[Any], list[str]]:
        """
        Build a parameterized WHERE clause.

        Text matching uses strpos(lower(text), lower(?)) rather than SQL LIKE.
        That makes %, _ and other user characters literal instead of wildcard
        syntax, which is easier to explain to researchers.
        """
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
                # Kept for compatibility with the existing Streamlit prototype.
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
            conditions.append(f"upper(coalesce(chamber, '')) IN ({placeholders})")
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
            conditions.append(f"upper(coalesce(state, '')) IN ({placeholders})")
            params.extend(states)

        speaker_query = sf.speaker_query.strip()
        if speaker_query:
            speaker_condition = """
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
            conditions.append(speaker_condition)
            params.extend([speaker_query] * 4)

        if not conditions:
            return "", params, highlight_terms

        return "WHERE " + " AND ".join(conditions), params, highlight_terms

    @staticmethod
    def _order_clause(sort_by: str) -> str:
        # speech_id gives deterministic ordering for ties.
        if sort_by == "date_desc":
            return "ORDER BY date DESC NULLS LAST, speech_id DESC"
        if sort_by == "word_count_desc":
            return (
                "ORDER BY word_count DESC NULLS LAST, "
                "date ASC NULLS LAST, speech_id ASC"
            )
        if sort_by == "word_count_asc":
            return (
                "ORDER BY word_count ASC NULLS LAST, "
                "date ASC NULLS LAST, speech_id ASC"
            )
        return "ORDER BY date ASC NULLS LAST, speech_id ASC"

    def search(
        self,
        sf: SearchFilter,
    ) -> tuple[pd.DataFrame, int, list[str]]:
        """
        Execute one search and return:

            (page_dataframe, total_matching_speeches, highlight_terms)

        COUNT(*) OVER() allows the normal case to obtain the result page and
        total count in one query rather than scanning the corpus twice.
        """
        if not self._ensure_view():
            return pd.DataFrame(columns=RESULT_COLUMNS), 0, []

        if sf.limit <= 0:
            raise ValueError("SearchFilter.limit must be greater than zero")
        if sf.offset < 0:
            raise ValueError("SearchFilter.offset cannot be negative")

        where_clause, params, highlight_terms = self._build_where_clause(sf)
        order_clause = self._order_clause(sf.sort_by)

        column_sql = ",\n                ".join(RESULT_COLUMNS)

        sql = f"""
            SELECT
                {column_sql},
                COUNT(*) OVER () AS _total_matches
            FROM speeches
            {where_clause}
            {order_clause}
            LIMIT ? OFFSET ?
        """

        query_params = [*params, int(sf.limit), int(sf.offset)]
        df = self.con.execute(sql, query_params).df()

        if not df.empty:
            total_count = int(df["_total_matches"].iloc[0])
            df = df.drop(columns=["_total_matches"])
            return df, total_count, highlight_terms

        # If an old pagination offset is now past the end after filters/query
        # changed, we still need the true match count so the UI can recover.
        if sf.offset > 0:
            count_sql = f"""
                SELECT COUNT(*)
                FROM speeches
                {where_clause}
            """
            total_count = int(self.con.execute(count_sql, params).fetchone()[0])
            return pd.DataFrame(columns=RESULT_COLUMNS), total_count, highlight_terms

        return pd.DataFrame(columns=RESULT_COLUMNS), 0, highlight_terms

    def get_aggregations(self, sf: SearchFilter) -> dict[str, pd.DataFrame]:
        """
        Return optional secondary summaries for the current search.

        These count matching speeches, NOT keyword occurrences.
        """
        if not self._ensure_view():
            return {
                "by_year": pd.DataFrame(columns=["year", "count"]),
                "by_party": pd.DataFrame(columns=["party_label", "count"]),
                "by_chamber": pd.DataFrame(columns=["chamber_label", "count"]),
            }

        where_clause, params, _ = self._build_where_clause(sf)

        by_year = self.con.execute(
            f"""
            SELECT year, COUNT(*) AS count
            FROM speeches
            {where_clause}
            GROUP BY year
            ORDER BY year
            """,
            params,
        ).df()

        by_party = self.con.execute(
            f"""
            SELECT
                CASE
                    WHEN party = 'R' THEN 'Republican'
                    WHEN party = 'D' THEN 'Democrat'
                    WHEN party IS NULL OR party = '' THEN 'Unknown/Procedural'
                    ELSE 'Other (' || party || ')'
                END AS party_label,
                COUNT(*) AS count
            FROM speeches
            {where_clause}
            GROUP BY 1
            ORDER BY count DESC
            """,
            params,
        ).df()

        by_chamber = self.con.execute(
            f"""
            SELECT
                CASE
                    WHEN upper(chamber) = 'S' THEN 'Senate'
                    WHEN upper(chamber) = 'H' THEN 'House'
                    ELSE 'Other/Unknown'
                END AS chamber_label,
                COUNT(*) AS count
            FROM speeches
            {where_clause}
            GROUP BY 1
            ORDER BY count DESC
            """,
            params,
        ).df()

        return {
            "by_year": by_year,
            "by_party": by_party,
            "by_chamber": by_chamber,
        }

    def get_filter_options(self) -> dict[str, list[Any]]:
        """
        Return values useful for building researcher-facing filters.

        This is not required by the current app.py, but gives the next UI pass
        a way to avoid hard-coded states, parties, and Congress numbers.
        """
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
                "WHERE chamber IS NOT NULL AND chamber <> '' ORDER BY chamber"
            ),
            "parties": one_column(
                "SELECT DISTINCT party FROM speeches "
                "WHERE party IS NOT NULL AND party <> '' ORDER BY party"
            ),
            "states": one_column(
                "SELECT DISTINCT state FROM speeches "
                "WHERE state IS NOT NULL AND state <> '' ORDER BY state"
            ),
        }

    @staticmethod
    def extract_snippet(
        text: str,
        terms: list[str],
        window_chars: int = 180,
        max_snippets: int = 2,
    ) -> str:
        """
        Return HTML-safe KWIC context with literal query terms highlighted.

        Historical source text is escaped before HTML markup is inserted, so
        characters such as <, > and & in the Congressional Record cannot be
        interpreted as HTML by Streamlit.
        """
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

            # Merge/skip strongly overlapping regions.
            overlaps = any(start <= old_end and end >= old_start for old_start, old_end in regions)
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
