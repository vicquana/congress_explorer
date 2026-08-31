"""
Prototype semantic (embedding-based) sentence search.

Experimental — scoped to a single pilot Congress. Unlike ``search_engine.py``,
this module does not scan the full corpus: it looks up a pre-built sentence
embedding index under ``data/embeddings/pilot_<congress>/`` (see
``scripts/build_sentence_embeddings.py``) and ranks sentences by cosine
similarity to a free-text query.

Not wired into automated tests or validated as research infrastructure yet;
see the Roadmap in README.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq


@dataclass
class SemanticSearchResult:
    congress: int
    model: str
    sentence_count: int
    results: pd.DataFrame


class SemanticSearchUnavailable(Exception):
    """Raised when no pre-built embedding index exists for a Congress."""


class SemanticSearchEngine:
    """Loads a pre-built sentence embedding index and ranks by similarity."""

    def __init__(
        self,
        congress: int,
        index_dir: Optional[Path | str] = None,
        processed_dir: Optional[Path | str] = None,
    ):
        self.congress = congress
        self.project_root = Path(__file__).resolve().parent.parent

        self.index_dir = (
            Path(index_dir)
            if index_dir is not None
            else self.project_root / "data" / "embeddings" / f"pilot_{congress:03d}"
        )

        self.processed_dir = (
            Path(processed_dir)
            if processed_dir is not None
            else self.project_root / "data" / "processed"
        )

        self._meta: Optional[dict] = None
        self._sentences: Optional[pd.DataFrame] = None
        self._embeddings: Optional[np.ndarray] = None
        self._model = None

    @staticmethod
    def index_exists(index_dir: Path) -> bool:
        return (
            (index_dir / "meta.json").is_file()
            and (index_dir / "sentences.parquet").is_file()
            and (index_dir / "embeddings.npy").is_file()
        )

    def available(self) -> bool:
        return self.index_exists(self.index_dir)

    def _ensure_loaded(self) -> None:
        if self._embeddings is not None:
            return

        if not self.available():
            raise SemanticSearchUnavailable(
                f"No sentence embedding index found for Congress "
                f"{self.congress:03d} at {self.index_dir}. Build it with "
                f"scripts/build_sentence_embeddings.py --congress "
                f"{self.congress}."
            )

        self._meta = json.loads((self.index_dir / "meta.json").read_text())
        self._sentences = pq.read_table(
            self.index_dir / "sentences.parquet"
        ).to_pandas()
        # Loaded fully into RAM rather than mmap'd: a memory-mapped
        # read-only array is backed by clean file pages the OS is free to
        # evict under memory pressure, which turns every subsequent query
        # into a multi-second disk re-read of the whole file.
        self._embeddings = np.load(self.index_dir / "embeddings.npy")

        if self._embeddings.shape[0] != len(self._sentences):
            raise RuntimeError(
                f"Corrupt embedding index at {self.index_dir}: "
                f"{self._embeddings.shape[0]:,} embedding rows vs "
                f"{len(self._sentences):,} sentence rows"
            )

    def _ensure_model(self):
        if self._model is not None:
            return self._model

        from sentence_transformers import SentenceTransformer

        self._ensure_loaded()
        self._model = SentenceTransformer(self._meta["model"], device="cpu")
        return self._model

    def _fetch_speech_metadata(self, speech_ids: list[int]) -> pd.DataFrame:
        source_path = self.processed_dir / f"congress_{self.congress:03d}.parquet"
        if not source_path.is_file():
            raise FileNotFoundError(f"Missing source Parquet: {source_path}")

        con = duckdb.connect(database=":memory:")
        try:
            ids_df = pd.DataFrame({"speech_id": speech_ids})
            con.register("_ids", ids_df)
            return con.execute(
                f"""
                SELECT
                    s.speech_id,
                    s.congress,
                    s.date,
                    s.year,
                    s.chamber,
                    s.speaker,
                    s.first_name,
                    s.last_name,
                    s.party,
                    s.state,
                    s.word_count,
                    s.speech_text
                FROM read_parquet('{source_path.resolve()}') AS s
                INNER JOIN _ids AS i ON s.speech_id = i.speech_id
                """
            ).df()
        finally:
            con.close()

    def search(self, query_text: str, top_k: int = 20) -> SemanticSearchResult:
        query_text = query_text.strip()
        if not query_text:
            raise ValueError("query_text must not be empty")
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")

        model = self._ensure_model()

        query_vec = model.encode(
            [query_text],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )[0].astype(np.float32)

        embeddings = np.asarray(self._embeddings)
        similarities = embeddings @ query_vec

        k = min(top_k, similarities.shape[0])
        top_indices = np.argpartition(-similarities, k - 1)[:k]
        top_indices = top_indices[np.argsort(-similarities[top_indices])]

        hits = self._sentences.iloc[top_indices].copy()
        hits["similarity"] = similarities[top_indices]

        metadata = self._fetch_speech_metadata(
            sorted(set(int(v) for v in hits["speech_id"]))
        )
        merged = hits.merge(metadata, on="speech_id", how="left")
        merged = merged.sort_values("similarity", ascending=False).reset_index(
            drop=True
        )

        return SemanticSearchResult(
            congress=self.congress,
            model=self._meta["model"],
            sentence_count=len(self._sentences),
            results=merged,
        )
