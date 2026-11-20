"""Shared fixtures: a tiny synthetic corpus in the exact Parquet schema the app expects."""
from __future__ import annotations

import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.process_corpus import PARQUET_SCHEMA  # noqa: E402

BLANK = dict(
    speech_id=None, congress=None, date="", year=None, chamber="", speaker_id=None, first_name="", last_name="",
    speaker="", state="", gender="", party="", district="", nonvoting="", char_count=None, word_count=None,
    speech_text="", source="hein-bound",
)


def speech(**kw):
    row = {**BLANK, **kw}
    row["char_count"] = len(row["speech_text"])
    row["word_count"] = len(row["speech_text"].split())
    row["year"] = int(row["date"][:4]) if row["date"] else None
    return row


def write_congress(directory: Path, congress: int, rows: list[dict]) -> Path:
    path = directory / f"congress_{congress:03d}.parquet"
    table = pa.Table.from_pylist([{**r, "congress": congress} for r in rows], schema=PARQUET_SCHEMA)
    pq.write_table(table, path)
    return path


CONGRESS_66 = [
    speech(speech_id=660001, date="19191117", chamber="S", speaker="Mr. LODGE", first_name="HENRY", last_name="LODGE",
           state="MA", party="R", speaker_id=1,
           speech_text="Mr. President, the treaty of peace with Germany must be ratified only with reservations. "
                       "The Senate will act upon the covenant."),
    speech(speech_id=660002, date="19191119", chamber="S", speaker="Mr. HITCHCOCK", first_name="GILBERT",
           last_name="HITCHCOCK", state="NE", party="D", speaker_id=2,
           speech_text="I stand upon the President's letter and the League of Nations."),
    speech(speech_id=660003, date="19191119", chamber="H", speaker="Mr. MONDELL", first_name="FRANK",
           last_name="MONDELL", state="WY", party="R", speaker_id=3,
           speech_text="The House adjourned in the matter of railroads."),
    # no speaker metadata at all: must still be searchable
    speech(speech_id=660004, date="19200105", chamber="S", speaker="The VICE PRESIDENT",
           speech_text="The Senate will be in order. The Chaplain offered a prayer to Almighty God."),
]
CONGRESS_77 = [
    speech(speech_id=770001, date="19410310", chamber="H", speaker="Mr. SMITH", first_name="JOHN", last_name="SMITH",
           state="TX", party="D", speaker_id=4,
           speech_text="Religious freedom and freedom of worship are essential. <b>Bold</b> & co"),
    speech(speech_id=770002, date="19411208", chamber="S", speaker="Mr. WHEELER", first_name="BURTON",
           last_name="WHEELER", state="MT", party="D", speaker_id=5,
           speech_text="Declaration of war. The sectarian divisions must not matter."),
    speech(speech_id=770003, date="19420201", chamber="H", speaker="Mr. JONES", first_name="PAUL", last_name="JONES",
           state="OH", party="I", speaker_id=6,
           speech_text="Sect and section are different words, and sectarian is another."),
]


@pytest.fixture
def corpus_dir(tmp_path) -> Path:
    d = tmp_path / "processed"
    d.mkdir()
    write_congress(d, 66, CONGRESS_66)
    write_congress(d, 77, CONGRESS_77)
    return d


@pytest.fixture
def engine(corpus_dir, tmp_path):
    from app.search_engine import SearchEngine

    e = SearchEngine(processed_dir=corpus_dir, cache_dir=tmp_path / "cache")
    yield e
    e.close()


def ids(df) -> list[int]:
    return [int(x) for x in df["speech_id"].tolist()]
