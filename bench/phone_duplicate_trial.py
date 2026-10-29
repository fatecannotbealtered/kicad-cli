"""Exact-copper subset cleanup via existing public route patch, not a new command."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

from functional_support import REPO, Case, put, sha

sys.path.insert(0, str(REPO))
from kicad_cli.fileformat.board import Board
from kicad_cli.fileformat.sexpr import render
from kicad_cli.module_project import compare_native

SOURCE_ZIP = Path(
    "D:/personalSpace/phone-design-20261025/delivery/compression-trials-r02-lite/phone-a1-review-delivery-r02-lite.zip"
)


def main(directory, binary=None):
    t = Case(directory, binary)
    assert sha(SOURCE_ZIP) == "6136585088cc76ebf1f15dc86de6e1141a4e5a3f85c8dde856e2d877fcda4ab1"
    with zipfile.ZipFile(SOURCE_ZIP) as archive:
        prefix = "PHONE-A1-ENGINEERING-REVIEW-r02-LITE/"
        for item in archive.infolist():
            if item.filename.startswith(prefix + "cad/") and not item.is_dir():
                target = (t.root / "phone" / item.filename[len(prefix) :]).resolve()
                assert target.is_relative_to(t.root)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(item))
    board = t.root / "phone/cad/main/PHONE_A1_MAIN.kicad_pcb"
    assert sha(board) == "0bc266ab2fc24e353fe62eb590d132bcc00bf87c5d60661f764060bb5860c0e8"
    net = "MODEM_NET_STATUS_1V8"
    observed = t.cli("board", "routing", "inspect", "--board", board, "--net", net)["observation"]
    put(t.root / "DIAGNOSIS-BEFORE.json", observed)
    parsed = Board.load(board)
    tracks = {s.node.find("uuid").value(1): s for s in parsed.tracks}

    def attributes(node):
        return [render(x) for x in node.lists() if x.head != "uuid"]

    targets = []
    removed = []
    for group in observed["duplicate_groups"]:
        if group["net"] != net or len(group["ids"]) != 2:
            continue
        keep, extra = group["ids"]
        if attributes(tracks[keep].node) != attributes(tracks[extra].node):
            continue
        targets.append(
            {
                "kept": keep,
                "removed": extra,
                "key": {k: group[k] for k in ("net", "layer", "width_nm", "endpoints_nm")},
                "basis": "same complete segment attributes except UUID; "
                "retained identical copper preserves every contact",
            }
        )
        removed.append(extra)
        if len(targets) == 5:
            break
    assert len(targets) == 5
    first = tracks[targets[0]["kept"]]
    context = t.cli("project", "context", "--board", board)["observation"]["context"]
    spec = {
        "baseline": context,
        "net": net,
        "remove_ids": removed,
        "allow_locked_ids": removed,
        "allowed_layers": sorted({tracks[i].layer for i in removed}),
        "bounds_nm": [0, 0, 75000000, 165000000],
        "endpoints": [
            {
                "object_uuid": targets[0]["kept"],
                "terminal": term,
                "at_nm": point,
                "layer": first.layer,
            }
            for term, point in [("start", first.start), ("end", first.end)]
        ],
        "segments": [],
        "minimum_improvement_mm": 0,
    }
    put(
        t.root / "TARGETS-BEFORE-PREVIEW.json",
        {
            "targets": targets,
            "request": spec,
            "excluded": "partial overlap, coincident different width, normal branch, "
            "testpad path or purpose-different objects; no vias deleted",
        },
    )
    request = t.root / "dedup.json"
    put(request, spec)
    original = board.read_bytes()
    before_files = {
        p.relative_to(t.root / "phone").as_posix(): sha(p)
        for p in (t.root / "phone").rglob("*")
        if p.is_file()
    }
    preview, result = t.write("board", "route", "patch", "--board", board, "--spec", request)
    fresh = t.cli("board", "routing", "inspect", "--board", board, "--net", net)["observation"]
    after = Board.load(board)

    def key(s):
        return s.net, s.layer, s.width, *sorted((s.start, s.end))

    assert set(map(key, parsed.tracks)) == set(map(key, after.tracks))
    before_protected = [
        render(n)
        for n in parsed.root.lists()
        if not (n.find("uuid") is not None and n.find("uuid").value(1) in removed)
    ]
    assert before_protected == [render(n) for n in after.root.lists()]
    changed = [n for n, h in before_files.items() if sha(t.root / "phone" / n) != h]
    assert changed == ["cad/main/PHONE_A1_MAIN.kicad_pcb"]
    assert fresh["extra_objects"] == observed["extra_objects"] - 5
    native = t.root / "native-reopened.json"
    t.native("pcb", "drc", "--format", "json", "--severity-all", "-o", native, board)
    report = json.loads(native.read_bytes())
    task = result["receipt"]["extensions"]["task"]
    check = {
        "report": report,
        "scope": task["native_after"]["scope"],
        "project_exclusions": task["native_after"]["project_exclusions"],
    }
    comparison = compare_native(task["native_before"], check)
    assert comparison["status"] == "passed"
    assert not [
        r
        for group in ("violations", "unconnected_items")
        for r in report[group]
        if r["severity"] == "error"
    ]
    put(
        t.root / "RESULT.json",
        {
            "targets": targets,
            "original_board_sha256": hashlib.sha256(original).hexdigest(),
            "saved_sha256": sha(board),
            "removed_objects": 5,
            "before_groups": observed["duplicate_group_count"],
            "after_groups": fresh["duplicate_group_count"],
            "before_extras": observed["extra_objects"],
            "after_extras": fresh["extra_objects"],
            "geometry_union_unchanged": True,
            "protected_objects_unchanged": True,
            "changed_files": changed,
            "source_rules_unchanged": True,
            "native_finding_comparison": comparison,
            "native_reports": {"before": task["native_before"]["report"], "after": report},
            "status": "saved and reopened; exact subset only; "
            "no automatic purpose or manufacturing approval",
        },
    )
    print(t.root)


if __name__ == "__main__":
    main(*sys.argv[1:])
