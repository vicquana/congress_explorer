"""Start the Streamlit app against a tiny corpus and run one search through the form (streamlit.testing, no browser)."""
from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from conftest import CONGRESS_66, CONGRESS_77, ROOT, write_congress
from scripts.process_corpus import PARQUET_SCHEMA

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


@pytest.fixture
def full_range_corpus(tmp_path) -> Path:
    """The loader insists on all 72 files, so write 72: two with speeches, the rest valid but empty."""
    d = tmp_path / "processed"
    d.mkdir()
    for c in range(43, 115):
        if c == 66:
            write_congress(d, c, CONGRESS_66)
        elif c == 77:
            write_congress(d, c, CONGRESS_77)
        else:
            pq.write_table(PARQUET_SCHEMA.empty_table(), d / f"congress_{c:03d}.parquet")
    return d


def all_text(at) -> str:
    parts = [m.value for m in at.markdown] + [m.value for m in at.warning] + [m.value for m in at.info] + [m.value for m in at.error]
    parts += [e.label for e in at.expander]
    return "\n".join(str(p) for p in parts)


def test_app_starts_searches_and_shows_a_result(full_range_corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("CONGRESS_DATA_DIR", str(full_range_corpus))
    monkeypatch.setenv("CONGRESS_SEARCH_CACHE_DIR", str(tmp_path / "cache"))
    at = AppTest.from_file(str(ROOT / "app" / "app.py"), default_timeout=90).run()
    assert not at.exception, at.exception
    assert "Suggested starting points" in all_text(at)

    box = next(t for t in at.text_input if t.label == "Search Congressional speech")
    box.set_value("treaty")
    next(b for b in at.button if b.label == "Search").click()
    at.run()
    assert not at.exception, at.exception
    text = all_text(at)
    assert "LODGE" in text.upper() or "ratified only with reservations" in text


def test_a_search_with_no_matches_says_so(full_range_corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("CONGRESS_DATA_DIR", str(full_range_corpus))
    monkeypatch.setenv("CONGRESS_SEARCH_CACHE_DIR", str(tmp_path / "cache"))
    at = AppTest.from_file(str(ROOT / "app" / "app.py"), default_timeout=90).run()
    next(t for t in at.text_input if t.label == "Search Congressional speech").set_value("zzzzqqqq")
    next(b for b in at.button if b.label == "Search").click()
    at.run()
    assert not at.exception, at.exception
    assert "No matching speeches found" in all_text(at)


def test_near_search_without_both_terms_is_refused_by_the_form(full_range_corpus, tmp_path, monkeypatch):
    monkeypatch.setenv("CONGRESS_DATA_DIR", str(full_range_corpus))
    monkeypatch.setenv("CONGRESS_SEARCH_CACHE_DIR", str(tmp_path / "cache"))
    at = AppTest.from_file(str(ROOT / "app" / "app.py"), default_timeout=90).run()
    next(s for s in at.selectbox if s.label == "Search mode").set_value("Near (proximity)")
    at.run()
    next(t for t in at.text_input if t.label == "Term A").set_value("treaty")
    next(b for b in at.button if b.label == "Search").click()
    at.run()
    assert not at.exception, at.exception
    assert "Enter both Term A and Term B" in all_text(at)
