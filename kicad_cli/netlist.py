"""The schematic netlist, as KiCad itself produces it.

Several commands need to know what the schematic says. They could each parse
the ``.kicad_sch`` files and derive it, and for read-only checks that is fine.
But anything that predicts or changes what KiCad will do needs the same bytes
KiCad uses, and that is this export: when the GUI updates a board it asks
eeschema for a netlist over ``MAIL_SCH_GET_NETLIST``, produced by the same
exporter the CLI drives here. Deriving it ourselves would be a second opinion
where only the first one counts.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from . import envelope, kicad_env, sexpr

_CONFIG_HOME: str | None = None


def _isolated_env() -> dict[str, str]:
    """Run KiCad's CLI against a throwaway config directory.

    Every invocation rewrites ``kicad_common.json`` -- it stores the caller's
    working directory there. That is a write into the user's KiCad settings,
    and if the GUI is open at the time, two processes are writing the same
    file. A read-only command has no business causing that, so we point
    KICAD_CONFIG_HOME somewhere disposable.

    One directory per process, not per call: pointing KiCad at an empty config
    makes it rebuild its defaults, and doing that on every invocation is both
    slow and a source of intermittent failures.
    """
    global _CONFIG_HOME
    if _CONFIG_HOME is None:
        _CONFIG_HOME = tempfile.mkdtemp(prefix="kicadcli-cfg-")
        real = Path(os.environ.get("APPDATA", "")) / "kicad"
        if real.is_dir():
            # Seed from the user's own settings so library tables and paths
            # resolve as they normally would; we only want the *writes* to land
            # somewhere else. KICAD_CONFIG_HOME is used as the root itself --
            # the version directory sits directly inside it -- so the contents
            # are copied, not the directory. Getting this wrong is silent: KiCad
            # simply rebuilds its defaults and the library tables go missing.
            try:
                shutil.copytree(real, _CONFIG_HOME, dirs_exist_ok=True)
            except (OSError, shutil.Error):
                pass  # falling back to KiCad's defaults is acceptable here
    env = dict(os.environ)
    env["KICAD_CONFIG_HOME"] = _CONFIG_HOME
    return env


def export(schematic: Path, timeout: int = 1800) -> tuple[sexpr.Node, str]:
    """Return the parsed netlist and whatever KiCad said on stderr.

    The stderr is not decoration. ``sch export netlist`` writes a perfectly
    well-formed file and exits 0 even when the schematic is not fully
    annotated -- the only sign is a warning on stderr. A caller that checks
    only the exit code gets a confident wrong answer.
    """
    exe = kicad_env.find_official_cli()
    if not exe:
        envelope.fail(
            "E_CONFIG",
            "this command needs the KiCad binary to export the schematic netlist",
            {"hint": "run `kicad-cli context` to see where KiCad was searched for"},
        )
    if not schematic.exists():
        envelope.fail("E_NOT_FOUND", "schematic file does not exist", {"path": str(schematic)})

    cmd = [
        str(exe),
        "sch",
        "export",
        "netlist",
        "--format",
        "kicadsexpr",
        "-o",
        "",
        str(schematic),
    ]
    # Launching another process on Windows fails occasionally for reasons that
    # have nothing to do with the design -- a scanner holding a freshly written
    # file, a transient lock. Observed at roughly one run in six, with an empty
    # stderr and no output. A read-only check has no reason to surface that as
    # a design problem, so try again before giving up.
    attempts, last = [], None
    for _ in range(3):
        tmp = Path(tempfile.mkdtemp(prefix="kicadcli-net-"))
        out = tmp / "reference.net"
        cmd[-2] = str(out)
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, env=_isolated_env())  # noqa: S603
        except subprocess.TimeoutExpired:
            envelope.fail("E_TIMEOUT", f"netlist export timed out after {timeout}s")
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        if out.exists():
            try:
                node = sexpr.parse(out.read_text(encoding="utf-8"))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            return node, stderr
        attempts.append({"returncode": proc.returncode, "stderr": stderr[-300:]})
        last = stderr
        shutil.rmtree(tmp, ignore_errors=True)
        time.sleep(0.4)

    envelope.fail(
        "E_IO",
        "KiCad could not export a netlist from this schematic",
        {"stderr": (last or "")[-800:], "schematic": str(schematic), "attempts": attempts},
    )
    raise AssertionError("unreachable")  # pragma: no cover


def components(node: sexpr.Node) -> list[dict[str, Any]]:
    """One record per component, with every uuid the updater will try.

    ``(tstamps "a" "b" "c")`` carries one uuid per unit of a multi-unit part,
    and KiCad's updater loops over all of them, so the match is against a set
    rather than a value. Reading only the first element -- which is what a
    naive ``value(child(...))`` does -- silently turns a multi-unit part into a
    single-unit one.
    """
    out = []
    for comp in sexpr.walk(node, "comp"):
        ref = sexpr.value(sexpr.child(comp, "ref"))
        if not ref:
            continue
        ts = sexpr.child(comp, "tstamps") or []
        uuids = [u for u in ts[1:] if isinstance(u, str)]
        sheetpath = sexpr.child(comp, "sheetpath")
        sheet = sexpr.value(sexpr.child(sheetpath, "tstamps"), default="/") if sheetpath else "/"

        fields: dict[str, str] = {}
        fnode = sexpr.child(comp, "fields")
        if fnode:
            for f in sexpr.children(fnode, "field"):
                name = sexpr.value(sexpr.child(f, "name"))
                if name:
                    fields[str(name)] = f[2] if len(f) > 2 and isinstance(f[2], str) else ""

        props: dict[str, str] = {}
        for p in sexpr.children(comp, "property"):
            name = sexpr.value(sexpr.child(p, "name"))
            if name:
                props[str(name)] = str(sexpr.value(sexpr.child(p, "value"), default=""))

        units = sexpr.child(comp, "units")
        out.append(
            {
                "ref": str(ref),
                "value": str(sexpr.value(sexpr.child(comp, "value"), default="")),
                "fpid": str(sexpr.value(sexpr.child(comp, "footprint"), default="")),
                "sheet": str(sheet),
                "uuids": uuids,
                "paths": [join_path(sheet, u) for u in uuids],
                "fields": fields,
                "properties": props,
                "unit_names": [
                    str(sexpr.value(sexpr.child(u, "name"), default=""))
                    for u in sexpr.children(units, "unit")
                ]
                if units
                else [],
            }
        )
    return out


def join_path(sheet: str, uuid: str) -> str:
    """The footprint ``path`` KiCad builds: the sheet path plus the symbol uuid."""
    return "/" + "/".join(x for x in f"{sheet}/{uuid}".split("/") if x)


def normalise(path: str) -> str:
    return "/" + "/".join(x for x in str(path).split("/") if x) if path else ""
