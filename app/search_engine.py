"""
DuckDB-based Search & Analytics Engine for Congressional Speeches Parquet Corpus.
"""

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

import duckdb
import pandas as pd


@dataclass
class SearchFilter:
    query: str = ""
    search_mode: str = "all_words"  # 'all_words', 'exact_phrase', 'any_word', 'regex'
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    congress_list: Optional[List[int]] = None
    chambers: Optional[List[str]] = None  # ['S', 'H']
    parties: Optional[List[str]] = None  # ['R', 'D', 'Other']
    states: Optional[List[str]] = None
    speaker_query: str = ""
    limit: int = 100
    offset: int = 0
    sort_by: str = "date_asc"  # 'date_asc', 'date_desc', 'word_count_desc'


class SearchEngine:

    def __init__(self, processed_dir: Optional[Path] = None):
        self.project_root = Path(__file__).resolve().parent.parent
        if processed_dir:
            self.processed_dir = processed_dir
        else:
            cand1 = self.project_root / "data" / "processed"
            cand2 = self.project_root / "processed"
            self.processed_dir = cand1 if cand1.exists() else cand2

        self.con = duckdb.connect(database=":memory:")
        self._table_view_initialized = False

    def get_parquet_files(self) -> List[Path]:
        if not self.processed_dir.exists():
            return []
        return sorted(list(self.processed_dir.glob("congress_*.parquet")))

    def get_corpus_stats(self) -> Dict[str, Any]:
        files = self.get_parquet_files()
        if not files:
            return {
                "total_congresses": 0,
                "congress_min": 0,
                "congress_max": 0,
                "total_speeches": 0,
                "year_min": 0,
                "year_max": 0,
                "total_size_mb": 0.0,
            }

        total_bytes = sum(f.stat().st_size for f in files)
        parquet_glob = str(self.processed_dir / "congress_*.parquet")

        try:
            summary = self.con.execute(f"""
                SELECT 
                    COUNT(DISTINCT congress) as congress_count,
                    MIN(congress) as min_c,
                    MAX(congress) as max_c,
                    COUNT(*) as total_rows,
                    MIN(year) as min_y,
                    MAX(year) as max_y
                FROM read_parquet('{parquet_glob}')
            """).fetchone()

            return {
                "total_congresses": summary[0] or len(files),
                "congress_min": summary[1] or 43,
                "congress_max": summary[2] or 114,
                "total_speeches": summary[3] or 0,
                "year_min": summary[4] or 1873,
                "year_max": summary[5] or 2016,
                "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            }
        except Exception:
            return {
                "total_congresses": len(files),
                "congress_min": 43,
                "congress_max": 114,
                "total_speeches": 0,
                "year_min": 1873,
                "year_max": 2016,
                "total_size_mb": round(total_bytes / (1024 * 1024), 2),
            }

    def _build_where_clause(
        self, sf: SearchFilter
    ) -> Tuple[str, List[Any], List[str]]:
        conditions = []
        params = []
        highlight_terms: List[str] = []

        # 1. Text search query
        q = sf.query.strip()
        if q:
            if sf.search_mode == "exact_phrase":
                conditions.append("speech_text ILIKE ?")
                params.append(f"%{q}%")
                highlight_terms.append(q)
            elif sf.search_mode == "any_word":
                words = [w for w in re.split(r"\s+", q) if w]
                if words:
                    sub_conds = ["speech_text ILIKE ?" for _ in words]
                    conditions.append(f"({' OR '.join(sub_conds)})")
                    params.extend([f"%{w}%" for w in words])
                    highlight_terms.extend(words)
            elif sf.search_mode == "regex":
                conditions.append("regexp_matches(speech_text, ?, 'i')")
                params.append(q)
                highlight_terms.append(q)
            else:  # 'all_words' default
                words = [w for w in re.split(r"\s+", q) if w]
                if words:
                    for w in words:
                        conditions.append("speech_text ILIKE ?")
                        params.append(f"%{w}%")
                        highlight_terms.append(w)

        # 2. Year filters
        if sf.year_min is not None:
            conditions.append("year >= ?")
            params.append(sf.year_min)
        if sf.year_max is not None:
            conditions.append("year <= ?")
            params.append(sf.year_max)

        # 3. Congress list
        if sf.congress_list:
            placeholders = ", ".join(["?"] * len(sf.congress_list))
            conditions.append(f"congress IN ({placeholders})")
            params.extend(sf.congress_list)

        # 4. Chamber filters ('S', 'H')
        if sf.chambers:
            placeholders = ", ".join(["?"] * len(sf.chambers))
            conditions.append(f"chamber IN ({placeholders})")
            params.extend(sf.chambers)

        # 5. Party filters
        if sf.parties:
            party_conds = []
            for p in sf.parties:
                if p == "Other":
                    party_conds.append("party NOT IN ('R', 'D') OR party IS NULL")
                else:
                    party_conds.append("party = ?")
                    params.append(p)
            if party_conds:
                conditions.append(f"({' OR '.join(party_conds)})")

        # 6. States
        if sf.states:
            placeholders = ", ".join(["?"] * len(sf.states))
            conditions.append(f"state IN ({placeholders})")
            params.extend(sf.states)

        # 7. Speaker name query
        if sf.speaker_query.strip():
            sq = sf.speaker_query.strip()
            conditions.append(
                "(speaker ILIKE ? OR last_name ILIKE ? OR first_name ILIKE ?)"
            )
            params.extend([f"%{sq}%", f"%{sq}%", f"%{sq}%"])

        where_clause = (
            "WHERE " + " AND ".join(conditions) if conditions else ""
        )
        return where_clause, params, highlight_terms

    def search(
        self, sf: SearchFilter
    ) -> Tuple[pd.DataFrame, int, List[str]]:
        """
        Execute search query and return (results_dataframe, total_count, highlight_terms).
        """
        files = self.get_parquet_files()
        if not files:
            return pd.DataFrame(), 0, []

        parquet_glob = str(self.processed_dir / "congress_*.parquet")
        where_clause, params, highlight_terms = self._build_where_clause(sf)

        # Sort clause
        if sf.sort_by == "date_desc":
            order_clause = "ORDER BY date DESC, speech_id DESC"
        elif sf.sort_by == "word_count_desc":
            order_clause = "ORDER BY word_count DESC"
        elif sf.sort_by == "word_count_asc":
            order_clause = "ORDER BY word_count ASC"
        else:
            order_clause = "ORDER BY date ASC, speech_id ASC"

        # Count total matching rows
        count_sql = f"SELECT COUNT(*) FROM read_parquet('{parquet_glob}') {where_clause}"
        total_count = self.con.execute(count_sql, params).fetchone()[0]

        if total_count == 0:
            return pd.DataFrame(), 0, highlight_terms

        # Select matching page
        select_sql = f"""
            SELECT 
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
            FROM read_parquet('{parquet_glob}')
            {where_clause}
            {order_clause}
            LIMIT ? OFFSET ?
        """
        page_params = params + [sf.limit, sf.offset]
        df = self.con.execute(select_sql, page_params).df()

        return df, total_count, highlight_terms

    def get_aggregations(self, sf: SearchFilter) -> Dict[str, pd.DataFrame]:
        """
        Get aggregated statistics for matching query results (yearly trends, party breakdown, chamber breakdown).
        """
        files = self.get_parquet_files()
        if not files:
            return {
                "by_year": pd.DataFrame(),
                "by_party": pd.DataFrame(),
                "by_chamber": pd.DataFrame(),
            }

        parquet_glob = str(self.processed_dir / "congress_*.parquet")
        where_clause, params, _ = self._build_where_clause(sf)

        # 1. By Year
        sql_year = f"""
            SELECT year, COUNT(*) as count 
            FROM read_parquet('{parquet_glob}')
            {where_clause}
            GROUP BY year
            ORDER BY year ASC
        """
        df_year = self.con.execute(sql_year, params).df()

        # 2. By Party
        sql_party = f"""
            SELECT 
                CASE 
                    WHEN party = 'R' THEN 'Republican'
                    WHEN party = 'D' THEN 'Democrat'
                    WHEN party IS NULL OR party = '' THEN 'Unknown/Procedural'
                    ELSE 'Other (' || party || ')'
                END as party_label,
                COUNT(*) as count
            FROM read_parquet('{parquet_glob}')
            {where_clause}
            GROUP BY 1
            ORDER BY count DESC
        """
        df_party = self.con.execute(sql_party, params).df()

        # 3. By Chamber
        sql_chamber = f"""
            SELECT 
                CASE 
                    WHEN chamber = 'S' THEN 'Senate'
                    WHEN chamber = 'H' THEN 'House'
                    ELSE 'Other/Unknown'
                END as chamber_label,
                COUNT(*) as count
            FROM read_parquet('{parquet_glob}')
            {where_clause}
            GROUP BY 1
            ORDER BY count DESC
        """
        df_chamber = self.con.execute(sql_chamber, params).df()

        return {
            "by_year": df_year,
            "by_party": df_party,
            "by_chamber": df_chamber,
        }

    @staticmethod
    def extract_snippet(
        text: str,
        terms: List[str],
        window_chars: int = 150,
        max_snippets: int = 2,
    ) -> str:
        """
        Extract snippet around query terms with HTML <mark> highlight tags.
        """
        if not text:
            return ""

        if not terms:
            return text[:300] + ("..." if len(text) > 300 else "")

        # Find all match spans
        spans = []
        for term in terms:
            if not term or len(term.strip()) == 0:
                continue
            escaped = re.escape(term)
            for m in re.finditer(escaped, text, flags=re.IGNORECASE):
                spans.append((m.start(), m.end()))

        if not spans:
            return text[:300] + ("..." if len(text) > 300 else "")

        spans.sort(key=lambda x: x[0])
        # Pick top snippet regions
        snippets = []
        used_ranges = []

        for start, end in spans[:max_snippets]:
            snippet_start = max(0, start - window_chars)
            snippet_end = min(len(text), end + window_chars)

            # Avoid overlapping snippets
            overlap = False
            for u_start, u_end in used_ranges:
                if not (snippet_end < u_start or snippet_start > u_end):
                    overlap = True
                    break
            if overlap:
                continue

            used_ranges.append((snippet_start, snippet_end))
            raw_chunk = text[snippet_start:snippet_end]

            prefix = "..." if snippet_start > 0 else ""
            suffix = "..." if snippet_end < len(text) else ""

            # Highlight terms inside chunk
            def repl(m):
                return f"<mark class='kw-match'>{m.group(0)}</mark>"

            pattern = "|".join([re.escape(t) for t in terms if t.strip()])
            if pattern:
                highlighted_chunk = re.sub(
                    pattern, repl, raw_chunk, flags=re.IGNORECASE
                )
            else:
                highlighted_chunk = raw_chunk

            snippets.append(f"{prefix}{highlighted_chunk}{suffix}")

        return " <span class='kw-separator'>&bull;&bull;&bull;</span> ".join(
            snippets
        )
