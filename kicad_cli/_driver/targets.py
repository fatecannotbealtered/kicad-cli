"""Target keys: building, normalizing and prefix matching (DCLI-028).

A key is ``file:///<absolute-path>[#<domain-path>]``, ``untitled:<session-id>/<n>[#<domain-path>]``
or ``device:<device-id>[/<part>]``. Paths are compared absolute, with one separator style,
and case-insensitively on Windows. A prefix matches only at a boundary, and target locks use
the same rule (DCLI-060).
"""
from __future__ import annotations

import os
import posixpath
import sys

WINDOWS = sys.platform == "win32"


def file_key(path: str, domain_path: str | None = None) -> str:
    """The key of a file, or of an object in it when ``domain_path`` is given."""
    absolute = os.path.abspath(path).replace("\\", "/")
    key = "file:///" + absolute.lstrip("/")
    return f"{key}#{domain_path}" if domain_path else key


def path_of(key: str) -> str | None:
    """The file path a ``file:`` key names, without its domain path."""
    if not key.startswith("file:///"):
        return None
    path = key[len("file:///"):].split("#", 1)[0]
    if not WINDOWS or not (len(path) > 1 and path[1] == ":"):
        path = "/" + path
    return path


def normalize(key: str) -> str:
    """The form keys are compared in. Only the path of a ``file:`` key is changed."""
    if not key.startswith("file:///"):
        return key
    rest = key[len("file:///"):]
    path, sep, fragment = rest.partition("#")
    trailing = path.endswith("/") or path.endswith("\\")
    path = posixpath.normpath(path.replace("\\", "/")) if path else ""
    if path == ".":
        path = ""
    if trailing and path and not path.endswith("/"):
        path += "/"
    if WINDOWS:
        path = path.casefold()
    return "file:///" + path.lstrip("/") + sep + fragment


def covers(prefix: str, key: str) -> bool:
    """Whether a scope or lock prefix covers a key, matching only at a boundary."""
    p, k = normalize(prefix), normalize(key)
    if not k.startswith(p):
        return False
    if len(k) == len(p) or p.endswith(("/", "#", ":")):
        return True
    return k[len(p)] in "/#"


def conflict(a: str, b: str) -> bool:
    """Two targets conflict when one covers the other (DCLI-060)."""
    return covers(a, b) or covers(b, a)


def kind_of(key: str) -> str:
    if key.startswith("file:///"):
        return "file_object" if "#" in key else "file"
    if key.startswith("untitled:"):
        return "untitled_object" if "#" in key else "untitled_document"
    return "device_part"
