"""Execute an explicit saved power-net recipe using an existing user grant.

Recipe: [{"command":"project netclass set", "args":[...],
          "require_target_width":false}, ...]. No grant is created. This bounded
runner stops on failed/unknown writes, width gaps or custom connection gaps;
native intent/DRC and manufacturing acceptance remain separately required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TASKS = {
    "project netclass set",
    "board route repair",
    "board route rebuild",
    "board route rewidth",
    "board track widen",
}


def run(board, recipe, *, binary=None):
    events, complete = [], True
    prefix = [str(binary)] if binary else [sys.executable, "-B", "-m", "kicad_cli.main"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for step in recipe:
        if step["command"] not in TASKS or not isinstance(step["args"], list):
            raise ValueError("Explicit supported power command/argument list required")
        argv = [*step["command"].split(), "--board", str(board), *step["args"], "--compact"]

        def call(flags, argv=tuple(argv)):
            proc = subprocess.run(
                [*prefix, *argv, *flags], cwd=REPO, env=env, capture_output=True, timeout=2000
            )
            return proc.returncode, json.loads(proc.stdout)

        code, plan = call(["--dry-run"])
        event = {
            "command": step["command"],
            "args": step["args"],
            "preview_exit": code,
            "preview": plan,
        }
        events.append(event)
        if not plan.get("ok"):
            complete = False
            break
        data = plan["data"]
        token = data.pop("confirm_token")
        if not data["preview"]["can_apply"]:
            event["stop_reason"] = "Bound candidate cannot apply; no confirm sent"
            complete = False
            break
        code, result = call(["--confirm", token])
        event.update(execution_exit=code, execution=result)
        if not result.get("ok"):
            complete = False
            break
        ext = result["data"]["receipt"]["extensions"]
        if step.get("require_target_width") and not ext["quality"]["all_selected_target_met"]:
            event["stop_reason"] = "Required actual width not met"
            complete = False
            break
        if step.get("require_connected") and ext["finding_comparison"]["final"]["unconnected"]:
            event["stop_reason"] = "Custom saved connection gaps remain"
            complete = False
            break
    return {
        "status": "passed_bounded_saved_task" if complete else "stopped",
        "board": str(board),
        "events": events,
        "board_sha256": hashlib.sha256(board.read_bytes()).hexdigest(),
        "project_sha256": hashlib.sha256(board.with_suffix(".kicad_pro").read_bytes()).hexdigest(),
        "native_DRC": "not_run",
        "independent_intent": "not_run",
        "manufacturing": "not_run",
        "ampacity": "not_assessed",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path)
    options = parser.parse_args()
    if options.output.exists():
        raise FileExistsError("New workflow report path required")
    result = run(
        options.board.resolve(strict=True),
        json.loads(options.recipe.read_bytes()),
        binary=options.binary,
    )
    with options.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"status": result["status"], "report": str(options.output.resolve())}))
    return 0 if result["status"] == "passed_bounded_saved_task" else 1


if __name__ == "__main__":
    raise SystemExit(main())
