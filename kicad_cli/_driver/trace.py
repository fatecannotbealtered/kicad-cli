"""Which code of the tool a live run used, for the fingerprint of its evidence (DVER-043).

Only when the matrix runner names a folder in ``TOOL_DRIVER_TRACE_DIR``, from the moment the
runtime is imported. A file of the tool's package counts as used, whole, when one of its
functions starts, when it was imported before the runtime, when it is opened other than by an
import, such as a module whose source is sent to the application, or when it is named on a command
line or copied, such as a script the application runs. The package's other imported modules are
listed as imported. When the process ends it writes both lists to its own file in that folder;
until then the file says the trace is not complete, so a process that ends without writing it
leaves the run counting the whole adapter. The pinned runtime and the command registrations are
left out: the evidence covers them otherwise.
"""
from __future__ import annotations

import atexit
import inspect
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

ENV = "TOOL_DRIVER_TRACE_DIR"
_finish: Any = None
_where: dict[str, Path] = {}


def locate(root: Path, package: Path) -> None:
    """The project root and the tool's package, which ``Tool`` knows once it is made."""
    _where.update(root=Path(os.path.realpath(root)), package=Path(os.path.realpath(package)))


def _by_import() -> bool:
    """The open being audited comes from the import system."""
    frame = sys._getframe(2)
    for _ in range(12):
        if frame is None:
            return False
        name = frame.f_code.co_filename
        if name.startswith("<frozen importlib") or os.sep + "importlib" + os.sep in name:
            return True
        frame = frame.f_back
    return False


def start() -> None:
    """Begin noting, once per process, when the runner asks for it."""
    global _finish
    folder = os.environ.get(ENV)
    monitoring = getattr(sys, "monitoring", None)
    if not folder or _finish is not None or monitoring is None:
        return
    tool = next((t for t in (3, 4, monitoring.COVERAGE_ID) if monitoring.get_tool(t) is None), None)
    if tool is None:
        return  # another tool holds every id: the run counts the whole adapter
    state: dict[str, Any] = {"path": None}

    def begin() -> bool:
        state["path"] = Path(folder) / f"trace-{os.getpid()}-{secrets.token_hex(4)}.json"
        try:
            state["path"].write_text(json.dumps({"complete": False}), encoding="utf-8")
            return True
        except OSError:
            return False

    if not begin():
        return
    early = {getattr(m, "__file__", None) for m in list(sys.modules.values())}  # imported before the runtime
    ran: set[str] = set()
    opened: set[str] = set()
    lines: list[str] = []
    folders: list[str] = []

    def started(code: Any, offset: int) -> Any:
        # Functions only: a module or a class body runs when it is imported.
        if code.co_flags & inspect.CO_OPTIMIZED:
            ran.add(code.co_filename)
        return monitoring.DISABLE  # each function once; afterwards it runs at full speed

    def audit(event: str, args: tuple[Any, ...]) -> None:
        if event == "open":
            if args and isinstance(args[0], str) and args[0].endswith(".py") and not _by_import():
                opened.add(args[0])
        elif event == "shutil.copytree" and args:
            folders.append(os.path.normcase(os.path.realpath(str(args[0]))) + os.sep)  # every file under it
        elif event in ("subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "shutil.copyfile"):
            lines.append(" ".join(map(str, args[:2])))

    monitoring.use_tool_id(tool, "tool-driver-trace")
    monitoring.register_callback(tool, monitoring.events.PY_START, started)
    monitoring.set_events(tool, monitoring.events.PY_START)
    sys.addaudithook(audit)

    def finish() -> None:
        global _finish
        if _finish is None:
            return
        _finish = None
        monitoring.set_events(tool, 0)
        package = _where.get("package") or Path(os.path.realpath(Path(__file__).parent.parent))
        root = _where.get("root") or package.parent
        inside = os.path.normcase(str(package)) + os.sep
        skip = (inside + "_driver" + os.sep, inside + "commands" + os.sep)

        def ours(name: Any) -> str | None:
            """The file's path relative to the project when it is the package's own Python file."""
            if not isinstance(name, str) or not name.endswith(".py"):
                return None
            real = os.path.realpath(name)
            key = os.path.normcase(real)
            if not key.startswith(inside) or key.startswith(skip):
                return None
            try:
                return Path(real).relative_to(root).as_posix()
            except ValueError:
                return None

        modules = {ours(getattr(m, "__file__", None)) for m in list(sys.modules.values())} - {None}
        used = {ours(n) for n in list(ran) + list(opened) + list(early)} - {None}
        said = os.path.normcase(" ".join(lines).replace("/", os.sep))
        for folder_name, _, names in os.walk(package):
            for name in names:
                full = os.path.join(folder_name, name)
                rel = ours(full)
                key = os.path.normcase(os.path.realpath(full))
                named = (os.path.normcase(full), os.path.normcase(rel.replace("/", os.sep)) if rel else "")
                if rel is not None and (any(n and n in said for n in named) or any(key.startswith(f) for f in folders)):
                    used.add(rel)  # a script started or copied, for itself or the application to run
        try:
            state["path"].write_text(json.dumps({"complete": True, "ran": sorted(used), "imported": sorted(modules - used)}),
                                     encoding="utf-8")
        except OSError:
            pass  # the trace stays incomplete: the run counts the whole adapter

    real_exit = os._exit

    def exit_now(code: int) -> None:
        finish()
        real_exit(code)

    os._exit = exit_now  # a process that ends at once still writes what it used
    if hasattr(os, "register_at_fork"):
        # A forked child writes its own trace; one that ends without it leaves the run counting the whole adapter.
        os.register_at_fork(after_in_child=lambda: begin())
    _finish = finish
    atexit.register(finish)


def finish() -> None:
    """Write the trace now, before a process ends without running its exit handlers."""
    if _finish is not None:
        _finish()
