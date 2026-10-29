"""Read-only native hole/material observation; no router or product imports.

--qualify creates only in-memory BOARD/PAD/PCB_VIA objects. It never saves a
board, alters a project, fills zones or starts another process. The controller
must freeze this script, native installation and observation plan beforehand.
"""

import sys

if __name__ == "__main__" and (not sys.flags.no_site or not sys.flags.dont_write_bytecode):
    raise SystemExit("native_hole_read requires Python -B -S")

import os

_NATIVE_BIN = os.path.dirname(os.path.abspath(sys.executable))
_NATIVE_SITE = os.path.join(_NATIVE_BIN, "Lib", "site-packages")
_EXPECTED_PATH = [os.path.join(_NATIVE_BIN, "Lib"), os.path.join(_NATIVE_BIN, "DLLs"),
                  os.path.join(_NATIVE_BIN, f"python{sys.version_info.major}{sys.version_info.minor}.zip"), _NATIVE_SITE]
_DLL_HANDLE = None
if __name__ == "__main__":
    sys.path[:] = _EXPECTED_PATH
    if os.name == "nt":
        _DLL_HANDLE = os.add_dll_directory(_NATIVE_BIN)

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

MARGIN_NM = 200000
QUERY_CLEARANCE_NM = 200001
QUAL_CASES = {
    "rect_same_net_hole_inside": (True, True),
    "rect_same_net_center_outside_edge_intersects": (True, True),
    "rect_gap_199999nm": (False, True),
    "rect_gap_200000nm_guarded": (False, True),
    "rect_gap_200002nm": (False, False),
    "rect_other_net_hole_inside": (True, True),
    "roundrect_corner_material_absent": (False, False),
    "roundrect_corner_material_present": (True, True),
    "roundrect_edge_gap_200002nm": (False, False),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def xy(point):
    return [int(point.x), int(point.y)]


def one_layer(pcbnew, layer):
    """KiCad 10.0.6 exposes default LSET plus its AddLayer wrapper."""
    selected = pcbnew.LSET()
    selected.AddLayer(layer)
    return selected


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def module_sources():
    root, this = Path(_NATIVE_BIN).resolve(), Path(__file__).resolve()
    result = []
    for name, module in sorted(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if not filename:
            origin = getattr(getattr(module, "__spec__", None), "origin", None)
            if name == "swig_runtime_data4":
                provider = Path(sys.modules["_pcbnew"].__file__).resolve()
                need(provider == root / "Lib/site-packages/_pcbnew.pyd", "Unbound SWIG provider")
                result.append({"module": name, "origin": "SWIG registration from bound _pcbnew.pyd", "keys": sorted(vars(module)), "provider": str(provider), "provider_sha256": sha(provider)})
                continue
            if name in {"typing.io", "typing.re"}:
                provider = Path(sys.modules["typing"].__file__).resolve()
                need(vars(sys.modules["typing"])[name.removeprefix("typing.")] is module and provider == root / "Lib/typing.py", "Unbound typing alias")
                result.append({"module": name, "origin": "exact alias in bound standard-library typing", "provider": str(provider), "provider_sha256": sha(provider)})
                continue
            need(origin in {"built-in", "frozen"}, "Unbound source-free module: " + name)
            result.append({"module": name, "origin": origin})
            continue
        path = Path(filename).resolve()
        need(path == this or root in path.parents, "Undeclared native module: " + name)
        if path.is_file():
            result.append({"module": name, "path": str(path), "sha256": sha(path)})
        else:
            archive = root / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
            need(archive in path.parents and archive.is_file(), "Unavailable module source: " + name)
            result.append({"module": name, "path": str(path), "archive_sha256": sha(archive)})
    return result


def startup_identity():
    need(list(sys.path) == _EXPECTED_PATH and "sitecustomize" not in sys.modules and "usercustomize" not in sys.modules, "Native startup drift")
    return {"no_site": bool(sys.flags.no_site), "dont_write_bytecode": bool(sys.flags.dont_write_bytecode),
            "actual_sys_path": list(sys.path), "explicit_site_packages": _NATIVE_SITE,
            "sitecustomize_or_usercustomize_loaded": False, "Documents_KiCad_thirdparty_consulted": False,
            "normal_site_addsitedir_executed": False,
            "native_bin_dll_directory": _NATIVE_BIN if _DLL_HANDLE is not None else None,
            "actual_module_sources": module_sources()}


def box(shape):
    bb = shape.BBox()
    return [int(bb.GetLeft()), int(bb.GetTop()), int(bb.GetRight()), int(bb.GetBottom())]


def pad_geometry(pad, board, pcbnew):
    """Finite fixture scope; unknown/rotated/special padstacks are not accepted."""
    need(pad.GetAttribute() == pcbnew.PAD_ATTRIB_SMD, "Unsupported non-SMD pad")
    need(pad.Padstack().Mode() == pcbnew.PADSTACK.MODE_NORMAL, "Unsupported special SMD padstack mode")
    need(pad.GetShape() in {pcbnew.PAD_SHAPE_RECTANGLE, pcbnew.PAD_SHAPE_ROUNDRECT}, "Unsupported SMD material shape")
    need(pad.GetOrientationDegrees() == 0 and xy(pad.GetOffset()) == [0, 0], "Unsupported rotated/offset SMD pad")
    need(xy(pad.GetDrillSize()) == [0, 0], "SMD pad unexpectedly has a drilled hole")
    layers = [layer for layer in pad.GetLayerSet().Seq() if board.GetLayerName(layer).endswith(".Cu")]
    need(layers == [pcbnew.F_Cu], "Unsupported SMD copper layer or special padstack")
    need(pad.GetShape(pcbnew.F_Cu) == pad.GetShape() and xy(pad.GetSize(pcbnew.F_Cu)) == xy(pad.GetSize()), "Layer-specific pad geometry differs")
    shape = pad.GetEffectiveShape(pcbnew.F_Cu)
    need(shape is not None, "Missing native effective SMD material shape")
    radius = None
    if pad.GetShape() == pcbnew.PAD_SHAPE_ROUNDRECT:
        radius = int(pad.GetRoundRectCornerRadius())
        need(radius > 0 and 0 < pad.GetRoundRectRadiusRatio() <= 0.5, "Invalid native roundrect shape")
    return {"uuid": pad.m_Uuid.AsString(), "ref": pad.GetParentFootprint().GetReference(), "number": pad.GetNumber(),
            "net": pad.GetNetname(), "position_nm": xy(pad.GetPosition()), "size_nm": xy(pad.GetSize()),
            "shape": int(pad.GetShape()), "attribute": int(pad.GetAttribute()), "rotation_deg": pad.GetOrientationDegrees(),
            "offset_nm": xy(pad.GetOffset()), "drill_nm": xy(pad.GetDrillSize()),
            "copper_layers": ["F.Cu"], "corner_radius_nm": radius,
            "material_bbox_nm": box(shape)}, shape, layers


def via_geometry(via, board, pcbnew):
    need(via.GetViaType() == pcbnew.VIATYPE_THROUGH, "Unsupported non-through via")
    need(via.Padstack().Mode() == pcbnew.PADSTACK.MODE_NORMAL, "Unsupported special via padstack mode")
    layers = [layer for layer in via.GetLayerSet().Seq() if board.GetLayerName(layer).endswith(".Cu")]
    need(set(layers) == {pcbnew.F_Cu, pcbnew.B_Cu}, "Unsupported through-via copper layers")
    center, drill = xy(via.GetPosition()), int(via.GetDrillValue())
    need(drill > 0 and drill % 2 == 0, "Unsupported nonpositive or odd-nm circular drill")
    hole = via.GetEffectiveHoleShape()
    need(hole is not None, "Missing native effective drill shape")
    expected_box = [center[0] - drill // 2, center[1] - drill // 2, center[0] + drill // 2, center[1] + drill // 2]
    need(xy(hole.GetCenter()) == center and box(hole) == expected_box, "Native effective hole does not match the saved ordinary round drill")
    widths = {board.GetLayerName(layer): int(via.GetWidth(layer)) for layer in layers}
    need(all(width > drill for width in widths.values()), "Invalid via copper ring dimensions")
    return {"uuid": via.m_Uuid.AsString(), "net": via.GetNetname(), "position_nm": center,
            "drill_nm": drill, "hole_radius_nm": drill // 2, "hole_bbox_nm": box(hole),
            "via_type": int(via.GetViaType()), "through_via": True, "copper_layers": ["F.Cu", "B.Cu"],
            "width_by_layer_nm": widths}, hole, layers


def pair_observation(via_row, hole, pad_row, material, layer_name):
    return {"via_uuid": via_row["uuid"], "pad_uuid": pad_row["uuid"], "pad_ref": pad_row["ref"], "pad_number": pad_row["number"],
            "via_net": via_row["net"], "pad_net": pad_row["net"], "layer": layer_name,
            "native_hole_intersects_material": bool(hole.Collide(material, 0)),
            "native_hole_with_margin_intersects_material": bool(hole.Collide(material, QUERY_CLEARANCE_NM)),
            "collision_query_clearance_nm": QUERY_CLEARANCE_NM}


def observe_board(board, pcbnew):
    pads, vias, pairs, incomplete = [], [], [], []
    material_objects = []
    need(board.GetCopperLayerCount() == 2, "Only a two-layer board is supported")
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            try:
                row, shape, layers = pad_geometry(pad, board, pcbnew)
                pads.append(row)
                material_objects.append((row, shape, layers))
            except Exception as exc:
                incomplete.append({"object_uuid": pad.m_Uuid.AsString(), "reason": type(exc).__name__ + ": " + str(exc)})
    for track in board.GetTracks():
        if not isinstance(track, pcbnew.PCB_VIA):
            if track.GetClass() != "PCB_TRACK":
                incomplete.append({"object_uuid": track.m_Uuid.AsString(), "reason": "Unsupported nonstraight copper class"})
            continue
        try:
            row, hole, layers = via_geometry(track, board, pcbnew)
            vias.append(row)
            for pad_row, shape, pad_layers in material_objects:
                for layer in pad_layers:
                    need(layer in layers, "Through hole did not traverse the actual SMD copper layer")
                    pairs.append(pair_observation(row, hole, pad_row, shape, board.GetLayerName(layer)))
        except Exception as exc:
            incomplete.append({"object_uuid": track.m_Uuid.AsString(), "reason": type(exc).__name__ + ": " + str(exc)})
    return {"status": "incomplete" if incomplete else "observed", "pads": sorted(pads, key=lambda r: r["uuid"]),
            "vias": sorted(vias, key=lambda r: r["uuid"]),
            "pairs": sorted(pairs, key=lambda r: (r["via_uuid"], r["pad_uuid"], r["layer"])), "incomplete_reasons": incomplete}


def qualify_api(pcbnew):
    """Independent known geometry; no saved engine result is used as truth."""
    board = pcbnew.BOARD()
    fp = pcbnew.FOOTPRINT(board)
    fp.SetReference("QUAL1")
    board.Add(fp)
    net = pcbnew.NETINFO_ITEM(board, "QUAL-SAME", 1)
    other = pcbnew.NETINFO_ITEM(board, "QUAL-OTHER", 2)
    board.Add(net)
    board.Add(other)
    pad = pcbnew.PAD(fp)
    fp.Add(pad)
    pad.SetNumber("1")
    pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    pad.SetLayerSet(one_layer(pcbnew, pcbnew.F_Cu))
    pad.SetPosition(pcbnew.VECTOR2I(0, 0))
    pad.SetSize(pcbnew.VECTOR2I(1000000, 1000000))
    pad.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
    pad.SetNetCode(1)
    via = pcbnew.PCB_VIA(board)
    board.Add(via)
    via.SetViaType(pcbnew.VIATYPE_THROUGH)
    via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
    via.SetWidth(600000)
    via.SetDrill(300000)
    via.SetNetCode(1)
    cases = []
    def run(name, center, rounded=False, drill=300000, netcode=1):
        pad.SetShape(pcbnew.PAD_SHAPE_ROUNDRECT if rounded else pcbnew.PAD_SHAPE_RECTANGLE)
        if rounded:
            pad.SetRoundRectRadiusRatio(0.25)
        via.SetDrill(drill)
        via.SetNetCode(netcode)
        via.SetPosition(pcbnew.VECTOR2I(*center))
        prow, material, _ = pad_geometry(pad, board, pcbnew)
        vrow, hole, _ = via_geometry(via, board, pcbnew)
        pair = pair_observation(vrow, hole, prow, material, "F.Cu")
        actual = (pair["native_hole_intersects_material"], pair["native_hole_with_margin_intersects_material"])
        cases.append({"id": name, "expected": list(QUAL_CASES[name]), "actual": list(actual),
                      "passed": actual == QUAL_CASES[name], "pad": prow, "via": vrow, "pair": pair})
    run("rect_same_net_hole_inside", [0, 0])
    run("rect_same_net_center_outside_edge_intersects", [600000, 0])
    run("rect_gap_199999nm", [849999, 0])
    run("rect_gap_200000nm_guarded", [850000, 0])
    run("rect_gap_200002nm", [850002, 0])
    run("rect_other_net_hole_inside", [0, 0], netcode=2)
    # At the outer square corner a 20 um hole misses the rounded material.
    # Gap to the r=250um corner is sqrt(250^2+250^2)-250-10=93.55um;
    # it therefore fails a 0.2mm margin. Use a deliberately much smaller pad-
    # local query below to qualify material-vs-bbox, while the accepted router
    # scope remains the common 0.2mm guarded test.
    pad.SetShape(pcbnew.PAD_SHAPE_ROUNDRECT)
    pad.SetRoundRectRadiusRatio(0.25)
    via.SetDrill(20000)
    via.SetPosition(pcbnew.VECTOR2I(500000, 500000))
    prow, material, _ = pad_geometry(pad, board, pcbnew)
    vrow, hole, _ = via_geometry(via, board, pcbnew)
    actual = (bool(hole.Collide(material, 0)), bool(hole.Collide(material, 50000)))
    cases.append({"id": "roundrect_corner_material_absent", "expected": [False, False], "actual": list(actual), "passed": actual == (False, False),
                  "query_clearance_nm": 50000, "pad": prow, "via": vrow})
    run("roundrect_corner_material_present", [425000, 425000], rounded=True, drill=200000)
    run("roundrect_edge_gap_200002nm", [850002, 0], rounded=True)
    rejected = []
    for name, change, undo in [
        ("back_layer_is_not_assumed_front", lambda: pad.SetLayerSet(one_layer(pcbnew, pcbnew.B_Cu)), lambda: pad.SetLayerSet(one_layer(pcbnew, pcbnew.F_Cu))),
        ("unread_shape_is_incomplete", lambda: pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE), lambda: pad.SetShape(pcbnew.PAD_SHAPE_RECTANGLE)),
        ("rotated_pad_is_incomplete", lambda: pad.SetOrientationDegrees(30), lambda: pad.SetOrientationDegrees(0)),
        ("special_padstack_is_incomplete", lambda: pad.Padstack().SetMode(pcbnew.PADSTACK.MODE_CUSTOM), lambda: pad.Padstack().SetMode(pcbnew.PADSTACK.MODE_NORMAL)),
    ]:
        change()
        reason = None
        try:
            pad_geometry(pad, board, pcbnew)
        except Exception as exc:
            reason = str(exc)
        finally:
            undo()
        rejected.append({"id": name, "passed": reason is not None, "reason": reason})
    return {"schema": "dual-rc-hole-native-api-qualification/v1", "status": "passed" if all(r["passed"] for r in cases + rejected) else "failed",
            "cases": cases, "unsupported_cases": rejected, "scope": "Only the declared in-memory shapes, layers, circular drill and integer-nm boundaries; no engineering file writes", "engineering_writes": 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qualify", action="store_true")
    parser.add_argument("--board", type=Path)
    parser.add_argument("--intent", type=Path)
    args = parser.parse_args()
    import pcbnew
    common = {"native_version": pcbnew.GetBuildVersion(), "reader_sha256": sha(__file__), "pcbnew_wrapper_sha256": sha(pcbnew.__file__)}
    if args.qualify:
        need(args.board is None and args.intent is None, "Qualification does not consume an engineering project")
        result = qualify_api(pcbnew)
    else:
        need(args.board is not None and args.intent is not None, "Both --board and frozen --intent are required")
        path, intent_path = args.board.resolve(strict=True), args.intent.resolve(strict=True)
        initial, project, intent_sha = sha(path), path.with_suffix(".kicad_pro"), sha(intent_path)
        project_sha = sha(project)
        intent = json.loads(intent_path.read_bytes())
        policy = intent["replacement_contract"]["via_hole_avoidance"]
        need(policy["minimum_material_gap_nm"] == MARGIN_NM and policy["native_collision_query_clearance_nm"] == QUERY_CLEARANCE_NM, "Frozen synthetic margin mismatch")
        manager = pcbnew.SETTINGS_MANAGER()
        need(manager.LoadProject(str(project), False), "Native LoadProject failed")
        native_project = manager.GetProject(str(project))
        board = pcbnew.LoadBoard(str(path))
        need(board is not None, "Native LoadBoard failed")
        board.SetProject(native_project)
        result = observe_board(board, pcbnew)
        board.ClearProject()
        need(manager.UnloadProject(native_project, False), "Native UnloadProject failed")
        need(sha(path) == initial and sha(project) == project_sha and sha(intent_path) == intent_sha, "Read-only hole observation inputs changed")
        result.update(schema="dual-rc-hole-material-read/v4", input_sha256=initial, project_sha256=project_sha,
                      observation_intent_sha256=intent_sha, input_unchanged=True, project_unchanged=True,
                      minimum_material_gap_nm=MARGIN_NM, collision_query_clearance_nm=QUERY_CLEARANCE_NM,
                      oracle="KiCad PCB_VIA.GetEffectiveHoleShape vs PAD.GetEffectiveShape(actual F.Cu), native SHAPE.Collide; same-net included",
                      engineering_writes=0)
    result.update(common, startup=startup_identity())
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"passed", "observed"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
