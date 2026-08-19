"""
Low-memory reader for the Stanford Congressional Record ZIP archives.

The Stanford ``hein-bound.zip`` archive has a known 4 GiB local-header offset
quirk that can cause Python's normal ``ZipFile.open()`` (and some unzip tools)
to fail with errors such as ``Truncated file header`` or ``bad zipfile offset``.

This module uses ``zipfile.ZipFile`` only for central-directory metadata, then
reads each member from the archive's raw bytes. Compressed data is decompressed
incrementally, so callers do not need to load an entire ZIP member into memory.

Use this module as the single archive-reading implementation for both
``inspect_zip.py`` and ``process_corpus.py``.
"""

from __future__ import annotations

import logging
import struct
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

ZIP_LOCAL_FILE_HEADER = b"PK\x03\x04"
ZIP32_WRAP = 1 << 32  # 4 GiB
DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB

# ZIP compression methods used by the Stanford archives.
ZIP_STORED = 0
ZIP_DEFLATED = 8


class ArchiveReadError(RuntimeError):
    """Raised when a Stanford archive member cannot be read or validated."""


@dataclass(frozen=True)
class ArchiveSpec:
    """Location and internal directory prefix for one Stanford archive."""

    path: Path
    prefix: str


def find_zip_path(filename: str) -> Path:
    """Locate a Stanford ZIP archive in the project's supported data paths."""
    project_root = Path(__file__).resolve().parent.parent
    candidates = (
        project_root / "data" / filename,
        project_root / "data" / "raw" / filename,
        project_root / "raw" / filename,
    )

    for path in candidates:
        if path.is_file():
            return path

    searched = "\n  - ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"Could not find {filename}. Searched:\n  - {searched}")


def get_archive_for_congress(congress_num: int) -> ArchiveSpec:
    """
    Return the canonical Stanford archive for a Congress number.

    Canonical full-corpus mapping:
      * Congresses 043-111 -> hein-bound.zip
      * Congresses 112-114 -> hein-daily.zip

    Phase 1 currently uses only Congresses 077-096, all from hein-bound.zip.
    """
    if 43 <= congress_num <= 111:
        return ArchiveSpec(find_zip_path("hein-bound.zip"), "hein-bound")
    if 112 <= congress_num <= 114:
        return ArchiveSpec(find_zip_path("hein-daily.zip"), "hein-daily")

    raise ValueError(
        f"Congress {congress_num} is outside the Stanford canonical range 43-114."
    )


def congress_member_names(congress_num: int, prefix: str) -> dict[str, str]:
    """Return the three core Stanford member names for one Congress."""
    c_str = f"{congress_num:03d}"
    return {
        "speeches": f"{prefix}/speeches_{c_str}.txt",
        "descr": f"{prefix}/descr_{c_str}.txt",
        "speakermap": f"{prefix}/{c_str}_SpeakerMap.txt",
    }


