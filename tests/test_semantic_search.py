"""SemanticSearchEngine with a fake embedding model (no sentence-transformers, no download)."""
from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.semantic_search import SemanticSearchEngine, SemanticSearchUnavailable
from conftest import CONGRESS_66, write_congress


class FakeModel:
    """Maps a query to a fixed vector; the test chooses which stored sentence it should be closest to."""

    def __init__(self, vec):
        self.vec = np.asarray(vec, dtype=np.float32)

    def encode(self, texts, convert_to_numpy=True, normalize_embeddings=True):
        return np.tile(self.vec, (len(texts), 1))


@pytest.fixture
def index(tmp_path):
    processed = tmp_path / "processed"
    processed.mkdir()
    write_congress(processed, 66, CONGRESS_66)
    idx = tmp_path / "pilot_066"
    idx.mkdir()
    sentences = pa.table({
        "sentence_id": pa.array([0, 1, 2, 3], pa.int64()),
        "speech_id": pa.array([660001, 660001, 660002, 660004], pa.int64()),
        "sentence_index": pa.array([0, 1, 0, 1], pa.int32()),
        "sentence_text": ["The treaty must be ratified.", "The Senate will act.", "I stand upon the letter.", "A prayer to God."],
    })
    pq.write_table(sentences, idx / "sentences.parquet")
    vecs = np.eye(4, dtype=np.float32)                           # sentence i points along axis i
    np.save(idx / "embeddings.npy", vecs)
    (idx / "meta.json").write_text(json.dumps({"congress": 66, "model": "fake/model", "dim": 4, "sentence_count": 4}))
    return idx, processed


def engine_for(index, vec):
    idx, processed = index
    e = SemanticSearchEngine(66, index_dir=idx, processed_dir=processed)
    e._ensure_loaded()
    e._model = FakeModel(vec)
    return e


def test_index_detection(index, tmp_path):
    idx, processed = index
    assert SemanticSearchEngine.index_exists(idx)
    assert not SemanticSearchEngine.index_exists(tmp_path / "missing")
    assert SemanticSearchEngine(66, index_dir=idx, processed_dir=processed).available()


def test_missing_index_raises_a_helpful_error(tmp_path):
    e = SemanticSearchEngine(66, index_dir=tmp_path / "none", processed_dir=tmp_path)
    assert not e.available()
    with pytest.raises(SemanticSearchUnavailable, match="build_sentence_embeddings"):
        e._ensure_loaded()


def test_nearest_sentence_comes_first_and_carries_speech_metadata(index):
    res = engine_for(index, [0, 0, 1, 0]).search("letter", top_k=2)
    top = res.results.iloc[0]
    assert top["sentence_text"] == "I stand upon the letter." and top["similarity"] == pytest.approx(1.0)
    assert top["last_name"] == "HITCHCOCK" and top["party"] == "D" and top["date"] == "19191119"
    assert res.model == "fake/model" and res.sentence_count == 4 and len(res.results) == 2
    assert list(res.results["similarity"]) == sorted(res.results["similarity"], reverse=True)


def test_top_k_is_clamped_to_the_index_size(index):
    assert len(engine_for(index, [1, 0, 0, 0]).search("x", top_k=50).results) == 4


def test_a_sentence_from_a_speech_without_metadata_is_still_returned(index):
    top = engine_for(index, [0, 0, 0, 1]).search("prayer", top_k=1).results.iloc[0]
    assert top["sentence_text"] == "A prayer to God." and top["speaker"] == "The VICE PRESIDENT"


def test_argument_checks(index):
    e = engine_for(index, [1, 0, 0, 0])
    with pytest.raises(ValueError):
        e.search("   ")
    with pytest.raises(ValueError):
        e.search("x", top_k=0)


def test_corrupt_index_is_detected(index):
    idx, processed = index
    np.save(idx / "embeddings.npy", np.eye(3, dtype=np.float32))          # 3 vectors for 4 sentences
    with pytest.raises(RuntimeError, match="Corrupt"):
        SemanticSearchEngine(66, index_dir=idx, processed_dir=processed)._ensure_loaded()


def test_missing_source_parquet_is_reported(index):
    idx, processed = index
    (processed / "congress_066.parquet").unlink()
    e = engine_for(index, [1, 0, 0, 0])
    with pytest.raises(FileNotFoundError):
        e.search("x")
