"""process_corpus: the speeches LEFT JOIN descr LEFT JOIN SpeakerMap logic, atomic output and failure reporting."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from scripts import process_corpus as pc
from scripts.archive_reader import ArchiveSpec

SPEECHES = "speech_id|speech\n{}"
DESCR_HEADER = "speech_id|chamber|date|number_within_file|speaker|first_name|last_name|state|gender|line_start|line_end|file|char_count|word_count\n"
SMAP_HEADER = "speakerid|speech_id|lastname|firstname|chamber|state|gender|party|district|nonvoting\n"


def stanford_zip(path: Path, speeches: list[str], descr: list[str], smap: list[str], congress=77) -> ArchiveSpec:
    c = f"{congress:03d}"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"hein-bound/speeches_{c}.txt", "speech_id|speech\n" + "".join(l + "\n" for l in speeches))
        z.writestr(f"hein-bound/descr_{c}.txt", DESCR_HEADER + "".join(l + "\n" for l in descr))
        z.writestr(f"hein-bound/{c}_SpeakerMap.txt", SMAP_HEADER + "".join(l + "\n" for l in smap))
    return ArchiveSpec(path, "hein-bound")


def descr_line(sid, chamber="H", date="19410310", speaker="Mr. SMITH", first="JOHN", last="SMITH", state="TX",
               gender="M", chars="40", words="7"):
    return "|".join([sid, chamber, date, "1", speaker, first, last, state, gender, "1", "2", "f.txt", chars, words])


def smap_line(sid, last="SMITH", first="JOHN", chamber="H", state="TX", gender="M", party="D", district="3", nonvoting="voting", speakerid="77001"):
    return "|".join([speakerid, sid, last, first, chamber, state, gender, party, district, nonvoting])


@pytest.fixture
def spec(tmp_path, monkeypatch):
    s = stanford_zip(
        tmp_path / "hein-bound.zip",
        speeches=["770001|Full metadata speech.", "770002|Only descr metadata here.", "770003|No metadata at all, but kept.",
                  "770004|Unknown chamber in the speaker map."],
        descr=[descr_line("770001"), descr_line("770002", chamber="S", speaker="Mr. DOE", first="JANE", last="DOE", state="OH", gender="F"),
               descr_line("770004", chamber="S", date="19420101")],
        smap=[smap_line("770001"), smap_line("770004", chamber="Unknown", last="Unknown", first="None", state="Unknown")],
    )
    monkeypatch.setattr(pc, "get_archive_for_congress", lambda congress: s)
    return s


# ---- _build_record (pure) ---------------------------------------------------------------------------------------

def test_record_with_full_metadata():
    descr = ("H", "19410310", "Mr. SMITH", "JOHN", "SMITH", "TX", "M", "40", "7")
    smap = ("77001", "SMITH", "JOHN", "H", "TX", "M", "D", "3", "voting")
    r = pc._build_record(congress_num=77, source="hein-bound", speech_id_raw="770001", text="hello world", descr=descr, speaker_meta=smap)
    assert (r["speech_id"], r["congress"], r["date"], r["year"], r["chamber"]) == (770001, 77, "19410310", 1941, "H")
    assert (r["speaker"], r["party"], r["district"], r["nonvoting"], r["speaker_id"]) == ("Mr. SMITH", "D", "3", "voting", 77001)
    assert (r["char_count"], r["word_count"]) == (40, 7)           # taken from descr, not recomputed


def test_record_with_no_metadata_is_kept_with_blanks_and_counts_from_the_text():
    r = pc._build_record(congress_num=77, source="s", speech_id_raw="5", text="three small words", descr=None, speaker_meta=None)
    assert r["speech_id"] == 5 and r["date"] == "" and r["year"] is None and r["chamber"] == ""
    assert r["first_name"] == r["last_name"] == r["state"] == r["party"] == ""
    assert (r["char_count"], r["word_count"]) == (17, 3)


def test_speaker_map_wins_over_descr_and_descr_fills_gaps():
    descr = ("S", "19410310", "Mr. SMITH", "J", "S", "TX", "M", "x", "y")
    smap = ("1", "SMITH", "", "H", "", "", "R", "", "")
    r = pc._build_record(congress_num=77, source="s", speech_id_raw="1", text="t", descr=descr, speaker_meta=smap)
    assert r["chamber"] == "H"                                      # speaker map first
    assert r["first_name"] == "J" and r["state"] == "TX" and r["gender"] == "M"   # gaps filled from descr
    assert (r["char_count"], r["word_count"]) == (1, 1)             # non-numeric counts fall back to the text


def test_placeholders_become_blank_and_bad_ids_become_null():
    smap = ("Unknown", "Unknown", "None", "H", "Unknown", "", "None", "", "")
    r = pc._build_record(congress_num=1, source="s", speech_id_raw="12x", text="t", descr=None, speaker_meta=smap)
    assert r["speech_id"] is None and r["speaker_id"] is None
    assert r["first_name"] == r["last_name"] == r["state"] == r["party"] == ""


@pytest.mark.xfail(strict=True, reason="BUG: a placeholder chamber ('Unknown'/'None') in the speaker map blanks the chamber "
                                       "and the descr chamber is never consulted (the elif is skipped). Remove this marker when fixed.")
def test_descr_chamber_is_used_when_the_speaker_map_chamber_is_a_placeholder():
    descr = ("S", "19410310", "x", "", "", "", "", "1", "1")
    smap = ("1", "X", "Y", "Unknown", "", "", "", "", "")
    r = pc._build_record(congress_num=77, source="s", speech_id_raw="1", text="t", descr=descr, speaker_meta=smap)
    assert r["chamber"] == "S"


# ---- process_single_congress ------------------------------------------------------------------------------------

def test_every_speech_is_kept_and_the_schema_is_exact(spec, tmp_path):
    out = pc.process_single_congress(77, tmp_path / "out", batch_size=2)
    assert out.name == "congress_077.parquet"
    f = pq.ParquetFile(out)
    assert f.schema_arrow == pc.PARQUET_SCHEMA and f.metadata.num_rows == 4
    rows = {r["speech_id"]: r for r in pq.read_table(out).to_pylist()}
    assert set(rows) == {770001, 770002, 770003, 770004}
    assert rows[770001]["party"] == "D" and rows[770001]["state"] == "TX"
    assert rows[770002]["last_name"] == "DOE" and rows[770002]["party"] == "" and rows[770002]["chamber"] == "S"
    assert rows[770003]["date"] == "" and rows[770003]["year"] is None and rows[770003]["word_count"] == 6
    assert rows[770004]["year"] == 1942 and rows[770004]["source"] == "hein-bound"
    assert not list((tmp_path / "out").glob("*.tmp"))


def test_existing_output_is_skipped_unless_forced(spec, tmp_path):
    out = pc.process_single_congress(77, tmp_path / "out")
    stamp = out.stat().st_mtime_ns
    assert pc.process_single_congress(77, tmp_path / "out") == out and out.stat().st_mtime_ns == stamp
    pc.process_single_congress(77, tmp_path / "out", force=True)
    assert out.stat().st_mtime_ns != stamp


def test_stale_temp_file_is_removed(spec, tmp_path):
    (tmp_path / "out").mkdir()
    stale = tmp_path / "out" / "congress_077.parquet.tmp"
    stale.write_bytes(b"junk")
    pc.process_single_congress(77, tmp_path / "out")
    assert not stale.exists()


def test_a_malformed_speech_line_fails_loudly_and_publishes_nothing(tmp_path, monkeypatch):
    s = stanford_zip(tmp_path / "bad.zip", ["770001|fine", "this line has no separator"], [descr_line("770001")], [smap_line("770001")])
    monkeypatch.setattr(pc, "get_archive_for_congress", lambda congress: s)
    with pytest.raises(RuntimeError, match="Malformed speech line"):
        pc.process_single_congress(77, tmp_path / "out")
    assert not list((tmp_path / "out").glob("congress_077*"))


def test_zero_speeches_is_an_error(tmp_path, monkeypatch):
    s = stanford_zip(tmp_path / "empty.zip", [], [], [])
    monkeypatch.setattr(pc, "get_archive_for_congress", lambda congress: s)
    with pytest.raises(RuntimeError, match="zero speech"):
        pc.process_single_congress(77, tmp_path / "out")
    assert not list((tmp_path / "out").glob("congress_077*"))


def test_malformed_metadata_lines_are_skipped_not_fatal(tmp_path, monkeypatch):
    s = stanford_zip(tmp_path / "m.zip", ["770001|speech"], ["too|few|fields", descr_line("770001")], ["also|short", smap_line("770001")])
    monkeypatch.setattr(pc, "get_archive_for_congress", lambda congress: s)
    out = pc.process_single_congress(77, tmp_path / "out")
    (row,) = pq.read_table(out).to_pylist()
    assert row["party"] == "D"


def test_argument_checks(spec, tmp_path):
    with pytest.raises(ValueError):
        pc.process_single_congress(77, tmp_path / "o", batch_size=0)
    with pytest.raises(ValueError):
        pc.process_range(80, 77, tmp_path / "o")


def test_validation_rejects_a_wrong_row_count(spec, tmp_path):
    out = pc.process_single_congress(77, tmp_path / "out")
    with pytest.raises(RuntimeError, match="row-count mismatch"):
        pc._validate_parquet_file(out, expected_rows=999)


# ---- process_range ----------------------------------------------------------------------------------------------

def test_range_continues_past_a_failing_congress(monkeypatch, tmp_path):
    seen = []

    def fake(congress, out_dir, **kw):
        seen.append(congress)
        if congress in (2, 4):
            raise RuntimeError("boom")

    monkeypatch.setattr(pc, "process_single_congress", fake)
    assert pc.process_range(1, 5, tmp_path) == [2, 4] and seen == [1, 2, 3, 4, 5]


def test_main_exit_status_reflects_failures(monkeypatch):
    monkeypatch.setattr(pc, "process_range", lambda *a, **k: [84])
    monkeypatch.setattr("sys.argv", ["process_corpus.py", "--start", "83", "--end", "85"])
    assert pc.main() == 1
    monkeypatch.setattr(pc, "process_range", lambda *a, **k: [])
    assert pc.main() == 0