class StanfordArchive:
    """
    Incremental reader for a Stanford Congressional Record ZIP archive.

    ``zipfile.ZipFile`` is used only to read the central directory. Member data
    is read from a separate raw file handle so the Stanford 4 GiB offset quirk
    can be corrected without relying on ``ZipFile.open()``.

    The class is safe for sequential member reads. Each member iterator opens
    its own raw file descriptor, so independent iterators do not share a seek
    position.
    """

    def __init__(self, zip_path: Path | str):
        self.zip_path = Path(zip_path)
        if not self.zip_path.is_file():
            raise FileNotFoundError(self.zip_path)
        self._zip: zipfile.ZipFile | None = None

    def __enter__(self) -> "StanfordArchive":
        self._zip = zipfile.ZipFile(self.zip_path, "r")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    @property
    def zip_file(self) -> zipfile.ZipFile:
        if self._zip is None:
            raise RuntimeError(
                "StanfordArchive is not open. Use it as a context manager: "
                "with StanfordArchive(path) as archive: ..."
            )
        return self._zip

    def get_info(self, member_name: str) -> zipfile.ZipInfo:
        """Return central-directory metadata for a member."""
        try:
            return self.zip_file.getinfo(member_name)
        except KeyError as exc:
            raise KeyError(
                f"{member_name!r} not found in {self.zip_path.name}"
            ) from exc

    def iter_member_bytes(
        self,
        member_name: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        validate: bool = True,
    ) -> Iterator[bytes]:
        """
        Yield one ZIP member as decompressed byte chunks.

        Memory use is bounded by ``chunk_size`` plus zlib's internal buffers.

        When the iterator is consumed to completion and ``validate=True``, this
        verifies:
          * compressed data was not truncated,
          * Deflate reached its end-of-stream marker,
          * uncompressed byte count matches the central directory,
          * CRC32 matches the central directory.

        If a caller stops iteration early (for example, when previewing five
        lines), full-member integrity validation cannot occur. Use
        ``validate=False`` for intentional previews.
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")

        zinfo = self.get_info(member_name)

        with self.zip_path.open("rb") as raw_fp:
            local_offset, compression_method, data_offset = self._locate_member_data(
                raw_fp, zinfo
            )

            if local_offset != zinfo.header_offset:
                delta = zinfo.header_offset - local_offset
                logger.info(
                    "Corrected ZIP local-header offset for %s by -%s bytes",
                    member_name,
                    f"{delta:,}",
                )

            raw_fp.seek(data_offset)
            remaining = zinfo.compress_size
            crc = 0
            uncompressed_size = 0

            if compression_method == ZIP_STORED:
                while remaining:
                    read_size = min(chunk_size, remaining)
                    chunk = raw_fp.read(read_size)
                    if not chunk:
                        raise ArchiveReadError(
                            f"Truncated stored member {member_name!r}: "
                            f"{remaining:,} compressed bytes were still expected"
                        )

                    remaining -= len(chunk)
                    crc = zlib.crc32(chunk, crc)
                    uncompressed_size += len(chunk)
                    yield chunk

            elif compression_method == ZIP_DEFLATED:
                decompressor = zlib.decompressobj(-zlib.MAX_WBITS)

                while remaining:
                    read_size = min(chunk_size, remaining)
                    compressed = raw_fp.read(read_size)
                    if not compressed:
                        raise ArchiveReadError(
                            f"Truncated deflated member {member_name!r}: "
                            f"{remaining:,} compressed bytes were still expected"
                        )

                    remaining -= len(compressed)

                    try:
                        output = decompressor.decompress(compressed)
                    except zlib.error as exc:
                        raise ArchiveReadError(
                            f"Deflate error while reading {member_name!r}: {exc}"
                        ) from exc

                    if output:
                        crc = zlib.crc32(output, crc)
                        uncompressed_size += len(output)
                        yield output

                try:
                    tail = decompressor.flush()
                except zlib.error as exc:
                    raise ArchiveReadError(
                        f"Deflate flush error for {member_name!r}: {exc}"
                    ) from exc

                if tail:
                    crc = zlib.crc32(tail, crc)
                    uncompressed_size += len(tail)
                    yield tail

                if validate and not decompressor.eof:
                    raise ArchiveReadError(
                        f"Deflate stream for {member_name!r} did not reach EOF"
                    )

                if validate and decompressor.unused_data:
                    raise ArchiveReadError(
                        f"Unexpected trailing compressed data inside {member_name!r}"
                    )

            else:
                raise ArchiveReadError(
                    f"Unsupported ZIP compression method {compression_method} "
                    f"for {member_name!r}"
                )

            if validate:
                self._validate_member(
                    zinfo,
                    actual_crc=crc,
                    actual_size=uncompressed_size,
                )

    def iter_member_lines(
        self,
        member_name: str,
        *,
        encoding: str = "utf-8",
        errors: str = "replace",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        validate: bool = True,
    ) -> Iterator[str]:
        """
        Yield decoded text lines from a ZIP member incrementally.

        Newline characters are preserved, matching normal file iteration.
        The internal buffer grows only to the size of the longest individual
        line, not to the size of the full member.
        """
        buffer = b""

        for chunk in self.iter_member_bytes(
            member_name,
            chunk_size=chunk_size,
            validate=validate,
        ):
            # Split once per decompressed chunk instead of repeatedly deleting
            # from the front of a bytearray. This keeps line iteration efficient
            # even for very large Stanford members.
            parts = (buffer + chunk).split(b"\n")
            buffer = parts.pop()

            for part in parts:
                yield (part + b"\n").decode(encoding, errors=errors)

        if buffer:
            yield buffer.decode(encoding, errors=errors)

    def _locate_member_data(
        self,
        raw_fp,
        zinfo: zipfile.ZipInfo,
    ) -> tuple[int, int, int]:
        """
        Resolve the physical local-header offset and return:

            (local_header_offset, compression_method, data_offset)

        Stanford's large archive can report a central-directory local-header
        offset shifted upward by exactly 2**32 bytes. We first try the reported
        offset, then subtract one or more 4 GiB wraps until a matching local
        header is found.
        """
        reported = zinfo.header_offset
        candidate = reported

        while candidate >= 0:
            raw_fp.seek(candidate)
            signature = raw_fp.read(4)

            if signature == ZIP_LOCAL_FILE_HEADER:
                try:
                    return self._parse_local_header(raw_fp, candidate, zinfo)
                except ArchiveReadError:
                    # A coincidental PK signature is extremely unlikely, but if
                    # the local filename does not match, try the next 4 GiB wrap.
                    pass

            if candidate < ZIP32_WRAP:
                break
            candidate -= ZIP32_WRAP

        raise ArchiveReadError(
            f"Could not locate a valid local header for {zinfo.filename!r}. "
            f"Central-directory offset was {reported:,}."
        )

    def _parse_local_header(
        self,
        raw_fp,
        offset: int,
        zinfo: zipfile.ZipInfo,
    ) -> tuple[int, int, int]:
        """Parse and validate one local file header."""
        raw_fp.seek(offset)
        header = raw_fp.read(30)

        if len(header) != 30:
            raise ArchiveReadError(
                f"Truncated local header for {zinfo.filename!r} at offset {offset:,}"
            )

        (
            signature,
            _version_needed,
            flags,
            compression_method,
            _mod_time,
            _mod_date,
            _crc32_local,
            _compressed_size_local,
            _uncompressed_size_local,
            filename_length,
            extra_length,
        ) = struct.unpack("<4sHHHHHIIIHH", header)

        if signature != ZIP_LOCAL_FILE_HEADER:
            raise ArchiveReadError(
                f"Invalid local header signature for {zinfo.filename!r} "
                f"at offset {offset:,}"
            )

        local_name_bytes = raw_fp.read(filename_length)
        if len(local_name_bytes) != filename_length:
            raise ArchiveReadError(f"Truncated local filename for {zinfo.filename!r}")

        filename_encoding = "utf-8" if (flags & 0x800) else "cp437"
        local_name = local_name_bytes.decode(filename_encoding, errors="replace")

        if local_name != zinfo.filename:
            raise ArchiveReadError(
                f"Local header at offset {offset:,} belongs to {local_name!r}, "
                f"not {zinfo.filename!r}"
            )

        if compression_method != zinfo.compress_type:
            raise ArchiveReadError(
                f"Compression method mismatch for {zinfo.filename!r}: "
                f"local header={compression_method}, "
                f"central directory={zinfo.compress_type}"
            )

        data_offset = offset + 30 + filename_length + extra_length
        return offset, compression_method, data_offset

    def _validate_member(
        self,
        zinfo: zipfile.ZipInfo,
        *,
        actual_crc: int,
        actual_size: int,
    ) -> None:
        """Validate decompressed size and CRC32 against central-directory data."""
        actual_crc &= 0xFFFFFFFF
        expected_crc = zinfo.CRC & 0xFFFFFFFF

        if actual_size != zinfo.file_size:
            raise ArchiveReadError(
                f"Uncompressed size mismatch for {zinfo.filename!r}: "
                f"expected {zinfo.file_size:,}, got {actual_size:,}"
            )

        if actual_crc != expected_crc:
            raise ArchiveReadError(
                f"CRC32 mismatch for {zinfo.filename!r}: "
                f"expected 0x{expected_crc:08x}, got 0x{actual_crc:08x}"
            )
