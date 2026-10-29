"""Bind one RC check/render/visual cycle; no product implementation imports.

These are acceptance records, not a replacement driver protocol or a security
boundary against a caller who can rewrite every record. Existing records are
never refreshed to carry an earlier pass onto changed inputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

IMAGES = ("schematic.png", "front.png", "back.png")
REPORTS = (
    "rc4.xml",
    "intent.json",
    "native-observation-xml-identity.json",
    "geometry.json",
    "erc.json",
    "drc.json",
    "schematic-svg/rc4.svg",
    "front.svg",
    "back.svg",
    *IMAGES,
)


class ReviewError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def read(path):
    try:
        return json.loads(Path(path).read_bytes())
    except (OSError, ValueError) as exc:
        raise ReviewError(
            "RC_REVIEW_MISSING", "Missing or invalid acceptance record", path=str(path)
        ) from exc


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def sha(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ReviewError(
            "RC_INPUT_MISSING", "Required regular file missing or linked", path=str(path)
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    path = Path(path)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def tree(path, *, python_only=False):
    path = Path(path)
    if not path.is_dir() or path.is_symlink():
        raise ReviewError(
            "RC_INPUT_MISSING", "Required input directory missing or linked", path=str(path)
        )
    files = {}
    for item in sorted(path.rglob("*")):
        if "__pycache__" in item.relative_to(path).parts:
            continue
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise ReviewError(
                "RC_INPUT_LINK", "Linked inputs are outside this bounded workflow", path=str(item)
            )
        if item.is_file() and (not python_only or item.suffix == ".py"):
            files[item.relative_to(path).as_posix()] = sha(item)
    return files


def capture(work, base, packet, stage, *, copies=True):
    """Exact relative file sets plus explicit, relevant external dependencies."""
    work, base, packet = map(Path, (work, base, packet))
    settings = read(packet / "DEPENDENCIES.json")
    selection = read(work / "plans/candidate-selection.json")
    environment = settings["environment"]
    candidate = Path(selection["candidate_path"])
    if sha(candidate) != selection["sha256"]:
        raise ReviewError(
            "RC_DEPENDENCY_DRIFT", "Selected candidate differs from its declared hash"
        )
    external = {"candidate": {"path": str(candidate.resolve()), "sha256": sha(candidate)}}
    for name, item in settings["libraries"].items():
        actual = sha(item["path"])
        if actual != item["sha256"]:
            raise ReviewError("RC_DEPENDENCY_DRIFT", "Frozen library changed", dependency=name)
        external["library:" + name] = {"path": str(Path(item["path"]).resolve()), "sha256": actual}
    native_files = settings.get("native_files", {})
    if not {"python", "official", "pcbnew", "_pcbnew"} <= native_files.keys():
        raise ReviewError(
            "RC_DEPENDENCY_MISSING", "Configure exact native program/module identities first"
        )
    for name, item in native_files.items():
        actual = sha(item["path"])
        if actual != item["sha256"]:
            raise ReviewError(
                "RC_DEPENDENCY_DRIFT", "Configured native dependency changed", dependency=name
            )
        external["native:" + name] = {"path": str(Path(item["path"]).resolve()), "sha256": actual}
    for name in ("KICAD_CLI_PYTHON", "KICAD_CLI_OFFICIAL", "KICAD_CLI_SCOPE_FILE"):
        path = Path(environment[name])
        external[name] = {"path": str(path.resolve()), "sha256": sha(path)}
    consumer = Path(settings["readonly_consumer_python"])
    external["consumer_python"] = {"path": str(consumer.resolve()), "sha256": sha(consumer)}
    sdk = Path(environment["KICAD_CLI_DRIVER_SDK_PATH"])
    dependency_trees = {
        "driver_sdk": {"path": str(sdk.resolve()), "files": tree(sdk)},
        "driver_spec": {
            "path": str((sdk.parent / "driver_spec").resolve()),
            "files": tree(sdk.parent / "driver_spec"),
        },
    }
    # A read-only review may examine copied native files. Bind their live source
    # files/directories as well, so changes after a visual pause cannot be hidden
    # by leaving the review copies untouched. Existing RC callers omit this.
    for name, item in settings.get("bound_sources", {}).items():
        path = Path(item["path"])
        if item["kind"] == "file":
            external["source:" + name] = {"path": str(path.resolve()), "sha256": sha(path)}
        elif item["kind"] == "directory":
            files = tree(path)
            if "expected_files" in item and files != item["expected_files"]:
                raise ReviewError(
                    "RC_INPUT_DRIFT", "Previously verified snapshot changed", source=name
                )
            dependency_trees["source:" + name] = {"path": str(path.resolve()), "files": files}
        else:
            raise ReviewError("RC_INPUT_KIND", "Unsupported bound source kind", source=name)
    plans = ["baseline-layout-v2.json", "candidate-selection.json", "zone-ids.json"]
    if stage == "eco":
        plans += ["eco-layout.json", "eco-absolute-target.json"]
    methods = {p.name: sha(p) for p in sorted(base.glob("*.py"))}
    methods.update(
        {"consumers/" + name: h for name, h in tree(base / "consumers", python_only=True).items()}
    )
    original = {
        "stage": stage,
        "plans": {name: sha(work / "plans" / name) for name in plans},
        "dependency_configuration_sha256": sha(packet / "DEPENDENCIES.json"),
        "external_files": external,
        "external_trees": dependency_trees,
        "methods": methods,
        "effective_environment": {
            **environment,
            "KICAD_CONFIG_HOME": str((work / "configuration").resolve()),
            "KICAD_CLI_STATE_DIR": str((work / "state").resolve()),
            **{k: os.environ.get(k) for k in ("PATH", "SystemRoot", "LANG", "LC_ALL")},
        },
    }
    if not copies:
        return original
    pre_copy = packet / "PRE_COPY.json"
    predecessors = {}
    if pre_copy.exists():
        binding = verify_origin(work, base, packet, stage, current=original)
        if (packet / "predecessors").exists():
            predecessors = tree(packet / "predecessors")
        if predecessors != binding["copied_inputs"].get("predecessors", {}):
            raise ReviewError("RC_INPUT_DRIFT", "Copied predecessor evidence changed")
    project = tree(work / "project")
    if not {"rc4.kicad_sch", "rc4.kicad_pcb", "rc4.kicad_pro"} <= project.keys():
        raise ReviewError("RC_INPUT_MISSING", "Complete schematic/PCB/project set is required")
    return {
        **original,
        "project": project,
        "configuration": tree(work / "configuration"),
        "requirements": tree(packet / "requirements"),
        **(
            {
                "pre_copy_sha256": sha(pre_copy),
                "configuration_relocation_sha256": sha(
                    work / "inputs/configuration-relocation.json"
                ),
                "predecessors": predecessors,
            }
            if pre_copy.exists()
            else {}
        ),
    }


def verify_origin(work, base, packet, stage, *, current=None):
    """An initialization may change review preferences, never the live baseline."""
    record = read(Path(packet) / "PRE_COPY.json")
    current = current if current is not None else capture(work, base, packet, stage, copies=False)
    if digest(record["origin"]) != record["origin_digest"] or current != record["origin"]:
        raise ReviewError(
            "RC_INPUT_DRIFT",
            "Original inputs/dependencies changed since before copying; start a new review",
        )
    return record


def begin(work, base, packet, review, stage):
    review = Path(review)
    # Capture first, then exclusively reserve a new check cycle. Never refresh.
    snapshot = capture(work, base, packet, stage)
    review.mkdir(parents=True, exist_ok=False)
    record = {
        "format": "rc-acceptance-start.v1",
        "review_id": "rc-review-" + secrets.token_hex(16),
        "stage": stage,
        "started_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "inputs": snapshot,
        "input_digest": digest(snapshot),
        "required_images": list(IMAGES),
    }
    write_new(review / "START.json", record)
    return record


def stable(work, base, packet, review):
    start = read(Path(review) / "START.json")
    current = capture(work, base, packet, start["stage"])
    if digest(start["inputs"]) != start["input_digest"] or current != start["inputs"]:
        changed = [key for key in current if current[key] != start["inputs"].get(key)]
        raise ReviewError(
            "RC_INPUT_DRIFT",
            (
                "Acceptance inputs/dependencies changed; prior checks and visual "
                "verdict cannot be reused"
            ),
            changed_groups=changed,
            next_action=(
                "Recheck the changed design in a new review directory, rerun all "
                "checks and renders, then review every required image again. Do not "
                "refresh hashes or inherit a prior pass."
            ),
        )
    return start


def required_checks(stage):
    result = {
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
    }
    if stage == "eco":
        result |= {"preservation", "current-sexpr"}
    return result


def complete(work, base, packet, review, steps):
    review = Path(review)
    start = stable(work, base, packet, review)
    if (
        len(steps) != len(required_checks(start["stage"]))
        or {s["check"] for s in steps} != required_checks(start["stage"])
        or not all(s["passed"] is True and s["review_id"] == start["review_id"] for s in steps)
    ):
        raise ReviewError(
            "RC_CHECKS_INCOMPLETE", "All current-cycle check/render steps are required"
        )
    names = [
        *REPORTS,
        *(["preservation.json", "native-current.net"] if start["stage"] == "eco" else []),
    ]
    for name in names:
        if not (review / name).is_file() or not (review / name).stat().st_size:
            raise ReviewError(
                "RC_CHECKS_INCOMPLETE", "Nonempty check artifacts are required", artifact=name
            )
    for name in IMAGES:
        if not (review / name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ReviewError(
                "RC_CHECKS_INCOMPLETE", "Required reviewed images must be rendered PNGs", image=name
            )
    files = {name: sha(review / name) for name in names}
    result = {
        "format": "rc-acceptance-ready.v1",
        "review_id": start["review_id"],
        "stage": start["stage"],
        "start_sha256": sha(review / "START.json"),
        "input_digest": start["input_digest"],
        "artifacts": files,
        "images": {name: files[name] for name in IMAGES},
        "checks": steps,
        "machine_checks_passed": True,
        "visual_status": "required",
    }
    write_new(review / "READY.json", result)
    return result


def verify(work, base, packet, review, *, visual=True):
    review = Path(review)
    start = stable(work, base, packet, review)
    ready = read(review / "READY.json")
    if (
        ready.get("review_id") != start["review_id"]
        or ready.get("stage") != start["stage"]
        or ready.get("start_sha256") != sha(review / "START.json")
        or ready.get("input_digest") != start["input_digest"]
        or ready.get("machine_checks_passed") is not True
    ):
        raise ReviewError(
            "RC_REVIEW_STALE", "Check results belong to a different or changed acceptance cycle"
        )
    required = set(REPORTS) | (
        {"preservation.json", "native-current.net"} if start["stage"] == "eco" else set()
    )
    if set(ready.get("artifacts", {})) != required or ready.get("images") != {
        name: ready["artifacts"].get(name) for name in IMAGES
    }:
        raise ReviewError("RC_REVIEW_INCOMPLETE", "Required check artifacts/images are missing")
    for name, wanted in ready["artifacts"].items():
        if sha(review / name) != wanted:
            raise ReviewError(
                "RC_REVIEW_DRIFT", "Checked artifact or image was replaced", artifact=name
            )
    steps = ready.get("checks", [])
    if len(steps) != len(required_checks(start["stage"])) or {
        s.get("check") for s in steps
    } != required_checks(start["stage"]):
        raise ReviewError("RC_CHECKS_INCOMPLETE", "Current-cycle check evidence is incomplete")
    for step in steps:
        if not Path(step["evidence_path"]).resolve().is_relative_to(Path(work).resolve()):
            raise ReviewError("RC_REVIEW_DRIFT", "Check evidence escaped this owned run")
        if (
            step["review_id"] != start["review_id"]
            or step["passed"] is not True
            or sha(step["evidence_path"]) != step["evidence_sha256"]
        ):
            raise ReviewError("RC_REVIEW_DRIFT", "Current-cycle execution evidence changed")
    if visual:
        verdict = read(review / "VISUAL_REVIEW.json")
        if (
            verdict.get("passed") is not True
            or verdict.get("review_id") != ready["review_id"]
            or verdict.get("ready_sha256") != sha(review / "READY.json")
            or verdict.get("images") != ready["images"]
        ):
            raise ReviewError(
                "RC_VISUAL_REQUIRED",
                "An explicit passing review of all exact current images is required",
            )
        if (
            not isinstance(verdict.get("reviewer"), str)
            or not verdict["reviewer"].strip()
            or not isinstance(verdict.get("observations"), dict)
            or set(verdict["observations"]) != set(IMAGES)
            or not all(isinstance(v, str) and v.strip() for v in verdict["observations"].values())
        ):
            raise ReviewError(
                "RC_VISUAL_REQUIRED",
                "Record a reviewer and concrete observations for every required image",
            )
    return ready


def freeze(work, base, packet, review, snapshot):
    work, base, packet, review, snapshot = map(Path, (work, base, packet, review, snapshot))
    ready = verify(work, base, packet, review)
    start = read(review / "START.json")
    snapshot.mkdir(parents=True, exist_ok=False)
    import shutil

    files = {}
    sources = []
    for group, folder in (
        ("project", work / "project"),
        ("configuration", work / "configuration"),
        ("requirements", packet / "requirements"),
        ("plans", work / "plans"),
        ("predecessors", packet / "predecessors"),
    ):
        for name, expected in start["inputs"].get(group, {}).items():
            sources.append((folder / name, group + "/" + name, expected))
    for name, expected in ready["artifacts"].items():
        sources.append((review / name, "review/" + name, expected))
    for name in ("START.json", "READY.json", "VISUAL_REVIEW.json"):
        sources.append((review / name, "review/" + name, sha(review / name)))
    if "pre_copy_sha256" in start["inputs"]:
        sources.append(
            (packet / "PRE_COPY.json", "binding/PRE_COPY.json", start["inputs"]["pre_copy_sha256"])
        )
        relocation = work / "inputs/configuration-relocation.json"
        sources.append(
            (
                relocation,
                "binding/configuration-relocation.json",
                start["inputs"]["configuration_relocation_sha256"],
            )
        )
    for source, name, expected in sources:
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if sha(target) != expected or sha(source) != expected:
            raise ReviewError(
                "RC_INPUT_DRIFT",
                "Input changed while freezing; incomplete snapshot must not be exported",
                path=str(source),
            )
        files[name] = expected
    verify(work, base, packet, review)
    manifest = {
        "format": "rc-reviewed-snapshot.v1",
        "version": ready["stage"],
        "review_id": ready["review_id"],
        "accepted_ready_sha256": sha(review / "READY.json"),
        "input_digest": start["input_digest"],
        "files": files,
    }
    write_new(snapshot / "MANIFEST.json", manifest)
    return manifest


def verify_snapshot(snapshot):
    snapshot = Path(snapshot)
    manifest = read(snapshot / "MANIFEST.json")
    actual = tree(snapshot)
    actual.pop("MANIFEST.json", None)
    if (
        manifest.get("format") != "rc-reviewed-snapshot.v1"
        or actual != manifest.get("files")
        or sha(snapshot / "review/READY.json") != manifest.get("accepted_ready_sha256")
    ):
        raise ReviewError("RC_SNAPSHOT_DRIFT", "Frozen reviewed snapshot changed or is incomplete")
    start = read(snapshot / "review/START.json")
    ready = read(snapshot / "review/READY.json")
    verdict = read(snapshot / "review/VISUAL_REVIEW.json")
    if (
        digest(start["inputs"]) != start["input_digest"]
        or manifest.get("version") != start["stage"]
        or ready.get("stage") != start["stage"]
        or ready.get("input_digest") != start["input_digest"]
        or manifest.get("input_digest") != start["input_digest"]
        or ready.get("start_sha256") != sha(snapshot / "review/START.json")
        or ready.get("review_id") != start["review_id"]
        or manifest.get("review_id") != start["review_id"]
        or verdict.get("review_id") != start["review_id"]
        or verdict.get("passed") is not True
        or verdict.get("ready_sha256") != sha(snapshot / "review/READY.json")
        or verdict.get("images") != ready.get("images")
    ):
        raise ReviewError(
            "RC_SNAPSHOT_DRIFT", "Snapshot does not retain its accepted check/visual cycle"
        )
    # A newly computed snapshot summary cannot launder changed inputs or images.
    if "pre_copy_sha256" in start["inputs"]:
        if sha(snapshot / "binding/PRE_COPY.json") != start["inputs"]["pre_copy_sha256"]:
            raise ReviewError("RC_SNAPSHOT_DRIFT", "Pre-copy original binding changed")
        origin = read(snapshot / "binding/PRE_COPY.json")
        if start["inputs"].get("predecessors", {}) != origin["copied_inputs"].get(
            "predecessors", {}
        ):
            raise ReviewError(
                "RC_SNAPSHOT_DRIFT", "Predecessor evidence differs from verified inputs"
            )
        if (
            sha(snapshot / "binding/configuration-relocation.json")
            != start["inputs"]["configuration_relocation_sha256"]
        ):
            raise ReviewError("RC_SNAPSHOT_DRIFT", "Initialization relocation evidence changed")
    for group in ("project", "configuration", "requirements", "plans", "predecessors"):
        for name, expected in start["inputs"].get(group, {}).items():
            if sha(snapshot / group / name) != expected:
                raise ReviewError(
                    "RC_SNAPSHOT_DRIFT",
                    "Frozen input differs from the checked source",
                    path=group + "/" + name,
                )
    for name, expected in ready["artifacts"].items():
        if sha(snapshot / "review" / name) != expected:
            raise ReviewError(
                "RC_SNAPSHOT_DRIFT",
                "Frozen artifact differs from the reviewed image/check",
                path=name,
            )
    return manifest


def verified_snapshot_inputs(snapshot):
    """Return metadata from exactly the snapshot bytes that passed verification.

    The caller binds this complete file set before copying or initializing. A
    later capture must compare it, never replace it with freshly computed hashes.
    Sidecar review/START.json outside the snapshot is not an input.
    """
    snapshot = Path(snapshot).resolve()
    files = tree(snapshot)
    manifest = verify_snapshot(snapshot)
    start = read(snapshot / "review/START.json")
    if tree(snapshot) != files or manifest != read(snapshot / "MANIFEST.json"):
        raise ReviewError("RC_SNAPSHOT_DRIFT", "Snapshot changed while verifying predecessor")
    return {
        "path": str(snapshot),
        "files": files,
        "stage": start["stage"],
        "review_id": start["review_id"],
        "source_paths": {
            name: start["inputs"]["external_files"]["source:" + name]["path"]
            for name in ("rc4.kicad_sch", "rc4.kicad_pcb")
        },
    }
