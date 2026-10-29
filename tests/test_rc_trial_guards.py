"""Binding/finite-geometry controls, distinct from native RC engineering acceptance."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

METHODS = Path(__file__).resolve().parents[1] / "bench/rc_trial/execution"


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


binding = module(METHODS / "review_binding.py", "rc_review_binding_test")
gerber = module(METHODS / "consumers/gerber_subset.py", "rc_gerber_test")


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def reviewed(tmp_path):
    base, packet, work = (tmp_path / name for name in ("methods", "packet", "work"))
    for folder in (
        base / "consumers",
        packet / "requirements",
        work / "project",
        work / "configuration",
        work / "plans",
    ):
        folder.mkdir(parents=True)
    for name in ("rc4.kicad_sch", "rc4.kicad_pcb", "rc4.kicad_pro"):
        (work / "project" / name).write_text("binding-only fixture " + name)
    live = tmp_path / "live-originals"
    live.mkdir()
    (live / "design.kicad_sch").write_text("live source, review copy separate")
    (work / "configuration/config.json").write_text("{}")
    for name in (
        "baseline-layout-v2.json",
        "zone-ids.json",
        "eco-layout.json",
        "eco-absolute-target.json",
    ):
        put(work / "plans" / name, {"fixture": name})
    dependency = tmp_path / "owned-dependency.bin"
    dependency.write_bytes(b"binding-only dependency; not a native executable")
    (tmp_path / "_driver_sdk").mkdir()
    (tmp_path / "driver_spec").mkdir()
    (tmp_path / "_driver_sdk/__init__.py").write_text("# fixture")
    (tmp_path / "driver_spec/PIN.json").write_text("{}")
    info = {"path": str(dependency), "sha256": binding.sha(dependency)}
    put(
        packet / "DEPENDENCIES.json",
        {
            "environment": {
                "KICAD_CLI_DRIVER_SDK_PATH": str(tmp_path / "_driver_sdk"),
                **{
                    name: str(dependency)
                    for name in ("KICAD_CLI_PYTHON", "KICAD_CLI_OFFICIAL", "KICAD_CLI_SCOPE_FILE")
                },
            },
            "readonly_consumer_python": str(dependency),
            "native_files": {name: info for name in ("python", "official", "pcbnew", "_pcbnew")},
            "libraries": {"fixture": info},
            "bound_sources": {"originals": {"kind": "directory", "path": str(live)}},
        },
    )
    put(
        work / "plans/candidate-selection.json",
        {"candidate_path": str(dependency), "sha256": binding.sha(dependency)},
    )
    review = work / "artifacts/eco-review"
    start = binding.begin(work, base, packet, review, "eco")
    for name in (*binding.REPORTS, "preservation.json", "native-current.net"):
        target = review / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            b"\x89PNG\r\n\x1a\nfixture"
            if name in binding.IMAGES
            else b"binding-only checked artifact"
        )
    evidence = work / "record.json"
    put(evidence, {"review_id": start["review_id"], "binding_unit_fixture": True})
    steps = [
        {
            "check": name,
            "review_id": start["review_id"],
            "passed": True,
            "evidence_path": str(evidence),
            "evidence_sha256": binding.sha(evidence),
        }
        for name in (
            "native-xml",
            "independent-xml",
            "reopened",
            "geometry",
            "erc",
            "drc",
            "schematic-svg",
            "schematic-raster",
            "front-svg",
            "front-raster",
            "back-svg",
            "back-raster",
            "preservation",
            "current-sexpr",
        )
    ]
    ready = binding.complete(work, base, packet, review, steps)
    put(
        review / "VISUAL_REVIEW.json",
        {
            "passed": True,
            "review_id": ready["review_id"],
            "ready_sha256": binding.sha(review / "READY.json"),
            "images": ready["images"],
            "reviewer": "binding-unit-fixture",
            "observations": {
                name: "Synthetic binding-only assertion; not an actual visual acceptance"
                for name in binding.IMAGES
            },
        },
    )
    return work, base, packet, review


@pytest.mark.parametrize("drift", ["changed-schematic", "added-config", "removed-source"])
def test_live_source_drift_refused_when_review_copies_are_unchanged(reviewed, drift):
    work, base, packet, review = reviewed
    binding.verify(*reviewed)
    live = Path(binding.read(packet / "DEPENDENCIES.json")["bound_sources"]["originals"]["path"])
    copied = binding.tree(work / "project")
    if drift == "changed-schematic":
        (live / "design.kicad_sch").write_text("changed real design after visual pause")
    elif drift == "added-config":
        (live / "design.kicad_dru").write_text("new project rules")
    else:
        (live / "design.kicad_sch").unlink()
    with pytest.raises(binding.ReviewError, match="changed"):
        binding.verify(*reviewed)
    assert binding.tree(work / "project") == copied


def test_current_review_freezes_identical_checked_inputs(reviewed):
    work, base, packet, review = reviewed
    binding.verify(*reviewed)
    snapshot = work / "snapshot"
    result = binding.freeze(*reviewed, snapshot)
    assert binding.verify_snapshot(snapshot) == result
    assert (snapshot / "project/rc4.kicad_sch").read_bytes() == (
        work / "project/rc4.kicad_sch"
    ).read_bytes()
    assert result["accepted_ready_sha256"] == binding.sha(review / "READY.json")


@pytest.mark.parametrize(
    "change",
    [
        "schematic-only",
        "configuration-change",
        "configuration-add",
        "configuration-delete",
        "native-dependency",
        "SDK-add",
        "checker-code",
    ],
)
def test_pause_input_drift_refuses_continue_and_freeze(reviewed, change):
    work, base, packet, review = reviewed
    board_hash = binding.sha(work / "project/rc4.kicad_pcb")
    if change == "schematic-only":
        (work / "project/rc4.kicad_sch").write_text("C2=330nF after review")
    elif change == "configuration-change":
        (work / "configuration/config.json").write_text('{"changed":true}')
    elif change == "configuration-add":
        (work / "configuration/added.json").write_text("{}")
    elif change == "configuration-delete":
        (work / "configuration/config.json").unlink()
    elif change == "native-dependency":
        Path(binding.read(packet / "DEPENDENCIES.json")["readonly_consumer_python"]).write_bytes(
            b"changed"
        )
    elif change == "SDK-add":
        (work.parent / "_driver_sdk/extra.py").write_text("# changed dependency set")
    else:
        (base / "consumers/new-code.py").write_text("# checker changed")
    for action in (
        lambda: binding.verify(*reviewed),
        lambda: binding.freeze(*reviewed, work / "snapshot"),
    ):
        with pytest.raises(binding.ReviewError):
            action()
    assert binding.sha(work / "project/rc4.kicad_pcb") == board_hash
    assert not (work / "snapshot").exists()


@pytest.mark.parametrize(
    "change",
    [
        "missing-review",
        "false",
        "truthy-string",
        "truthy-integer",
        "old-review-id",
        "missing-image",
        "extra-image",
        "replaced-image",
        "missing-image-file",
        "missing-observation",
        "refreshed-start",
        "missing-check",
    ],
)
def test_visual_verdict_must_bind_every_current_image_and_complete_cycle(reviewed, change):
    work, base, packet, review = reviewed
    path = review / "VISUAL_REVIEW.json"
    verdict = binding.read(path)
    if change == "missing-review":
        path.unlink()
    elif change in {"false", "truthy-string", "truthy-integer"}:
        verdict["passed"] = {"false": False, "truthy-string": "true", "truthy-integer": 1}[change]
    elif change == "old-review-id":
        verdict["review_id"] = "old"
    elif change == "missing-image":
        verdict["images"].pop("schematic.png")
    elif change == "extra-image":
        verdict["images"]["extra.png"] = "0" * 64
    elif change == "replaced-image":
        (review / "front.png").write_bytes(b"another image")
    elif change == "missing-image-file":
        (review / "back.png").unlink()
    elif change == "missing-observation":
        verdict["observations"].pop("schematic.png")
    elif change == "refreshed-start":
        start = binding.read(review / "START.json")
        start["review_id"] = "refreshed-digest-is-not-a-new-check"
        put(review / "START.json", start)
    else:
        ready = binding.read(review / "READY.json")
        ready["checks"] = []
        put(review / "READY.json", ready)
        verdict["ready_sha256"] = binding.sha(review / "READY.json")
    if change != "missing-review":
        put(path, verdict)
    with pytest.raises(binding.ReviewError):
        binding.verify(*reviewed)


def test_new_cycle_cannot_inherit_previous_visual_pass(reviewed):
    work, base, packet, review = reviewed
    with pytest.raises(FileExistsError):
        binding.begin(work, base, packet, review, "eco")
    ready = binding.read(review / "READY.json")
    ready["review_id"] = "other-cycle"
    put(review / "READY.json", ready)
    with pytest.raises(binding.ReviewError):
        binding.verify(*reviewed)


@pytest.mark.parametrize("relative", ["project/rc4.kicad_sch", "review/front.png"])
def test_refreshing_frozen_manifest_cannot_inherit_a_pass(reviewed, relative):
    work, base, packet, review = reviewed
    snapshot = work / "snapshot"
    binding.freeze(*reviewed, snapshot)
    (snapshot / relative).write_bytes(b"changed after accepted review")
    manifest = binding.read(snapshot / "MANIFEST.json")
    manifest["files"][relative] = binding.sha(snapshot / relative)
    put(snapshot / "MANIFEST.json", manifest)
    with pytest.raises(binding.ReviewError):
        binding.verify_snapshot(snapshot)


EDGE = (
    "%FSLAX46Y46*%\n%MOMM*%\n%LPD*%\n%ADD10C,0.100000*%\nG01*\nD10*\nX0Y0D02*\n"
    "X10000000Y0D01*\nX10000000Y10000000D01*\nX0Y10000000D01*\nX0Y0D01*\n"
    "M02*\n"
)


@pytest.mark.parametrize(
    "attribute",
    [
        "",
        "%TF.FilePolarity,Positive*%\n",
        "G04 #@! TF.FilePolarity,Positive*\n",
        "G04 ordinary annotation*\n",
    ],
)
def test_supported_straight_edge_positive(attribute):
    observed = gerber.gerber(attribute + EDGE)
    assert not gerber.outline_problems(observed)
    assert len(observed["strokes"]) == 4


@pytest.mark.parametrize(
    "state",
    [
        "%SRX2Y1I20.0J0.0*%",
        "%SR*%",
        "%LR90*%",
        "%LS2*%",
        "%LMX*%",
        "%OFA1B0*%",
        "%AB10*%",
        "G02*",
        "G03*\nG01*",
        "%TF.FilePolarity,Negative*%",
        "G04 #@! TF.FilePolarity,Negative*",
        "%LPC*%",
        "%LPX*%",
        "G99*",
    ],
)
def test_edge_does_not_certify_uninterpreted_state_with_same_straight_coordinates(state):
    source = EDGE.replace("D10*", state + "\nD10*")
    observed = gerber.gerber(source)
    assert len(observed["strokes"]) == 4
    assert gerber.outline_problems(observed), state
