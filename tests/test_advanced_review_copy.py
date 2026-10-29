"""Controlled copy/initialization races at the public review entry.

Native subprocesses are synthetic: these assertions prove binding, not ERC/DRC
or image quality. Real final-EXE engineering acceptance is recorded separately.
"""

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench/rc_trial/execution"))
import review_binding as audit  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "advanced_review_entry", ROOT / "bench/advanced_edit/review.py"
)
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def session(tmp_path, monkeypatch):
    package, live, config = (tmp_path / name for name in ("package", "live", "configuration"))
    base = package / "rc/execution"
    (base / "consumers").mkdir(parents=True)
    live.mkdir()
    for ext in ("sch", "pcb", "pro"):
        shutil.copyfile(
            ROOT / "bench/advanced_edit/fixtures/rc-baseline" / ("rc4.kicad_" + ext),
            live / ("rc4.kicad_" + ext),
        )
    for name in ("sym-lib-table", "fp-lib-table"):
        target = config / "10.0" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic empty table")
    put(
        package / "advanced-edit/requirements/FROZEN.json",
        {"baseline_files": audit.tree(live), "pcb_strategy": {"same_board": True}},
    )
    dependency = tmp_path / "dependency.bin"
    dependency.write_bytes(b"synthetic, never executed")
    info = {"path": str(dependency), "sha256": audit.sha(dependency)}
    sdk = tmp_path / "_driver_sdk"
    sdk.mkdir()
    (tmp_path / "driver_spec").mkdir()
    settings = {
        "environment": {
            "KICAD_CLI_DRIVER_SDK_PATH": str(sdk),
            **{
                name: str(dependency)
                for name in ("KICAD_CLI_PYTHON", "KICAD_CLI_OFFICIAL", "KICAD_CLI_SCOPE_FILE")
            },
        },
        "readonly_consumer_python": sys.executable,
        "native_files": {name: info for name in ("python", "official", "pcbnew", "_pcbnew")},
        "libraries": {"fixture": info},
    }
    put(package / "rc/agent-packet/DEPENDENCIES.json", settings)
    put(
        package / "rc/agent-packet/REVISION.json",
        {"candidate": str(dependency), "sha256": info["sha256"]},
    )
    work = base / "review-cycle"
    state = SimpleNamespace(
        package=package, live=live, config=config, work=work, calls=[], inject=lambda phase: None
    )

    def fake_run(argv, **kwargs):
        work = state.work
        name = next(
            p.name for p in (work / "evidence").iterdir() if not (p / "finish.json").exists()
        )
        state.calls.append(name)
        if name == "geometry":
            state.inject("before-geometry")
        # Produce complete synthetic artifacts, allowing the real binding/READY
        # implementation to run instead of mocking its verdict.
        if "--output" in argv:
            output = Path(argv[argv.index("--output") + 1])
            if name == "schematic-svg":
                output = output / "rc4.svg"
            output.parent.mkdir(parents=True, exist_ok=True)
            if "erc" in name:
                put(output, {"sheets": [{"violations": []}]})
            elif "drc" in name:
                put(output, {"violations": [], "unconnected_items": [], "schematic_parity": []})
            else:
                output.write_text("binding-only synthetic output")
        elif name.endswith("raster"):
            Path(argv[3]).write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
        elif name == "reopened":
            put(Path(argv[4]), {"footprints": []})
        elif name == "geometry":
            put(Path(argv[6]), {"passed": True, "synthetic": True})
        state.inject(name)
        return SimpleNamespace(returncode=0, stdout=b'{"passed":true,"synthetic":true}', stderr=b"")

    monkeypatch.setattr(entry.subprocess, "run", fake_run)
    state.argv = [
        "review.py",
        "check",
        "--package-root",
        str(package),
        "--out",
        str(work),
        "--stage",
        "S0",
        "--schematic",
        str(live / "rc4.kicad_sch"),
        "--board",
        str(live / "rc4.kicad_pcb"),
        "--config-root",
        str(config),
    ]
    monkeypatch.setattr(sys, "argv", state.argv)
    return state


