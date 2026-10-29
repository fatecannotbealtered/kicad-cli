"""One-shot native test calls with persistent output and owned-tree evidence."""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def directory(kind):
    root = Path(os.environ["KICAD_CLI_NATIVE_TEST_EVIDENCE"])
    root.mkdir(parents=True, exist_ok=True)
    result = root / f"{kind}-{len(list(root.iterdir())):04d}"
    result.mkdir(exist_ok=False)
    return result


def run(
    command,
    folder,
    *,
    timeout=600,
    env=None,
    report=None,
    allowed=(0,),
    role="native-reference",
    cwd=None,
):
    sys.path.insert(0, str(ROOT / "bench"))
    from native_quarantine import HASH_KEY, PATH_KEY, blocked_attempt
    from process_evidence import native_debug_enabled, run_recorded

    blocked = blocked_attempt(command, os.environ)
    if blocked:
        import pytest

        with (Path(folder) / "QUARANTINED.json").open("x", encoding="utf-8") as stream:
            json.dump(blocked, stream, indent=2)
        pytest.skip("Retained native failure quarantined before launch: " + blocked["id"])
    environment = dict(os.environ if env is None else env)
    environment.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    diagnostic_flag = os.environ.get("KICAD_BENCH_NATIVE_DEBUG", "0")
    if diagnostic_flag not in {"0", "1"}:
        raise ValueError("KICAD_BENCH_NATIVE_DEBUG must be 0 or 1")
    debug = native_debug_enabled(
        command,
        os.environ.get("KICAD_BENCH_NATIVE_ROOT"),
        enabled=diagnostic_flag == "1",
    )
    # Test-control variables never become product/native application settings.
    for key in ("KICAD_BENCH_NATIVE_ROOT", "KICAD_BENCH_NATIVE_DEBUG", PATH_KEY, HASH_KEY):
        environment.pop(key, None)
    record, _ = run_recorded(
        folder,
        "process",
        command,
        cwd=cwd or folder,
        env=environment,
        timeout=timeout,
        report=report,
        role=role,
        debug=debug,
    )
    stdout = (folder / "process.stdout").read_bytes()
    stderr = (folder / "process.stderr").read_bytes()
    assert record["execution"] in {"completed", "nonzero_exit"}, record
    assert 0 <= record["exit"] < 256, ("Spontaneous process exception indicator", record)
    assert b"Windows fatal exception:" not in stderr, (
        "Spontaneous process exception indicator",
        record,
    )
    if allowed is not None:
        assert record["exit"] in allowed, (record, stderr.decode("utf-8", "replace"))
    return record, stdout, stderr


def payload(name, args, *, timeout=1800):
    """The retained SDK reference payload, never a public-driver acceptance."""
    from kicad_cli import kicad_env

    folder = directory("sdk-" + name)
    env = dict(
        os.environ,
        KICAD_CLI_STATE=str(Path(os.environ["KICAD_CLI_NATIVE_TEST_ROOT"]) / "domain-state"),
    )
    env.update(TEMP=str(folder), TMP=str(folder))
    for key in ("KICAD_CLI_TRACE", "KICAD_CLI_ERROR_TRACE"):
        env.pop(key, None)
    record, stdout, _ = run(
        [
            kicad_env.find_python(),
            "-B",
            "-u",
            str(ROOT / "kicad_cli/payload" / (name + ".py")),
            *args,
        ],
        folder,
        env=env,
        timeout=timeout,
        allowed=None,
        role="legacy-native-reference-payload",
    )
    # Historical private payloads can append SWIG leak diagnostics after JSON.
    # This tolerance is not used for the public driver stdout contract.
    values = []
    for line in stdout.decode("utf-8", "replace").splitlines():
        if line.strip().startswith("{"):
            try:
                values.append(json.loads(line))
            except ValueError:
                pass
    assert len(values) == 1, ("no unique private payload envelope", record)
    value = values[0]
    details = value.get("error", {}).get("details") or {}
    for key in ("exit_code", "returncode"):
        code = details.get(key)
        if isinstance(code, int):
            assert 0 <= code < 256, ("Spontaneous native child exception", value, record)
    assert isinstance(value.get("ok"), bool), (value, record)
    assert (record["exit"] == 0) == value["ok"], (value, record)
    return value
