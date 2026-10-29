"""Record one public EXE invocation; default is a plan with no process or output writes."""

import argparse
import json
import os
import sys
from pathlib import Path

from .support import ASSETS, sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--text", action="store_true", help="Text help instead of a JSON envelope")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorized-by")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not all(p.is_absolute() for p in (args.exe, args.cwd, args.out)):
        parser.error("Explicit absolute executable, working and new output paths required")
    if sha(args.exe) != args.sha256 or args.out.exists():
        parser.error("EXE identity mismatch or output already exists; preserve the original")
    if not 1 <= args.timeout <= 3600:
        parser.error("A bounded timeout from 1 to 3600 seconds is required")
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not args.execute:
        print(
            json.dumps(
                {
                    "action": "plan_only",
                    "exe_sha256": args.sha256,
                    "engineering_arguments_supplied_by_caller": True,
                }
            )
        )
        return 0
    if not args.authorized_by:
        parser.error("Attribute existing test-copy authorization before execution")
    args.out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(ASSETS["support"]))
    from process_evidence import run_recorded

    record, _ = run_recorded(
        args.out,
        "public",
        [str(args.exe), *command],
        cwd=args.cwd,
        env=os.environ.copy(),
        timeout=args.timeout,
        stdout_json=not args.text,
        role="public-candidate-call",
        binding={"exe_sha256": args.sha256, "authorized_by": args.authorized_by},
    )
    print(
        json.dumps(
            {
                "exit": record["exit"],
                "execution": record["execution"],
                "job_finalization": record.get("job_finalization"),
                "stdout": str(args.out / "public.stdout"),
                "stderr": str(args.out / "public.stderr"),
                "record": str(args.out / "public.call.json"),
            }
        )
    )
    if record["execution"] not in {"completed", "nonzero_exit"}:
        return 1
    if record.get("capture_errors") or (not args.text and record.get("stdout_parse") != "valid"):
        return 1
    return record["exit"]


if __name__ == "__main__":
    raise SystemExit(main())
