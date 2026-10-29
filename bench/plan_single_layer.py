"""Development-only bounded path planner. Outputs JSON intent, never CAD bytes.

The product's explicit patch independently checks endpoints, scope and native DRC.
This heuristic is not a newly qualified automatic-router product capability.
"""

import heapq
import math


def plan(board_path, net, layer, start, goal):
    from kicad_cli.fileformat.board import Board

    board = Board.load(board_path)
    grid = 50000
    x0, y0 = 1000000, 60000000
    width, height = 1180, 1350
    blocked = set()

    def mark(a, b, radius):
        lx = max(0, math.floor((min(a[0], b[0]) - radius - x0) / grid))
        rx = min(width - 1, math.ceil((max(a[0], b[0]) + radius - x0) / grid))
        ly = max(0, math.floor((min(a[1], b[1]) - radius - y0) / grid))
        ry = min(height - 1, math.ceil((max(a[1], b[1]) + radius - y0) / grid))
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = dx * dx + dy * dy
        for j in range(ly, ry + 1):
            for i in range(lx, rx + 1):
                x, y = x0 + i * grid, y0 + j * grid
                t = max(0, min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / length)) if length else 0
                if (x - a[0] - t * dx) ** 2 + (y - a[1] - t * dy) ** 2 <= radius**2:
                    blocked.add((i, j))

    inflation = 75000 + 100000  # target halfwidth + project minimum; native gate remains required
    for track in board.tracks:
        if track.net != net and track.layer == layer:
            if track.kind != "segment":
                raise ValueError("Planner does not support foreign arcs")
            mark(track.start, track.end, track.width / 2 + inflation)
    for via in board.vias:
        if via.net != net and layer in via.layers:
            mark(via.position, via.position, via.diameter / 2 + inflation)
    for fp in board.footprints:
        for pad in fp.pads:
            if (pad.net != net and layer in board.expand_layers(pad.layers)) or pad.drill:
                radius = math.hypot(*pad.size) / 2 + inflation
                mark(pad.position, pad.position, radius)
    if any(layer in z.layers and not z.rule_area for z in board.zones):
        raise ValueError("Planner refuses plane/power-fill layers")

    def cell(point):
        return (round((point[0] - x0) / grid), round((point[1] - y0) / grid))

    source, target = cell(start), cell(goal)
    if source in blocked or target in blocked:
        raise ValueError("Endpoint grid cell blocked under conservative clearance")
    queue = [(math.dist(source, target), 0, source)]
    costs, previous = {source: 0}, {}
    expanded = 0
    while queue:
        _, cost, here = heapq.heappop(queue)
        if cost != costs[here]:
            continue
        if here == target:
            break
        expanded += 1
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            there = (here[0] + dx, here[1] + dy)
            if not (0 <= there[0] < width and 0 <= there[1] < height) or there in blocked:
                continue
            if (
                dx
                and dy
                and ((here[0] + dx, here[1]) in blocked or (here[0], here[1] + dy) in blocked)
            ):
                continue
            new = cost + math.hypot(dx, dy)
            if new < costs.get(there, math.inf):
                costs[there] = new
                previous[there] = here
                heapq.heappush(queue, (new + math.dist(there, target), new, there))
    else:
        raise ValueError(
            "No path in this bounded conservative planning grid; no physical impossibility claim"
        )
    path = [target]
    while path[-1] != source:
        path.append(previous[path[-1]])
    path.reverse()
    simple = [path[0]]
    for i in range(1, len(path) - 1):
        if (path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]) != (
            path[i + 1][0] - path[i][0],
            path[i + 1][1] - path[i][1],
        ):
            simple.append(path[i])
    simple.append(path[-1])
    points = [tuple(start), *[(x0 + i * grid, y0 + j * grid) for i, j in simple], tuple(goal)]
    points = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
    return points, {
        "method": "developer-only single-layer grid; no CAD writes",
        "expanded": expanded,
        "grid_nm": grid,
        "inflation_nm": inflation,
        "bounds": [x0, y0, x0 + width * grid, y0 + height * grid],
    }
