"""Controlled Host-only counterexamples for plane-via candidate selection.

Method declared before execution:
* Load exactly route.py/router.py from the requested immutable Git revision.
  Other imports are the working source's pure-Python support modules.
* Do not call entry.py, a public CLI, KiCad, pcbnew, a native consumer, or save.
  The only subprocesses are two read-only `git show` calls; the whole process
  tree must be observed by the external Windows Job runner.
* Use a 10x10 mm synthetic two-layer grid and a GND B.Cu filled square from
  (1,1) to (9,9) mm. One front SMD pad is at (5,5) mm. Requested ordinary
  via diameter/drill are 0.6/0.3 mm. GND code is 1.
* same-net-hole: paint an old GND via at (5,5), diameter/drill 0.6/0.3 mm;
  the effective project hole-to-hole gap is 0.25 mm. The first candidate
  (5,5) is illegal, while (5.6,5) has a 0.3 mm drill-edge gap and is legal.
* smd-margin: a saved-geometry-equivalent 0.6x0.6 mm rectangular GND SMD
  copper pad at (5,5), optional drill-to-SMD gap 0.1 mm. The first candidate
  drills the pad; (5.6,5) has a 0.15 mm material gap and is legal.

Expected historical observations, not evidence until the runner records them:
* 8c12f644d1f52b502a0f5f7b1667e3aeff841960 (663's parent): same-net-hole
  creates the coincident unsafe via; smd-margin raises DrillConstraintError.
* 719461ee2070cdb2cc150fbf28f7c95e645fcc8a: same-net-hole raises
  ViaHoleConstraintError at the first point; smd-margin raises
  DrillConstraintError. Neither searches the later legal point.
* Fixed working source: both serve GND using a later legal position.

This isolates source/Host behavior. It is neither public-command coverage nor
Native plane contact, refill, connectivity, DRC or engineering acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import subprocess
import sys
import types
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kicad_cli.native import route as current_route  # noqa: E402
from kicad_cli.native.smd_drill_guard import SmdDrillGuard, SmdPad  # noqa: E402
from kicad_cli.native.via_hole_guard import RULE, ViaHoleGuard, separated  # noqa: E402


def make_case(module, *, smd_gap_nm=None, old_hole=False, pads=((5, 5),)):
    """Only analytic guards, file-format-independent zones and Host grid state."""
    smd = None
    if smd_gap_nm is not None:
        smd = SmdDrillGuard(
            (
                SmdPad(
                    "U1.1:pad-1",
                    "GND",
                    "F.Cu",
                    "rect",
                    5_000_000,
                    5_000_000,
                    600_000,
                    600_000,
                    Fraction(0),
                ),
            ),
            smd_gap_nm,
        )
    guard = ViaHoleGuard({"rule": RULE, "minimum_nm": 250_000})
    copper_kwargs = {"smd_drill_guard": smd}
    if "via_hole_guard" in inspect.signature(module.Copper).parameters:
        copper_kwargs["via_hole_guard"] = guard
    copper = module.Copper((0, 0, 10, 10), [], [], [], **copper_kwargs)
    router = module.Router(copper)
    if old_hole:
        kwargs = {}
        if "drill_nm" in inspect.signature(router.paint_via).parameters:
            kwargs = {"drill_nm": 300_000, "position_nm": (5_000_000, 5_000_000)}
        router.paint_via(5, 5, 0.6, 1, **kwargs)
    ring = [
        (1_000_000, 1_000_000),
        (9_000_000, 1_000_000),
        (9_000_000, 9_000_000),
        (1_000_000, 9_000_000),
    ]
    zone = SimpleNamespace(rule_area=False, net="GND", layers=["B.Cu"], filled={"B.Cu": [ring]})
    created = []
    layout = SimpleNamespace(
        board=SimpleNamespace(zones=[zone], copper_layers=["F.Cu", "B.Cu"]),
        pads=[
            (None, SimpleNamespace(net="GND", kind="smd", position=(module.MM(x), module.MM(y))))
            for x, y in pads
        ],
        codes={"GND": 1},
        find=lambda name: 1 if name == "GND" else None,
        is_on=lambda _pad, layer: layer == "F.Cu",
        add=lambda piece: created.append(piece) or piece,
    )
    classes = SimpleNamespace(params=lambda _net: (0.2, 0.2, 0.6, 0.3))
    return layout, router, classes, created, guard


def historical_module(root, revision):
    sources = {}
    for name in ("router", "route"):
        path = "kicad_cli/native/" + name + ".py"
        command = ["git", "-C", str(root), "show", revision + ":" + path]
        result = subprocess.run(command, capture_output=True, check=True)
        sources[name] = result.stdout
    prior = sys.modules.get("kicad_cli.native.router")
    router = types.ModuleType("kicad_cli.native.router")
    router.__package__ = "kicad_cli.native"
    sys.modules[router.__name__] = router
    try:
        exec(compile(sources["router"], revision + ":router.py", "exec"), router.__dict__)
        module = types.ModuleType("kicad_cli.native._plane_candidate_repro")
        module.__package__ = "kicad_cli.native"
        sys.modules[module.__name__] = module  # dataclass resolves its module here
        exec(compile(sources["route"], revision + ":route.py", "exec"), module.__dict__)
    finally:
        if prior is None:
            sys.modules.pop(router.__name__, None)
        else:
            sys.modules[router.__name__] = prior
    return module, {name: hashlib.sha256(raw).hexdigest() for name, raw in sources.items()}


def observe(module, name):
    layout, router, classes, created, guard = make_case(
        module,
        smd_gap_nm=100_000 if name == "smd-margin" else None,
        old_hole=name == "same-net-hole",
    )
    log = {}
    result = {"case": name, "served": None, "exception": None}
    try:
        result["served"] = sorted(module.fanout_planes(layout, router, classes, {"GND"}, log))
    except Exception as exc:
        result["exception"] = {"type": type(exc).__name__, "reason": str(exc)}
    result["fanout"] = log.get("fanout")
    result["created_vias"] = [
        {
            "position_nm": list(piece.start),
            "diameter_nm": piece.width,
            "drill_nm": piece.drill,
            "old_hole_clear": separated(
                (*piece.start, piece.drill), (5_000_000, 5_000_000, 300_000), guard.gap_nm
            )
            if name == "same-net-hole"
            else None,
            "smd_clear": router.copper.smd_drill_guard.allows(*piece.start, piece.drill)
            if name == "smd-margin"
            else None,
        }
        for piece in created
        if piece.kind == "via"
    ]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if args.revision == "working":
        module = current_route
        hashes = {
            name: hashlib.sha256(
                (args.source_root / "kicad_cli" / "native" / (name + ".py")).read_bytes()
            ).hexdigest()
            for name in ("route", "router")
        }
    else:
        module, hashes = historical_module(args.source_root, args.revision)
    print(
        json.dumps(
            {
                "method": "controlled-host-only; no public CLI or Native consumer",
                "revision": args.revision,
                "source_root": str(args.source_root.resolve()),
                "module_sha256": hashes,
                "results": [observe(module, name) for name in ("same-net-hole", "smd-margin")],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
