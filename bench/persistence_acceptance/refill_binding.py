"""Bind a refill export to original, prepared and saved identities without replay."""

import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree(path):
    if not path.is_dir():
        raise ValueError("Required frozen/current directory is missing")
    return {p.relative_to(path).as_posix(): sha(p) for p in sorted(path.rglob("*")) if p.is_file()}


def require(condition, reason):
    if not condition:
        raise ValueError("Refill export identity mismatch: " + reason)


def validate(directory, result):
    directory = Path(directory)
    frozen, setup = load(directory / "FROZEN.json"), load(directory / "SETUP.json")
    require(result["binding"] == setup["binding"] == frozen["binding"], "build binding")
    baseline = frozen["files"]
    require(baseline == setup["files"], "frozen input set")
    board = "rc4.kicad_pcb"
    copies = {}
    for stage, folder in (
        ("before", "original-copy"),
        ("prepared", "prepared-copy"),
        ("after", "final-files"),
    ):
        manifest = load(directory / folder / "FILES.json")
        expected = {
            n.removeprefix("project/"): h for n, h in manifest.items() if n.startswith("project/")
        }
        require(tree(directory / folder / "project") == expected, stage + " frozen copy")
        require(set(expected) == set(baseline), stage + " frozen file set")
        require(
            all(expected[n] == h for n, h in baseline.items() if n != board),
            stage + " protected files",
        )
        copies[stage] = expected
    require(copies["before"] == baseline, "original snapshot")
    current = tree(directory / "project")
    require(current == copies["after"], "current project versus frozen final set")
    require(tree(directory / "configuration") == setup["configuration"], "configuration")
    identities = {stage: files[board] for stage, files in copies.items()}
    require(identities["before"] != identities["prepared"], "public move must change board")
    require(identities["prepared"] != identities["after"], "first refill must change board")
    operations = (
        ("preparation", "move", "before", "prepared", "applied", "saved"),
        ("refill", "refill", "prepared", "after", "applied", "saved"),
        ("repeat", "repeat-refill", "after", "after", "not_applied", "unchanged"),
    )

    def call(name):
        record = load(directory / "calls" / (name + ".call.json"))
        stdout = directory / "calls" / (name + ".stdout")
        require(record["stdout_sha256"] == sha(stdout), name + " stdout")
        require(record["source_binding"] == result["binding"], name + " source binding")
        require(record["exit"] == 0 and record["execution"] == "completed", name + " completion")
        return load(stdout)

    for key, command, start, end, change, persistence in operations:
        operation = result[key]
        require(
            operation["before"] == identities[start] and operation["after"] == identities[end],
            key + " stages",
        )
        require(
            operation["change"] == change and operation["persistence"] == persistence,
            key + " outcome",
        )
        receipt = call(command + "-confirm")["data"]["receipt"]
        require(receipt["operation_id"] == operation["operation_id"], key + " operation ID")
        require(
            receipt["snapshot_before"]["revision"] == identities[start], key + " receipt before"
        )
        require(receipt["snapshot_after"]["revision"] == identities[end], key + " receipt after")
        require(
            receipt["outcome"]["change"] == change
            and receipt["outcome"]["persistence"] == persistence,
            key + " receipt outcome",
        )
    for stage, result_key in (("before", "baseline"), ("prepared", "prepared"), ("after", "after")):
        summary = load(directory / stage / "SUMMARY.json")
        require(
            summary == result[result_key] and summary["board_sha256"] == identities[stage],
            stage + " summary",
        )
        native = directory / stage / "native-board.json"
        fill = load(directory / stage / "fill.json")
        require(
            load(native)["board_sha256"] == identities[stage], stage + " native board reference"
        )
        require(fill["board_sha256"] == identities[stage], stage + " native fill reference")
        require(
            call(stage + "-native-board")["report_sha256"] == sha(native),
            stage + " native report digest",
        )
        require(call(stage + "-native-fill") == fill, stage + " native fill receipt")
    require(
        result["native_fill_after"] == load(directory / "after/fill.json"), "RESULT native fill"
    )

    # Only recorded dependencies can be authenticated retrospectively. Do not
    # silently assert coverage for installed library contents never fingerprinted.
    original_root = Path(setup["environment"]["KICAD_CLI_STATE_DIR"]).parent.resolve()
    dependencies = {}
    for path in (directory / "calls").glob("*.call.json"):
        record = load(path)
        for item in record.get("program", []) + record.get("inputs_before", []):
            dependency = Path(item["path"]).resolve()
            if dependency.is_relative_to(original_root):
                continue
            expected = item.get("sha256") if item.get("present") else None
            require(
                str(dependency) not in dependencies or dependencies[str(dependency)] == expected,
                "dependency observations disagree",
            )
            dependencies[str(dependency)] = expected
            actual = sha(dependency) if dependency.is_file() else None
            require(actual == expected, "recorded external dependency drift")
    return {
        "stage_board_sha256": identities,
        "current_files": current,
        "configuration_files": len(setup["configuration"]),
        "recorded_external_dependencies": len(dependencies),
        "dependency_coverage": (
            "Recorded file dependencies only; "
            "no retrospective claim for unrecorded installed-library contents"
        ),
        "passed": True,
    }
