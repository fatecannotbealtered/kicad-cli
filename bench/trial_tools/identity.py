"""Finite serial file inventories for the Windows trial methods.

These checks require no concurrent writer. They are ordinary serial observations,
not an atomic filesystem snapshot or proof of the bytes present at DLL load time.
Path-stat and handle-stat ctime values are retained and compared within their own
API only; Windows does not give the two APIs interchangeable ctime semantics.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class IdentityError(ValueError):
    """An unsupported path, concurrent change, alias mismatch or inventory drift."""

    def __init__(self, message: str, *, details: Any = None):
        super().__init__(message)
        self.details = details


def ordinary_path(value: str | Path, *, exists: bool = True) -> Path:
    """Require an absolute ordinary path, including every existing ancestor."""
    path = Path(os.path.abspath(os.fspath(value)))
    if not Path(value).is_absolute():
        raise IdentityError("An absolute path is required", details=str(value))
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if exists or part != path:
                raise IdentityError("Required path is missing", details=str(part)) from None
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise IdentityError("Symlinks and reparse points are unsupported", details=str(part))
    return path


def _identity(info: os.stat_result, *, handle: bool = False) -> dict[str, Any]:
    if not info.st_ino:
        raise IdentityError("Filesystem did not expose a usable file ID")
    return {
        "dev": str(info.st_dev),
        "file_id": str(info.st_ino),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        ("handle_ctime_ns" if handle else "path_ctime_ns"): info.st_ctime_ns,
    }


def file_identity(value: str | Path) -> dict[str, Any]:
    """Read one regular file, checking both stat APIs for within-read change."""
    path = ordinary_path(value)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise IdentityError("A regular file is required", details=str(path))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if any(
            getattr(before, key) != getattr(opened, key)
            for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        ):
            raise IdentityError("Path changed while opening", details=str(path))
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        closed = os.fstat(stream.fileno())
    after = path.lstat()
    if _identity(before) != _identity(after) or _identity(opened, handle=True) != _identity(
        closed, handle=True
    ):
        raise IdentityError("File changed during serial hashing", details=str(path))
    return {**_identity(after), "handle_ctime_ns": closed.st_ctime_ns, "sha256": digest.hexdigest()}


def _directory_identity(path: Path) -> dict[str, str]:
    info = ordinary_path(path).lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise IdentityError("A directory is required", details=str(path))
    if not info.st_ino:
        raise IdentityError("Filesystem did not expose a usable directory ID", details=str(path))
    return {"dev": str(info.st_dev), "file_id": str(info.st_ino)}


def _walk_error(error: OSError) -> None:
    raise IdentityError(
        "Directory enumeration failed; inventory is incomplete", details=error.filename
    ) from error


def tree_inventory(value: str | Path) -> dict[str, Any]:
    """Include the complete relative file/directory sets, without exclusions."""
    root = ordinary_path(value)
    directories = {".": _directory_identity(root)}
    files: dict[str, Any] = {}
    for directory, names, filenames in os.walk(root, followlinks=False, onerror=_walk_error):
        parent = Path(directory)
        names.sort()
        filenames.sort()
        for name in names:
            path = parent / name
            directories[path.relative_to(root).as_posix()] = _directory_identity(path)
        for name in filenames:
            path = parent / name
            files[path.relative_to(root).as_posix()] = file_identity(path)
    # Re-enumeration detects a changed set during hashing. It does not make the
    # observations atomic; the no-concurrent-writer precondition remains required.
    observed_files, observed_dirs = set(), {"."}
    for directory, names, filenames in os.walk(root, followlinks=False, onerror=_walk_error):
        for name in names:
            path = Path(directory) / name
            _directory_identity(path)
            observed_dirs.add(path.relative_to(root).as_posix())
        for name in filenames:
            path = Path(directory) / name
            ordinary_path(path)
            observed_files.add(path.relative_to(root).as_posix())
    if observed_files != set(files) or observed_dirs != set(directories):
        raise IdentityError(
            "Directory membership changed during serial inventory", details=str(root)
        )
    if _directory_identity(root) != directories["."]:
        raise IdentityError("Inventory root was replaced", details=str(root))
    return {"kind": "tree", "root": str(root), "directories": directories, "files": files}


def capture(value: str | Path) -> dict[str, Any]:
    path = ordinary_path(value)
    if path.is_dir():
        return tree_inventory(path)
    return {"kind": "file", "path": str(path), "identity": file_identity(path)}


def inventory_diff(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> dict[str, Any]:
    """Return explicit membership, replacement and content/metadata differences."""
    if expected.get("kind") != actual.get("kind"):
        return {"kind_changed": True}
    if expected.get("kind") == "file":
        return {} if expected == actual else {"changed": {"before": expected, "after": actual}}
    old, new = expected["files"], actual["files"]
    differences: dict[str, Any] = {}
    for key, value in (
        ("added", sorted(set(new) - set(old))),
        ("deleted", sorted(set(old) - set(new))),
        (
            "changed",
            {
                name: {"before": old[name], "after": new[name]}
                for name in sorted(set(old) & set(new))
                if old[name] != new[name]
            },
        ),
        ("directories_added", sorted(set(actual["directories"]) - set(expected["directories"]))),
        ("directories_deleted", sorted(set(expected["directories"]) - set(actual["directories"]))),
        (
            "directories_replaced",
            [
                name
                for name in sorted(set(expected["directories"]) & set(actual["directories"]))
                if expected["directories"][name] != actual["directories"][name]
            ],
        ),
    ):
        if value:
            differences[key] = value
    if expected.get("root") != actual.get("root"):
        differences["root_changed"] = True
    return differences


def verify_inventory(expected: Mapping[str, Any]) -> dict[str, Any]:
    actual = capture(expected.get("root", expected.get("path")))
    differences = inventory_diff(expected, actual)
    if differences:
        raise IdentityError("Frozen file inventory changed", details=differences)
    return actual


def verify_aliases(groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Prove current aliases share file ID, size and SHA; never infer from history."""
    result = []
    for group in groups:
        canonical = ordinary_path(group["canonical"])
        expected = file_identity(canonical)
        rows = []
        for value in group.get("aliases", ()):
            alias = ordinary_path(value)
            actual = file_identity(alias)
            keys = ("dev", "file_id", "size", "sha256")
            if any(expected[key] != actual[key] for key in keys):
                raise IdentityError(
                    "Aliases do not identify the same current file",
                    details={
                        "canonical": str(canonical),
                        "alias": str(alias),
                        "canonical_identity": expected,
                        "alias_identity": actual,
                    },
                )
            rows.append({"path": str(alias), "identity": actual})
        result.append({"canonical": str(canonical), "identity": expected, "aliases": rows})
    return result
