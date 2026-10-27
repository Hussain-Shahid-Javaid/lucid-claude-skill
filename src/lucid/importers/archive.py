"""Safe reading of user-supplied export archives (ZIP) and files.

Never extracts to disk. Rejects absolute paths, parent traversal, symlinks,
oversized members, too many members and suspicious compression ratios.
"""
from __future__ import annotations

import os
import stat
import zipfile

MAX_MEMBERS = 20_000
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_RATIO = 200  # uncompressed / compressed


class UnsafeArchive(ValueError):
    pass


def _check_name(name: str) -> None:
    n = name.replace("\\", "/")
    if n.startswith("/") or (len(n) > 1 and n[1] == ":") or any(p == ".." for p in n.split("/")):
        raise UnsafeArchive(f"unsafe path in archive: {name!r}")


def open_zip(path: str) -> zipfile.ZipFile:
    zf = zipfile.ZipFile(path)
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise UnsafeArchive(f"archive has {len(infos)} members (limit {MAX_MEMBERS})")
    total = 0
    for i in infos:
        _check_name(i.filename)
        mode = (i.external_attr >> 16) & 0o170000
        if mode and stat.S_ISLNK(mode):
            raise UnsafeArchive(f"symlink in archive: {i.filename!r}")
        if i.file_size > MAX_MEMBER_BYTES:
            raise UnsafeArchive(f"member too large: {i.filename!r}")
        if i.compress_size and i.file_size / max(1, i.compress_size) > MAX_RATIO and i.file_size > 10 * 1024 * 1024:
            raise UnsafeArchive(f"suspicious compression ratio: {i.filename!r}")
        total += i.file_size
    if total > MAX_TOTAL_BYTES:
        raise UnsafeArchive("archive uncompressed size exceeds limit")
    return zf


def read_member(zf: zipfile.ZipFile, wanted_basename: str) -> bytes | None:
    """Read the first member whose basename matches (case-insensitive), with a hard size cap."""
    for i in zf.infolist():
        if os.path.basename(i.filename).lower() == wanted_basename.lower():
            with zf.open(i) as fh:
                data = fh.read(MAX_MEMBER_BYTES + 1)
            if len(data) > MAX_MEMBER_BYTES:
                raise UnsafeArchive("member exceeded size cap while reading")
            return data
    return None


def read_input(path: str, member: str) -> bytes:
    """Read `member` from a .zip, or the file itself if `path` is not a zip."""
    if os.path.getsize(path) > MAX_TOTAL_BYTES:
        raise UnsafeArchive("input file too large")
    if zipfile.is_zipfile(path):
        with open_zip(path) as zf:
            data = read_member(zf, member)
        if data is None:
            raise FileNotFoundError(f"{member} not found in archive")
        return data
    with open(path, "rb") as fh:
        return fh.read()
