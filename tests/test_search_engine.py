"""SearchEngine: modes, filters, ordering, pagination, cache, CSV export, snippets."""
from __future__ import annotations

import io
import os

import pandas as pd
import pytest

from app.search_engine import MAX_NEAR_GAP, MAX_REGEX_PATTERN_LENGTH, SearchEngine, SearchFilter
from conftest import CONGRESS_66, ids, write_congress


def run(engine, **kw):
    df, total, terms = engine.search(SearchFilter(**kw))
    return ids(df), total, terms


# ---- corpus discovery -------------------------------------------------------------------------------------------

def test_only_canonical_congress_files_are_used(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    for name in ["congress_042.parquet", "congress_043.parquet", "congress_114.parquet", "congress_115.parquet",
                 "congress_66.parquet", "notes.parquet", "congress_066.parquet.tmp"]:
        (d / name).write_bytes(b"")
    names = [p.name for p in SearchEngine(processed_dir=d, cache_dir=tmp_path / "c").get_parquet_files()]
    assert names == ["congress_043.parquet", "congress_114.parquet"]


def test_missing_corpus_returns_empty_results_not_an_error(tmp_path):
    e = SearchEngine(processed_dir=tmp_path / "nowhere", cache_dir=tmp_path / "c")
    df, total, terms = e.search(SearchFilter(query="treaty"))
    assert df.empty and total == 0 and terms == []
    assert e.get_corpus_stats()["total_speeches"] == 0
    csv, exported, total = e.export_csv(SearchFilter(query="treaty"))
    assert exported == 0 and total == 0 and csv.decode().startswith("speech_id,")


def test_corpus_stats_and_filter_options(engine):
    s = engine.get_corpus_stats()
    assert (s["total_congresses"], s["congress_min"], s["congress_max"]) == (2, 66, 77)
    assert s["total_speeches"] == 7 and (s["year_min"], s["year_max"]) == (1919, 1942)
    o = engine.get_filter_options()
    assert o["congresses"] == [66, 77] and o["chambers"] == ["H", "S"]
    assert set(o["parties"]) == {"D", "I", "R"} and "TX" in o["states"]


# ---- search modes -----------------------------------------------------------------------------------------------

def test_all_words_is_and_and_case_insensitive(engine):
    assert run(engine, query="TREATY germany")[0] == [660001]
    assert run(engine, query="treaty railroads")[0] == []


def test_any_word_is_or(engine):
    got, total, _ = run(engine, query="treaty railroads", search_mode="any_word")
    assert got == [660001, 660003] and total == 2


def test_exact_phrase_needs_the_words_in_order(engine):
    assert run(engine, query="league of nations", search_mode="exact_phrase")[0] == [660002]
    assert run(engine, query="nations of league", search_mode="exact_phrase")[0] == []


def test_matching_is_a_substring_match_as_the_readme_says(engine):
    # "sect" also matches "sectarian" and "section" (documented behaviour)
    assert run(engine, query="sect")[0] == [770002, 770003]


def test_regex_mode_and_its_guards(engine):
    assert run(engine, query=r"^Mr\. President", search_mode="regex")[0] == [660001]
    with pytest.raises(ValueError, match="Invalid regular expression"):
        run(engine, query="(unclosed", search_mode="regex")
    with pytest.raises(ValueError, match="too long"):
        run(engine, query="a" * (MAX_REGEX_PATTERN_LENGTH + 1), search_mode="regex")


def test_near_mode(engine):
    # "treaty of peace with Germany": three words sit between the two terms
    base = dict(search_mode="near", near_term_a="treaty", near_term_b="Germany")
    assert run(engine, **base, near_max_gap=3)[0] == [660001]
    assert run(engine, **base, near_max_gap=2)[0] == []
    # reversed order only matches when any_order is on
    rev = dict(search_mode="near", near_term_a="Germany", near_term_b="treaty")
    assert run(engine, **rev, near_max_gap=3, near_any_order=True)[0] == [660001]
    assert run(engine, **rev, near_max_gap=3, near_any_order=False)[0] == []


def test_near_gap_is_bounded(engine):
    with pytest.raises(ValueError):
        run(engine, search_mode="near", near_term_a="a", near_term_b="b", near_max_gap=MAX_NEAR_GAP + 1)
    with pytest.raises(ValueError):
        SearchEngine._build_near_pattern("a", "b", -1, True)


def test_near_terms_are_escaped_not_run_as_regex(engine):
    assert run(engine, search_mode="near", near_term_a=".*", near_term_b="treaty", near_max_gap=3)[0] == []


# ---- filters ----------------------------------------------------------------------------------------------------

def test_year_range(engine):
    assert run(engine, year_min=1941, year_max=1941)[0] == [770001, 770002]
    assert run(engine, year_min=1942)[0] == [770003]


def test_congress_list_ignores_out_of_range_numbers(engine):
    assert run(engine, congress_list=[77])[1] == 3
    assert run(engine, congress_list=[10, 200])[1] == 0         # nothing valid left: no results, not all results


def test_chamber_party_state_filters(engine):
    assert run(engine, chambers=["s"])[1] == 4                  # case-insensitive
    assert run(engine, parties=["D"])[0] == [660002, 770001, 770002]
    assert run(engine, parties=["Other"])[0] == [660004, 770003]  # anything that is not R or D, including blank
    assert run(engine, states=["tx", "ne"])[0] == [660002, 770001]


def test_speaker_filter_matches_label_surname_and_full_name(engine):
    assert run(engine, speaker_query="lodge")[0] == [660001]
    assert run(engine, speaker_query="gilbert hitchcock")[0] == [660002]
    assert run(engine, speaker_query="vice president")[0] == [660004]


def test_a_speech_without_metadata_is_still_found(engine):
    assert run(engine, query="almighty god")[0] == [660004]


def test_filters_combine_with_and(engine):
    assert run(engine, query="the", chambers=["S"], year_max=1919)[0] == [660001, 660002]


# ---- ordering and pagination ------------------------------------------------------------------------------------

def test_sort_orders(engine):
    assert run(engine, query="the", sort_by="date_asc")[0][0] == 660001
    assert run(engine, query="the", sort_by="date_desc")[0][0] == 770003
    wc = run(engine, query="the", sort_by="word_count_desc")[0]
    lengths = [len(next(r for r in CONGRESS_66 if r["speech_id"] == i)["speech_text"].split()) for i in wc if i < 700000]
    assert lengths == sorted(lengths, reverse=True)


def test_pages_partition_the_result_set(engine):
    everything, total, _ = run(engine, query="the", limit=100)
    pages = []
    for off in range(0, total, 2):
        pages += run(engine, query="the", limit=2, offset=off)[0]
    assert pages == everything and total == len(everything)


def test_offset_past_the_end_is_empty_but_reports_the_total(engine):
    got, total, _ = run(engine, query="the", limit=5, offset=50)
    assert got == [] and total > 0


def test_bad_paging_arguments(engine):
    with pytest.raises(ValueError):
        run(engine, query="the", limit=0)
    with pytest.raises(ValueError):
        run(engine, query="the", offset=-1)


# ---- cache ------------------------------------------------------------------------------------------------------

def test_second_search_and_other_pages_reuse_one_cache_file(engine, tmp_path):
    sf = SearchFilter(query="the", limit=2)
    _, _, _, hit1 = engine._materialize_search(sf)
    _, _, _, hit2 = engine._materialize_search(SearchFilter(query="the", limit=2, offset=2))
    assert (hit1, hit2) == (False, True)
    assert len(list((tmp_path / "cache").glob("search_*.parquet"))) == 1


def test_changing_any_filter_changes_the_cache_key(engine):
    a = engine._search_cache_key(SearchFilter(query="the"))
    assert a == engine._search_cache_key(SearchFilter(query="the", limit=99, offset=7))   # paging does not matter
    for other in (SearchFilter(query="the", year_min=1900), SearchFilter(query="the", chambers=["S"]),
                  SearchFilter(query="the", sort_by="date_desc"), SearchFilter(query="thee")):
        assert engine._search_cache_key(other) != a


def test_replacing_a_corpus_file_invalidates_the_cache(engine, corpus_dir):
    before = engine.corpus_signature()
    rows = CONGRESS_66[:1]
    write_congress(corpus_dir, 66, rows)
    os.utime(corpus_dir / "congress_066.parquet", None)
    assert engine.corpus_signature() != before


def test_cache_is_pruned_to_the_file_limit(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("CONGRESS_SEARCH_CACHE_MAX_FILES", "2")
    for q in ["the", "treaty", "senate", "house"]:
        engine.search(SearchFilter(query=q))
    assert len(list((tmp_path / "cache").glob("search_*.parquet"))) <= 2


def test_failed_search_leaves_no_temp_file(engine, tmp_path):
    with pytest.raises(ValueError):
        engine.search(SearchFilter(query="(", search_mode="regex"))
    assert not list((tmp_path / "cache").glob("*.tmp"))


# ---- CSV export -------------------------------------------------------------------------------------------------

def test_csv_export_has_full_text_and_all_columns(engine):
    data, exported, total = engine.export_csv(SearchFilter(query="treaty"))
    df = pd.read_csv(io.BytesIO(data))
    assert (exported, total, len(df)) == (1, 1, 1)
    assert "must be ratified only with reservations" in df.loc[0, "speech_text"]
    assert list(df.columns)[:3] == ["speech_id", "congress", "date"]


def test_csv_export_is_capped_and_says_so(engine):
    data, exported, total = engine.export_csv(SearchFilter(query="the"), max_rows=2)
    assert exported == 2 and total > 2
    assert len(pd.read_csv(io.BytesIO(data))) == 2


def test_csv_export_with_no_matches_is_header_only(engine):
    data, exported, total = engine.export_csv(SearchFilter(query="zzzzzz"))
    assert (exported, total) == (0, 0) and data.decode().startswith("speech_id,")


def test_csv_export_rejects_nonpositive_cap(engine):
    with pytest.raises(ValueError):
        engine.export_csv(SearchFilter(query="the"), max_rows=0)


# ---- snippets ---------------------------------------------------------------------------------------------------

def test_snippet_highlights_and_keeps_the_original_text(engine):
    out = SearchEngine.extract_snippet("The treaty was ratified.", ["TREATY"])
    assert out == "The <mark class='kw-match'>treaty</mark> was ratified."


def test_snippet_escapes_html_in_historical_text():
    out = SearchEngine.extract_snippet("<script>alert(1)</script> bold & treaty", ["treaty"])
    assert "<script>" not in out and "&lt;script&gt;" in out and "&amp;" in out


def test_snippet_without_terms_or_matches_is_an_escaped_preview():
    long = "<x> " + "word " * 200
    for terms in ([], ["absent"]):
        out = SearchEngine.extract_snippet(long, terms)
        assert out.startswith("&lt;x&gt;") and out.endswith("...") and len(out) < 420


def test_snippet_ellipses_and_regions():
    text = "alpha " * 100 + "treaty" + " beta" * 100 + " treaty " + "gamma " * 100
    out = SearchEngine.extract_snippet(text, ["treaty"], window_chars=20, max_snippets=2)
    assert out.count("<mark") == 2 and out.startswith("...") and "&bull;" in out


def test_snippet_argument_checks():
    with pytest.raises(ValueError):
        SearchEngine.extract_snippet("x", ["x"], window_chars=-1)
    with pytest.raises(ValueError):
        SearchEngine.extract_snippet("x", ["x"], max_snippets=0)
    assert SearchEngine.extract_snippet("", ["x"]) == ""


@pytest.mark.xfail(strict=True, reason="The engine returns every speech when a near search has a blank term (the Streamlit form guards "
                                       "against it, direct callers and the CLI do not). Remove this marker when it raises or returns nothing.")
def test_near_search_with_a_blank_term_should_not_match_the_whole_corpus(engine):
    assert run(engine, search_mode="near", near_term_a="treaty", near_term_b="")[1] < 7
