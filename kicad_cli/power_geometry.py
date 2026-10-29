"""Reuse existing routing primitives on captured, disposable files.

This is a domain preparation API, never the public legacy handlers/gates.
Prepared copper is consumed independently before it can reach the source.
"""

from __future__ import annotations

from . import drc_baseline
from .fileformat.drc.settings import NetClasses as ResolvedClasses
from .native import route, widen, write
from .native.project import NetClasses
from .native.smd_drill_guard import clearance_nm, not_requested
from .payload import write_txn


class EffectiveClasses(NetClasses):
    def __init__(self, project):
        super().__init__(project)
        self.resolver = ResolvedClasses(project.get("net_settings") or {})

    def name_for(self, name):
        return self.resolver.of(name).sources["track_width"]

    def params(self, name):
        values = self.resolver.of(name).values
        return tuple(
            values[key] / 1e6 for key in ("track_width", "clearance", "via_diameter", "via_drill")
        )


def prepare_route(path, params, selected):
    """Generate a saved candidate using the existing grid algorithm."""
    write.start()
    gap = params.get("smd-drill-clearance-mm")
    clearance_nm(gap)
    if gap is not None and (params["mode"] != "rewidth" or params.get("engine", "grid") != "grid"):
        raise ValueError("smd-drill-clearance-mm is supported only by grid rewidth")
    layout = route.Layout(str(path), **({"smd_drill_clearance_mm": gap} if gap is not None else {}))
    guard = layout.copper.smd_drill_guard if gap is not None else None
    hole_guard = layout.copper.via_hole_guard if params.get("engine", "grid") == "grid" else None
    project, _ = route.load_project(str(path))
    classes = EffectiveClasses(project)
    neck = float(params.get("neck", 0.2))
    mode = params["mode"]
    before = route.unconnected(layout.board)
    count, baseline = route.errors(str(path), library_checks=False)
    log = {"mode": mode, "engine": "grid"}
    if params.get("engine") == "freerouting":
        result = prepare_freerouting(layout, params, count, baseline, before)
    elif mode == "rewidth":
        result = route._rewidth(
            str(path),
            {**params, "nets": ",".join(selected), "library-checks": False},
            layout,
            classes,
            neck,
            (),
            log,
            count,
            baseline,
            before,
            lambda x: x,
        )
    else:
        if mode == "repair":
            grid = layout.router()
            log["existing_tracks"] = route.paint_existing(grid, layout.pieces)
            route.mode_repair(
                layout,
                grid,
                classes,
                neck,
                log,
                ripup=bool(params.get("ripup")),
                selected=set(selected),
                library_checks=False,
            )
        else:
            old = list(layout.pieces)
            for piece in old:
                layout.remove(piece)
            log["cleared_tracks"] = len(old)
            route.mode_full(
                layout,
                layout.router(),
                classes,
                neck,
                log,
                use_planes=bool(params.get("use-planes")),
            )
        final = layout.fill_and_save()
        after_count, comparison = route.verify_baseline(
            str(path), count, baseline, library_checks=False
        )
        result = {
            **log,
            "unconnected_before": before,
            "unconnected_after": route.unconnected(final),
            "verify": {
                "errors_baseline": count,
                "errors_final": after_count,
                "finding_comparison": comparison,
            },
        }
    if guard is not None:
        saved = route.Layout(str(path), smd_drill_guard=guard)
        _ = saved.copper  # validate actual re-read pad material against the planning instance
        guard.check_selected_vias(saved.board, set(selected))
    if hole_guard is not None:
        saved = route.Layout(str(path), via_hole_guard=hole_guard)
        _ = saved.copper
        hole_guard.check_selected_vias(saved.board, set(selected))
        result["via_hole_spacing"] = hole_guard.report()
    result["smd_drill_clearance"] = guard.report() if guard is not None else not_requested()
    write_txn.commit()
    return result


