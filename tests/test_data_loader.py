"""resolve_processed_dir: environment override, local corpus, pinned Hugging Face snapshot."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import data_loader as dl


def make_corpus(directory: Path, congresses=range(dl.CORPUS_START, dl.CORPUS_END + 1)) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for c in congresses:
        (directory / f"congress_{c:03d}.parquet").write_bytes(b"")
    return directory


@pytest.fixture(autouse=True)
def no_env_and_no_network(monkeypatch):
    monkeypatch.delenv("CONGRESS_DATA_DIR", raising=False)

    def refuse(**kw):
        raise AssertionError(f"unexpected download: {kw}")

    monkeypatch.setattr(dl, "snapshot_download", refuse)


def test_constants_are_consistent():
    assert dl.EXPECTED_FILE_COUNT == dl.CORPUS_END - dl.CORPUS_START + 1 == 72
    assert re.fullmatch(r"[0-9a-f]{40}", dl.HF_REVISION), "the dataset revision must be pinned to a full commit hash"
    assert dl.HF_REPO_ID == "yeeder/congressional-record-parquet"


def test_canonical_files_ignores_other_names_and_missing_dirs(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    for name in ["congress_042.parquet", "congress_043.parquet", "congress_115.parquet", "congress_66.parquet", "x.parquet"]:
        (d / name).write_bytes(b"")
    (d / "congress_044.parquet").mkdir()                         # a directory, not a file
    assert [p.name for p in dl._canonical_files(d)] == ["congress_043.parquet"]
    assert dl._canonical_files(tmp_path / "nope") == []


def test_environment_variable_wins_and_must_be_complete(tmp_path, monkeypatch):
    corpus = make_corpus(tmp_path / "env")
    monkeypatch.setenv("CONGRESS_DATA_DIR", str(corpus))
    assert dl.resolve_processed_dir(tmp_path / "project") == corpus.resolve()

    broken = make_corpus(tmp_path / "broken", range(43, 114))     # 71 files
    monkeypatch.setenv("CONGRESS_DATA_DIR", str(broken))
    with pytest.raises(RuntimeError, match=r"71/72"):
        dl.resolve_processed_dir(tmp_path / "project")


def test_complete_local_corpus_is_used_without_downloading(tmp_path):
    project = tmp_path / "project"
    local = make_corpus(project / "data" / "processed")
    assert dl.resolve_processed_dir(project) == local


def test_incomplete_local_corpus_falls_back_to_the_pinned_snapshot(tmp_path, monkeypatch):
    project = tmp_path / "project"
    make_corpus(project / "data" / "processed", range(43, 60))   # not enough
    snapshot = tmp_path / "snap"
    make_corpus(snapshot / "data")
    calls = []

    def fake_download(**kw):
        calls.append(kw)
        return str(snapshot)

    monkeypatch.setattr(dl, "snapshot_download", fake_download)
    assert dl.resolve_processed_dir(project) == snapshot / "data"
    (kw,) = calls
    assert kw["repo_id"] == dl.HF_REPO_ID and kw["repo_type"] == "dataset" and kw["revision"] == dl.HF_REVISION
    assert set(kw["allow_patterns"]) == {"data/*.parquet", "manifest.json"}


def test_incomplete_snapshot_is_an_error(tmp_path, monkeypatch):
    snapshot = tmp_path / "snap"
    make_corpus(snapshot / "data", range(43, 100))
    monkeypatch.setattr(dl, "snapshot_download", lambda **kw: str(snapshot))
    with pytest.raises(RuntimeError, match="incomplete"):
        dl.resolve_processed_dir(tmp_path / "empty_project")
