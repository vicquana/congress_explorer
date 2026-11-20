"""StanfordArchive: incremental reading, validation, and the 4 GiB local-header offset correction."""
from __future__ import annotations

import copy
import zipfile
from pathlib import Path

import pytest

from scripts import archive_reader as ar
from scripts.archive_reader import ArchiveReadError, StanfordArchive

TEXT = "first line\nsecond line with é and 世界\nthird, no final newline"


def make_zip(path: Path, members: dict[str, bytes], method=zipfile.ZIP_DEFLATED) -> Path:
    with zipfile.ZipFile(path, "w", compression=method) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


@pytest.fixture(params=[zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED], ids=["stored", "deflated"])
def zip_path(request, tmp_path):
    return make_zip(tmp_path / "t.zip", {"d/a.txt": TEXT.encode(), "d/b.txt": b"other\n"}, request.param)


def test_bytes_roundtrip(zip_path):
    with StanfordArchive(zip_path) as a:
        assert b"".join(a.iter_member_bytes("d/a.txt")) == TEXT.encode()


@pytest.mark.parametrize("chunk", [1, 3, 7, 1024])
def test_lines_keep_newlines_and_survive_any_chunk_size(zip_path, chunk):
    with StanfordArchive(zip_path) as a:
        lines = list(a.iter_member_lines("d/a.txt", chunk_size=chunk))
    assert "".join(lines) == TEXT
    assert lines[0] == "first line\n" and lines[1].endswith("世界\n") and lines[2] == "third, no final newline"


def test_invalid_utf8_is_replaced_not_fatal(tmp_path):
    p = make_zip(tmp_path / "t.zip", {"d/x.txt": b"ok \xff\xfe bytes\n"}, zipfile.ZIP_STORED)
    with StanfordArchive(p) as a:
        assert "ok" in next(a.iter_member_lines("d/x.txt"))


def test_crc_mismatch_is_detected(tmp_path):
    payload = b"0123456789" * 50
    p = make_zip(tmp_path / "t.zip", {"d/x.bin": payload}, zipfile.ZIP_STORED)
    raw = bytearray(p.read_bytes())
    raw[raw.index(payload) + 5] ^= 0xFF
    p.write_bytes(bytes(raw))
    with StanfordArchive(p) as a:
        with pytest.raises(ArchiveReadError, match="CRC32 mismatch"):
            b"".join(a.iter_member_bytes("d/x.bin"))
        assert len(b"".join(a.iter_member_bytes("d/x.bin", validate=False))) == len(payload)   # previews are allowed


def test_size_mismatch_is_detected(zip_path):
    with StanfordArchive(zip_path) as a:
        info = copy.copy(a.get_info("d/a.txt"))
        info.file_size += 1
        a.get_info = lambda name: info
        with pytest.raises(ArchiveReadError, match="size mismatch"):
            b"".join(a.iter_member_bytes("d/a.txt"))


def test_truncated_member_is_detected(tmp_path):
    p = make_zip(tmp_path / "t.zip", {"d/x.bin": b"abc" * 100}, zipfile.ZIP_STORED)
    with StanfordArchive(p) as a:
        info = copy.copy(a.get_info("d/x.bin"))
        info.compress_size = 10**9                              # claims far more data than the file holds
        a.get_info = lambda name: info
        with pytest.raises(ArchiveReadError, match="Truncated"):
            b"".join(a.iter_member_bytes("d/x.bin"))


def test_4gib_offset_quirk_is_corrected(zip_path):
    """Stanford's archive reports local-header offsets that are 2**32 too large; reading must still work."""
    with StanfordArchive(zip_path) as a:
        good = copy.copy(a.get_info("d/b.txt"))
        good.header_offset += ar.ZIP32_WRAP
        a.get_info = lambda name: good
        assert b"".join(a.iter_member_bytes("d/b.txt")) == b"other\n"


def test_offset_pointing_at_the_wrong_member_is_refused(zip_path):
    with StanfordArchive(zip_path) as a:
        info_a = a.get_info("d/a.txt")
        wrong = copy.copy(a.get_info("d/b.txt"))
        wrong.header_offset = info_a.header_offset              # a.txt's header, but we asked for b.txt
        a.get_info = lambda name: wrong
        with pytest.raises(ArchiveReadError, match="Could not locate"):
            b"".join(a.iter_member_bytes("d/b.txt"))


def test_compression_method_mismatch_is_refused(zip_path):
    with StanfordArchive(zip_path) as a:
        info = copy.copy(a.get_info("d/a.txt"))
        info.compress_type = 14
        a.get_info = lambda name: info
        with pytest.raises(ArchiveReadError):
            b"".join(a.iter_member_bytes("d/a.txt"))


def test_usage_errors(tmp_path, zip_path):
    with pytest.raises(FileNotFoundError):
        StanfordArchive(tmp_path / "missing.zip")
    a = StanfordArchive(zip_path)
    with pytest.raises(RuntimeError, match="not open"):
        a.get_info("d/a.txt")
    with StanfordArchive(zip_path) as a:
        with pytest.raises(KeyError, match="not found"):
            a.get_info("d/none.txt")
        with pytest.raises(ValueError):
            next(a.iter_member_bytes("d/a.txt", chunk_size=0))


def test_member_names_and_archive_choice(monkeypatch, tmp_path):
    assert ar.congress_member_names(7, "hein-bound") == {
        "speeches": "hein-bound/speeches_007.txt",
        "descr": "hein-bound/descr_007.txt",
        "speakermap": "hein-bound/007_SpeakerMap.txt",
    }
    monkeypatch.setattr(ar, "find_zip_path", lambda filename: tmp_path / filename)
    assert [ar.get_archive_for_congress(c).prefix for c in (43, 111, 112, 114)] == [
        "hein-bound", "hein-bound", "hein-daily", "hein-daily"]
    for bad in (0, 42, 115):
        with pytest.raises(ValueError, match="outside"):
            ar.get_archive_for_congress(bad)
