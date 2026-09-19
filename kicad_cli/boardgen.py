"""Turn a netlist into the plan for a board, and check it before building.

KiCad's own "Update PCB from Schematic" has no headless entry point: the
application implements it, SWIG does not bind it and the official CLI has no
subcommand for it. That is a statement about the dialog, not about the work --
the work is loading a footprint per component, placing it, and joining pads
into nets, all of which `pcbnew` exposes.

So this module does the half that does not need KiCad: read the netlist, find
each component's footprint *file* on disk, and refuse the ones that are not
there. A footprint is a file, and whether it exists is a question the
filesystem answers, so a specification with a typo in it fails on a machine
with no KiCad at all rather than waiting to fail inside the interpreter.

The other half -- the part that needs pcbnew -- is `payload/board_build.py`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import envelope, kicad_env, sexpr


def _footprint_dirs() -> list[Path]:
    root = kicad_env.find_kicad_root()
    if root is None:
        return []
    return [Path(root) / "share" / "kicad" / "footprints"]


def resolve_footprint(identifier: str, directories: list[Path]) -> Path | None:
    """`Library:Name` to the `.kicad_mod` file, or None.

    Only the installation's own libraries are searched. A project fp-lib-table
    can point anywhere, and following one would mean honouring a path out of a
    file the caller supplied -- worth doing later, deliberately, rather than
    by accident here.
    """
    if ":" not in identifier:
        return None
    library, name = identifier.split(":", 1)
    for directory in directories:
        candidate = directory / f"{library}.pretty" / f"{name}.kicad_mod"
        if candidate.is_file():
            return candidate
    return None


def read(netlist_path: str) -> dict[str, Any]:
    """Parse a KiCad netlist into components and nets. No KiCad needed."""
    path = Path(netlist_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        envelope.fail("E_NOT_FOUND", "cannot read the netlist", {"path": str(path)})
    try:
        node = sexpr.parse(text)
    except Exception as exc:  # noqa: BLE001 - the parser raises several types
        envelope.fail(
            "E_VALIDATION",
            "the netlist is not a readable s-expression",
            {"path": str(path), "reason": str(exc)[:200]},
        )
        raise AssertionError from exc  # pragma: no cover

    components = []
    for comp in sexpr.walk(node, "comp"):
        ref = sexpr.value(sexpr.child(comp, "ref"))
        if not ref:
            continue
        components.append(
            {
                "ref": ref,
                "value": sexpr.value(sexpr.child(comp, "value"), default=""),
                "footprint": sexpr.value(sexpr.child(comp, "footprint"), default=""),
            }
        )

    nets = []
    for net in sexpr.walk(node, "net"):
        name = sexpr.value(sexpr.child(net, "name"))
        if not name:
            continue
        nodes = []
        for node_item in sexpr.walk(net, "node"):
            ref = sexpr.value(sexpr.child(node_item, "ref"))
            pin = sexpr.value(sexpr.child(node_item, "pin"))
            if ref and pin:
                nodes.append({"ref": ref, "pin": str(pin)})
        if nodes:
            nets.append({"name": name, "nodes": nodes})

    if not components:
        envelope.fail(
            "E_VALIDATION",
            "the netlist declares no components",
            {"path": str(path), "hint": "`sch create` or KiCad's own `sch export netlist`"},
        )
    return {"components": components, "nets": nets}


def plan(netlist_path: str, pitch: float, margin: float) -> dict[str, Any]:
    """Everything checkable before pcbnew is involved.

    A component without a footprint, or with one that is not installed, is the
    common failure and it is a file check -- so it is answered here, named, and
    all of them at once rather than one per attempt.
    """
    parsed = read(netlist_path)

    # Structure first, and without KiCad. Whether a component was assigned a
    # footprint at all is a question about the netlist; whether that footprint
    # is installed is a question about this machine. Answering the first one
    # only after finding KiCad tells someone without an installation to go and
    # install one, when the real problem is a netlist that named nothing.
    unassigned = [
        {"ref": component["ref"], "problem": "the netlist assigns no footprint"}
        for component in parsed["components"]
        if not component["footprint"]
    ]
    if unassigned:
        envelope.fail(
            "E_VALIDATION",
            "the netlist cannot be turned into a board as it stands",
            {"problems": unassigned[:40], "problem_count": len(unassigned)},
        )

    directories = _footprint_dirs()
    if not directories:
        envelope.fail(
            "E_CONFIG",
            "could not find the KiCad installation, so footprints cannot be resolved",
            {"hint": f"set {kicad_env.ENV_ROOT} to the KiCad installation; run: kicad-cli doctor"},
        )

    problems: list[dict[str, Any]] = []
    placed = []
    for component in parsed["components"]:
        identifier = component["footprint"]
        found = resolve_footprint(identifier, directories)
        if found is None:
            problems.append(
                {
                    "ref": component["ref"],
                    "problem": "footprint is not in the installed libraries",
                    "footprint": identifier,
                }
            )
            continue
        placed.append({**component, "file": str(found)})

    if problems:
        envelope.fail(
            "E_VALIDATION",
            "the netlist cannot be turned into a board as it stands",
            {"problems": problems[:40], "problem_count": len(problems)},
        )

    # A grid, ordered by reference, because a board with everything at the
    # origin is not a board. This is placement in the sense of "somewhere
    # definite", not in the sense a layout engineer means: nothing here knows
    # which parts belong together. `board move` is how you say that, and an
    # automatic placer is the obvious next thing this file does not do.
    columns = max(1, int(len(placed) ** 0.5 + 0.999))
    for index, component in enumerate(sorted(placed, key=lambda c: c["ref"])):
        component["x"] = round(margin + (index % columns) * pitch, 3)
        component["y"] = round(margin + (index // columns) * pitch, 3)
    rows = max(1, (len(placed) + columns - 1) // columns)
    return {
        "components": sorted(placed, key=lambda c: c["ref"]),
        "nets": parsed["nets"],
        "board_mm": [
            round(margin * 2 + max(0, columns - 1) * pitch, 3),
            round(margin * 2 + max(0, rows - 1) * pitch, 3),
        ],
        "placement": "grid",
    }
