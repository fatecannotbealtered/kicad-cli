"""A board's project file: net classes, and the current a track width carries.

Moved out of `payload/kicad_lib.py` unchanged, so the commands that run in
this process decide net classes and ampacity exactly as the payloads did.
"""

from __future__ import annotations

import fnmatch
import json
import os
from typing import Any

from .. import envelope


def project_path(pcb_path: str) -> str:
    return os.path.splitext(pcb_path)[0] + ".kicad_pro"


def load_project(pcb_path: str) -> tuple[dict[str, Any], str]:
    path = project_path(pcb_path)
    if not os.path.exists(path):
        envelope.fail(
            "E_NOT_FOUND",
            "找不到工程文件，设计规则与网络类都存在其中",
            {"path": path, "write_state": "not_started"},
        )
    with open(path, encoding="utf-8") as handle:
        return json.load(handle), path


class NetClasses:
    """Which class a net is in, decided by the project's netclass_patterns.

    The class names embedded in a board can be stale -- editing .kicad_pro does
    not rewrite them -- so the patterns are the authority, not the board.
    """

    def __init__(self, project: dict[str, Any]) -> None:
        settings = project.get("net_settings", {})
        self.classes = {c["name"]: c for c in settings.get("classes", [])}
        self.patterns = settings.get("netclass_patterns", []) or []
        if "Default" not in self.classes:
            self.classes["Default"] = {
                "name": "Default",
                "track_width": 0.2,
                "clearance": 0.2,
                "via_diameter": 0.6,
                "via_drill": 0.3,
            }

    def name_for(self, netname: str) -> str:
        for pattern in self.patterns:
            if fnmatch.fnmatch(netname, pattern["pattern"]):
                return pattern["netclass"]
        return "Default"


def ampacity(width_mm: float, oz: float = 1.0, dT: float = 10.0, external: bool = True) -> float:  # noqa: N803
    """IPC-2221: I = k * dT^0.44 * A^0.725, A in square mils; amperes.

    k is 0.048 on an outer layer and 0.024 on an inner one.
    """
    area_mil2 = (width_mm / 0.0254) * (1.378 * oz)
    k = 0.048 if external else 0.024
    return k * (dT**0.44) * (area_mil2**0.725)
