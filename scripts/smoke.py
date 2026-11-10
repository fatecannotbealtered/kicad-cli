#!/usr/bin/env python3
"""Platform delivery for this machine (DREPO-042).

    python scripts/smoke.py [--fake-host]

It installs the packed packages from dist/ the way a user does, into a clean npm prefix,
from a local registry that serves only those tarballs. Then it starts the tool through the
npm command entry, runs `update` once, which points the entry at the native program (DCLI-075),
and runs the key operation through the entry. With --fake-host the key operation runs
against the fake host, for machines without the application (DVER-020); the record says so.
The package digests and the result go to dist/delivery-<os>-<cpu>.json.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build import target  # noqa: E402
from spec import bundle, pinned_version  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# The representative key operation, without the tool name. It must work on a clean machine;
# with --fake-host the fake host stands in for the application.
# context loads every registration and runs the adapter's probe; it needs no sample file and no KiCad.
KEY_OPERATION = ["context"]


def tarballs(package: dict) -> dict[str, Path]:
    found = {}
    for tarball in sorted((ROOT / "dist").glob("*.tgz")):
        with tarfile.open(tarball) as archive:
            manifest = json.loads(archive.extractfile("package/package.json").read().decode("utf-8"))
        if manifest.get("version") == package["version"]:
            found[manifest["name"]] = tarball
    return found


class Registry:
    """A local registry that serves the packed tarballs and nothing else."""

    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.files: dict[str, Path] = {}
        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add(self, files: dict[str, Path], version: str, latest: bool = True) -> None:
        for name, path in files.items():
            data = path.read_bytes()
            with tarfile.open(path) as archive:
                manifest = json.loads(archive.extractfile("package/package.json").read().decode("utf-8"))
            manifest["dist"] = {"tarball": f"{self.url}-/{path.name}", "shasum": hashlib.sha1(data).hexdigest(),  # noqa: S324 - npm's format
                                "integrity": "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()}
            document = self.documents.setdefault(name, {"name": name, "dist-tags": {}, "versions": {}})
            document["versions"][version] = manifest
            if latest or "latest" not in document["dist-tags"]:
                document["dist-tags"]["latest"] = version
            self.files[path.name] = path

    def _handler(self):
        registry = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                path = unquote(self.path.lstrip("/"))
                if path.startswith("-/") and path[2:] in registry.files:
                    body, kind = registry.files[path[2:]].read_bytes(), "application/octet-stream"
                elif path in registry.documents:
                    body, kind = json.dumps(registry.documents[path]).encode(), "application/json"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return Handler

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def entry(prefix: Path, tool: str) -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", str(prefix / f"{tool}.cmd")]
    return [str(prefix / "bin" / tool)]


def call(cmd: list[str], env: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=600, creationflags=NO_WINDOW)


def main(argv: list[str]) -> int:
    fake = "--fake-host" in argv
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    tool = next(iter(package["bin"]))
    prefix_var = tool.upper().replace("-", "_")
    system, cpu = target()
    platform_name = f"{package['name']}-{system}-{cpu}"
    files = tarballs(package)
    if package["name"] not in files or platform_name not in files:
        raise SystemExit(f"dist/ needs the tarballs of {package['name']} and {platform_name}; run build.py and pack.py")
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("npm was not found on PATH")
    registry = Registry()
    registry.add({n: files[n] for n in (package["name"], platform_name)}, package["version"])
    work = Path(tempfile.mkdtemp(prefix="delivery-"))
    prefix = work / "prefix"
    home = work / "home"
    home.mkdir()
    host = None
    try:
        env = {k: v for k, v in os.environ.items() if not k.lower().startswith("npm_config_") and not k.startswith(prefix_var + "_")}
        bin_dir = prefix if os.name == "nt" else prefix / "bin"
        env.update({"npm_config_prefix": str(prefix), "npm_config_registry": registry.url, "npm_config_cache": str(work / "cache"),
                    "npm_config_update_notifier": "false", "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
                    f"{prefix_var}_STATE_DIR": str(work / "state"), "HOME": str(home), "USERPROFILE": str(home)})
        steps = []

        def step(name: str, cmd: list[str], **extra: str) -> subprocess.CompletedProcess[str]:
            reply = call(cmd, {**env, **extra})
            steps.append({"step": name, "exit": reply.returncode})
            if reply.returncode != 0:
                print(f"{name} failed ({reply.returncode}):\n{reply.stdout[-2000:]}\n{reply.stderr[-2000:]}", file=sys.stderr)
            return reply

        step("install", [npm, "install", "-g", f"{package['name']}@{package['version']}", "--no-fund", "--no-audit"])
        version = step("version", entry(prefix, tool) + ["--version"])
        updated = step("update", entry(prefix, tool) + ["update"])
        extra = {}
        if fake:
            spec, _ = bundle(pinned_version())
            host = subprocess.Popen([sys.executable, "-m", "fakehost", "--state-dir", str(work / "state"),
                                     "--commands", str(ROOT / "contract" / "commands.json")], cwd=spec / "conformance",
                                    stdout=subprocess.PIPE, text=True, creationflags=NO_WINDOW)
            extra[f"{prefix_var}_FAKE_HOST"] = json.loads(host.stdout.readline())["address"]
        key = step("key_operation", entry(prefix, tool) + KEY_OPERATION, **extra)
        reported = _data(version.stdout).get("version")
        entry_state = _data(updated.stdout).get("entry")
        ok = all(s["exit"] == 0 for s in steps) and reported == package["version"] and entry_state == "direct" \
            and _envelope(key.stdout).get("ok") is True
        record = {
            "package": package["name"], "version": package["version"], "platform": f"{system}-{cpu}",
            "packages": {name: "sha512-" + base64.b64encode(hashlib.sha512(files[name].read_bytes()).digest()).decode()
                         for name in (package["name"], platform_name)},
            "key_operation": [tool, *KEY_OPERATION], "fake_host": fake, "entry": entry_state, "steps": steps,
            "result": "pass" if ok else "fail",
            "recorded_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        out = ROOT / "dist" / f"delivery-{system}-{cpu}.json"
        out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"Delivery on {system}-{cpu}: {record['result']} ({out.relative_to(ROOT)})")
        return 0 if ok else 1
    finally:
        if host is not None:
            host.terminate()
        registry.close()
        shutil.rmtree(work, ignore_errors=True)


def _envelope(text: str) -> dict:
    try:
        return json.loads(text.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def _data(text: str) -> dict:
    data = _envelope(text).get("data")
    return data if isinstance(data, dict) else {}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
