"""Public module dispatch through the existing authenticated document runtime."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .module_contract import MODULES
from .module_project import load_json, sha, strict_context


def prepare(request):
    from . import library_build, pcb_patch
    from .payload.drc_runner import DrcError
    from .sync_inputs import Drift

    mapping = {Path(k): Path(v) for k, v in request["mapping"].items()}
    params = request["params"]
    path = Path(request["primary"])
    original = Path(request["original_primary"])
    spec = load_json(mapping[Path(params["spec"])].read_bytes())
    try:
        if request["command"] == "library build":
            originals = {
                p: (mapping[p].read_bytes() if mapping[p].is_file() else None)
                for p in library_build.output_paths(original.parent, spec)
            }
            values, result = library_build.build(spec, original.parent, originals)
            comparison = None
            for target, raw in values.items():
                mapping[target].parent.mkdir(parents=True, exist_ok=True)
                mapping[target].write_bytes(raw)
            outputs = list(values)
        else:
            result, comparison = pcb_patch.prepare(path, spec)
            outputs = [original]
        return {
            "ok": True,
            "primary_candidate": str(path),
            "files": [
                {
                    "path": str(p),
                    "candidate": str(mapping[p]),
                    "intended": sha(mapping[p].read_bytes()),
                }
                for p in outputs
            ],
            "result": result,
            "finding_comparison": comparison,
            "official_native": request["command"] == "board route patch",
        }
    except (ValueError, OSError, KeyError, DrcError) as exc:
        return {
            "ok": False,
            "error": {
                "code": "E_CONFLICT"
                if isinstance(exc, Drift)
                else getattr(exc, "code", "E_VALIDATION"),
                "message": str(exc),
                "details": getattr(exc, "details", {}),
            },
        }


def run(call):
    from . import drc_baseline, library_build, pcb_patch
    from ._driver_sdk.models import DriverError
    from .engineering_inputs import Inputs
    from .module_project import capture_board_context
    from .sync_inputs import Drift

    try:
        if call.command == "modules":
            from .driver_runtime import PROTOCOL_REVISION, declarations
            from .internal_release import profile
            from .module_contract import authorization_scope_schema

            ref = declarations()["reference"]
            source_binding = drc_baseline._source()
            executable = (
                sha(Path(sys.executable).read_bytes()) if getattr(sys, "frozen", False) else None
            )
            example = {
                "scope_id": "replace-with-current-task-id",
                "tool": "kicad-cli",
                "granted_by": "replace-with-existing-user-task-authorization",
                "granted_at": drc_baseline._now(),
                "expires_at": None,
                "effects": ["observe", "change", "persist", "artifact"],
                "dangerous": False,
                "roots": [str(Path.cwd().resolve())],
                "outputs": [str(Path.cwd().resolve())],
                "limits": {"max_operations": 20},
            }
            data = {
                "modules": MODULES,
                "internal_delivery": profile(),
                "binding": source_binding,
                "executable_sha256": executable,
                "protocol_revision": PROTOCOL_REVISION,
                "module_bindings": {
                    name: {
                        "source_code_sha256": source_binding["code_sha256"],
                        "executable_sha256": executable,
                        "protocol": PROTOCOL_REVISION,
                        "command_contract_sha256": sha(
                            json.dumps(
                                [c for c in ref["commands"] if c["path"] in module["commands"]],
                                sort_keys=True,
                            ).encode()
                        ),
                    }
                    for name, module in MODULES.items()
                },
                "operation_scope": {
                    "schema": authorization_scope_schema(),
                    "example": example,
                    "writes_scope": False,
                    "policy": "Replace example roots/outputs/limits with the "
                    "existing user-authorized task range; the example grants nothing",
                },
                "states": {
                    k: {
                        "implementation": v["state"],
                        "regression_group": v["regression_group"],
                        "qualification": v["qualification"],
                    }
                    for k, v in MODULES.items()
                },
                "installation": "Each generated Skill is self-contained; compare its "
                "resource manifest and source binding with this runtime",
            }
        elif call.command == "library inspect":
            project = Path(call.params["project"]).resolve(strict=True)
            name = call.params["library"]
            if not library_build.NAME.fullmatch(name):
                raise ValueError("Expected bounded library identifier")
            inputs = Inputs()
            inputs.add(project, required=True)
            meta_path = inputs.add(project.parent / (name + ".provenance.json"), required=True)
            metadata = json.loads(inputs.files[meta_path])
            assets = []
            for key, value in metadata["assets"].items():
                spec = {"library": name, **value}
                paths = library_build.output_paths(project.parent, spec)
                for p in paths:
                    inputs.add(p, required=True)
                assets.append(
                    {
                        "symbol": key,
                        **value,
                        "baseline": {
                            p.relative_to(project.parent).as_posix(): sha(inputs.files[p])
                            for p in paths
                        },
                    }
                )
            inputs.validate()
            data = {
                "library": name,
                "assets": assets,
                "inputs": inputs.manifest,
                "native_instance_refresh": "not performed",
            }
        else:
            path = Path(call.params["board"]).resolve()
            context = strict_context(path)
            inputs = capture_board_context(path)
            data = {"context": context, "inputs": inputs.manifest}
            if call.command == "board routing inspect":
                data.update(pcb_patch.inspect(path, call.params.get("net")))
            inputs.validate()
        return {"module": call.command, "observation": data, "_untrusted": ["observation"]}
    except (ValueError, OSError, KeyError) as exc:
        raise DriverError(
            "E_CONFLICT"
            if isinstance(exc, Drift)
            else "E_NOT_FOUND"
            if isinstance(exc, FileNotFoundError)
            else "E_VALIDATION",
            "Module observation failed",
            details={
                **getattr(exc, "details", {}),
                **({"conflict": "state_drift"} if isinstance(exc, Drift) else {}),
                "reason": str(exc),
                "problem": "invalid_value",
                "parameter": "--board/--project/--library",
            },
        ) from exc
