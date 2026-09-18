"""Record the live smoke evidence CLI-SPEC §11 requires for `stable`.

    python scripts/record_evidence.py

Writes `docs/evidence/live-smoke-<version>.md`: the verbatim output of the test
suite and of the frozen binary's smoke test, with the environment they ran in.
Two runs, not one, because passing the suite and shipping a binary that cannot
start is exactly what happened once.

Absolute paths are replaced with `<repo>`, `<kicad>` and `<python>`. The file
records a result, not one machine's filesystem, and the substitution is stated
in the file itself so "verbatim" stays true.

Run it on a machine with KiCad installed, after `python build.py`.
"""

from __future__ import annotations

import datetime
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def envelope(argv: list[str]) -> dict:
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    ).stdout
    for line in out.splitlines():
        if line.startswith("{"):
            return json.loads(line)
    raise SystemExit(f"no envelope from {' '.join(argv)}:\n{out[:400]}")


def source_identity() -> str:
    """The commit this evidence is about, refusing to guess.

    The filename used to be the version alone, and the version does not move
    between candidates here -- so a second run would have overwritten the
    recorded 1.0.0 evidence with a different tree's result under the same name.
    CLI-SPEC asks for source identity for exactly this reason: a record that
    cannot say which tree it describes is not evidence of anything.
    """

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603
            ["git", *args], capture_output=True, text=True, cwd=ROOT, check=True
        ).stdout.strip()

    try:
        head = git("rev-parse", "--short=12", "HEAD")
        dirty = git("status", "--porcelain")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit("cannot determine the commit this evidence is for") from exc
    if dirty:
        raise SystemExit(
            "the working tree has uncommitted changes, so this run cannot be tied to a "
            "commit. Evidence that cannot say which tree it describes is not evidence; "
            "commit first, then record."
        )
    return head


def coverage(name: str) -> dict:
    """The measured coverage the suite just wrote, if it was able to measure."""
    path = ROOT / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> int:
    cfg = envelope(["context"])["data"]["config"]
    version = envelope(["reference", "--fields", "version"])["data"]["version"]
    commit = source_identity()

    binary = ROOT / "dist" / ("kicad-cli.exe" if sys.platform == "win32" else "kicad-cli")
    if not binary.exists():
        raise SystemExit(f"{binary} is missing. Run `python build.py` first.")

    print("1/2 test suite ...")
    suite = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short", "-p", "no:cacheprovider"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )
    print("2/2 frozen binary ...")
    smoke = subprocess.run(  # noqa: S603
        [sys.executable, "scripts/smoke_binary.py", str(binary)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )

    kicad_root = Path(cfg["kicad_python"]).parents[2]
    replacements = [
        (str(ROOT), "<repo>"),
        (str(kicad_root), "<kicad>"),
        (str(Path(sys.executable).parent), "<python>"),
    ]

    def scrub(text: str) -> str:
        for real, token in replacements:
            for spelling in (real, real.replace("\\", "/"), real.replace("\\", "\\\\")):
                text = text.replace(spelling, token)
        return text

    fcc = coverage(".fcc-coverage.json")
    errs = coverage(".error-coverage.json")
    dispatch = (
        f"{fcc['covered']}/{fcc['leaves']} leaf commands" if fcc else "not measured in this run"
    )
    error_codes = (
        f"{len(errs['produced'])} produced, {len(errs['missing'])} applicable and unreached, "
        f"{len(errs['not_applicable'])} declared not applicable"
        if errs
        else "not measured in this run"
    )

    ok = suite.returncode == 0 and smoke.returncode == 0
    verdict = "PASS" if ok else "FAIL"
    body = f"""# Live smoke evidence — kicad-cli {version}

CLI-SPEC §11 makes this a precondition for `stable`: at least one recorded live
smoke/E2E run against the real upstream, for the release candidate being
declared. This file is that record, produced by `scripts/record_evidence.py`.

Two parts, because passing the first and failing the second is exactly what
happened once: the suite ran green while the binary it was supposed to vouch
for could not start at all. Testing the source is not testing the artifact.

Output is verbatim except that absolute paths are replaced — `<repo>` for the
checkout, `<kicad>` for the KiCad installation, `<python>` for the interpreter —
so this records a result rather than one machine's filesystem.

| | |
|---|---|
| Tool version | `{version}` |
| Source | `{commit}` (clean tree) |
| Recorded | {datetime.date.today().isoformat()} |
| Platform | {platform.system()} {platform.release()} ({platform.machine()}) |
| KiCad | {cfg["kicad_version"]} |
| Runner | Python {platform.python_version()} |
| Suite | **{"PASS" if suite.returncode == 0 else "FAIL"}** (exit {suite.returncode}) |
| Frozen binary | **{"PASS" if smoke.returncode == 0 else "FAIL"}** (exit {smoke.returncode}) |
| Overall | **{verdict}** |

Measured coverage from this same run, where the suite was able to measure it:

| | |
|---|---|
| Command dispatch | {dispatch} |
| Declared error codes | {error_codes} |

Both are floors for Functional Contract Coverage, not FCC. CLI-SPEC asks for
every documented behavior to have a command-level test; these count leaf
commands dispatched and declared `E_*` produced. `fcc_status` stays `unknown`
until the remaining dimensions -- global options, error-details shape -- are
measured too.

What "live" means here: nothing is stubbed. Each test launches the real
`kicad-cli` process, which launches KiCad's own interpreter and binary, against
KiCad's own demo projects copied into a temp directory. The exception is
`test_mock_upstream.py`, which deliberately substitutes KiCad in order to reach
the failure paths a real KiCad will not produce on demand.

What this evidence does **not** cover, stated here so the level is not read as
more than it is: one platform, one KiCad version. There is no second machine and
no CI run behind it, because CI has no KiCad. `docs/COMPATIBILITY.md` lists what
that leaves unproven.

Reproduce with `pytest tests/ -v --tb=short`, then `python build.py` and
`python scripts/smoke_binary.py dist/kicad-cli[.exe]`.

## 2. Frozen binary

```text
{scrub(smoke.stdout.strip())}
```

## 1. Test suite

```text
{scrub(suite.stdout.rstrip())}
```
"""
    out = ROOT / "docs" / "evidence" / f"live-smoke-{version}+{commit}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(f"wrote {out.relative_to(ROOT)} — {verdict}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
