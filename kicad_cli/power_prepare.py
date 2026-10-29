"""Private candidate preparation in an operation-owned copy, no public CLI gate.

Invoked only with captured inputs. Native domain preparation primitives preserve
their existing algorithm. No legacy public handler is dispatched or relayed.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
from pathlib import Path

from . import drc_baseline, power_geometry
from .native.smd_drill_guard import DrillConstraintError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True, type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_bytes())
    root = args.request.resolve().parent
    candidate = Path(request["board"]).resolve(strict=True)
    if (
        root.parent.name != "power-trials"
        or not root.name.startswith("op-")
        or not candidate.is_relative_to(root / "candidate")
        or request.get("owner") != "kicad-cli.power-trial.v1"
    ):
        raise ValueError("Candidate must be an operation-owned local copy")
    from . import driver_runtime
    from .move_transaction import regular

    with driver_runtime.create_cli() as cli:
        from ._driver_sdk import tokens

        if not root.is_relative_to(cli.state_dir.resolve() / "power-trials"):
            raise ValueError("Private trial escaped configured state")
        scope = cli.scopes.resolve()
        scope.require(
            effects=["change", "persist"],
            target=request["engineering_target"],
            outputs=[{"path": str(root), "effect": "persist"}],
        )
        signature = request.pop("hmac", "")
        expected = hmac.new(
            cli._store_open().secret(), tokens.canonical(request), hashlib.sha256
        ).hexdigest()
        if (
            not hmac.compare_digest(signature, expected)
            or request["binding"] != cli._binding()
            or request["scope_digest"] != tokens.digest(scope.record)
        ):
            raise ValueError("Private trial authentication/build/grant mismatch")
    for name, expected_sha in request["captured"].items():
        file = Path(name).resolve(strict=True)
        regular(file)
        if (
            not file.is_relative_to(candidate.parent)
            or hashlib.sha256(file.read_bytes()).hexdigest() != expected_sha
        ):
            raise ValueError("Private captured candidate changed")

    for file in candidate.parent.iterdir():
        regular(file)
    before = drc_baseline.record(drc_baseline.capture(candidate)[1])
    try:
        if request["command"] == "board track widen":
            result = power_geometry.prepare_widen(candidate, request["selected"])
        else:
            result = power_geometry.prepare_route(candidate, request["params"], request["selected"])
    except DrillConstraintError as exc:
        # These finite SMD/circular-via drill constraints have explanatory
        # transport. Other preparation failures retain their existing evidence.
        value = {"ok": False, "error": exc.record()}
        sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n")
        return 2
    _, checked = drc_baseline.capture(candidate)
    value = {
        "ok": True,
        "before": before,
        "result": result,
        "drc": drc_baseline.record(checked),
        "candidate_sha256": drc_baseline._digest(candidate.read_bytes()),
        "kind": "custom_file_trial",
        "official_native": False,
    }
    # Frozen Python on Windows need not honor PYTHONIOENCODING. The private
    # transport is UTF-8 bytes, independent of console/default code pages.
    sys.stdout.buffer.write(json.dumps(value, ensure_ascii=False).encode("utf-8") + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
