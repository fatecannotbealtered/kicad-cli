"""Bounded public observations and actual backend discovery."""

from __future__ import annotations

import math
from pathlib import Path

from . import drc_baseline, kicad_env, read_inputs
from ._driver_sdk.models import DriverError
from .fileformat.board import Board
from .fileformat.sexpr import Document
from .read_driver_adapter import now

COMMANDS = frozenset({"board analyze ampacity", "board analyze reference-plane", "session inspect"})


def run(call):
    from .driver_runtime import _trace

    _trace(call.command)
    if call.command == "session inspect":
        from .commands.live import connect, inspect

        client, kind, reason = connect()
        if kind != "ok":
            raise DriverError(
                "E_CONFIG",
                "A live KiCad IPC connection was not observed",
                details={
                    "backend": kind,
                    "reason": reason,
                    "fix": (
                        "Set KICAD_CLI_IPC_CLIENT_PATH to an existing absolute directory "
                        "containing kipy and its dependencies. "
                        "Open the owned board in an existing enabled session. "
                        "Preferences > KiCad API belongs to the application "
                        "owner; this command changes no settings."
                    ),
                    "note": (
                        "Saved-file tasks do not require a running editor; "
                        "this path reads live memory only."
                    ),
                    "next_action": "Check KICAD_CLI_IPC_CLIENT_PATH, then use an owned "
                    "enabled session; saved-file tasks do not require IPC",
                },
                dispatch_state="not_started",
            )
        return inspect(client)
    try:
        inputs = read_inputs.capture(board=call.params["board"])
        raw = inputs.raw(inputs.board)
        board = Board(Document.parse(raw.decode()), inputs.board)
        if call.command == "board analyze reference-plane":
            from .native.plane import run as plane

            step = call.params["step"]
            if not math.isfinite(step) or not 0.01 <= step <= 10:
                raise ValueError("Sampling step must be 0.01 through 10 mm")
            data = plane(str(inputs.board), step, captured_board=board)
            data["method"] = {
                "kind": "sampled filled-plane coverage heuristic",
                "step_mm": step,
                "limits": (
                    "Sampled target-net copper coverage, not impedance, "
                    "signal-integrity or full continuous return-path proof"
                ),
            }
        else:
            from .native.project import ampacity

            oz, dt = call.params["oz"], call.params["dt"]
            if not all(math.isfinite(v) and 0 < v <= 100 for v in (oz, dt)):
                raise ValueError(
                    "Copper weight and temperature rise must be finite, positive and "
                    "within this model's declared 100 oz / 100 K input bound"
                )
            tracks = [
                {
                    "id": t.node.find("uuid").value(1),
                    "net": t.net,
                    "layer": board.layer_name(t.layer),
                    "width_mm": t.width / 1e6,
                    "width_nm": t.width,
                    "estimated_current_a": ampacity(
                        t.width / 1e6, oz, dt, board.layer_name(t.layer) in {"F.Cu", "B.Cu"}
                    ),
                }
                for t in board.tracks
            ]
            data = {
                "board": str(inputs.board),
                "model": {
                    "name": "IPC-2221 empirical estimate",
                    "equation": "I = k * delta_T^0.44 * area_mil2^0.725",
                    "external_k": 0.048,
                    "internal_k": 0.024,
                    "thickness_mil_per_oz": 1.378,
                    "copper_oz": oz,
                    "delta_t_k": dt,
                    "width_unit": "mm",
                    "current_unit": "A",
                    "assumptions": "Uniform thickness, requested rise, layer-dependent coefficient",
                    "source": "https://www.ti.com/lit/an/snva766/snva766.pdf",
                    "limits": (
                        "Empirical screening estimate; no measured current, actual copper "
                        "thickness, ambient/airflow, neighboring plane heat spreading, via "
                        "capacity or IPC-2152 validation. Defaults are assumptions, not "
                        "project requirements. Not a load-capacity acceptance."
                    ),
                },
                "tracks": tracks,
                "nets": [
                    {
                        "net": net,
                        "segments": len(rows := [t for t in tracks if t["net"] == net]),
                        "minimum_estimated_current_a": min(t["estimated_current_a"] for t in rows),
                    }
                    for net in sorted({t["net"] for t in tracks})
                ],
            }
        inputs.validate()
        data["observation"] = {
            "inputs": inputs.manifest,
            "snapshot": {"revision": drc_baseline._digest(raw), "observed_at": now()},
            "state": "saved_files",
            "input_drift_checked": True,
            "live_editor_observed": False,
            "source": drc_baseline._source(),
        }
        return data
    except FileNotFoundError as exc:
        raise DriverError(
            "E_NOT_FOUND", "Required saved analysis input is missing", details={"path": str(exc)}
        ) from exc
    except (ValueError, KeyError, TypeError, OSError) as exc:
        from .sync_inputs import Drift

        raise DriverError(
            "E_CONFLICT" if isinstance(exc, Drift) else "E_VALIDATION",
            "Bounded observation failed",
            details={
                "reason": str(exc),
                **(
                    {"conflict": "state_drift"}
                    if isinstance(exc, Drift)
                    else {"problem": "invalid_value", "parameter": "--board/--step/--oz/--dt"}
                ),
            },
        ) from exc


