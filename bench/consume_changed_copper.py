"""Compare selected native straight tracks to independent Gerber line records.

Limited to metric, absolute, non-mirrored board-origin X/Y-negated output with
circular apertures. No general Gerber flattening, macros, arcs or net extraction.
"""

from collections import Counter

from consume_engineering_outputs import gerber, sha


def segment(start, end, width):
    return (*sorted((tuple(start), tuple(end))), width)


def consume_tracks(root, native, nets):
    reports = []
    assert native["origin_nm"] == [0, 0], "This bounded consumer requires board origin"
    for layer in ("F.Cu", "B.Cu"):
        tracks = [
            t
            for t in native["track_geometry"]
            if t["net"] in nets and t["kind"] == "PCB_TRACK" and t["layer"] == layer
        ]
        file = next((root / "gerber").glob("*-" + layer.replace(".", "_") + ".gbr"))
        parsed = gerber(file)
        observed = Counter(segment(*row) for row in parsed["straight_strokes"])
        expected = Counter(segment(t["start_nm"], t["end_nm"], t["width_nm"]) for t in tracks)
        missing = expected - observed
        assert not missing, ("Changed copper absent from Gerber", layer, missing)
        reports.append(
            {
                "layer": layer,
                "selected_nets": nets,
                "expected_segments": len(tracks),
                "observed_matching_segments": sum(expected.values()),
                "segments": [
                    {
                        "start_nm": list(key[0]),
                        "end_nm": list(key[1]),
                        "width_nm": key[2],
                        "count": count,
                    }
                    for key, count in sorted(expected.items())
                ],
                "tolerance_nm": 0,
                "file_sha256": sha(file),
                "polarity_scope": parsed["polarity_scope"],
                "unchecked_flash_shapes": parsed["unchecked_flash_shapes"],
                "status": "passed",
            }
        )
    return {
        "status": "passed",
        "layers": reports,
        "not_checked": [
            "Gerber net attributes",
            "arcs/macros/composite mask geometry",
            "unselected copper equivalence",
        ],
    }
