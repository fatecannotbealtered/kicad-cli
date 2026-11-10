#!/usr/bin/env python3
"""Run the scripts of the pinned specification release on this project.

    python scripts/spec.py check                 the project check (check_project)
    python scripts/spec.py generate              write the generated content, then check
    python scripts/spec.py suites [--only G01]   both conformance suites, against the fake host
    python scripts/spec.py matrix --command "<path>" --set doc=<file> [...]
                                                 run a behavior matrix on the real application
    python scripts/spec.py eval --workspace-root <folder> [--only <id>]
                                                 run the evaluation questions with Claude Code
    python scripts/spec.py eval --check --fake-host
                                                 check each grader against its scripted agents
    python scripts/spec.py sync <version> [--since <version>]
                                                 pin another specification release; --since lists
                                                 the template changes since that one

The scripts come from the release named in .agent/SPEC_VERSION (for sync, the new one). It is
downloaded once from GitHub and kept in ~/.cache/tool-driver-cli-spec/. The download must match
.agent/SPEC_SHA256, which sync writes; without it, the SHA-256 file published with the release.
To use an unpacked release or a checkout of the specification instead, set TOOL_DRIVER_SPEC to
its directory.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "fatecannotbealtered/tool-driver-cli-spec"
RELEASES = f"https://github.com/{REPOSITORY}/releases/download"
API = f"https://api.github.com/repos/{REPOSITORY}/releases/tags"
WAITS = (2, 4, 8, None)  # a failed read is tried again after 2, 4 and 8 seconds
NAME = "tool-driver-cli-spec"
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def pinned_version() -> str:
    return (ROOT / ".agent" / "SPEC_VERSION").read_text(encoding="utf-8").strip()


def pinned_digest(version: str) -> str | None:
    """The SHA-256 of the release bundle that sync pinned, when it is this version's."""
    path = ROOT / ".agent" / "SPEC_SHA256"
    if version != pinned_version() or not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def bundle(version: str) -> tuple[Path, str | None]:
    """The unpacked release of this version, and the SHA-256 of its bundle when known."""
    local = os.environ.get("TOOL_DRIVER_SPEC")
    if local:
        path = Path(local).resolve()
        found = (path / "VERSION").read_text(encoding="utf-8").strip() if (path / "VERSION").is_file() else None
        if found != version:
            raise SystemExit(f"TOOL_DRIVER_SPEC={path} holds release {found}, not {version}")
        return path, None
    return download(version, pinned_digest(version))


def download(version: str, pin: str | None) -> tuple[Path, str]:
    """Release ``version`` from the download cache, downloaded and unpacked first when it is
    missing there; the bundle must have the SHA-256 ``pin``, or without it the published one."""
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / NAME / version
    folder = cache / f"{NAME}-{version}"
    known = (cache / "bundle.sha256").read_text(encoding="utf-8").strip() if (cache / "bundle.sha256").is_file() else None
    if (folder / "VERSION").is_file() and known and (pin is None or pin == known):
        return folder, known
    print(f"Downloading {NAME} {version} ...", file=sys.stderr)
    data = _asset(version, f"{NAME}-{version}.zip")
    expected = pin or _asset(version, f"{NAME}-{version}.sha256").decode("ascii").split()[0]
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        raise SystemExit(f"{NAME}-{version}.zip has SHA-256 {digest}, not {expected}; nothing was unpacked")
    cache.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.namelist():
            target = (cache / member).resolve()
            if not target.is_relative_to(cache.resolve()):
                raise SystemExit(f"the release archive holds an unsafe path: {member}")
        archive.extractall(cache)
    (cache / "bundle.sha256").write_text(digest + "\n", encoding="utf-8", newline="\n")
    return folder, digest


def _asset(version: str, name: str) -> bytes:
    """A file of the release: from its download link, or, when that keeps failing, through the
    GitHub API, which serves the same file another way. The caller checks its SHA-256."""
    url = f"{RELEASES}/v{version}/{name}"
    try:
        return _get(url)
    except OSError as exc:
        first = exc
    try:
        release = json.loads(_get(f"{API}/v{version}", "application/vnd.github+json"))
        asset = next(a["url"] for a in release["assets"] if a["name"] == name)
        return _get(asset, "application/octet-stream")
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        raise SystemExit(f"cannot download {url}: {first}; nor through the GitHub API: {exc!r}") from None


def _get(url: str, accept: str | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": accept} if accept else {})
    for wait in WAITS:
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - a fixed https URL
                return response.read()
        except OSError as exc:
            if wait is None or isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
                raise
            time.sleep(wait)
    return b""


def run(cmd: list[str], cwd: Path | None = None) -> int:
    # Without a console window the child must be handed this process's standard streams.
    sys.stdout.flush()
    return subprocess.call(cmd, cwd=cwd, creationflags=NO_WINDOW, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("check", "generate", "suites", "matrix", "eval", "sync"):
        print(__doc__, file=sys.stderr)
        return 2
    action, rest = argv[0], argv[1:]
    python = sys.executable
    if action == "sync":
        if len(rest) not in (1, 3) or len(rest) == 3 and rest[1] != "--since":
            print("usage: python scripts/spec.py sync <version> [--since <version>]", file=sys.stderr)
            return 2
        spec, digest = bundle(rest[0])
        code = run([python, str(spec / "scripts" / "sync_spec.py"), str(ROOT), *rest[1:]])
        if code:
            return code
        pin = ROOT / ".agent" / "SPEC_SHA256"
        if digest:
            pin.write_text(digest + "\n", encoding="utf-8", newline="\n")
        else:
            pin.unlink(missing_ok=True)
        return run([python, str(spec / "scripts" / "check_project.py"), str(ROOT), "--fix"])
    spec, _ = bundle(pinned_version())
    if action == "check":
        return run([python, str(spec / "scripts" / "check_project.py"), str(ROOT), *rest])
    if action == "generate":
        return run([python, str(spec / "scripts" / "check_project.py"), str(ROOT), "--fix", *rest])
    if action == "matrix":
        return run([python, str(spec / "scripts" / "run_matrix.py"), str(ROOT), *rest])
    if action == "eval":
        return run([python, str(spec / "scripts" / "run_eval.py"), str(ROOT), *rest])
    tool = next(iter(json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["bin"]))
    return run([python, "-m", "suites", "--project", str(ROOT), *rest, "--", python, "-m", tool.replace("-", "_")],
               cwd=spec / "conformance")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
