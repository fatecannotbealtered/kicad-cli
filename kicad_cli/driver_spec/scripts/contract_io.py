"""Bounded, noninteractive JSON input shared by the offline checking tools."""

from __future__ import annotations

import json
import math
import os
import stat
import unicodedata
from pathlib import Path, PurePosixPath


class InputError(ValueError):
    pass


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise InputError("duplicate JSON member")
        value[key] = item
    return value


def invalid_constant(value):
    raise InputError("non-JSON numeric constant")


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise InputError("number exceeds the supported finite numeric range")
    return number


def parse_json(payload: bytes):
    if payload.startswith(b"\xef\xbb\xbf"):
        raise InputError("UTF-8 BOM is not permitted")
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant, parse_float=finite_float)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputError("input must contain one complete, supported UTF-8 JSON document") from exc


def read_regular_file(path: Path, limit: int) -> bytes:
    if not stat.S_ISREG(path.stat().st_mode):
        raise InputError("file input must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise InputError("input changed to a nonregular file while opening it")
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise InputError(f"input exceeds the declared {limit}-byte limit")
    return payload


def canonical_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise InputError("package and managed paths must be nonempty relative POSIX paths")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise InputError("control characters are not permitted in paths")
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or value != path.as_posix() or any(part in {".", ".."} for part in path.parts):
        raise InputError("unsafe or noncanonical relative path")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)), *(f"LPT{number}" for number in range(1, 10))}
    if any(part.endswith((".", " ")) or part.split(".", 1)[0].upper() in reserved for part in path.parts):
        raise InputError("relative path contains a platform alias or reserved name")
    return value


def path_identity(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()
