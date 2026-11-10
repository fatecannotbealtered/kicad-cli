"""What every command handler shares: the call, documents, keys, writes and errors.

In file mode the document ID is the absolute file path (``documents`` reports it that way),
so a handler finds its file in ``call.doc``. A write first refuses a file that KiCad has
open (KiCad keeps ``~<name>.lck`` beside it), because KiCad's next save would undo the
change (DCLI-201).
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .._driver import AdapterError
from .._driver.targets import file_key
from . import install

KIND_BY_SUFFIX = {
    ".kicad_pro": "project",
    ".kicad_sch": "schematic",
    ".kicad_pcb": "board",
    ".kicad_sym": "symbol_library",
    ".pretty": "footprint_library",
    ".kicad_mod": "footprint",
}


@dataclass
class Call:
    """One capability request: the command, its inputs (standard units) and its document."""

    command: str
    inputs: dict[str, Any]
    doc: Path | None
    operation_id: str | None = None
    targets: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, request: dict[str, Any]) -> Call:
        doc = request.get("document")
        return cls(command=request["command"], inputs=dict(request.get("inputs") or {}),
                   doc=Path(os.path.abspath(doc)) if doc else None, operation_id=request.get("operation_id"),
                   targets=list(request.get("targets") or []), fields=list(request.get("fields") or []))

    def require_doc(self, *suffixes: str) -> Path:
        """The document, which must exist and have one of the suffixes."""
        if self.doc is None:
            raise usage(f"{self.command} needs --doc.")
        if suffixes and self.doc.suffix.lower() not in suffixes:
            raise usage(f"{self.command} needs --doc to be a {' or '.join(suffixes)} file, not {self.doc.name}.")
        if not self.doc.exists():
            raise not_found(f"There is no file {self.doc}.")
        return self.doc


# ---------------------------------------------------------------------------- errors
# An adapter may return only the host error codes of DCLI-279; the runtime turns any other
# code into E_PROTOCOL.

def usage(message: str) -> AdapterError:
    """Inputs that do not fit the command, found only once the adapter looks at the file."""
    return AdapterError("E_VALIDATION", message, dispatch_state="not_started")


def invalid(message: str) -> AdapterError:
    return AdapterError("E_VALIDATION", message, dispatch_state="not_started")


def not_found(message: str) -> AdapterError:
    return AdapterError("E_NOT_FOUND", message, dispatch_state="not_started")


def ambiguous(message: str, candidates: list[dict[str, Any]]) -> AdapterError:
    """A name that matches several objects: at least two candidates, each an object with the
    ``id`` that names it and what tells it apart (DCLI-279)."""
    return AdapterError("E_AMBIGUOUS", message, dispatch_state="not_started", candidates=candidates)


def unsupported(message: str) -> AdapterError:
    return AdapterError("E_UNSUPPORTED", message, dispatch_state="not_started")


# ---------------------------------------------------------------------------- KiCad

@lru_cache(maxsize=1)
def _found() -> install.Installation | None:
    return install.find()


def kicad() -> install.Installation:
    """The KiCad installation, or an error that says how to point kicad-cli at it."""
    found = _found()
    if found is None:
        raise AdapterError("E_BACKEND_UNAVAILABLE",
                           f"KiCad was not found. A person installs KiCad 10.0.6 or later, or sets {install.ENV_DIR} "
                           "to its installation directory.", dispatch_state="not_started", knowledge=["kicad-not-found"])
    if not install.supported(found.version):
        raise AdapterError("E_UNSUPPORTED",
                           f"KiCad {found.version} at {found.root} is outside the supported range >=10.0.6 <11.",
                           dispatch_state="not_started", knowledge=["kicad-11-not-supported"])
    return found


# ---------------------------------------------------------------------------- documents

def key(path: Path, domain: str | None = None) -> str:
    return file_key(str(path), domain)


def revision(path: Path) -> str:
    """A hash of the file, or of a library folder's files; changes with every write."""
    digest = hashlib.sha256()
    if path.is_dir():
        for child in sorted(p for p in path.rglob("*") if p.is_file()):
            digest.update(str(child.relative_to(path)).encode("utf-8") + b"\0")
            digest.update(child.read_bytes())
    elif path.is_file():
        digest.update(path.read_bytes())
    else:
        return "missing"
    return digest.hexdigest()


def lock_file(path: Path) -> Path:
    return path.with_name(f"~{path.name}.lck")


def document(path: Path) -> dict[str, Any]:
    """The document object of a file in file mode (DCLI-213)."""
    exists = path.exists()
    writable = os.access(path, os.W_OK) if exists else os.access(path.parent, os.W_OK) if path.parent.exists() else True
    return {"id": str(path), "path": str(path), "revision": revision(path), "dirty": False,
            "readonly": not writable, "disk": "in_sync" if exists else "missing"}


def refuse_if_open(*paths: Path) -> None:
    """Refuse a write to a file KiCad has open: its next save would undo the change."""
    for path in paths:
        if lock_file(path).exists():
            raise AdapterError("E_CONFLICT", f"KiCad has {path.name} open; a write to the file would be lost at "
                               "KiCad's next save. A person saves and closes it in KiCad, then the command runs again.",
                               dispatch_state="not_started", conflict="file_open_in_session",
                               knowledge=["files-open-in-kicad"])


def open_notices(*paths: Path | None) -> list[dict[str, str]]:
    """For each file KiCad has open, the notice that the call used the file on disk: KiCad may
    hold changes to it that are not saved yet (DCLI-201). Writes that change such a file are
    refused instead (``refuse_if_open``)."""
    return [{"severity": "warning",
             "message": f"KiCad has {path.name} open and may have unsaved changes; this call used the file on disk."}
            for path in dict.fromkeys(paths) if path is not None and lock_file(path).exists()]


# ---------------------------------------------------------------------------- writes

def write_text(path: Path, text: str) -> None:
    """Write a whole file at once: a temporary file in the same folder, then a rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as out:
            out.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def saved(result: Any = None) -> dict[str, Any]:
    """The facts of a completed file-mode change: the file is written, so it is saved."""
    return {"dispatch_state": "dispatched", "change": "applied", "delivery": "not_applicable",
            "persistence": "saved", "result": result if result is not None else {}}


def produced(files: list[Path], result: Any = None) -> dict[str, Any]:
    """The facts of an export or render: new files, nothing changed in the design."""
    return {"dispatch_state": "dispatched", "change": "not_applicable", "delivery": "not_applicable",
            "persistence": "saved", "result": result if result is not None else {},
            "files": [{"path": str(f)} for f in files]}


def mm(nm: int | float) -> float:
    """Nanometres to millimetres, rounded to the file's 1 nm resolution."""
    return round(nm / 1_000_000, 6)


class Handler:
    """One command. Reads implement ``read``; writes ``preview``, ``apply`` and ``read_back``."""

    def __init__(self, call: Call) -> None:
        self.call = call

    def sources(self) -> list[Path]:
        """The KiCad files the command reads; a notice names each one KiCad has open."""
        return [self.call.doc] if self.call.doc is not None else []

    def read(self) -> Any:
        raise unsupported(f"{self.call.command} is not a read.")

    def preview(self) -> dict[str, Any]:
        raise unsupported(f"{self.call.command} is not a write.")

    def apply(self) -> dict[str, Any]:
        raise unsupported(f"{self.call.command} is not a write.")

    def read_back(self) -> dict[str, Any]:
        raise unsupported(f"{self.call.command} is not a write.")
