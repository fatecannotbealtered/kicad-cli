"""The adapter layer: the only code of kicad-cli that talks to KiCad (DCLI-260).

kicad-cli works in file mode. It reads and writes KiCad's files itself (``fileformat``) and
runs KiCad's own command-line program for checks and exports (``kicad_tools``). Each domain
command has a handler in ``handlers``; ``read``, ``preview``, ``apply`` and ``read_back``
dispatch on the command path.

The conformance suites never call this code: they run the tool with ``KICAD_CLI_FAKE_HOST``
set, and the fake host answers every capability (DVER-023).
"""
from __future__ import annotations

import os

from pathlib import Path
from typing import Any

from .._driver import Adapter as BaseAdapter
from . import install
from .base import KIND_BY_SUFFIX, Call, Handler, document, open_notices, unsupported
from .handlers import HANDLERS


def handler(request: dict[str, Any]) -> Handler:
    call = Call.of(request)
    cls = HANDLERS.get(call.command)
    if cls is None:
        raise unsupported(f"kicad-cli has no handler for {call.command}.")
    return cls(call)


def _noting(h: Handler, reply: dict[str, Any]) -> dict[str, Any]:
    """Add the notices for the files the command read that KiCad has open (DCLI-201, DCLI-260)."""
    notices = open_notices(*h.sources())
    return {**reply, "notices": notices} if notices else reply


class Adapter(BaseAdapter):
    def probe(self, request: dict[str, Any]) -> dict[str, Any]:
        found = install.find()
        if found is None:
            return {"found": False}
        return {"found": True, "path": str(found.root), "version": found.version, "license": "not_required"}

    def documents(self, request: dict[str, Any]) -> dict[str, Any]:
        """File mode: the document at a path, or the KiCad documents in a folder.

        A path that does not exist yet is a document with ``disk: missing`` when its folder
        exists, so that create commands can name their new file with ``--doc``.
        """
        path_text = request.get("path")
        if not path_text:
            return {"items": []}
        path = Path(os.path.abspath(path_text))
        if path.is_dir() and path.suffix.lower() != ".pretty":
            children = [p for p in sorted(path.iterdir(), key=lambda p: p.name.casefold())
                        if p.suffix.lower() in KIND_BY_SUFFIX and (p.is_file() or p.suffix.lower() == ".pretty")]
            return {"items": [document(p) for p in children]}
        if not path.exists() and not path.parent.is_dir():
            return {"items": []}
        return {"items": [document(path)]}

    def read(self, request: dict[str, Any]) -> dict[str, Any]:
        h = handler(request)
        return _noting(h, {"result": h.read()})

    def preview(self, request: dict[str, Any]) -> dict[str, Any]:
        return handler(request).preview()

    def apply(self, request: dict[str, Any]) -> dict[str, Any]:
        h = handler(request)
        return _noting(h, h.apply())

    def read_back(self, request: dict[str, Any]) -> dict[str, Any]:
        return handler(request).read_back()