def visual_and_freeze(s):
    review = s.work / "review"
    ready = audit.read(review / "READY.json")
    put(
        review / "VISUAL_REVIEW.json",
        {
            "passed": True,
            "review_id": ready["review_id"],
            "ready_sha256": audit.sha(review / "READY.json"),
            "images": ready["images"],
            "reviewer": "synthetic binding test only",
            "observations": {name: "Not an actual image acceptance" for name in audit.IMAGES},
        },
    )
    audit.freeze(s.work, s.package / "rc/execution", s.work / "packet", review, s.work / "snapshot")


def test_no_drift_completes_one_bound_copy_cycle(session):
    assert entry.main() == 0
    visual_and_freeze(session)
    audit.verify_snapshot(session.work / "snapshot")


@pytest.mark.parametrize("target", ["configuration", "project"])
def test_copy_differing_from_unchanged_original_is_rejected(session, monkeypatch, target):
    original = shutil.copyfile

    def faulty(src, dst, *args, **kwargs):
        result = original(src, dst, *args, **kwargs)
        dst = Path(dst)
        if (target == "project" and dst.name == "rc4.kicad_pcb") or (
            target == "configuration" and dst.name == "sym-lib-table"
        ):
            dst.write_bytes(dst.read_bytes() + b" changed copy")
        return result

    monkeypatch.setattr(entry.shutil, "copyfile", faulty)
    original_tree = shutil.copytree

    def faulty_tree(src, dst, *args, **kwargs):
        result = original_tree(src, dst, *args, **kwargs)
        if target == "configuration" and Path(src) == session.config:
            altered = Path(dst) / "10.0/sym-lib-table"
            altered.write_bytes(altered.read_bytes() + b" changed copy")
        return result

    monkeypatch.setattr(entry.shutil, "copytree", faulty_tree)
    with pytest.raises(ValueError, match="differ"):
        entry.main()
    assert not session.calls
    assert not (session.work / "review/READY.json").exists()


def test_declared_copy_preference_initialization_is_recorded_but_not_inherited(session):
    def preferences(name):
        if name == "initialize-xml":
            put(session.work / "configuration/10.0/eeschema.json", {"synthetic": True})
            put(session.work / "project/rc4.kicad_prl", {"synthetic": True})

    session.inject = preferences
    assert entry.main() == 0
    report = audit.read(session.work / "inputs/configuration-relocation.json")
    assert report["original"] != report["initialized"]
    assert report["project_before"] != report["project_initialized"]
    visual_and_freeze(session)
    (session.work / "inputs/configuration-relocation.json").write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        audit.verify(
            session.work,
            session.package / "rc/execution",
            session.work / "packet",
            session.work / "review",
        )


@pytest.mark.parametrize("phase", ["copy", "initialize-xml", "initialize-erc", "initialize-drc"])
@pytest.mark.parametrize(
    "target", ["sch", "pcb", "pro", "configuration", "local-rule", "local-library"]
)
def test_original_drift_cannot_be_adopted_as_first_binding(session, monkeypatch, phase, target):
    def drift():
        path = {
            "sch": session.live / "rc4.kicad_sch",
            "pcb": session.live / "rc4.kicad_pcb",
            "pro": session.live / "rc4.kicad_pro",
            "configuration": session.config / "10.0/added.json",
            "local-rule": session.live / "rc4.kicad_dru",
            "local-library": session.live / "sym-lib-table",
        }[target]
        if target == "sch":
            before = path.read_bytes()
            assert b'"220nF"' in before
            path.write_bytes(before.replace(b'"220nF"', b'"330nF"'))
        else:
            path.write_bytes((path.read_bytes() if path.exists() else b"") + b" drift")

    if phase == "copy":
        original = shutil.copytree

        def copytree(src, dst, *args, **kwargs):
            result = original(src, dst, *args, **kwargs)
            if Path(src) == session.config:
                drift()
            return result

        monkeypatch.setattr(entry.shutil, "copytree", copytree)
    else:
        session.inject = lambda name: drift() if name == phase else None
    with pytest.raises(ValueError, match="(?i)(drift|changed|differ)"):
        entry.main()
    assert not (session.work / "review/READY.json").exists()
    with pytest.raises(ValueError):
        audit.verify(
            session.work,
            session.package / "rc/execution",
            session.work / "packet",
            session.work / "review",
        )
    assert not (session.work / "snapshot/MANIFEST.json").exists()
