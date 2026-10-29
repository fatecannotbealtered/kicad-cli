"""The old payloads as the reference for the commands that replaced them.

A command moved off pcbnew must say exactly what its payload said -- every
number, every list in its order, every word -- on every board KiCad ships.
Until the payloads are deleted along with SWIG, they are the most exacting
reference there is; anything a port changes on purpose belongs in a later
change that says so.

Test scaffolding, deleted with the payloads.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import kicad_env  # noqa: E402


def kicad_python() -> str | None:
    try:
        return kicad_env.find_python()
    except Exception:  # noqa: BLE001 - resolution must never break collection
        return None


needs_payload = pytest.mark.skipif(
    not DEMOS.exists() or kicad_python() is None,
    reason=f"needs KiCad's interpreter to run the old payload ({SKIP_REASON})",
)

DEMO_BOARDS = sorted(DEMOS.rglob("*.kicad_pcb")) if DEMOS.exists() else []


def legacy_port(*argv, environment=None):
    """Explicit source-only domain equivalence, never the public driver's contract."""
    from native_process import directory, run

    assert not os.environ.get("KICAD_CLI_TEST_BINARY"), "Private ports cannot certify an EXE"
    aliases = {
        ("board", "analyze", "ampacity"): ("board", "audit"),
        ("board", "analyze", "reference-plane"): ("board", "plane"),
        ("project", "parity"): ("board", "parity"),
        ("board", "from-netlist"): ("board", "from-netlist"),
        ("board", "footprint", "place"): ("board", "place"),
        ("board", "zone", "stitch"): ("board", "stitch"),
        ("board", "pour"): ("board", "pour"),
        ("board", "route", "rebuild"): ("board", "route"),
    }
    selected = next((p for p in aliases if tuple(argv[: len(p)]) == p), None)
    assert selected is not None, argv
    words = [*aliases[selected], *argv[len(selected) :]]
    if selected == ("board", "route", "rebuild"):
        words += ["--mode", "full"]
    owned = Path(os.environ["KICAD_CLI_NATIVE_TEST_ROOT"]).resolve()
    if selected not in {
        ("board", "analyze", "ampacity"),
        ("board", "analyze", "reference-plane"),
        ("project", "parity"),
    }:
        for flag in ("--board", "--out"):
            if flag in words:
                assert Path(words[words.index(flag) + 1]).resolve().is_relative_to(owned)
    env = dict(os.environ if environment is None else environment)
    env["KICAD_CLI_STATE"] = str(owned / "domain-state")
    for key in ("KICAD_CLI_TRACE", "KICAD_CLI_ERROR_TRACE"):
        env.pop(key, None)
    script = (
        "import json,sys; from kicad_cli import arguments,envelope,registry; "
        "command,opts=arguments.parse(json.loads(sys.argv[1]),"
        "registry._base_commands(),registry.GLOBAL_OPTIONS); "
        "envelope.configure(compact=True); command['handler'](opts)"
    )
    record, stdout, _ = run(
        [sys.executable, "-B", "-c", script, json.dumps(words)],
        directory("domain-port"),
        cwd=REPO,
        env=env,
        timeout=3600,
        allowed=None,
        role="source-domain-equivalence",
    )
    value = json.loads(stdout)
    assert (record["exit"] == 0) == value["ok"], (record, value)
    return value


def native(*argv: str) -> dict:
    """Public move/read tests plus explicitly retained private power-port comparisons."""
    # Domain/native-SDK port equivalence, separate from public driver receipts.
    # This branch deliberately emits no synthetic FCC trace.
    task = " ".join(argv[:3])
    modules = {
        "project netclass set": "netclass",
        "board track widen": "widen",
        "board route repair": "route",
        "board route rebuild": "route",
        "board route rewidth": "route",
    }
    if task in modules:
        from native_process import directory, run

        from kicad_cli import arguments, registry

        words = [
            "board",
            "netclass"
            if task == "project netclass set"
            else "widen"
            if task == "board track widen"
            else "route",
            *argv[3:],
        ]
        if task.startswith("board route "):
            words += ["--mode", "full" if task.endswith("rebuild") else argv[2]]
        _, opts = arguments.parse(words, registry._base_commands(), registry.GLOBAL_OPTIONS)
        script = (
            "import json,sys,importlib\nfrom kicad_cli import envelope\n"
            "envelope.configure(compact=True)\np=json.loads(sys.argv[1])\n"
            "importlib.import_module('kicad_cli.native.'+sys.argv[2]).run(p['board'],p)"
        )
        owned = Path(os.environ["KICAD_CLI_NATIVE_TEST_ROOT"]).resolve()
        assert Path(opts["board"]).resolve().is_relative_to(owned)
        env = dict(os.environ, KICAD_CLI_STATE=str(owned / "domain-state"))
        for key in ("KICAD_CLI_TRACE", "KICAD_CLI_ERROR_TRACE"):
            env.pop(key, None)
        record, stdout, _ = run(
            [sys.executable, "-B", "-c", script, json.dumps(opts), modules[task]],
            directory("domain-power"),
            cwd=REPO,
            env=env,
            timeout=3600,
            allowed=None,
            role="source-domain-power-equivalence",
        )
        value = json.loads(stdout)
        assert (record["exit"] == 0) == value["ok"], (record, value)
        return value
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    if argv[:3] == ("board", "footprint", "move") and "--board" in argv:
        directory = Path(argv[argv.index("--board") + 1]).parent
        scope = directory / "move-scope.json"
        if not scope.exists():
            grant(directory)
        env.update(
            KICAD_CLI_SCOPE_FILE=str(scope), KICAD_CLI_STATE_DIR=str(directory / "driver-state")
        )
        if "--confirm" not in argv and "--dry-run" not in argv:
            argv = (*argv, "--dry-run")
    from native_process import directory, run

    record, stdout, _ = run(
        [sys.executable, "-m", "kicad_cli.main", *argv],
        directory("public-read-move"),
        cwd=REPO,
        env=env,
        timeout=3600,
        allowed=None,
        role="public-source-read-move",
    )
    value = json.loads(stdout)
    assert (record["exit"] == 0) == value["ok"], (record, value)
    return value


def differences(old, new, where="data") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for key in sorted(set(old) | set(new)):
            if key not in old or key not in new:
                out.append(f"{where}.{key}: only in {'the payload' if key in old else 'native'}")
            else:
                out += differences(old[key], new[key], f"{where}.{key}")
        return out
    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            return [f"{where}: {len(old)} items from the payload, {len(new)} native"]
        out = []
        for i, (a, b) in enumerate(zip(old, new, strict=True)):
            out += differences(a, b, f"{where}[{i}]")
        return out
    return [] if old == new else [f"{where}: payload {old!r:.100}, native {new!r:.100}"]


def assert_same(old: dict, new: dict) -> None:
    """Same outcome; the same error, or every field of the data the same."""
    assert old.get("ok") == new.get("ok"), (old.get("error"), new.get("error"))
    if not old.get("ok"):
        keep = ("code", "message", "details")
        assert {k: old["error"][k] for k in keep} == {k: new["error"][k] for k in keep}
        return
    assert differences(old["data"], new["data"]) == []