def environment():
    python = kicad_env.find_python()
    version = kicad_env.kicad_version(python) if python else None
    components = [
        {
            "name": "KiCad external pcbnew interpreter",
            "role": "application",
            "version": version,
            "build_id": drc_baseline._digest(Path(python).read_bytes()) if python else None,
            "location": "disk",
            "identity_status": "observed" if version else "unavailable",
            "reason": (
                f"Selected interpreter: {python}; import/version probe "
                + ("completed" if version else "unavailable; check KICAD_CLI_ROOT/KICAD_CLI_PYTHON")
            ),
        }
    ]
    official = kicad_env.find_official_cli()
    official_version = kicad_env.official_version(official) if official else None
    components.append(
        {
            "name": "KiCad official CLI",
            "role": "application",
            "version": official_version,
            "build_id": drc_baseline._digest(Path(official).read_bytes()) if official else None,
            "location": "disk",
            "identity_status": "observed" if official_version else "unavailable",
            "reason": f"Selected official CLI: {official}; "
            + (
                "native version observed"
                if official_version
                else "set KICAD_CLI_OFFICIAL to the official application binary, not this driver"
            ),
        }
    )
    return {
        "components": components,
        "connection": {
            "status": "not_connected",
            "instance_id": None,
            "generation": None,
            "ownership": None,
            "reason": "Saved backend observed; GUI generation unobserved",
        },
    }


def doctor_checks():
    from .internal_release import SERIES

    component, official = environment()["components"]
    found = component["identity_status"] == "observed"
    return [
        {
            "check": "internal_delivery",
            "status": "warn",
            "fix": "Read modules for the bounded internal scope and exact-artifact "
            "qualification; it does not promote whole-product release readiness.",
            "message": f"Internal series {SERIES} candidate requires fresh artifact acceptance",
        },
        {
            "check": "native_backend",
            "status": "pass" if found else "fail",
            "fix": None
            if found
            else "Set KICAD_CLI_ROOT to an absolute existing KiCad installation directory, "
            "or KICAD_CLI_PYTHON to its absolute Python executable with pcbnew. "
            "Explicit invalid paths do not fall back; unset an override for automatic discovery.",
            "message": "External pcbnew import/version: " + str(component["version"]),
        },
        {
            "check": "official_cli",
            "status": "pass" if official["version"] else "fail",
            "fix": None
            if official["version"]
            else (
                "Set KICAD_CLI_OFFICIAL to the absolute official KiCad kicad-cli executable "
                "inside the existing installation, not this same-named driver. "
                "Required for native DRC/ERC/netlist gates; pure saved-file reads remain available."
            ),
            "message": official["reason"],
        },
        {
            "check": "session_backend",
            "status": "warn",
            "fix": "Only session inspect needs an existing IPC client via "
            "KICAD_CLI_IPC_CLIENT_PATH and an owned enabled GUI session. "
            "No installation or GUI preference is changed by discovery.",
            "message": "Optional live IPC is not probed by doctor; file tasks do not require it.",
        },
        {
            "check": "sdk_redistribution",
            "status": "warn",
            "fix": (
                "Preserve Apache-2.0 and inherited MIT licenses and the exact asset inventory; "
                "npm publication remains disabled as a release workflow choice"
            ),
            "message": "The fixed SDK/spec is licensed under Apache-2.0 with an MIT baseline; "
            "whole-product unpublishable readiness is retained",
        },
    ]
