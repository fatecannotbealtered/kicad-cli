"""Plan from public observations; execute every CAD change through product CLI."""

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

from module_trial import Trial, put


def main(directory, layer=None):
    t = Trial(directory)
    # The earlier developer-only native API probe left its own lock. Preserve
    # that failed fixture and copy only immutable archive members afresh.
    import zipfile

    baseline = json.loads((t.root.parent / "BASELINE.json").read_bytes())
    with zipfile.ZipFile(baseline["phone_zip"]) as archive:
        for entry in archive.infolist():
            if not entry.filename.startswith("PHONE-A1-ENGINEERING-REVIEW-r02-LITE/"):
                continue
            relative = Path(entry.filename).relative_to("PHONE-A1-ENGINEERING-REVIEW-r02-LITE")
            if relative.parts and relative.parts[0] == "cad" and not entry.is_dir():
                output = (t.root / relative).resolve()
                assert output.is_relative_to(t.root)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(archive.read(entry))
    board = t.root / "cad/main/PHONE_A1_MAIN.kicad_pcb"
    context = t.cli("project", "context", "--board", board)["observation"]["context"]
    observation = t.cli(
        "board", "routing", "inspect", "--board", board, "--net", "MODEM_SIM1_CLK_RAW"
    )["observation"]
    put(t.root / "BEFORE.json", observation)
    net = observation["nets"][0]
    north = layer == "north"
    a, b = (
        ((28600000, 84700000), (31500000, 66700000))
        if north
        else ((28240000, 95600000), (31500000, 95500000))
    )
    edges = defaultdict(list)
    selected = [s for s in net["segments"] if s["layer"] == "In2.Cu"]
    for s in selected:
        x, y = tuple(s["start_nm"]), tuple(s["end_nm"])
        edges[x].append((y, s))
        edges[y].append((x, s))
    queue, predecessor = deque([a]), {a: None}
    while queue:
        here = queue.popleft()
        for there, s in edges[here]:
            if there not in predecessor:
                predecessor[there] = (here, s)
                queue.append(there)
    path, here = [], b
    while here != a:
        here, s = predecessor[here]
        path.append(s)
    endpoint_b = next(s for s in selected if tuple(s["start_nm"]) == b and s not in path)
    via = next((v for v in net["vias"] if tuple(v["at_nm"]) == a), None)
    anchor_a = next(s for s in selected if tuple(s["start_nm"]) == a)
    spec = {
        "baseline": context,
        "net": net["net"],
        "remove_ids": [s["uuid"] for s in path],
        "allow_locked_ids": [s["uuid"] for s in path if s["locked"]],
        "allowed_layers": ["In2.Cu"],
        "bounds_nm": [27000000, 63000000, 34000000, 97000000],
        "endpoints": [
            {
                "object_uuid": via["uuid"] if via else anchor_a["uuid"],
                "terminal": "center" if via else "start",
                "at_nm": a,
                "layer": "In2.Cu",
            },
            {"object_uuid": endpoint_b["uuid"], "terminal": "start", "at_nm": b, "layer": "In2.Cu"},
        ],
        "segments": [
            {"start_nm": a, "end_nm": [31500000, 95600000], "layer": "In2.Cu", "width_nm": 150000},
            {"start_nm": [31500000, 95600000], "end_nm": b, "layer": "In2.Cu", "width_nm": 150000},
        ],
        "minimum_improvement_mm": 1,
    }
    if north:
        points = [a, [28600000, 64900000], [33500000, 64900000], [33500000, 66700000], b]
        spec["segments"] = [
            {"start_nm": x, "end_nm": y, "layer": "In2.Cu", "width_nm": 150000}
            for x, y in zip(points, points[1:], strict=False)
        ]
    elif layer:
        source_right = layer == "grid-right"
        grid_plan = layer in {"grid", "grid-right"}
        layer = "In5.Cu" if grid_plan else layer
        source, target = net["vias"][0], next(v for v in net["vias"] if v["at_nm"][0] < 5000000)
        if source_right:
            source = max(net["vias"], key=lambda v: v["at_nm"][0])
        spec.update(
            remove_ids=[s["uuid"] for s in selected],
            allow_locked_ids=[s["uuid"] for s in selected if s["locked"]],
            allowed_layers=["In2.Cu", layer],
            bounds_nm=[3000000, 63000000, 41000000, 119000000],
            endpoints=[
                {
                    "object_uuid": v["uuid"],
                    "terminal": "center",
                    "at_nm": v["at_nm"],
                    "layer": layer,
                }
                for v in (source, target)
            ],
            segments=[
                {
                    "start_nm": source["at_nm"],
                    "end_nm": target["at_nm"],
                    "layer": layer,
                    "width_nm": 150000,
                }
            ],
        )
        if grid_plan:
            from plan_single_layer import plan

            points, search = plan(board, net["net"], layer, source["at_nm"], target["at_nm"])
            put(t.root / "PLANNING-ONLY.json", search)
            spec["bounds_nm"] = [1000000, 60000000, 60000000, 127500000]
            spec["segments"] = [
                {"start_nm": x, "end_nm": y, "layer": layer, "width_nm": 150000}
                for x, y in zip(points, points[1:], strict=False)
            ]
    put(t.root / "patch.json", spec)
    preview = t.cli(
        "board",
        "route",
        "patch",
        "--board",
        board,
        "--spec",
        t.root / "patch.json",
        "--dry-run",
        require=False,
    )
    put(t.root / "PATCH-PREVIEW.json", preview)
    if "error" in preview:
        print(json.dumps(preview["error"]))
        return
    receipt = t.cli(
        "board",
        "route",
        "patch",
        "--board",
        board,
        "--spec",
        t.root / "patch.json",
        "--confirm",
        preview["confirm_token"],
    )
    after = t.cli("board", "routing", "inspect", "--board", board, "--net", net["net"])
    put(t.root / "PATCH-RESULT.json", {"receipt": receipt, "after": after})
    print(t.root)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
