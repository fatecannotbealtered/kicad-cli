"""Read-only review of retained public observations; never rerun a command."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from .public_contract import infrastructure
from .support import put, read, sha, tree


def review(root, binary, system_root):
    root = Path(root)
    report = read(root / "PUBLIC-RESULT.json")
    start = read(root / "PUBLIC-START.json")
    matrix = {c["id"]: c for c in read(root / "PUBLIC-MATRIX.json")["cases"]}
    environment = read(root / "SETUP.json")["environment"]
    native_paths = {
        Path(environment[k]).resolve() for k in ("KICAD_CLI_PYTHON", "KICAD_CLI_OFFICIAL")
    }
    assert report["binding"] == start["binding"]
    assert sha(binary) == report["binding"]["exe_sha256"]
    rows = []
    for case in report["cases"]:
        folder = root / "calls" / case["id"]
        # The original observation is the anchor. Never derive this expected
        # value from the trace being reviewed (including its embedded hashes).
        expected = case.get("trace_sha256")
        raw = (folder / "TRACE.json").read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            binding_problem = "original_trace_binding_missing"
        elif actual != expected:
            binding_problem = "trace_differs_from_original_binding"
        else:
            binding_problem = None
        if binding_problem:
            rows.append(
                {
                    "id": case["id"],
                    "original_status": case["status"],
                    "review_status": "failed_or_unproven",
                    "reason": binding_problem,
                    "trace_binding_verified": False,
                    "original_trace_sha256": expected,
                    "trace_sha256": actual,
                    "observer_complete": None,
                    "no_native_or_unclassified_process_observed": None,
                    "project_unchanged_now": tree(root / "project") == start["project_files"],
                }
            )
            continue
        trace = json.loads(raw)
        complete = (
            bool(trace.get("complete_tree"))
            and all(
                p.get("owned") and p.get("creation_filetime") and p.get("exit") is not None
                for p in trace.get("processes", [])
            )
            and bool(trace.get("processes"))
        )
        assert sha(folder / "stdout") == trace["stdout_sha256"]
        assert sha(folder / "stderr") == trace["stderr_sha256"]
        no_additional = all(infrastructure(p, binary, system_root) for p in trace["processes"])
        unchanged = tree(root / "project") == start["project_files"]
        allowed_processes = all(
            infrastructure(p, binary, system_root)
            or (
                matrix[case["id"]]["native_allowed"]
                and Path(p["image"]).resolve() in native_paths
                and sha(p["image"]) == p["image_sha256"]
            )
            for p in trace["processes"]
        )
        sufficient = complete and unchanged and allowed_processes
        rows.append(
            {
                "id": case["id"],
                "original_status": case["status"],
                "observer_complete": complete,
                "no_native_or_unclassified_process_observed": no_additional,
                "native_allowed": matrix[case["id"]]["native_allowed"],
                "project_unchanged_now": unchanged,
                "review_status": "retained_pass"
                if sufficient and case["status"] == "passed"
                else "failed_or_unproven",
                "trace_sha256": actual,
                "original_trace_sha256": expected,
                "trace_binding_verified": True,
                "stdout_sha256": trace["stdout_sha256"],
                "stderr_sha256": trace["stderr_sha256"],
            }
        )
    return {
        "binding": report["binding"],
        "original_report_sha256": sha(root / "PUBLIC-RESULT.json"),
        "cases": rows,
        "scope": "Three independent predicates; original failure is never upgraded. "
        "Native-allowed rows retain their original native checks, not a new native acceptance.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", required=True, type=Path)
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--system-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    put(args.out, review(args.case_dir, args.exe, args.system_root))


if __name__ == "__main__":
    main()
