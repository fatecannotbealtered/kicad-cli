"""``board netclass``, in this process: create a net class and put nets in it.

A port of `payload/netclass.py`, which ran in KiCad's interpreter without
ever needing pcbnew: net classes live in the project file's `net_settings`,
`classes` defining them and `netclass_patterns` saying which net is in which.
It is a JSON edit, and it still goes through the write gate and the write
transaction, because it changes a file on disk.

The copper keeps its width: a class is a target, not the current state.
`board rewidth` re-routes to it, `board widen` widens in place.
"""

from __future__ import annotations

import json
from typing import Any

from .. import envelope
from . import write
from .project import project_path

# IPC-2221, outer layer, 1 oz copper, 10 C rise: the constants the audit uses,
# so the amperes reported here agree with what the audit reports afterwards.
IPC_K, IPC_B, IPC_C = 0.048, 0.44, 0.725
COPPER_OZ, DELTA_T = 1.0, 10.0
OPTIONS = (
    ("width", "track_width"),
    ("clearance", "clearance"),
    ("via-diameter", "via_diameter"),
    ("via-drill", "via_drill"),
)


def ampacity(width_mm: float, oz: float = COPPER_OZ, dt: float = DELTA_T) -> float:
    """Roughly how many amperes this width carries: a magnitude, not a design basis."""
    area_mil2 = (width_mm / 0.0254) * (oz * 1.378)
    return round(IPC_K * (dt**IPC_B) * (area_mil2**IPC_C), 2)


def load(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        envelope.fail(
            "E_NOT_FOUND",
            "找不到工程文件，网络类存在其中",
            {"path": path, "hint": "板文件旁边应当有同名的 .kicad_pro"},
        )
    except (OSError, ValueError) as exc:
        envelope.fail("E_IO", "工程文件读不出来", {"path": path, "reason": str(exc)[:200]})
    raise AssertionError("unreachable")


def apply(project, name, params, nets):
    """Create or update the class and assign each net to it; what really changed.

    Each net is assigned by its exact name, never a wildcard: the caller gave
    net names, and inventing `+3V3*` would also catch `+3V3_SENSE`, which
    nobody asked for.
    """
    settings = project.setdefault("net_settings", {})
    classes = settings.setdefault("classes", [])
    patterns = settings.setdefault("netclass_patterns", [])

    existing = next((c for c in classes if c.get("name") == name), None)
    if existing is None:
        # A new class starts from Default and is overridden, so no field is
        # missing -- KiCad gives a class without one its built-in default,
        # which would not be the number reported here.
        base = next((c for c in classes if c.get("name") == "Default"), {})
        created = dict(base)
        created.update({"name": name, **params})
        classes.append(created)
        action = "created"
    else:
        existing.update(params)
        action = "updated"

    assigned, already = [], []
    for net in nets:
        match = next((p for p in patterns if p.get("pattern") == net), None)
        if match is None:
            patterns.append({"pattern": net, "netclass": name})
            assigned.append(net)
        elif match.get("netclass") != name:
            match["netclass"] = name
            assigned.append(net)
        else:
            already.append(net)
    return action, assigned, already


def run(board: str, args: dict[str, Any]) -> None:
    write.start()
    name = str(args["name"]).strip()
    if not name:
        envelope.fail("E_USAGE", "--name 不能为空", {"param": "name"})
    # Joined and split on commas as the payload received them, so a net whose
    # name has a comma in it is still two nets -- listed in DEVELOPMENT_STATUS.md.
    given = args.get("nets")
    listed = given if isinstance(given, list) else [given] if given else []
    joined = ",".join(str(n) for n in listed)
    nets = [n.strip() for n in joined.split(",") if n.strip()]
    if not nets:
        envelope.fail("E_USAGE", "--nets 至少要给一个网络名", {"param": "nets"})

    params = {}
    for option, key in OPTIONS:
        if args.get(option) is not None:
            try:
                value = float(str(args[option]))
            except (TypeError, ValueError):
                envelope.fail(
                    "E_USAGE", "尺寸必须是数字，单位 mm", {"param": option, "got": args[option]}
                )
            if value <= 0:
                envelope.fail("E_USAGE", "尺寸必须大于 0", {"param": option, "got": value})
            params[key] = value
    if "track_width" not in params:
        envelope.fail(
            "E_USAGE", "--width 是必需的：网络类的意义就是给出目标线宽", {"param": "width"}
        )

    path = project_path(board)
    project = load(path)
    classes = project.get("net_settings", {}).get("classes", [])
    before = next((c for c in classes if c.get("name") == name), None)

    preview = {
        "project": path,
        "netclass": name,
        "exists": before is not None,
        "track_width_mm": params["track_width"],
        "ampacity_a": ampacity(params["track_width"]),
        "nets": nets,
        "will": f"在 .kicad_pro 里{'改写' if before else '新建'}网络类 {name} "
        f"并把 {len(nets)} 个网络指派给它；铜先不变",
    }
    envelope.check_confirm(args.get("confirm"), "netclass:" + name, preview, path)

    action, assigned, already = apply(project, name, params, nets)

    write.begin(path)
    # Text mode, as the payload wrote it: on Windows the file comes out with
    # CRLF line ends.
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(project, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    write.done(
        {
            "project": path,
            "netclass": name,
            "action": action,
            "params": params,
            "ampacity_a": ampacity(params["track_width"]),
            "assigned": assigned,
            "already_assigned": already,
            "note": "网络类是目标，不是现状——板上的铜还是原来的宽度。"
            "接着跑 board rewidth 按新宽度整条重布（推荐），或 board widen 原地加宽；"
            "然后 board audit 复查 width_compliance，board drc 复查间距。"
            "载流量按 1 oz 铜、10C 温升估算，仅供量级参考。",
        }
    )
