"""Freeze or consume one harmless four-case response-loss plan.

Freeze is read-only apart from the explicitly new plan file; no child starts.
Execute requires the original plan SHA and a fresh output directory. This
controller itself must be observed by the parent's complete Job controller.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import types


ROOT = Path(__file__).resolve().parent
SOURCE_FILES = ("receiver_probe.py", "response_relay.py", "harmless_service.py", "validate_receiver.py")
CASE_IDS = ("chunked-ascii", "unicode", "nonzero", "empty-stdout")
CANARIES = (b"SERVICE-STDOUT-CANARY-7fb6", b"SERVICE-STDERR-CANARY-671a")


def utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": sha(path), "bytes": path.stat().st_size}


def put(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def load_raw(name, path, verified_raw):
    module = types.ModuleType(name)
    module.__file__ = str(Path(path).resolve())
    sys.modules[name] = module
    # Compile the exact bytes that verify_plan checked, with no intervening read
    # of a same-named path in a copied or mutable method directory.
    exec(compile(verified_raw, module.__file__, "exec"), module.__dict__)
    return module


def environment():
    allowed = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP")
    inherited = {key.upper(): value for key, value in os.environ.items() if key.upper() in allowed}
    inherited.update(
        PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
    )
    return inherited


def source_members():
    # No sibling executable input can quietly enter the receiver method.
    return sorted(path.name for path in ROOT.iterdir() if path.is_file() and path.suffix.lower() in (".py", ".pyc", ".pyd"))


def private_path_in_caller_input(value, private_directories, own_empty_cwd):
    """Inspect decoded values, preserving Windows directory component boundaries."""
    normalized = value.replace("\\", "/").casefold().rstrip("/")
    allowed = str(own_empty_cwd.resolve()).replace("\\", "/").casefold().rstrip("/")
    if normalized == allowed:
        return False
    for private in private_directories:
        prefix = str(private.resolve()).replace("\\", "/").casefold().rstrip("/")
        # Prefix-only sibling names are not the private directory. Accept a
        # separator, end of string, or a common decoded path-list/text delimiter.
        if re.search(re.escape(prefix) + r"(?=$|[/;\s\"'])", normalized):
            return True
    return False


def freeze(args):
    if source_members() != sorted(SOURCE_FILES):
        raise ValueError("Receiver method has undeclared executable inputs")
    plan = {
        "kind": "receiver-response-loss-harmless-plan-v1", "created_at": utc(),
        "method_root": str(ROOT), "controller_path": str(Path(__file__).resolve()),
        "method_files": {name: record(ROOT / name) for name in SOURCE_FILES},
        "executable_members": source_members(),
        "python": record(args.python), "owned_process_source": record(args.owned_process_source),
        "case_ids": list(CASE_IDS), "environment": environment(),
        "working_directory_rule": "Each caller and service uses a distinct, initially empty output child cwd",
        "output_location_is_delivery_only": True,
        "service_dispatch_budget_per_case": 1,
        "caller_request_identity": "receiver-proof:<case>; SHA256 of the exact frozen service chunks and exit",
        "timeouts_seconds": {"caller_ready": 30, "caller_after_eof": 30, "service": 60, "service_tree": 10},
        "caller_output_contract": {"received_bytes": 0, "eof": True, "service_outcome_known": False},
        "expected_service_exits": {"chunked-ascii": 0, "unicode": 0, "nonzero": 7, "empty-stdout": 0},
        "unexecuted_error_paths": ["service callback raises instead of returning its actual observed result"],
        "counters": {
            "controller": "one Python process, externally observed",
            "caller": "four independent harmless Python processes",
            "service": "four independent harmless Python processes",
            "product": 0, "kicad_native": 0,
        },
        "do_not_claim": ["product functional pass", "service success from exit zero alone", "write interruption", "process-kill recovery", "power-loss durability", "OS sandbox"],
    }
    put(args.plan, plan)
    print(json.dumps({"kind": "plan_only", "plan": record(args.plan), "child_processes_started": 0}))
    return 0


def verify_plan(path, digest):
    raw_plan = Path(path).read_bytes()
    if hashlib.sha256(raw_plan).hexdigest() != digest:
        raise ValueError("Original frozen plan digest differs")
    plan = json.loads(raw_plan)
    if plan["kind"] != "receiver-response-loss-harmless-plan-v1" or tuple(plan["case_ids"]) != CASE_IDS:
        raise ValueError("Plan kind or exact case IDs differ")
    if Path(plan["method_root"]).resolve(strict=True) != ROOT:
        raise ValueError("Frozen method root cannot migrate")
    if Path(plan["controller_path"]).resolve(strict=True) != Path(__file__).resolve():
        raise ValueError("Frozen controller path cannot migrate")
    if plan["executable_members"] != source_members() or source_members() != sorted(SOURCE_FILES):
        raise ValueError("Executable membership drift")
    if len(plan["method_files"]) != len(SOURCE_FILES) or tuple(plan["method_files"]) != SOURCE_FILES:
        raise ValueError("Exact method source set differs")
    verified_raw = {}
    for name in SOURCE_FILES:
        identity = plan["method_files"][name]
        if Path(identity["path"]).resolve(strict=True) != (ROOT / name).resolve(strict=True):
            raise ValueError("Frozen method file cannot migrate: " + name)
    for key, identity in [*plan["method_files"].items(), ("python", plan["python"]), ("owned_process_source", plan["owned_process_source"])]:
        frozen_path = Path(identity["path"]).resolve(strict=True)
        raw = frozen_path.read_bytes()
        actual = {"path": str(frozen_path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        if actual != identity:
            raise ValueError("Frozen method or executable input drift: " + identity["path"])
        if key != "python":
            verified_raw[key] = raw
    if plan["environment"] != environment():
        raise ValueError("Frozen caller/service environment differs")
    return plan, verified_raw


def execute(args):
    plan, verified_raw = verify_plan(args.plan, args.plan_sha256)
    output = Path(args.output).resolve()
    if output.exists() or output.is_relative_to(ROOT) or ROOT.is_relative_to(output):
        raise ValueError("Output must be a fresh directory outside immutable method inputs")
    output.mkdir(parents=True, exist_ok=False)
    put(output / "INPUT-CONSUMED.json", {"at": utc(), "plan": record(args.plan), "controller_pid": os.getpid(), "controller": record(__file__)})
    relay = load_raw("receiver_loss_relay_bound", plan["method_files"]["response_relay.py"]["path"], verified_raw["response_relay.py"])
    service = load_raw("receiver_loss_service_bound", plan["method_files"]["harmless_service.py"]["path"], verified_raw["harmless_service.py"])
    owned = load_raw("receiver_loss_owned_bound", plan["owned_process_source"]["path"], verified_raw["owned_process_source"])
    cases = []
    for case_id in CASE_IDS:
        # Recheck every dependency and exact ID before starting the next case.
        verify_plan(args.plan, args.plan_sha256)
        case_output = output / case_id
        case_output.mkdir()
        caller_cwd = case_output / "caller-cwd"
        service_cwd = case_output / "service-cwd"
        caller_cwd.mkdir()
        service_cwd.mkdir()
        chunks = service.CASES[case_id]
        request_bytes = json.dumps({
            "case_id": case_id,
            "stdout_hex": [chunk.hex() for chunk in chunks["stdout"]],
            "stderr_hex": [chunk.hex() for chunk in chunks["stderr"]],
            "exit": chunks["exit"],
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        spec = relay.CallerSpec(
            python=plan["python"]["path"], python_sha256=plan["python"]["sha256"],
            probe=str(ROOT / "receiver_probe.py"), probe_sha256=plan["method_files"]["receiver_probe.py"]["sha256"],
            operation_id="receiver-proof:" + case_id,
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            cwd=str(caller_cwd), environment=dict(plan["environment"]),
        )
        caller_inputs = json.dumps({"argv": spec.command(), "environment": spec.environment}, ensure_ascii=False).encode("utf-8")
        decoded_caller_inputs = [*spec.command(), *spec.environment.keys(), *spec.environment.values()]
        private_directories = [case_output, case_output / "relay-private", case_output / "service"]
        no_private_paths = all(
            not private_path_in_caller_input(value, private_directories, caller_cwd)
            for value in decoded_caller_inputs
        )
        if not no_private_paths:
            raise ValueError("Private service or relay evidence path leaked into decoded caller argv/environment")
        if any(canary in caller_inputs for canary in CANARIES):
            raise ValueError("Service canary leaked into caller inputs before dispatch")
        caller = relay.OwnedCaller(spec, case_output / "caller", owned.WindowsJob)
        service_calls = []

        def execute_service_once():
            if service_calls:
                raise RuntimeError("Second service dispatch denied")
            service_calls.append(utc())
            command = [plan["python"]["path"], "-B", "-S", str(ROOT / "harmless_service.py"), "--case", case_id]
            job = owned.WindowsJob()
            service_output = case_output / "service"
            service_output.mkdir()
            put(service_output / "INPUT.json", {"at": utc(), "command": command, "cwd": str(service_cwd), "environment": plan["environment"], "single_dispatch": True})
            timed_out = False
            process = None
            try:
                with (service_output / "stdout").open("xb") as stdout, (service_output / "stderr").open("xb") as stderr:
                    process = subprocess.Popen(
                        command, cwd=service_cwd, env=plan["environment"], stdout=stdout, stderr=stderr,
                        stdin=subprocess.DEVNULL, creationflags=0x08000000 | 0x4, close_fds=True,
                    )
                    job.assign_and_resume(process)
                    put(service_output / "START.json", {"at": utc(), "root_pid": process.pid, "root_identity": job.root_identity, "initial_job": job.snapshot()})
                    try:
                        exit_code = process.wait(timeout=plan["timeouts_seconds"]["service"])
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        # Only this already-attributed synthetic service's Job.
                        job.terminate()
                        exit_code = process.wait(timeout=15)
            finally:
                deadline = time.monotonic() + plan["timeouts_seconds"]["service_tree"]
                while job.active() and time.monotonic() < deadline:
                    time.sleep(0.01)
                finalization = job.close()
                put(service_output / "JOB-FINISH.json", {"at": utc(), "job_finalization": finalization, "root_identity": getattr(job, "root_identity", None), "timed_out": timed_out})
            return {
                "exit": exit_code, "timed_out": timed_out,
                "root_identity": job.root_identity, "job_finalization": finalization,
                "stdout": record(service_output / "stdout"), "stderr": record(service_output / "stderr"),
            }

        result = relay.lose_one_response(
            caller=caller, execute_service_once=execute_service_once,
            private_output=case_output / "relay-private",
            binding={"plan": record(args.plan), "case_id": case_id, "request_sha256": spec.request_sha256},
        )
        actual_service = result["service_outcome"]
        service_stdout = (case_output / "service/stdout").read_bytes()
        service_stderr = (case_output / "service/stderr").read_bytes()
        caller_stdout = (case_output / "caller/stdout").read_bytes()
        caller_stderr = (case_output / "caller/stderr").read_bytes()
        checks = {
            "single_actual_dispatch": result["dispatches"] == len(service_calls) == 1,
            "callback_returned": result["service_callback_returned"] and result["service_callback_error"] is None,
            "service_exact_stdout": service_stdout == b"".join(chunks["stdout"]),
            "service_exact_stderr": service_stderr == b"".join(chunks["stderr"]),
            "actual_service_exit_preserved": actual_service is not None and actual_service["exit"] == chunks["exit"],
            "natural_service_job": actual_service is not None and actual_service["job_finalization"]["natural_tree_complete"] and not actual_service["job_finalization"]["controlled_termination"] and not actual_service["timed_out"],
            "caller_actual_empty_response": result["response_lost_at_caller"],
            "canaries_absent_from_caller_stdout_stderr_argv_env": not any(canary in caller_stdout + caller_stderr + caller_inputs for canary in CANARIES),
            "caller_received_no_evidence_paths": no_private_paths,
            "zero_response_bytes_written": result["caller"]["bytes_written_to_caller"] == 0,
        }
        # cwd necessarily identifies its own empty workspace, but is neither a
        # service-evidence path nor an argv/environment field. This is deliberate.
        case_result = {"case_id": case_id, "checks": checks, "passed": all(checks.values()), "relay_finish": record(case_output / "relay-private/RELAY-FINISH.json")}
        put(case_output / "RESULT.json", case_result)
        cases.append(case_result)
        if not case_result["passed"]:
            break
    verify_plan(args.plan, args.plan_sha256)
    result = {
        "at": utc(), "kind": "harmless-receiver-loss-result-v1",
        "plan": record(args.plan), "cases": cases,
        "passed": len(cases) == len(CASE_IDS) and all(case["passed"] for case in cases),
        "controller_processes": 1, "caller_processes": len(cases), "service_dispatches": len(cases),
        "product_processes": 0, "kicad_native_processes": 0,
        "controller_whole_job_evidence": "Must be bound by the external parent controller; this result alone is insufficient",
        "coverage": "Real receiver stdin EOF after withheld synthetic service response; separate exact output and actual exit evidence",
    }
    put(output / "RESULT.json", result)
    print(json.dumps({"result": record(output / "RESULT.json"), "passed": result["passed"]}))
    return 0 if result["passed"] else 1


def main():
    if not sys.dont_write_bytecode or not sys.flags.no_site:
        raise RuntimeError("Run this method with -B -S; frozen modules are loaded from raw source")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    freeze_parser = sub.add_parser("freeze")
    freeze_parser.add_argument("--plan", required=True)
    freeze_parser.add_argument("--python", required=True)
    freeze_parser.add_argument("--owned-process-source", required=True)
    execute_parser = sub.add_parser("execute")
    execute_parser.add_argument("--plan", required=True)
    execute_parser.add_argument("--plan-sha256", required=True)
    execute_parser.add_argument("--output", required=True)
    args = parser.parse_args()
    return freeze(args) if args.mode == "freeze" else execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
