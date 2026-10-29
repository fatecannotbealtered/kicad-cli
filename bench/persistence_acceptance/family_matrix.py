"""Finite discovery/parser requests generated from the public declaration, not handlers."""

import argparse
import json
from pathlib import Path


def sample(parameter):
    if parameter.get("enum"):
        return str(parameter["enum"][0])
    return {"number": "1", "integer": "1", "boolean": "true"}.get(parameter["type"], "sample")


def generate(reference):
    commands = reference["commands"]
    rows = []

    def add(
        identity,
        argv,
        *,
        kind="error",
        error="E_USAGE",
        parameter=None,
        command=None,
        schema=None,
        expected_data=None,
    ):
        rows.append(
            {
                "id": identity,
                "argv": argv,
                "kind": kind,
                "expected_exit": 2 if kind == "error" else 0,
                "expected_error": error if kind == "error" else None,
                "expected_parameter": parameter,
                "command": command,
                "schema": schema,
                "expected_data": expected_data,
                "native_allowed": False,
                "engineering_write_allowed": False,
            }
        )

    add("full-reference", ["reference", "--compact"], kind="full-reference", schema="reference")
    add(
        "exact-index", ["reference", "--index", "--compact"], kind="index", schema="reference-index"
    )
    for command in commands:
        path = command["path"]
        word = path.split()
        add("help:" + path, [*word, "--help"], kind="help", command=path)
        add(
            "reference:" + path,
            ["reference", "--command", path, "--compact"],
            kind="reference-command",
            command=path,
            schema="reference-command",
        )
        add(
            "unknown:" + path,
            [*word, "--unknown-first-version-flag", "--compact"],
            parameter="--unknown-first-version-flag",
            command=path,
        )
        required = {p["name"]: sample(p) for p in command["params"] if p["required"]}
        for param in command["params"]:
            name = param["name"]
            if param["required"]:
                args = [f"--{n}={v}" for n, v in required.items() if n != name]
                add(
                    f"required:{path}:{name}",
                    [*word, *args, "--compact"],
                    parameter="--" + name,
                    command=path,
                )
            raw = required | {name: sample(param)}
            args = [f"--{n}={v}" for n, v in raw.items()]
            if not param["multiple"]:
                add(
                    f"scalar-repeat:{path}:{name}",
                    [*word, *args, f"--{name}={sample(param)}", "--compact"],
                    parameter="--" + name,
                    command=path,
                )
            if param["type"] in {"number", "integer", "boolean"}:
                raw[name] = {"number": "NaN", "integer": "1.25", "boolean": "maybe"}[param["type"]]
                add(
                    f"invalid-type:{path}:{name}",
                    [*word, *[f"--{n}={v}" for n, v in raw.items()], "--compact"],
                    error="E_VALIDATION",
                    parameter="--" + name,
                    command=path,
                )
            if param.get("enum"):
                raw[name] = "not-a-declared-enum"
                add(
                    f"invalid-enum:{path}:{name}",
                    [*word, *[f"--{n}={v}" for n, v in raw.items()], "--compact"],
                    error="E_VALIDATION",
                    parameter="--" + name,
                    command=path,
                )
    for name, value in (
        ("compact", "1"),
        ("quiet", "falsex"),
        ("json", "yes"),
        ("help", "invalid"),
        ("version", "invalid"),
        ("dangerous", "invalid"),
        ("dry-run", "invalid"),
    ):
        add(
            "global-invalid:" + name,
            ["reference", f"--{name}={value}"],
            error="E_VALIDATION",
            parameter="--" + name,
        )
    add("global-format-raw", ["reference", "--format=raw"], parameter="--format")
    add(
        "global-json-override",
        ["reference", "--format=text", "--json", "--compact"],
        kind="full-reference",
        schema="reference",
    )
    add(
        "global-fields",
        ["reference", "--fields=tool,version", "--compact"],
        kind="projection",
        expected_data={"tool": reference["tool"], "version": reference["version"]},
    )
    add(
        "global-fields-unknown",
        ["reference", "--fields=not_a_field", "--compact"],
        error="E_VALIDATION",
        parameter="--fields",
    )
    add("global-version", ["--version", "--compact"], kind="version")
    base = [
        "project",
        "netclass",
        "set",
        "--board={board}",
        "--name=MatrixProbe",
        "--width=0.25",
        "--nets=GND",
        "--dry-run",
    ]
    add(
        "global-key-conflict",
        [*base, "--request-id=a", "--idempotency-key=b"],
        parameter="--idempotency-key",
    )
    add(
        "global-key-empty", [*base, "--request-id="], error="E_VALIDATION", parameter="--request-id"
    )
    add(
        "global-key-too-long",
        [*base, "--request-id=" + "x" * 513],
        error="E_VALIDATION",
        parameter="--request-id",
    )
    add("global-write-fields", [*base, "--fields=tool"], parameter="--fields")
    add("global-confirm-dryrun", [*base, "--confirm=not-a-token"], parameter="--confirm")
    add(
        "unknown-before-invalid-value",
        ["board", "check", "--unknown-first-version-flag", "--limit=NaN"],
        parameter="--unknown-first-version-flag",
    )
    add(
        "unknown-after-invalid-value",
        ["board", "check", "--limit=NaN", "--unknown-first-version-flag"],
        parameter="--unknown-first-version-flag",
    )
    for path, name, values in (
        ("board analyze ampacity", "oz", ["0", "100.01"]),
        ("board analyze reference-plane", "step", ["0.009", "10.01"]),
        ("board footprint place", "iterations", ["0", "100001"]),
        ("board silkscreen place", "clearance", ["-0.01", "10.01"]),
    ):
        for value in values:
            add(
                f"declared-bound:{path}:{name}:{value}",
                [*path.split(), "--board={board}", f"--{name}={value}", "--compact"],
                error="E_VALIDATION",
                parameter="--" + name,
                command=path,
            )
    return {
        "format": "finite-first-version-family.v1",
        "group": "discovery-parser",
        "reference": reference,
        "cases": rows,
        "native_allowed": False,
        "limits": (
            "Declared syntax/default discovery and selected rejection boundaries only; "
            "no universal error precedence or engineering acceptance"
        ),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reference", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    value = json.loads(args.reference.read_bytes())
    reference = value.get("data", value)
    result = generate(reference)
    with args.out.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"requests": len(result["cases"]), "paths": len(reference["commands"])}))


if __name__ == "__main__":
    main()
