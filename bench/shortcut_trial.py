"""Read-only development path proposal; all actual modifications use public CLI."""

import heapq
import json
import math
import sys
import zipfile
from collections import defaultdict

from module_trial import Trial, put


def main(directory):
    from kicad_cli.fileformat.board import Board
    from kicad_cli.fileformat.connectivity import _distance_to_segment, _segment_distance
    from kicad_cli.pcb_patch import graph

    t = Trial(directory)
    baseline = json.loads((t.root.parent / "BASELINE.json").read_bytes())
    with zipfile.ZipFile(baseline["phone_zip"]) as archive:
        prefix = "PHONE-A1-ENGINEERING-REVIEW-r02-LITE/"
        for entry in archive.infolist():
            if entry.filename.startswith(prefix + "cad/") and not entry.is_dir():
                output = (t.root / entry.filename[len(prefix) :]).resolve()
                assert output.is_relative_to(t.root)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(archive.read(entry))
    path = t.root / "cad/main/PHONE_A1_MAIN.kicad_pcb"
    context = t.cli("project", "context", "--board", path)["observation"]["context"]
    board = Board.load(path)
    proposals = []
    for net in ["RESET_CMD", "MODEM_SIM1_CLK_RAW"]:
        observation = t.cli("board", "routing", "inspect", "--board", path, "--net", net)
        put(t.root / (net + "-BEFORE.json"), observation)
        tracks = [v for v in board.tracks if v.net == net and v.kind == "segment"]
        # Only shorten the actual pad-to-pad path; open branches are reviewed separately.
        g = graph(board, net)
        pads = observation["observation"]["nets"][0]["pads"]
        source = (*pads[0]["at_nm"], "F.Cu")
        goal = (*pads[1]["at_nm"], "F.Cu")
        queue = [(0, source)]
        costs, prev = {source: 0}, {}
        while queue:
            cost, here = heapq.heappop(queue)
            if cost != costs[here]:
                continue
            if here == goal:
                break
            for there, weight in g[here].items():
                candidate = cost + weight
                if candidate < costs.get(there, math.inf):
                    costs[there] = candidate
                    prev[there] = here
                    heapq.heappush(queue, (candidate, there))
        main_path = {goal}
        while goal != source:
            goal = prev[goal]
            main_path.add(goal)
        for layer in {v.layer for v in tracks}:
            if any(layer in z.layers and not z.rule_area for z in board.zones):
                continue
            selected = [v for v in tracks if v.layer == layer]
            edges = defaultdict(list)
            for s in selected:
                edges[s.start].append((s.end, s))
                edges[s.end].append((s.start, s))
            foreign = [s for s in board.tracks if s.net != net and s.layer == layer]
            vias = [v for v in board.vias if v.net != net and layer in v.layers]
            pads = [
                p
                for fp in board.footprints
                for p in fp.pads
                if p.net != net and layer in board.expand_layers(p.layers)
            ]

            def clear(a, b, width, foreign=foreign, vias=vias, pads=pads):
                for s in foreign:
                    if s.kind != "segment":
                        continue
                    if _segment_distance(a, b, s.start, s.end) < (width + s.width) / 2 + 100001:
                        return False
                for v in vias:
                    if (
                        _distance_to_segment(*v.position, *a, *b)
                        < (width + v.diameter) / 2 + 100001
                    ):
                        return False
                for p in pads:
                    x, y, X, Y = p.bbox()
                    gap = width / 2 + 100001
                    x, y, X, Y = x - gap, y - gap, X + gap, Y + gap
                    if any(x <= v[0] <= X and y <= v[1] <= Y for v in (a, b)):
                        return False
                    corners = [(x, y), (X, y), (X, Y), (x, Y), (x, y)]
                    if any(
                        _segment_distance(a, b, c, d) < 1
                        for c, d in zip(corners, corners[1:], strict=False)
                    ):
                        return False
                return True

            vertices = sorted(edges)
            for i, a in enumerate(vertices):
                queue = [(0, a)]
                costs, prev = {a: 0}, {}
                while queue:
                    cost, here = heapq.heappop(queue)
                    if cost != costs[here]:
                        continue
                    for there, s in edges[here]:
                        candidate = cost + s.length()
                        if candidate < costs.get(there, math.inf):
                            costs[there] = candidate
                            prev[there] = (here, s)
                            heapq.heappush(queue, (candidate, there))
                for b in vertices[i + 1 :]:
                    if (*a, layer) not in main_path or (*b, layer) not in main_path:
                        continue
                    if b not in costs or costs[b] - math.dist(a, b) < 200000:
                        continue
                    chain, here = [], b
                    while here != a:
                        here, s = prev[here]
                        chain.append(s)
                    if len(chain) < 2 or len({s.width for s in chain}) != 1:
                        continue
                    width = chain[0].width
                    if not clear(a, b, width):
                        continue
                    # Avoid disconnecting a branch attached inside the removed path.
                    internal = {s.start for s in chain} | {s.end for s in chain}
                    if any(len(edges[v]) > 2 for v in internal - {a, b}):
                        continue
                    proposals.append((costs[b] - math.dist(a, b), net, layer, a, b, width, chain))
    proposals.sort(key=lambda row: row[0], reverse=True)
    put(
        t.root / "PROPOSALS.json",
        [
            {
                "improvement_mm": r[0] / 1e6,
                "net": r[1],
                "layer": r[2],
                "a": r[3],
                "b": r[4],
                "removed": len(r[-1]),
            }
            for r in proposals[:40]
        ],
    )
    for index, (_, net, layer, a, b, width, chain) in enumerate(proposals[:8]):

        def endpoint(point, chain=chain, layer=layer):
            s = next(s for s in chain if point in (s.start, s.end))
            return {
                "object_uuid": s.node.find("uuid").value(1),
                "terminal": "start" if s.start == point else "end",
                "at_nm": point,
                "layer": layer,
            }

        ids = [s.node.find("uuid").value(1) for s in chain]
        spec = {
            "baseline": context,
            "net": net,
            "remove_ids": ids,
            "allow_locked_ids": ids,
            "allowed_layers": [layer],
            "bounds_nm": [1000000, 1000000, 74000000, 164000000],
            "endpoints": [endpoint(a), endpoint(b)],
            "segments": [{"start_nm": a, "end_nm": b, "layer": layer, "width_nm": width}],
            "minimum_improvement_mm": 0.1,
        }
        spec_path = t.root / (f"proposal-{index:02}.json")
        put(spec_path, spec)
        preview = t.cli(
            "board",
            "route",
            "patch",
            "--board",
            path,
            "--spec",
            spec_path,
            "--dry-run",
            require=False,
        )
        put(t.root / (f"preview-{index:02}.json"), preview)
        if "error" in preview:
            continue
        result = t.cli(
            "board",
            "route",
            "patch",
            "--board",
            path,
            "--spec",
            spec_path,
            "--confirm",
            preview["confirm_token"],
        )
        after = t.cli("board", "routing", "inspect", "--board", path, "--net", net)
        put(t.root / "PATCH-RESULT.json", {"receipt": result, "after": after, "proposal": spec})
        print(t.root)
        return
    raise ValueError("No accepted proposal in this finite developer planning batch")


if __name__ == "__main__":
    main(sys.argv[1])
