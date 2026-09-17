"""Run the built binary and check it behaves like the tool it claims to be.

    python scripts/smoke_binary.py dist/kicad-cli.exe

The test suite runs `python -m kicad_cli.main`, which has a package context the
frozen binary does not. That difference is not theoretical: the first binary
this repo produced crashed on its first relative import and printed a Python
traceback instead of an envelope, for every single command. It built cleanly,
archived cleanly, and would have been published.

So this checks the artifact, not the source: that it starts, that stdout carries
one parseable envelope, that the payload and changelog data came along, and that
the version inside matches package.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def envelope(binary: str, argv: list[str]) -> tuple[dict, int, str]:
    proc = subprocess.run(  # noqa: S603
        [binary, *argv, "--compact"], capture_output=True, timeout=300, cwd=ROOT
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    lines = [line for line in out.splitlines() if line.startswith("{")]
    if len(lines) != 1:
        raise SystemExit(
            f"FAIL  {' '.join(argv)}: expected one envelope on stdout, got {len(lines)}\n"
            f"      stdout: {out[:400]}\n      stderr: {err[:400]}"
        )
    return json.loads(lines[0]), proc.returncode, err


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise SystemExit(f"FAIL  {label}{': ' + detail if detail else ''}")
    print(f"  ok    {label}")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: smoke_binary.py <path-to-binary>")
    binary = str(Path(sys.argv[1]).resolve())
    expected = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    print(f"smoking {binary}")

    doc, code, _ = envelope(binary, ["reference"])
    check("reference starts and returns an envelope", doc["ok"] and code == 0)
    check(
        "version matches package.json",
        doc["data"]["version"] == expected,
        f"binary says {doc['data']['version']}, package.json says {expected}",
    )
    commands = {c["path"] for c in doc["data"]["commands"]}
    check("the command registry survived freezing", len(commands) >= 20, f"{len(commands)} found")

    doc, code, _ = envelope(binary, ["context"])
    check("context starts", doc["ok"] and code == 0)

    doc, code, _ = envelope(binary, ["doctor"])
    check("doctor starts", doc["ok"] and code == 0)

    # CHANGELOG.md rides along as data; without it this returns nothing.
    doc, code, _ = envelope(binary, ["changelog"])
    check("changelog data file was bundled", doc["ok"] and bool(doc["data"]["entries"]))

    # An unknown command must still produce a proper envelope rather than a
    # traceback -- which is exactly how the broken build failed.
    doc, code, _ = envelope(binary, ["definitely", "not", "a", "command"])
    check(
        "unknown command fails as an envelope, not a traceback",
        doc["ok"] is False and doc["error"]["code"] == "E_USAGE" and code == 2,
    )

    # board live needs the vendored IPC client. Either it connects, or it fails
    # with E_CONFIG -- but "the client is not available" means the bundle is
    # missing it, which is a packaging defect, not the user's environment.
    doc, code, _ = envelope(binary, ["board", "live"])
    if doc["ok"]:
        check("board live reached a running KiCad", True)
    else:
        check("board live fails as E_CONFIG", doc["error"]["code"] == "E_CONFIG")
        check(
            "the IPC client was bundled",
            "not available" not in doc["error"]["message"],
            "board live cannot find kipy in the bundle; build.py did not carry .vendor",
        )

    print("smoke: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