def prepare_freerouting(layout, params, count, baseline, before):
    """Existing external router primitives, on the owned disposable candidate."""
    from .fileformat import specctra
    from .fileformat.drc.settings import Settings
    from .native import freeroute

    settings = Settings.of(layout.board.path.with_suffix(".kicad_pro"))
    dsn = specctra.Dsn(layout.board, settings)
    scratch = layout.board.path.parent.parent / "freerouting"
    scratch.mkdir(exist_ok=False)
    dsn_path, ses_path = scratch / "board.dsn", scratch / "board.ses"
    dsn_path.write_text(dsn.text(str(dsn_path), "10.0"), encoding="utf-8")
    policy = params.get("layer-policy", "balanced")
    output = freeroute.run_freerouting(
        params["java"],
        params["jar"],
        str(dsn_path),
        str(ses_path),
        params.get("passes", 10),
        1800,
        str(scratch),
        policy,
    )
    (scratch / "router.log").write_text(output, encoding="utf-8")
    session = specctra.read_session(ses_path.read_text(encoding="utf-8"))
    freeroute.apply_session(layout, session, {name: layer for layer, name in dsn.names.items()})
    freeroute.restore_min_width(layout, settings.nm("min_track_width"))
    final = layout.fill_and_save()
    after_count, comparison = route.verify_baseline(
        str(layout.path), count, baseline, library_checks=False
    )
    return {
        "mode": "rebuild",
        "engine": "freerouting",
        "layer_policy": policy,
        "unconnected_before": before,
        "unconnected_after": route.unconnected(final),
        "verify": {
            "errors_baseline": count,
            "errors_final": after_count,
            "finding_comparison": comparison,
        },
        "router_artifacts": str(scratch),
    }


def prepare_widen(path, selected):
    """Reuse width trials, with identity gates rather than count-only acceptance."""
    write.start()
    board = widen.load_board(str(path))
    project, _ = widen.load_project(str(path))
    classes = EffectiveClasses(project)
    keys = [widen.key_of(t.start, t.end, t.layer) for t in widen.segments(board)]
    if len(keys) != len(set(keys)):
        raise ValueError("Width trial cannot disambiguate overlapping rounded segment keys")
    original = {
        widen.key_of(t.start, t.end, t.layer): t.width / 1e6
        for t in widen.segments(board)
        if t.net in selected
    }
    target = {
        widen.key_of(t.start, t.end, t.layer): classes.params(t.net)[0]
        for t in widen.segments(board)
        if t.net in selected
    }
    _, baseline = drc_baseline.capture(path)
    settled, rounds = dict(original), []
    for width in sorted(set(target.values()), reverse=True):
        pending = {key for key in target if target[key] == width and settled[key] < width - 1e-6}
        for fraction in widen.STEPS:
            trial = {
                key: round(target[key] * fraction, 2)
                for key in pending
                if round(target[key] * fraction, 2) > settled[key] + 1e-6
            }
            if not trial:
                continue
            widen.set_widths(str(path), trial)
            _, report = drc_baseline.capture(path)
            comparison = drc_baseline.compare(baseline, report)
            kept = dict(trial)
            # Bisect a failed trial, retaining accepted earlier widths. Equality
            # of total finding counts is deliberately insufficient.
            while comparison["status"] != "passed" and kept:
                if comparison["status"] == "incomplete":
                    raise ValueError("Widen candidate finding comparison incomplete")
                bad = set(sorted(kept)[: max(1, len(kept) // 2)])
                widen.set_widths(str(path), {key: settled[key] for key in bad})
                for key in bad:
                    kept.pop(key)
                _, report = drc_baseline.capture(path)
                comparison = drc_baseline.compare(baseline, report)
            settled.update(kept)
            rounds.append(
                {
                    "fraction": fraction,
                    "tried": len(trial),
                    "kept": len(kept),
                    "finding_status": comparison["status"],
                }
            )
            pending = set(trial) - set(kept)
    write_txn.commit()
    return {"mode": "widen", "rounds": rounds, "ampacity_assessed": False}
