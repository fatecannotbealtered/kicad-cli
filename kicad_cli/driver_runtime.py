"""Incremental driver runtime: real saved-board tasks and durable operations.

Other domain commands retain their declared legacy protocol. This module never
turns a legacy handler's output into a driver receipt.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
SPEC = PACKAGE / "driver_spec"
PROTOCOL_REVISION = "driver-board.v2.20-integer-nm-library"
PIN_SHA256 = "e26dabd06dd559f70ce07535f1b589497dba0a002b5cee59049ff855671b432f"
MIGRATED = {
    "reference",
    "context",
    "doctor",
    "changelog",
    "board analyze ampacity",
    "session inspect",
    "board analyze reference-plane",
    "sch create",
    "sch edit",
    "project links repair",
    "board from-netlist",
    "board zone stitch",
    "board silkscreen place",
    "board footprint place",
    "board footprint move",
    "board zone create",
    "board zone refill",
    "project sync",
    "operation show",
    "operation list",
    "operation resolve",
    "project links inspect",
    "project parity",
    "board check",
    "sch check",
    "sch objects",
    "sch symbols",
    "sch connections",
    "fab gerber",
    "fab drill",
    "board plot",
    "project netclass set",
    "board route repair",
    "board route rebuild",
    "board route rewidth",
    "board track widen",
    "modules",
    "project context",
    "library build",
    "library inspect",
    "board routing inspect",
    "board route patch",
    "sch export",
}


def selection(argv, commands):
    """Locate pilot paths without letting the legacy parser own their errors."""
    types = {p["name"]: p["type"] for command in commands for p in command["params"]}
    booleans = {"compact", "quiet", "dry-run", "json", "dangerous", "version", "help"}
    booleans.update(name for name, kind in types.items() if kind == "boolean")
    words, selected, i = [], None, 0
    while i < len(argv):
        token = argv[i]
        if not token.startswith("--"):
            words.append(token)
        else:
            key, separator, value = token[2:].partition("=")
            if (
                not separator
                and key not in booleans
                and i + 1 < len(argv)
                and not argv[i + 1].startswith("--")
            ):
                i += 1
                value = argv[i]
            if key == "command":
                selected = value
        i += 1
    for width in range(max(len(path.split()) for path in MIGRATED), 0, -1):
        path = " ".join(words[:width])
        if path in MIGRATED and not path.startswith("operation "):
            return path
    if words and words[0] == "operation":
        return " ".join(words[:2])
    if words[:1] == ["reference"] and selected in MIGRATED:
        return "reference"
    return None


def declarations() -> dict:
    from .commands.changelog import _changelog_path, _parse

    contract = json.loads((PACKAGE / "driver_contract.json").read_bytes())
    path = _changelog_path()
    if path is not None:
        # The pinned SDK's --since comparator accepts released SemVer entries.
        # Keep local Unreleased notes in the shipped CHANGELOG.md, not in this
        # released-version list (where they previously caused E_UNKNOWN).
        contract["changelog"] = [
            entry
            for entry in _parse(path.read_text(encoding="utf-8"))
            if entry["version"].lower() != "unreleased"
        ]
    from .module_contract import extend

    contract = extend(contract, PROTOCOL_REVISION)
    _reference_output_schemas(contract["reference"])
    return contract


def _reference_output_schemas(reference):
    """Keep review resources inside the output schemas selected by the fixed SDK."""
    products = reference["json_schemas"]
    # These declared page/preflight schemas reference another product resource.
    # Embed it as a standard nested resource so a selected view resolves offline
    # without adding unnamed schemas to the closed SDK catalog.
    for identity, dependency in {
        "kicad-cli://schemas/move-preflight/v1": "kicad-cli://schemas/move-placement-guard/v1",
        "urn:kicad-cli:sch-object-page:1": "urn:kicad-cli:sch-object:1",
        "urn:kicad-cli:sch-symbol-page:1": "urn:kicad-cli:sch-symbol:1",
        "urn:kicad-cli:sch-connection-page:1": "urn:kicad-cli:sch-connection:1",
    }.items():
        schema = products[identity]
        products[identity] = {
            **schema,
            "$defs": {**schema.get("$defs", {}), "offline_dependency": products[dependency]},
        }
    outputs = {}
    for command in reference["commands"]:
        for field in (
            "selection_schema",
            "preflight_schema",
            "routing_constraint_schema",
            "candidate_review_schema",
        ):
            if not command.get(field):
                continue
            label = command["output_schema"]
            if label not in outputs:
                identity = f"urn:kicad-cli:reference-output:{label}:1"
                entry = reference["schemas"][label]
                outputs[label] = {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": identity,
                    **entry["json_schema"],
                    "$defs": {},
                }
                products[identity] = outputs[label]
                entry["json_schema"] = {"$ref": identity}
            # Preserve each resource's original $id and fragment references.
            # $defs does not change the output validation or grant permission.
            identity = command[field].split("#", 1)[0]
            outputs[label]["$defs"][field] = products[identity]


def migration_status(commands) -> dict:
    return {
        "specification_commit": "9607a35fe0ea33fe822e1f2ad2704eae260d4018",
        "protocol_revision": PROTOCOL_REVISION,
        "migrated": sorted(MIGRATED),
        "legacy": [c["path"] for c in commands if c["path"] not in MIGRATED],
        "pilot_driver_profile": declarations()["reference"]["driver_profile"],
        "scope_environment": "KICAD_CLI_SCOPE_FILE",
        "state_environment": "KICAD_CLI_STATE_DIR",
        "limits": "Only listed pilot paths use runtime 2.0; "
        "whole-product conformance and production readiness remain unverified.",
    }


class PinMismatch(ValueError):
    """Fixed SDK/spec bytes or schema inventory failed authentication."""


def _dependencies() -> None:
    # Existing source checkout cache, or normal declared package dependencies.
    # No downloading or installing; frozen builds collect the same dependencies.
    if getattr(sys, "frozen", False):
        return
    vendor = PACKAGE.parent / ".vendor"
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))


def _pin() -> Path:
    from . import sdk_loader

    sdk = sdk_loader.root(PACKAGE)
    frozen = bool(getattr(sys, "frozen", False))
    external_sdk = frozen or sdk.resolve() != (PACKAGE / "_driver_sdk").resolve()
    validation_root = sdk.parent / "driver_spec" if external_sdk else SPEC
    raw = (SPEC / "PIN.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN_SHA256:
        raise PinMismatch("unauthenticated specification manifest")
    pin = json.loads(raw)
    if pin["commit"] != "9607a35fe0ea33fe822e1f2ad2704eae260d4018":
        raise PinMismatch("wrong specification pin")
    for name, record in pin["files"].items():
        external = name.startswith("_driver_sdk/")
        path = sdk / name.removeprefix("_driver_sdk/") if external else PACKAGE / name
        allowed = sdk if external else PACKAGE
        # Validate every externally consumed schema and semantic checker too.
        # The frozen data copy has no Python; the unchanged SDK validator loads
        # its executable checker from the authenticated external spec tree.
        if (frozen or validation_root != SPEC) and name.startswith("driver_spec/"):
            path = validation_root / name.removeprefix("driver_spec/")
            allowed = validation_root
            if not name.endswith(".py"):
                if hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest() != record["sha256"]:
                    raise PinMismatch("packaged specification data mismatch")
        if path.resolve().is_relative_to(allowed.resolve()) is False:
            raise PinMismatch("invalid specification manifest path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise PinMismatch("pinned specification or SDK source mismatch")
    expected_schemas = {
        name.removeprefix("driver_spec/")
        for name in pin["files"]
        if name.startswith("driver_spec/schemas/") and name.endswith(".schema.json")
    }
    actual_schemas = {
        p.relative_to(validation_root).as_posix()
        for p in (validation_root / "schemas").glob("*.schema.json")
    }
    if actual_schemas != expected_schemas:
        raise PinMismatch("unattributed specification schema")
    # Authenticate the complete SDK/spec before any SDK code can execute. An
    # external SDK's __init__ imports validation immediately, so source-only
    # dependency discovery must precede load just as it does for the local SDK.
    _dependencies()
    sdk_loader.load(sdk, PACKAGE)
    return validation_root


def create_cli(**options):
    validation_root = _pin()
    from . import __version__
    from ._driver_sdk import tokens
    from ._driver_sdk.models import DriverError
    from ._driver_sdk.runtime import DriverCLI, UsageProblem
    from ._driver_sdk.store import effective_status
    from ._driver_sdk.validation import ContractValidator, parse_json
    from .board_driver_adapter import BoardAdapter
    from .drc_baseline import _source
    from .engineering_driver_adapter import COMMANDS as ENGINEERING_COMMANDS
    from .move_driver_adapter import file_path
    from .move_transaction import digest, owned_journal, paths, process_alive, regular, write_new
    from .power_driver_adapter import COMMANDS as POWER_COMMANDS

    validator = ContractValidator(validation_root)
    contract = declarations()
    contract["reference"]["version"] = __version__
    validator.validate_reference(contract["reference"])
    source_binding = _source()["code_sha256"]
    executable_binding = (
        hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
        if getattr(sys, "frozen", False)
        else None
    )

    class MoveDriver(DriverCLI):
        def _help(self, path):
            output = super()._help(path)
            extra = "Discovery flags: --help (-h), root --version; --json aliases --format json.\n"
            extra += (
                "SDK-free root bootstrap: no arguments, one --help/-h/help, or "
                "--check-environment with optional --compact. "
                "Additional arguments require the pinned runtime; repeated boolean flags "
                "use the last value and invalid boolean values refuse.\n"
            )
            if path:
                command = self.commands[path]
                defaults = {p["name"]: p["default"] for p in command["params"] if "default" in p}
                extra += "Literal defaults: " + json.dumps(defaults, sort_keys=True) + "\n"
                extra += "Effects: " + ", ".join(command["driver"]["effects"]) + ". "
                extra += "Read selected reference for scope, derived defaults and support limits.\n"
                if command.get("selection_schema"):
                    extra += "Selection constraints: " + command["selection_constraints"] + "\n"
                    extra += (
                        "Expanded-selection review schema: " + command["selection_schema"] + "\n"
                    )
            return replace(
                output,
                stdout=output.stdout + extra.encode("utf-8"),
            )

        def _reference(self, params):
            _trace("reference")
            response = super()._reference(params)
            data = response["data"]
            if not params.get("index") and not params.get("command"):
                from .module_contract import authorization_scope_schema

                # Index and command views have closed, fixed SDK schemas.
                # Full reference and modules retain the authorization discovery.
                data["authorization"] = {
                    **self.reference["authorization"],
                    "schema": authorization_scope_schema(),
                }
            return response

        def _context(self):
            _trace("context")
            return super()._context()

        def _doctor(self):
            _trace("doctor")
            return super()._doctor()

        def _changelog(self, params):
            _trace("changelog")
            return super()._changelog(params)

        def _params(self, command, raw, stdin):
            if command["path"] in POWER_COMMANDS:
                for name in {"nets", "classes"} & raw.keys():
                    names = []
                    for item in raw[name]:
                        try:
                            values = (
                                parse_json(item.encode())
                                if item.lstrip().startswith("[")
                                else item.split(",")
                            )
                        except ValueError as exc:
                            raise UsageProblem(
                                "invalid_value",
                                f"--{name} contains malformed selection text.",
                                parameter=f"--{name}",
                                next_action="Use CSV or repeat the option with one name per value.",
                            ) from exc
                        if not isinstance(values, list) or any(
                            not isinstance(value, str) or not value.strip() for value in values
                        ):
                            raise UsageProblem(
                                "invalid_value",
                                f"--{name} requires nonempty names.",
                                parameter=f"--{name}",
                                next_action="Use CSV or repeat this option with a nonempty name.",
                            )
                        names.extend(value.strip() for value in values)
                    if len(names) != len(set(names)):
                        raise UsageProblem(
                            "invalid_value",
                            f"--{name} repeats a name.",
                            parameter=f"--{name}",
                            next_action="List each name once.",
                        )
            if command["path"] in {"fab gerber", "board plot"} and "layers" in raw:
                names = []
                for item in raw["layers"]:
                    try:
                        values = (
                            parse_json(item.encode())
                            if item.lstrip().startswith("[")
                            else item.split(",")
                        )
                    except ValueError as exc:
                        raise UsageProblem(
                            "invalid_value",
                            "--layers contains malformed selection text.",
                            parameter="--layers",
                            next_action="Use CSV or repeat this option with one layer per value.",
                        ) from exc
                    if not isinstance(values, list) or any(
                        not isinstance(value, str) or not value.strip() for value in values
                    ):
                        raise UsageProblem(
                            "invalid_value",
                            "--layers requires nonempty layer names.",
                            parameter="--layers",
                            next_action="Use CSV or repeat this option with a nonempty layer name.",
                        )
                    names.extend(value.strip() for value in values)
                if len(names) != len(set(names)):
                    raise UsageProblem(
                        "invalid_value",
                        "--layers repeats a layer name.",
                        parameter="--layers",
                        next_action="List each layer once.",
                    )
            params = super()._params(command, raw, stdin)
            if command["path"] == "board silkscreen place" and params.get("targets"):
                from .sync_inputs import _absolute

                # The private trial resolves only its captured mapping. Make a
                # relative caller selection path unambiguous before dispatch.
                params["targets"] = str(_absolute(params["targets"]))
            import math

            bounds = {
                "board analyze ampacity": {"oz": (0, 100, False), "dt": (0, 100, False)},
                "board analyze reference-plane": {"step": (0.01, 10, True)},
                "board footprint place": {
                    "iterations": (1, 100000, True),
                    "clearance": (0, 100, True),
                },
                "board silkscreen place": {"clearance": (0, 10, True)},
                "board zone stitch": {"min-area": (0, 1000000, True)},
                "board from-netlist": {"pitch": (0, 1000000, False), "margin": (0, 1000000, True)},
            }
            for name, (low, high, inclusive) in bounds.get(command["path"], {}).items():
                value = params[name]
                if (
                    not math.isfinite(value)
                    or not (value >= low if inclusive else value > low)
                    or value > high
                ):
                    raise UsageProblem(
                        "invalid_value",
                        f"--{name} is outside the declared numeric bounds.",
                        parameter="--" + name,
                        code="E_VALIDATION",
                        next_action="Read reference for units and supported input bounds.",
                    )
            if command["path"] in POWER_COMMANDS and params.get("nets") and params.get("classes"):
                raise UsageProblem(
                    "conflicting_flags",
                    "Use nets or classes, not both.",
                    parameter="--nets/--classes",
                    next_action="Choose one exact selection mechanism.",
                )
            if command["path"] == "board route rewidth" and not (
                params.get("nets") or params.get("classes")
            ):
                raise UsageProblem(
                    "missing_parameter",
                    "Rewidth requires an explicit selection.",
                    parameter="--nets/--classes",
                    next_action="Select exact nets or existing netclass names.",
                )
            return params

        def _binding(self):
            return tokens.digest(
                {
                    "tool": self.tool,
                    "version": self.version,
                    "protocol": PROTOCOL_REVISION,
                    "contract": tokens.digest(contract),
                    "contract_build_id": contract["build_id"],
                    "spec": "9607a35fe0ea33fe822e1f2ad2704eae260d4018",
                    "source": source_binding,
                    "executable": executable_binding,
                }
            )

        def _digest(self, command, params):
            return tokens.digest(
                {"binding": self._binding(), "command": command["path"], "params": params}
            )

        def _dry_run(self, command, params, scope):
            from ._driver_sdk.store import target_key
            from .move_driver_adapter import target_of

            name = params.get("board") or params.get("schematic") or params.get("project")
            if command["path"] == "board from-netlist":
                name = params["out"]
            elif command["path"] == "sch create":
                spec_path = Path(params["spec"]).expanduser().resolve()
                name = Path(params.get("out") or spec_path.parent) / (spec_path.stem + ".kicad_sch")
            if name:
                target = target_of(Path(name).expanduser().resolve())
                scope.require(effects=command["driver"]["effects"], target=target)
                blocking = self._store_open().blocking([target_key(target)])
                if blocking:
                    return self._blocked(*blocking)
            envelope = super()._dry_run(command, params, scope)
            if not envelope["ok"]:
                return envelope
            data = envelope["data"]
            claims = tokens.verify(
                self._store_open().secret(), data["confirm_token"], self.now().timestamp()
            )
            plan = data["preview"]
            claims.update(
                binding=self._binding(),
                scope_digest=tokens.digest(scope.record),
                target_digest=tokens.digest(plan["target"]),
                baseline=plan["base_snapshot"]["revision"],
                plan_digest=tokens.digest(plan),
            )
            data["confirm_token"] = tokens.issue(self._store_open().secret(), claims)
            return envelope

        def _confirmable(self, command, params, flags, scope, kind):
            try:
                claims = tokens.verify(
                    self._store_open().secret(), flags["confirm"], self.now().timestamp()
                )
            except tokens.TokenError as exc:
                return self._conflict(exc.reason, "The confirm token is invalid.")
            if claims.get("binding") != self._binding():
                return self._conflict(
                    "token_mismatch",
                    "The tool version, protocol or contract differs from the plan.",
                )
            if claims.get("scope_digest") != tokens.digest(scope.record):
                return self._conflict(
                    "scope_changed", "The complete scope grant changed since dry-run."
                )
            record = self._store_open().get(claims.get("op", ""))
            if record is not None:
                plan = record["plan"]
                if (
                    claims.get("target_digest") != tokens.digest(plan["target"])
                    or claims.get("baseline") != plan["base_snapshot"]["revision"]
                    or claims.get("plan_digest") != tokens.digest(plan)
                ):
                    return self._conflict(
                        "token_mismatch",
                        "The stored target or baseline differs from the HMAC-bound plan.",
                    )
            result = super()._confirmable(command, params, flags, scope, kind)
            if isinstance(result, tuple):
                self.adapter.validate_plan(result[2])
            return result

        def _dispatched(self, command, call, record, plan, key, fingerprint):
            # Durable synchronous owner identity, before entering the adapter. The
            # lease alone cannot prove that a slow/lost-heartbeat writer stopped.
            extra = dict(record.get("extra") or {}, writer_pid=os.getpid())
            self._store_open().connection().execute(
                "UPDATE operations SET extra = ? WHERE operation_id = ? AND status = 'dispatched'",
                (json.dumps(extra), record["operation_id"]),
            )
            return super()._dispatched(command, call, record, plan, key, fingerprint)

        def _confirm(self, command, params, flags, scope):
            # Exports require absent destinations, so a completed destination
            # cannot obtain another plan merely to replay a recorded key. A
            # still-authentic spent token can retrieve its own keyed receipt.
            key = flags.get("request_id") or flags.get("idempotency_key")
            if (
                command["path"]
                in {"fab gerber", "fab drill", "board plot"} | POWER_COMMANDS | ENGINEERING_COMMANDS
                and key
            ):
                try:
                    claims = tokens.verify(
                        self._store_open().secret(), flags["confirm"], self.now().timestamp()
                    )
                except tokens.TokenError:
                    return super()._confirm(command, params, flags, scope)
                record = self._store_open().get(claims.get("op", ""))
                if record and record["status"] != "planned" and record["idempotency_key"] == key:
                    plan = record["plan"]
                    if (
                        claims.get("binding") != self._binding()
                        or claims.get("scope_digest") != tokens.digest(scope.record)
                        or claims.get("target_digest") != tokens.digest(plan["target"])
                        or claims.get("plan_digest") != tokens.digest(plan)
                        or claims.get("baseline") != plan["base_snapshot"]["revision"]
                        or claims.get("cap") != command["driver"]["capability_id"]
                        or claims.get("digest") != self._digest(command, params)
                        or record["request_digest"] != self._digest(command, params)
                    ):
                        return self._conflict(
                            "idempotency_mismatch",
                            "The keyed export's authenticated request, build or scope differs.",
                        ), None
                    scope.require(
                        effects=record["effects"], target=plan["target"], outputs=plan["outputs"]
                    )
                    return self._replay(record, key), None
            return super()._confirm(command, params, flags, scope)

        def _operation_show(self, params):
            _trace("operation show")
            envelope = super()._operation_show(params)
            record = self._record(params["id"])
            if (
                effective_status(record, self.now().timestamp()) in {"unknown", "partial"}
                and record.get("resolution") is None
            ):
                status = envelope["data"]["operation_status"]
                pid = (record.get("extra") or {}).get("writer_pid")
                status.setdefault("extensions", {})["writer_process"] = {
                    "pid": pid,
                    "alive_or_unknown": process_alive(pid) if isinstance(pid, int) else None,
                }
                try:
                    observed = self.adapter.recovery(record)
                    evidence = dict(observed, binding=self._binding())
                    evidence["hmac"] = hmac.new(
                        self._store_open().secret(), tokens.canonical(evidence), hashlib.sha256
                    ).hexdigest()
                    directory = self._store_open()._directory() / "recovery"
                    directory.mkdir(exist_ok=True)
                    name = "readback-" + tokens.digest(evidence) + ".json"
                    path = directory / name
                    write_new(path, tokens.canonical(evidence))
                    status.setdefault("extensions", {})["recovery_readback"] = dict(
                        observed,
                        evidence_file=str(path),
                        evidence_sha256=digest(path),
                        completion="completed",
                    )
                except (OSError, ValueError, KeyError) as exc:
                    status.setdefault("extensions", {})["recovery_readback"] = {
                        "completion": "incomplete",
                        "reason": str(exc),
                        "outcome": "unknown",
                    }
            return envelope

        def _operation_resolve(self, params):
            _trace("operation resolve")
            record = self._record(params["id"])
            status = effective_status(record, self.now().timestamp())
            if status not in {"unknown", "partial"} or record.get("resolution") is not None:
                return super()._operation_resolve(params)
            plan = record["plan"]
            self._scope().require(effects=record["effects"], target=plan["target"])
            pid = (record.get("extra") or {}).get("writer_pid")
            if not isinstance(pid, int) or process_alive(pid):
                return self._conflict(
                    "lock_held",
                    "The native writer has not been proven stopped.",
                    active_operation=record["operation_id"],
                    **self._lookup(record["operation_id"]),
                )
            try:
                evidence_path = Path(params["evidence"]).resolve(strict=True)
                regular(evidence_path)
                if not evidence_path.is_relative_to((self.state_dir / "recovery").resolve()):
                    raise ValueError(
                        "evidence must be the readback file returned by operation show"
                    )
                if evidence_path.stat().st_size > 1024 * 1024:
                    raise ValueError("recovery evidence is too large")
                evidence = parse_json(evidence_path.read_bytes())
                signature = evidence.pop("hmac", "")
                expected_mac = hmac.new(
                    self._store_open().secret(), tokens.canonical(evidence), hashlib.sha256
                ).hexdigest()
                if not isinstance(signature, str) or not hmac.compare_digest(
                    signature, expected_mac
                ):
                    raise ValueError("recovery evidence authentication failed")
                if (
                    evidence.get("operation_id") != record["operation_id"]
                    or evidence.get("binding") != self._binding()
                    or evidence.get("target") != plan["target"]
                ):
                    raise ValueError(
                        "recovery evidence belongs to another operation, target or tool build"
                    )
                engineering = record["command"] in ENGINEERING_COMMANDS
                path = (
                    Path(plan["extensions"]["primary"])
                    if engineering
                    else file_path(plan["target"])
                )
                export = record["command"] in {"fab gerber", "fab drill", "board plot"}
                power = record["command"] in POWER_COMMANDS
                sync_documents = (
                    record["command"] == "project sync"
                    and plan["extensions"].get("cleanup_protocol") == "document-set.v2"
                )
                if power:
                    path = self.adapter.power.journal_path(record)
                journal = (
                    None
                    if export or engineering or sync_documents
                    else owned_journal(path, record["operation_id"])
                )
                if journal and process_alive(journal["pid"]):
                    return self._conflict(
                        "lock_held",
                        "The operation's native writer process is still alive.",
                        active_operation=record["operation_id"],
                        **self._lookup(record["operation_id"]),
                    )
                current = self.adapter.recovery(record)
                if (
                    current["snapshot"]["revision"] != evidence["snapshot"]["revision"]
                    or current["snapshot"].get("dependencies")
                    != evidence["snapshot"].get("dependencies")
                    or current["readback"]["objects"] != evidence["readback"]["objects"]
                    or current["readback"]["protected_sha256"]
                    != evidence["readback"]["protected_sha256"]
                ):
                    return self._conflict(
                        "state_drift",
                        "The target changed after the recovery readback; observe it again.",
                    )
                if (
                    params["outcome"] != current["outcome"]
                    or params["outcome"] != evidence["outcome"]
                ):
                    raise ValueError(
                        "requested resolution disagrees with the actual independent readback"
                    )
                if sync_documents:
                    self._scope().require(
                        effects=record["effects"], target=plan["target"], outputs=plan["outputs"]
                    )
                    self.adapter.sync.resolve_recovery(record, current, self.state_dir)
                elif engineering:
                    self._scope().require(
                        effects=record["effects"], target=plan["target"], outputs=plan["outputs"]
                    )
                    self.adapter.engineering.resolve_recovery(record, current, self.state_dir)
                elif export:
                    self._scope().require(
                        effects=record["effects"], target=plan["target"], outputs=plan["outputs"]
                    )
                    self.adapter.export.resolve_recovery(record, current, self.state_dir)
                elif power:
                    self._scope().require(
                        effects=record["effects"], target=plan["target"], outputs=plan["outputs"]
                    )
                    self.adapter.power.resolve_recovery(record, current, self.state_dir)
                elif journal:
                    # Explicit resolution keeps the observed board; archive the real baseline
                    # before cleaning only this operation's sidecars. No target write occurs.
                    lock, backup, journal_path = paths(path)
                    archive = (
                        self.state_dir
                        / "recovery"
                        / (
                            tokens.digest(record["operation_id"])
                            + "-baseline-"
                            + digest(backup)
                            + ".pcb"
                        )
                    )
                    if not archive.exists():
                        write_new(archive, backup.read_bytes())
                    if (
                        digest(archive) != journal["baseline"]
                        or digest(path) != current["snapshot"]["revision"]
                    ):
                        raise OSError("baseline archive or current target changed during recovery")
                    for sidecar in (backup, lock, journal_path):
                        sidecar.unlink()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise DriverError(
                    "E_VALIDATION",
                    "Recovery was not accepted; the unresolved write remains blocked.",
                    details={
                        "problem": "invalid_value",
                        "parameter": "--evidence",
                        "reason": str(exc),
                        "next_action": "Run operation show; use its authenticated current "
                        "readback evidence and matching outcome.",
                    },
                    dispatch_state="not_started",
                ) from exc
            return super()._operation_resolve(params)

        def _operation_list(self, params):
            _trace("operation list")
            return super()._operation_list(params)

    adapter = BoardAdapter(validator)
    cli = MoveDriver(contract, adapter, validator=validator, **options)
    adapter.scope_provider = cli.scopes.resolve
    adapter.move.state_dir = cli.state_dir
    adapter.move.environ = cli.environ
    adapter.move.json_schemas = contract["reference"].get("json_schemas", {})
    adapter.power.state_dir = cli.state_dir
    adapter.power.environ = cli.environ
    adapter.power.secret_provider = lambda: cli._store_open().secret()
    adapter.power.binding_provider = cli._binding
    adapter.engineering.state_dir = cli.state_dir
    adapter.engineering.environ = cli.environ
    adapter.engineering.secret_provider = lambda: cli._store_open().secret()
    adapter.engineering.record_provider = lambda operation: cli._store_open().get(operation)
    adapter.engineering.binding_provider = cli._binding
    adapter.sync.state_dir = cli.state_dir
    adapter.sync.json_schemas = contract["reference"].get("json_schemas", {})
    adapter.sync.secret_provider = lambda: cli._store_open().secret()
    adapter.sync.guard_documents = adapter.engineering._guard_documents
    return cli


def runtime_context():
    with create_cli() as cli:
        scope = cli.scopes.resolve()
        return {
            "protocol_revision": PROTOCOL_REVISION,
            "migrated": sorted(MIGRATED),
            "authorization": scope.authorization,
            "grant_file": scope.grant_file,
            "state_dir": str(cli.state_dir),
            "operation_store_exists": (cli.state_dir / "operations.sqlite3").is_file(),
            "limits": "Context creates no state or scope; "
            "native and whole-product readiness are separate.",
        }


def execute(argv, **options) -> int:
    try:
        cli = create_cli(**options)
    except Exception as exc:
        failure_kind = "runtime_unavailable"
        message = "The pinned driver runtime could not load; nothing was dispatched."
        next_action = (
            "Use the complete matched internal candidate with its bundled pinned SDK/spec. "
            "An explicit external override must name the exact authenticated _driver_sdk "
            "beside its matching driver_spec (9607a35fe0ea33fe822e1f2ad2704eae260d4018); "
            "correct that override or remove it to use the bundle. Do not bypass the pin. "
        )
        if isinstance(exc, PinMismatch):
            failure_kind = "pin_mismatch"
            message = "Pinned SDK/specification integrity check failed; nothing was dispatched."
        elif isinstance(exc, ImportError):
            failure_kind = (
                "missing_dependency"
                if isinstance(exc, ModuleNotFoundError)
                else "dependency_load_failed"
            )
            message = "A required Python dependency could not load; nothing was dispatched."
            next_action = (
                "Use the matching candidate EXE with its collected Python dependencies; "
                "the EXE does not use a source checkout's .vendor. "
                if getattr(sys, "frozen", False)
                else "Make the declared Python dependencies (jsonschema, referencing and their "
                "dependencies) available to this interpreter or the source checkout's existing "
                ".vendor, with a compatible Python version and architecture. "
            )
            next_action += (
                "Changing the pinned SDK/spec cannot repair a missing Python dependency. "
            )
        next_action += (
            "Run root --check-environment (optional --compact) for structured SDK-free "
            "diagnostics, or exactly --help. Normal doctor requires the runtime."
        )
        error = {
            "ok": False,
            "schema_version": "1.0",
            "meta": {"duration_ms": 0},
            "error": {
                "code": "E_CONFIG",
                "retryable": False,
                "message": message,
                "details": {
                    "dispatch_state": "not_started",
                    "cause": type(exc).__name__,
                    "missing_module": exc.name if isinstance(exc, ModuleNotFoundError) else None,
                    "phase": "runtime_bootstrap",
                    "failure_kind": failure_kind,
                    "next_action": next_action,
                },
            },
        }
        sys.stdout.buffer.write((json.dumps(error) + "\n").encode())
        return 4
    with cli:
        output = cli.run(argv)
    if output.envelope and not output.envelope["ok"]:
        trace = os.environ.get("KICAD_CLI_ERROR_TRACE")
        if trace:
            try:
                with open(trace, "a", encoding="utf-8") as handle:
                    handle.write("error:" + output.envelope["error"]["code"] + "\n")
            except OSError:
                pass  # Diagnostics cannot replace a real dispatched receipt.
    try:
        return output.write()
    except OSError:
        sys.stderr.write(
            "Driver reply could not be written. Query the planned operation ID; "
            "do not repeat the write. Its durable record is preserved.\n"
        )
        return 6


def _trace(path):
    target = os.environ.get("KICAD_CLI_TRACE")
    if target:
        try:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(path + "\n")
        except OSError:
            pass


def block_legacy_write(command, options) -> None:
    """Share the pilot's unresolved-target guard, without claiming legacy migration."""
    if command["type"] != "write" or not options.get("board"):
        return
    state = Path(os.environ.get("KICAD_CLI_STATE_DIR") or (Path.home() / ".kicad-cli"))
    if not (state / "operations.sqlite3").exists():
        return
    from . import envelope

    try:
        with create_cli() as cli:
            from ._driver_sdk.store import target_key
            from .move_driver_adapter import target_of

            target = target_of(Path(options["board"]).resolve())
            blocking = cli._store_open().blocking([target_key(target)])
            if blocking:
                refused = cli._blocked(*blocking)["error"]
                envelope.fail(refused["code"], refused["message"], refused["details"])
    except SystemExit:
        raise
    except Exception as exc:
        envelope.fail(
            "E_CONFIG",
            "Cannot establish the pilot's unresolved-target guard.",
            {"reason": str(exc), "dispatch_state": "not_started"},
        )
