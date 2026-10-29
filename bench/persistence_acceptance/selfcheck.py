"""Harmless external-control check. Never classified as a KiCad fault experiment."""

import json
import os
import sys
import time

from .support import ROOT, SETTINGS, now, put
from .win32_control import OwnedProcess


def run():
    ROOT.mkdir(parents=True, exist_ok=False)
    result = {"class": "harmless_controller_validation", "started_at": now()}
    control = OwnedProcess(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        ROOT,
        os.environ.copy(),
        ROOT / "child.stdout",
        ROOT / "child.stderr",
        allow_control=SETTINGS["allow_owned_termination"],
    )
    try:
        birth = control.identify(control.pid)["creation_filetime"]
        try:
            control.identify(os.getpid())
        except RuntimeError:
            result["unowned_process_refused"] = True
        else:
            raise AssertionError("Unowned process was accepted")
        try:
            control.freeze(control.pid, sys.executable, expected_birth=birth + 1)
        except RuntimeError:
            result["wrong_birth_refused_before_attach"] = control.debug_pid is None
        else:
            raise AssertionError("Wrong birth identity was accepted")
        control.freeze(control.pid, sys.executable, expected_birth=birth)
        result["stop"] = control.stop()
        result["events"] = control.events
        assert result["stop"]["parent_exit"] == 0xE0424331
        result["status"] = "passed"
    except BaseException as exc:
        result.update(status="failed", failure=type(exc).__name__ + ": " + str(exc))
    finally:
        control.close()
    result["finished_at"] = now()
    put(ROOT / "RESULT.json", result)
    print(json.dumps(result))
    return result


def run_resume():
    """A file handshake proves pause/release without treating a delay as a stage."""
    ROOT.mkdir(parents=True, exist_ok=False)
    child = ROOT / "handshake.py"
    child.write_text(
        "from pathlib import Path\nimport time\n"
        "Path('ready').write_text('ready')\n"
        "while not Path('release').exists(): time.sleep(0.01)\n"
        "Path('completed').write_text('completed')\n",
        encoding="utf-8",
    )
    result = {"class": "harmless_resume_validation", "started_at": now()}
    control = OwnedProcess(
        [sys.executable, "-B", str(child)],
        ROOT,
        os.environ.copy(),
        ROOT / "child.stdout",
        ROOT / "child.stderr",
        allow_control=SETTINGS["allow_owned_termination"],
    )
    try:
        deadline = time.monotonic() + 10
        while not (ROOT / "ready").exists() and time.monotonic() < deadline:
            if not control.running():
                raise RuntimeError("Harmless child stopped before its handshake")
            time.sleep(0.01)
        assert (ROOT / "ready").is_file(), "Handshake stage not reached"
        birth = control.identify(control.pid)["creation_filetime"]
        control.freeze(control.pid, sys.executable, expected_birth=birth)
        (ROOT / "release").write_text("release", encoding="ascii")
        assert not (ROOT / "completed").exists()
        try:
            control.resume(expected_birth=birth + 1)
        except RuntimeError:
            result["wrong_birth_resume_refused"] = control.debug_event is not None
        else:
            raise AssertionError("Wrong birth resume accepted")
        control.resume(expected_birth=birth)
        deadline = time.monotonic() + 10
        while control.running() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not control.running() and control.exit_code() == 0
        assert (ROOT / "completed").read_text() == "completed"
        assert control.wait_tree_empty(5)
        result.update(status="passed", natural_exit=control.exit_code())
    except BaseException as exc:
        result.update(status="failed", failure=type(exc).__name__ + ": " + str(exc))
    finally:
        finalization = control.close()
        result["tree_finalization"] = finalization
        if not finalization["natural_tree_complete"]:
            result["status"] = "failed"
        result["events"] = control.events
    result["finished_at"] = now()
    put(ROOT / "RESULT.json", result)
    return result


def run_tree():
    """Root exit cannot stand in for owned descendant completion."""
    ROOT.mkdir(parents=True, exist_ok=False)
    result = {"class": "harmless_job_tree_validation", "cases": [], "started_at": now()}
    for natural in (False, True):
        folder = ROOT / ("natural-child" if natural else "live-child-refused")
        folder.mkdir()
        (folder / "child.py").write_text(
            "import os,time\nfrom pathlib import Path\n"
            "Path('ready').write_text(str(os.getpid()))\n"
            "while not Path('release').exists(): time.sleep(.01)\n"
            "Path('finished').write_text('natural')\n",
            encoding="utf-8",
        )
        (folder / "parent.py").write_text(
            "import subprocess,sys,time\nfrom pathlib import Path\n"
            "subprocess.Popen([sys.executable,'-B','child.py'])\n"
            "while not Path('ready').exists(): time.sleep(.01)\n",
            encoding="utf-8",
        )
        control = OwnedProcess(
            [sys.executable, "-B", str(folder / "parent.py")],
            folder,
            os.environ.copy(),
            folder / "stdout",
            folder / "stderr",
            allow_control=SETTINGS["allow_owned_termination"],
        )
        row = {"expected_natural": natural, "status": "failed"}
        try:
            deadline = time.monotonic() + 15
            while control.running() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not control.running() and control.exit_code() == 0
            row["root_exit"] = control.exit_code()
            child_pid = int((folder / "ready").read_text())
            row["child_before_close"] = control.identify(child_pid)
            try:
                control.identify(os.getpid())
            except RuntimeError:
                row["foreign_process_refused"] = True
            else:
                raise AssertionError("Foreign process accepted into the control scope")
            assert control.tree_snapshot()["members"]
            if natural:
                (folder / "release").write_text("release", encoding="ascii")
                assert control.wait_tree_empty(10)
            final = control.close()
            row["finalization"] = final
            assert final["natural_tree_complete"] is natural
            assert final["controlled_termination"] is (not natural)
            assert not final["after_cleanup"]["members"]
            assert (folder / "finished").exists() is natural
            row["status"] = "passed"
        except BaseException as exc:
            row["failure"] = type(exc).__name__ + ": " + str(exc)
        finally:
            if not control.closed:
                control.close()
            row["events"] = control.events
            result["cases"].append(row)
    result.update(
        status="passed" if all(r["status"] == "passed" for r in result["cases"]) else "failed",
        finished_at=now(),
    )
    put(ROOT / "RESULT.json", result)
    return result
