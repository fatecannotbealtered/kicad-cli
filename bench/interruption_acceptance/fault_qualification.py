"""Two finite observer-only fault probes; never modifies or imports product code."""

import ctypes as C
import json
import os
import sys
from pathlib import Path

import controller
from qualify import HOST, predicate, witness


def main():
    output = Path(sys.argv[1]).resolve()
    output.mkdir(exist_ok=False)
    here = Path(__file__).resolve().parent
    original_api, original_impl, original_put = (
        controller.APIs,
        controller._run_impl,
        controller.put,
    )
    source_hashes = {
        name: controller.sha(here / name)
        for name in (
            "controller.py",
            "fixture.py",
            "qualify.py",
            "fault_qualification.py",
            "METHOD.md",
        )
    }
    original_put(
        output / "FROZEN.json",
        {
            "at": controller.now(),
            "source_sha256": source_hashes,
            "expected": [
                "callback exception stays private; own Job terminates",
                "report-write exception cannot bypass independent own Job close",
            ],
            "scope": "harmless observer faults only; no product hook or product invocation",
        },
    )
    results = []
    for mode in ("callback_error", "callback_and_report_error"):
        case = output / mode
        case.mkdir()
        work, cache = case / "own-work", case / "empty-pycache"
        work.mkdir()
        cache.mkdir()
        operation = "fault-qualification-" + mode
        resources = {}
        close_proof = {"job_closed": False, "active_before_job_close": None, "process_handles": []}

        def track_impl(*args, resources=resources, **kwargs):
            resources.update(shared=kwargs["_resources"])
            return original_impl(*args, **kwargs)

        def tracked_api(resources=resources, close_proof=close_proof):
            api = original_api()
            native_close = api.CloseHandle

            def close(handle):
                job = resources.get("shared", {}).get("job")
                if handle == job:
                    account = C.create_string_buffer(48)
                    if api.QueryInformationJobObject(job, 1, account, 48, None):
                        close_proof["active_before_job_close"] = C.c_uint32.from_buffer(
                            account, 40
                        ).value
                code = C.c_uint32()
                if api.GetExitCodeProcess(handle, C.byref(code)):
                    close_proof["process_handles"].append(
                        {
                            "signaled": api.WaitForSingleObject(handle, 0) == 0,
                            "exit_code": code.value,
                        }
                    )
                closed = native_close(handle)
                if handle == job:
                    close_proof["job_closed"] = bool(closed)
                return closed

            api.CloseHandle = close
            return api

        def fault_put(path, value, mode=mode):
            if mode == "callback_and_report_error" and Path(path).name in (
                "controller-failure.json",
                "process-identities.json",
            ):
                raise OSError("synthetic report write failure")
            return original_put(path, value)

        def failed_witness(boundary, work=work, operation=operation):
            stage = witness(work, operation, "saved_committed")
            if not stage["qualified"] or not boundary["debug_event_held"]:
                raise AssertionError("The harmless fault probe did not reach its frozen stage")
            raise RuntimeError("synthetic-private-fixture-value")

        result, caught = None, None
        try:
            controller.APIs, controller._run_impl, controller.put = (
                tracked_api,
                track_impl,
                fault_put,
            )
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.upper().startswith(("PYTHON", "KICAD_CLI"))
            }
            result = controller.run(
                [
                    str(HOST),
                    "-B",
                    "-S",
                    "-X",
                    "pycache_prefix=" + str(cache),
                    str(here / "fixture.py"),
                    "saved_committed",
                    str(work),
                    operation,
                ],
                work,
                env,
                case / "observation",
                predicate=lambda raw, operation=operation: predicate(raw, operation),
                stage_witness=failed_witness,
                timeout=12,
                allow_owned_termination=True,
            )
        except OSError as exc:
            caught = type(exc).__name__
        finally:
            controller.APIs, controller._run_impl, controller.put = (
                original_api,
                original_impl,
                original_put,
            )
        checks = {
            "own_job_closed": close_proof["job_closed"],
            "job_empty_before_close": close_proof["active_before_job_close"] == 0,
            "process_exit_proven": bool(close_proof["process_handles"])
            and all(
                row["signaled"] and row["exit_code"] == controller.STOP_CODE
                for row in close_proof["process_handles"]
            ),
        }
        if mode == "callback_error":
            checks.update(
                no_boundary_match=result is not None and not result["qualified_interruption"],
                caller_zero_bytes=result is not None and result["caller_stdout_bytes"] == 0,
                public_exception_type_only=result is not None
                and result["controller_failure"] == "RuntimeError",
            )
        else:
            checks.update(
                report_error_propagated=caught == "OSError",
                no_success_report=not (case / "observation" / "result.json").exists(),
            )
        results.append(
            {
                "mode": mode,
                "checks": checks,
                "passed": all(checks.values()),
                "native_close_proof": close_proof,
            }
        )
    unchanged = all(controller.sha(here / name) == digest for name, digest in source_hashes.items())
    summary = {
        "at": controller.now(),
        "all_passed": all(row["passed"] for row in results) and unchanged,
        "sources_unchanged": unchanged,
        "cases": results,
        "real_product_qualified": False,
    }
    original_put(output / "SUMMARY.json", summary)
    print(json.dumps({"all_passed": summary["all_passed"], "cases": len(results)}))
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
