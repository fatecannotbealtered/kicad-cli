"""Maintained public-CLI recipe derived from the earlier independent RC execution.

No product imports, internal scripts, SDK mutation, or hand-written KiCad files.
Each phase stops for the agent's actual full-image inspection before publication.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import review_binding as audit

BASE = Path(__file__).resolve().parent
PACKET = BASE.parent / "agent-packet"


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


parser = argparse.ArgumentParser()
parser.add_argument("--work", required=True)
parser.add_argument("--revision", required=True)
parser.add_argument(
    "--phase",
    choices=("baseline", "eco", "export-eco", "check-baseline", "check-eco"),
    required=True,
)
options = parser.parse_args()
WORK = Path(options.work).resolve()
assert WORK.is_relative_to(BASE) and WORK != BASE, (
    "New work directory must be owned execution subdirectory"
)
revision = read(options.revision)
candidate = Path(revision.get("candidate_path") or revision["candidate"])
assert sha(candidate) == revision["sha256"], "Candidate manifest hash mismatch"
env = dict(
    os.environ, RC4_WORK_ROOT=str(WORK), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"
)
SCH = WORK / "project/rc4.kicad_sch"
PCB = WORK / "project/rc4.kicad_pcb"
INTENT = PACKET / "requirements/intent-baseline.json"
ECO_INTENT = PACKET / "requirements/intent-eco.json"
NETS = "GND,/IN1,/IN2,/IN3,/IN4,/OUT1,/OUT2,/OUT3,/OUT4"
LAYERS = "F.Cu,B.Cu,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"
CONSUMERS = BASE / "consumers"


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)


def call(mode, label, *args, config=None, allow_error=False):
    prefix = [sys.executable, str(BASE / "evidence.py")]
    if config:
        prefix += ["--config-root", str(config)]
    argv = [*prefix, mode, label, "--", *map(str, args)]
    folder = WORK / "workflow-console"
    folder.mkdir(exist_ok=True)
    with (
        (folder / (label + ".stdout.txt")).open("xb") as out,
        (folder / (label + ".stderr.txt")).open("xb") as err,
    ):
        rc = subprocess.run(argv, cwd=WORK, env=env, stdout=out, stderr=err).returncode
    print(
        json.dumps({"phase": options.phase, "label": label, "exit_code": rc}),
        file=sys.stderr,
        flush=True,
    )
    if rc and not allow_error:
        raise RuntimeError(f"{label} failed; preserved raw evidence. No replay.")
    text = (WORK / "evidence" / label / "stdout.txt").read_text(encoding="utf-8", errors="replace")
    if mode == "candidate":
        try:
            return read(WORK / "evidence" / label / "stdout.txt")
        except json.JSONDecodeError:
            return {"exit_code": rc, "non_json_output": text}
    return {"exit_code": rc, "stdout": text}


def change(label, args, selected=None, allow_refusal=False, config=None):
    before = {str(p): sha(p) for p in (PCB, PCB.with_suffix(".kicad_pro"), SCH) if p.exists()}
    p = call(
        "candidate",
        label + "-preview",
        *args,
        "--dry-run",
        "--compact",
        config=config,
        allow_error=allow_refusal,
    )
    if not p.get("ok"):
        assert (
            allow_refusal and p["error"].get("details", {}).get("dispatch_state") == "not_started"
        )
        details = p["error"]["details"]
        assert (
            p["error"]["code"] == "E_VALIDATION"
            and "candidate_error" in details
            and "E_INTEGRITY" in json.dumps(details["candidate_error"])
        ), "Unexpected refusal, inspect before continuing"
        assert all(sha(path) == digest for path, digest in before.items()), (
            "Failed preview changed primary inputs"
        )
        put(
            WORK / "plans" / (label + "-refusal.json"),
            {
                "error": p["error"],
                "primary_hashes_unchanged": before,
                "response_to_failure": (
                    "Observed refusal, do not confirm/retry; use the separately "
                    "documented selected rewidth capability."
                ),
            },
        )
        return None
    preview = p["data"]["preview"]
    assert preview["can_apply"], "Preview not applicable"
    for output in preview.get("outputs", []):
        assert Path(output["path"]).resolve().is_relative_to(WORK)
    if selected is not None:
        assert sorted(preview["extensions"]["selected"]) == sorted(selected)
    quality = preview.get("extensions", {}).get("quality")
    if args[:2] == ["board", "route"] and quality and "all_selected_target_met" in quality:
        assert quality["all_selected_target_met"]
    put(
        WORK / "plans" / (label + "-preview-review.json"),
        {
            "operation_id": preview["operation_id"],
            "automated_review": (
                "Explicit authorized arguments, owned outputs, can_apply and actual "
                "width quality gate; later independent intent/native/visual checks "
                "remain required."
            ),
            "selected": selected,
            "candidate_sha256": revision["sha256"],
        },
    )
    receipt = call(
        "candidate",
        label + "-confirm",
        *args,
        "--confirm",
        p["data"]["confirm_token"],
        "--compact",
        config=config,
        allow_error=True,
    )
    observed = call(
        "candidate",
        label + "-operation",
        "operation",
        "show",
        "--id",
        preview["operation_id"],
        "--compact",
        config=config,
        allow_error=True,
    )
    assert receipt.get("ok"), "Confirmed operation failed or unknown; observed operation, no replay"
    assert observed.get("ok") and observed["data"]["operation_status"]["status"] == "succeeded"
    assert receipt["data"]["receipt"]["outcome"]["verification"]["status"] == "passed"
    return preview


def netlist(version):
    review = WORK / "artifacts" / ("schematic-review" if version == "baseline" else "eco-review")
    review.mkdir(parents=True, exist_ok=True)
    call(
        "official",
        version + "-native-xml",
        "sch",
        "export",
        "netlist",
        "--format",
        "kicadxml",
        "--output",
        review / "rc4.xml",
        SCH,
    )
    call(
        "consumer",
        version + "-independent-xml",
        CONSUMERS / "compare_native_netlist.py",
        review / "rc4.xml",
        INTENT if version == "baseline" else ECO_INTENT,
    )


def current_review(version):
    pointer = read(WORK / "plans" / (version + "-review.json"))
    review = (WORK / pointer["directory"]).resolve()
    if not review.is_relative_to(WORK / "artifacts"):
        raise audit.ReviewError("RC_REVIEW_PATH", "Review must belong to this run")
    return review


def native_and_render(version):
    import secrets

    suffix = secrets.token_hex(6)
    review = WORK / "artifacts" / (version + "-review-" + suffix)
    # KiCad creates local .prl preferences on first opening an existing project.
    # Initialize before the acceptance cycle; never reuse this export as a check.
    original_project = audit.tree(WORK / "project")
    original_configuration = audit.tree(WORK / "configuration")
    call(
        "official",
        version + "-" + suffix + "-initialize-native-context",
        "sch",
        "export",
        "netlist",
        "--format",
        "kicadxml",
        "--output",
        WORK / "inputs" / ("native-context-" + suffix + ".xml"),
        SCH,
    )
    # ERC lazily creates cvpcb preferences on this supported native version.
    # Initialization reports are deliberately not accepted check evidence.
    if not (WORK / "configuration/10.0/cvpcb.json").is_file():
        call(
            "official",
            version + "-" + suffix + "-initialize-native-preferences",
            "sch",
            "erc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--output",
            WORK / "inputs" / ("native-preferences-" + suffix + ".json"),
            SCH,
        )
    if any(
        not (WORK / "configuration/10.0" / name).is_file()
        for name in ("pcbnew.json", "fpedit.json", "3d_viewer.json")
    ):
        call(
            "official",
            version + "-" + suffix + "-initialize-board-preferences",
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--output",
            WORK / "inputs" / ("native-board-preferences-" + suffix + ".json"),
            PCB,
        )
    initialized_project = audit.tree(WORK / "project")
    if any(initialized_project.get(name) != value for name, value in original_project.items()):
        raise audit.ReviewError(
            "RC_INITIALIZATION_DRIFT",
            "Native context initialization changed an existing project file",
        )
    if set(initialized_project) - set(original_project) - {"rc4.kicad_prl"}:
        raise audit.ReviewError(
            "RC_INITIALIZATION_DRIFT",
            "Unexpected files created during native context initialization",
        )
    initialized_configuration = audit.tree(WORK / "configuration")
    allowed_preferences = {
        "10.0/cvpcb.json",
        "10.0/pcbnew.json",
        "10.0/fpedit.json",
        "10.0/3d_viewer.json",
    }
    if (
        any(
            initialized_configuration.get(name) != value
            for name, value in original_configuration.items()
        )
        or set(initialized_configuration) - set(original_configuration) - allowed_preferences
    ):
        raise audit.ReviewError(
            "RC_INITIALIZATION_DRIFT",
            "Native initialization changed existing preferences or added an unexpected file",
        )
    begin = audit.begin(WORK, BASE, PACKET, review, version)
    # Latest attempt becomes authoritative before checking; no fallback after failure.
    pointer = WORK / "plans" / (version + "-review.json")
    pointer.write_text(
        json.dumps(
            {"directory": review.relative_to(WORK).as_posix(), "review_id": begin["review_id"]}
        ),
        encoding="utf-8",
    )
    steps = []

    def checked(mode, name, *args):
        audit.stable(WORK, BASE, PACKET, review)
        label = version + "-" + suffix + "-" + name
        result = call(mode, label, *args)
        audit.stable(WORK, BASE, PACKET, review)
        record = WORK / "evidence" / label / "finish.json"
        steps.append(
            {
                "check": name,
                "passed": True,
                "review_id": begin["review_id"],
                "evidence_path": str(record),
                "evidence_sha256": sha(record),
            }
        )
        return result

    intent = INTENT if version == "baseline" else ECO_INTENT
    plan = (
        WORK / "plans" / ("baseline-layout-v2.json" if version == "baseline" else "eco-layout.json")
    )
    checked(
        "official",
        "native-xml",
        "sch",
        "export",
        "netlist",
        "--format",
        "kicadxml",
        "--output",
        review / "rc4.xml",
        SCH,
    )
    result = checked(
        "consumer",
        "independent-xml",
        CONSUMERS / "compare_native_netlist.py",
        review / "rc4.xml",
        intent,
    )
    put(review / "intent.json", json.loads(result["stdout"]))
    checked(
        "native-consumer",
        "reopened",
        CONSUMERS / "native_board_observe.py",
        PCB,
        intent,
        review / "native-observation-xml-identity.json",
        review / "rc4.xml",
    )
    if version == "eco":
        baseline = WORK / "snapshots/baseline"
        audit.verify_snapshot(baseline)
        checked(
            "consumer",
            "preservation",
            CONSUMERS / "compare_eco_preservation.py",
            baseline / "review/native-observation-xml-identity.json",
            review / "native-observation-xml-identity.json",
            WORK / "plans/eco-absolute-target.json",
            review / "preservation.json",
        )
    checked(
        "native-consumer",
        "geometry",
        CONSUMERS / "native_geometry_acceptance.py",
        PCB,
        intent,
        plan,
        WORK / "artifacts/board-created/native-observation.json",
        review / "geometry.json",
    )
    checked(
        "official",
        "erc",
        "sch",
        "erc",
        "--format",
        "json",
        "--units",
        "mm",
        "--severity-all",
        "--exit-code-violations",
        "--output",
        review / "erc.json",
        SCH,
    )
    checked(
        "official",
        "drc",
        "pcb",
        "drc",
        "--format",
        "json",
        "--units",
        "mm",
        "--severity-all",
        "--all-track-errors",
        "--schematic-parity",
        "--exit-code-violations",
        "--output",
        review / "drc.json",
        PCB,
    )
    erc = read(review / "erc.json")
    drc = read(review / "drc.json")
    assert not any(sheet["violations"] for sheet in erc["sheets"])
    assert not drc["violations"] and not drc["unconnected_items"] and not drc["schematic_parity"]
    checked(
        "official",
        "schematic-svg",
        "sch",
        "export",
        "svg",
        "--output",
        review / "schematic-svg",
        SCH,
    )
    checked(
        "consumer",
        "schematic-raster",
        CONSUMERS / "render_svg.py",
        review / "schematic-svg/rc4.svg",
        review / "schematic.png",
        "3",
    )
    for side, copper, silk in (("front", "F.Cu", "F.SilkS"), ("back", "B.Cu", "B.SilkS")):
        checked(
            "official",
            side + "-svg",
            "pcb",
            "export",
            "svg",
            "--layers",
            f"{copper},{silk},Edge.Cuts",
            "--mode-single",
            "--fit-page-to-board",
            "--exclude-drawing-sheet",
            "--output",
            review / (side + ".svg"),
            PCB,
        )
        checked(
            "consumer",
            side + "-raster",
            CONSUMERS / "render_svg.py",
            review / (side + ".svg"),
            review / (side + ".png"),
            "10",
        )
    if version == "eco":
        checked(
            "official",
            "current-sexpr",
            "sch",
            "export",
            "netlist",
            "--format",
            "kicadsexpr",
            "--output",
            review / "native-current.net",
            SCH,
        )
    ready = audit.complete(WORK, BASE, PACKET, review, steps)
    put(
        review / "VISUAL_TEMPLATE.json",
        {
            "passed": False,
            "review_id": ready["review_id"],
            "ready_sha256": sha(review / "READY.json"),
            "images": ready["images"],
            "reviewer": "",
            "observations": {name: "" for name in audit.IMAGES},
            "instruction": (
                "Actually view every full image, then save an explicit verdict as "
                "VISUAL_REVIEW.json. This template is not a passing review."
            ),
        },
    )
    return {
        "workflow_status": "awaiting_visual_review",
        "review_directory": str(review),
        "review_id": ready["review_id"],
        "next_action": (
            "View schematic/front/back PNGs; complete VISUAL_REVIEW.json from "
            "the template. Changed inputs or images require a new check cycle."
        ),
    }


def freeze_and_fab(version):
    review = current_review(version)
    audit.verify(WORK, BASE, PACKET, review)
    snapshot = WORK / "snapshots" / version
    audit.freeze(WORK, BASE, PACKET, review, snapshot)
    fab = WORK / "artifacts" / (version + "-fab")
    source = snapshot / "project/rc4.kicad_pcb"
    config = snapshot / "configuration"
    for kind, extra in (("gerber", ["--layers", LAYERS]), ("drill", ["--map", "none"])):
        audit.verify(WORK, BASE, PACKET, review)
        audit.verify_snapshot(snapshot)
        change(
            version + "-" + kind,
            ["fab", kind, "--board", source, *extra, "--out", fab / kind],
            config=config,
        )
        audit.verify(WORK, BASE, PACKET, review)
        audit.verify_snapshot(snapshot)
    call(
        "consumer",
        version + "-fab-consumer",
        CONSUMERS / "consume_fabrication.py",
        snapshot,
        review / "native-observation-xml-identity.json",
        fab,
        fab / "independent-report.json",
    )
    audit.verify(WORK, BASE, PACKET, review)
    audit.verify_snapshot(snapshot)


def finish_baseline(label="netclass"):
    change(
        label,
        [
            "project",
            "netclass",
            "set",
            "--board",
            PCB,
            "--name",
            "RC4",
            "--nets",
            NETS,
            "--width",
            "0.25",
            "--clearance",
            "0.20",
            "--via-diameter",
            "0.60",
            "--via-drill",
            "0.30",
        ],
    )
    change(
        "route",
        [
            "board",
            "route",
            "repair",
            "--board",
            PCB,
            "--nets",
            NETS,
            "--neck",
            "0.25",
            "--engine",
            "grid",
        ],
        selected=NETS.split(","),
    )
    zones = []
    for zone_label, layer in (("zone-front", "F.Cu"), ("zone-back", "B.Cu")):
        p = change(
            zone_label,
            [
                "board",
                "zone",
                "create",
                "--board",
                PCB,
                "--net",
                "GND",
                "--layer",
                layer,
                "--margin",
                "0.5",
                "--clearance",
                "0.2",
                "--min-width",
                "0.25",
                "--connect",
                "thermal",
            ],
        )
        zones.append(p["changes"][0]["id"])
    put(WORK / "plans/zone-ids.json", zones)
    change("fill", ["board", "zone", "refill", "--board", PCB, "--zones", ",".join(zones)])
    change("silkscreen", ["board", "silkscreen", "place", "--board", PCB, "--clearance", "0.15"])
    return native_and_render("baseline")


try:
    if options.phase == "baseline":
        WORK.mkdir(parents=True, exist_ok=False)
        for folder in ("project", "plans", "inputs", "artifacts", "configuration/10.0"):
            (WORK / folder).mkdir(parents=True, exist_ok=True)
        dependencies = read(PACKET / "DEPENDENCIES.json")
        library_hashes = {
            info["path"]: sha(info["path"]) for info in dependencies["libraries"].values()
        }
        assert all(
            library_hashes[info["path"]] == info["sha256"]
            for info in dependencies["libraries"].values()
        )
        for source in (PACKET / "config/10.0").iterdir():
            shutil.copyfile(source, WORK / "configuration/10.0" / source.name)
        put(
            WORK / "plans/candidate-selection.json",
            {
                "candidate_path": str(candidate),
                "sha256": revision["sha256"],
                "revision_manifest": str(Path(options.revision).resolve()),
                "source_commit": revision["source_commit"],
                "recipe_provenance": (
                    "This run uses the packaged public recipe and frozen inputs. "
                    "Their initial method originated in an earlier independent RC session. "
                    "This provenance does not assert developer assistance in the current run; "
                    "record any current assistance separately."
                ),
            },
        )
        put(
            WORK / "plans/SETUP.json",
            {
                "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "candidate_sha256": sha(candidate),
                "recipe_sha256": sha(__file__),
                "library_hashes": library_hashes,
                "isolation": (
                    "new project/state/config/temp under authorized execution; original "
                    "grant unchanged; not an OS sandbox"
                ),
            },
        )
        frozen = read(INTENT)
        spec = {
            "title": "Four-channel RC interface",
            "parts": [
                {k: p[k] for k in ("ref", "symbol", "value", "footprint")}
                for p in frozen["components"]
            ],
            "nets": [{"name": name, "connect": pins} for name, pins in frozen["nets"].items()],
        }
        put(WORK / "inputs/rc4.json", spec)
        shutil.copyfile(
            BASE / "plans/baseline-layout-v2.json", WORK / "plans/baseline-layout-v2.json"
        )
        shutil.copyfile(
            BASE / "inputs/schema-discovery-intent-v1.json",
            WORK / "inputs/old-format-negative.json",
        )
        call("candidate", "discovery-root-help", "--help")
        call("candidate", "discovery-create-help", "sch", "create", "--help")
        call(
            "candidate", "discovery-reference", "reference", "--command", "sch create", "--compact"
        )
        call("candidate", "discovery-context", "context", "--compact")
        doctor = call("candidate", "discovery-doctor", "doctor", "--compact")
        assert all(
            c["status"] == "pass"
            for c in doctor["data"]["checks"]
            if c["check"] in ("native_backend", "official_cli", "authorization")
        )
        negative = call(
            "candidate",
            "input-format-negative",
            "sch",
            "create",
            "--spec",
            WORK / "inputs/old-format-negative.json",
            "--out",
            WORK / "project",
            "--dry-run",
            "--compact",
            allow_error=True,
        )
        assert negative["error"]["code"] == "E_VALIDATION" and "connect" in json.dumps(
            negative["error"]
        )
        change(
            "sch-create",
            ["sch", "create", "--spec", WORK / "inputs/rc4.json", "--out", WORK / "project"],
        )
        netlist("baseline")
        change(
            "board-create",
            [
                "board",
                "from-netlist",
                "--netlist",
                WORK / "project/rc4.net",
                "--out",
                PCB,
                "--pitch",
                "15",
                "--margin",
                "10",
            ],
        )
        call(
            "native-consumer",
            "board-initial-independent",
            CONSUMERS / "native_board_observe.py",
            PCB,
            INTENT,
            WORK / "artifacts/board-created/native-observation.json",
        )
        links = call(
            "candidate",
            "unlinked-diagnostic",
            "project",
            "links",
            "inspect",
            "--board",
            PCB,
            "--schematic",
            SCH,
            "--compact",
        )
        text = json.dumps(links)
        put(
            WORK / "plans/unlinked-guidance-check.json",
            {
                "unlinked": links["data"]["unlinked"],
                "current_repair_hint": "project links repair" in text,
                "current_parity_hint": "project parity" in text,
                "obsolete_hint_present": "sch relink" in text or "board parity" in text,
            },
        )
        assert links["data"]["unlinked"] == 10
        change(
            "links-repair",
            [
                "project",
                "links",
                "repair",
                "--board",
                PCB,
                "--refs",
                "J1,J2,R1,C1,R2,C2,R3,C3,R4,C4",
            ],
        )
        change("project-sync", ["project", "sync", "--board", PCB, "--schematic", SCH])
        change(
            "place",
            [
                "board",
                "footprint",
                "move",
                "--board",
                PCB,
                "--moves",
                (
                    "J1:11,16;J2:52,16;R1:25,12;C1:39,12;R2:25,20;C2:39,20;R3:25,28;C3:39"
                    ",28;R4:25,36;C4:39,36"
                ),
            ],
        )
        result = finish_baseline()
    elif options.phase == "eco":
        freeze_and_fab("baseline")
        call("consumer", "freeze-eco-target", BASE / "freeze_eco_plan.py")
        put(
            WORK / "inputs/eco-c2-value.json",
            {"changes": [{"op": "set", "ref": "C2", "value": "220nF"}]},
        )
        change(
            "eco-sch-edit",
            ["sch", "edit", "--schematic", SCH, "--changes", WORK / "inputs/eco-c2-value.json"],
        )
        netlist("eco")
        change("eco-sync", ["project", "sync", "--board", PCB, "--schematic", SCH])
        change("eco-move", ["board", "footprint", "move", "--board", PCB, "--moves", "R2:26.5,20"])
        change(
            "eco-repair",
            [
                "board",
                "route",
                "repair",
                "--board",
                PCB,
                "--nets",
                "/IN2,/OUT2",
                "--neck",
                "0.25",
                "--engine",
                "grid",
            ],
            selected=["/IN2", "/OUT2"],
            allow_refusal=True,
        )
        change(
            "eco-rewidth",
            ["board", "route", "rewidth", "--board", PCB, "--nets", "/IN2,/OUT2", "--neck", "0.25"],
            selected=["/IN2", "/OUT2"],
        )
        change(
            "eco-refill",
            [
                "board",
                "zone",
                "refill",
                "--board",
                PCB,
                "--zones",
                ",".join(read(WORK / "plans/zone-ids.json")),
            ],
        )
        result = native_and_render("eco")
    elif options.phase.startswith("check-"):
        result = native_and_render(options.phase.removeprefix("check-"))
    else:
        freeze_and_fab("eco")
        result = {
            "workflow_status": "completed_bounded_acceptance",
            "snapshot": str(WORK / "snapshots/eco"),
        }
        put(
            WORK / "plans/COMPLETE.json",
            {
                "status": "both bounded engineering versions completed",
                "candidate_sha256": revision["sha256"],
                "source": (
                    "per-command evidence and independent consumers; visuals reviewed separately"
                ),
                "full_manufacturing_or_supplier_approval": False,
            },
        )
    print(json.dumps({"ok": True, "phase": options.phase, "work": str(WORK), **result}), flush=True)

except (audit.ReviewError, OSError, ValueError, AssertionError, RuntimeError) as exc:
    print(
        json.dumps(
            {
                "ok": False,
                "error": {
                    "code": getattr(exc, "code", "RC_WORKFLOW_FAILED"),
                    "message": str(exc),
                    "details": getattr(exc, "details", {}),
                    "next_action": (
                        "Inspect preserved evidence. Recheck changed inputs in a new review "
                        "cycle; do not inherit prior visual passes or replay unknown writes."
                    ),
                },
            }
        ),
        flush=True,
    )
    raise SystemExit(2) from exc
