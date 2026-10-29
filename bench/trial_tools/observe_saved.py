"""Public, narrowly scoped fresh read-only observation after a known SDK refusal.

Prepare a NEW work root with the current public trial entry, then use plan,
observe, review-template and review-record here. This never resumes an old
context, upgrades its READY, invokes a product executable or repeats a write.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).absolute().parent.parent))
    from trial_tools import preparation as core
    from trial_tools.eco import (
        PRODUCT_SHA256,
        digest,
        require_repeat_helpers,
        validate_acknowledgment,
    )
    from trial_tools.identity import capture, file_identity, ordinary_path, verify_inventory
    from trial_tools.reading import STEM, VIEWS, Readers, ident, load, require, write_new
else:
    from . import preparation as core
    from .eco import PRODUCT_SHA256, digest, require_repeat_helpers, validate_acknowledgment
    from .identity import capture, file_identity, ordinary_path, verify_inventory
    from .reading import STEM, VIEWS, Readers, ident, load, require, write_new

SCHEMA = "trial-known-refusal-fresh-readonly/v1"


def permitted_old_path(value):
    # Refuse these explicitly excluded historical operations before opening any
    # file or enumerating the selected directory.
    text = str(value).replace("\\", "/").casefold()
    require(
        not re.search(r"(?:^|[ /_-])f4(?:$|[ /_-])|source[ _-]*recovery0?1", text),
        "Excluded historical work is outside this observation method",
    )
    return ordinary_path(value)


def verify_original_inputs(context):
    require(
        context.get("schema") == core.SCHEMA
        and context.get("stopped") is True
        and isinstance(context.get("inputs"), dict)
        and context["inputs"],
        "Only an existing stopped context with frozen inputs is supported",
    )
    for expected in context["inputs"].values():
        permitted_old_path(expected.get("root", expected.get("path")))
        verify_inventory(expected)  # Compare original expectations; never adopt current bytes.


def verify_known_refusal_trace(trace, generic, argv, old_ready, new_ready):
    require(
        generic.get("status") == "unexpected_exit"
        and generic.get("observation_complete") is True
        and generic.get("issues") == []
        and generic.get("second_chance_notifications") == []
        and generic.get("fail_fast_status_notifications") == []
        and generic.get("functional_output_verified") is False
        and generic.get("may_validate_functional_output") is False
        and generic.get("stop_required") is True
        and trace.get("observation_schema") == 4
        and trace.get("exceptions") == []
        and trace.get("complete_tree") is True
        and trace.get("timed_out") is False
        and trace.get("job_active_before_close") == 0
        and trace.get("exit") == 6,
        "Old stop is not the complete, exception-free known SDK refusal",
    )
    final = trace.get("job_finalization", {})
    require(
        final.get("natural_tree_complete") is True
        and final.get("controlled_termination") is False
        and final.get("control_events") == [],
        "Old Job did not end naturally",
    )
    product = old_ready.record["dependencies"]["paths"]["product_exe"]
    require(
        argv[0].casefold() == product.casefold()
        and argv[1:3] == ["project", "sync"]
        and "--confirm" in argv
        and not any(
            word.split("=", 1)[0] in {"--dry-run", "--request-id", "--idempotency-key"}
            for word in argv
        ),
        "Old invocation is outside the unkeyed sync refusal scope",
    )
    require(
        old_ready.record["dependencies"]["files"][product]["sha256"] == PRODUCT_SHA256,
        "Old product is not the exact C10 candidate",
    )
    helpers = require_repeat_helpers(new_ready)
    rows = trace.get("processes", [])
    root = [
        row
        for row in rows
        if row.get("process_instance_id") == trace.get("root_process_instance_id")
    ]
    require(len(root) == 1, "Old refusal has no unique root")
    root = root[0]
    workers = [
        row
        for row in rows
        if row is not root and row.get("image", "").casefold() == product.casefold()
    ]
    require(len(workers) == 1 and len(rows) == 4, "Unexpected old refusal process set")
    worker = workers[0]
    require(
        worker.get("parent_process_instance_id") == root["process_instance_id"]
        and worker.get("parent_binding") == "observed-parent"
        and worker.get("metadata", {}).get("ParentProcessId") == root["pid"]
        and type(root.get("creation_filetime")) is int
        and root["creation_filetime"] > 0
        and type(worker.get("creation_filetime")) is int
        and worker["creation_filetime"] >= root["creation_filetime"],
        "Old worker parent is not bound",
    )
    for row in (root, worker):
        actual = core._windows_argv(row["metadata"]["CommandLine"])
        require(
            row.get("image", "").casefold() == product.casefold()
            and row.get("image_sha256") == PRODUCT_SHA256
            and row.get("exit") == 6
            and row.get("identity_status") == "bound"
            and row.get("owned") is True
            and actual[0].casefold() == argv[0].casefold()
            and actual[1:] == argv[1:],
            "Old root/worker image, identity, arguments or exit differs",
        )
    seen = set()
    for row in rows:
        if row is root or row is worker:
            continue
        matches = [
            item for item in helpers if item["path"].casefold() == row.get("image", "").casefold()
        ]
        require(len(matches) == 1, "Old refusal has an unknown helper or Native child")
        item = matches[0]
        parent = root if item["parent"] == "root" else worker
        require(
            item["path"] not in seen
            and row.get("image_sha256") == item["identity"]["sha256"]
            and row.get("exit") == 0
            and row.get("identity_status") == "bound"
            and row.get("owned") is True
            and row.get("parent_binding") == "observed-parent"
            and row.get("parent_process_instance_id") == parent["process_instance_id"]
            and row.get("metadata", {}).get("ParentProcessId") == parent["pid"]
            and row.get("creation_filetime", -1) >= parent.get("creation_filetime", 0)
            and core._windows_argv(row["metadata"]["CommandLine"])[0].casefold()
            == item["argv"][0].casefold()
            and core._windows_argv(row["metadata"]["CommandLine"])[1:] == item["argv"][1:],
            "Old helper identity, parent, birth, argv or exit is incomplete",
        )
        seen.add(item["path"])
    require(len(seen) == 2, "Old refusal lacks one finite helper observation")


def qualify_original(old_work, new_ready):
    old = permitted_old_path(old_work)
    require(
        old != new_ready.work_root
        and not old.is_relative_to(new_ready.work_root)
        and not new_ready.work_root.is_relative_to(old),
        "Use a separate new prepared root",
    )
    context = load(old / "method-state/CONTEXT.json")
    verify_original_inputs(context)
    ready = core.load_ready(old)
    require(context["work_root"] == str(old), "Old context belongs to another root")
    frozen = context["inputs"]
    required = {
        "project",
        "candidate-seven",
        "review:candidate",
        "candidate-binding",
        "reply:edit-preview",
        "reply:edit-confirm",
        "reply:edit-show",
        "reply:sync-preview",
        "reply:sync-confirm",
        "reply:sync-show",
        "human-ack:candidate",
        "actual-views:candidate",
        "review-request:candidate",
        "checkpoint:confirm",
    }
    require(required <= set(frozen), "Original frozen evidence is incomplete")

    def document(label):
        item = frozen[label]
        require(item["kind"] == "file", "Original evidence requires a frozen file")
        return load(Path(item["path"]))

    ids = {}
    for stage in ("edit", "sync"):
        reply, query = document("reply:" + stage + "-confirm"), document("reply:" + stage + "-show")
        receipt, status = (
            reply.get("data", {}).get("receipt"),
            query.get("data", {}).get("operation_status", {}),
        )
        require(
            reply.get("ok") is True
            and query.get("ok") is True
            and isinstance(receipt, dict)
            and status.get("status") == "succeeded"
            and status.get("receipt") == receipt
            and status.get("operation_id") == receipt.get("operation_id"),
            "Original write/receipt/query is not known succeeded",
        )
        outcome = receipt.get("outcome", {})
        require(
            outcome.get("execution") == "completed"
            and outcome.get("change") == "applied"
            and outcome.get("persistence") == "saved",
            "Original receipt is not completed/applied/saved",
        )
        expiry = datetime.fromisoformat(
            document("reply:" + stage + "-preview")["data"]["expires_at"].replace("Z", "+00:00")
        )
        started, finished, observed = (
            datetime.fromisoformat(status[key].replace("Z", "+00:00"))
            for key in ("started_at", "finished_at", "observed_at")
        )
        require(
            started <= finished <= observed <= expiry,
            "Original success/query was not established within its original TTL",
        )
        ids[stage] = receipt["operation_id"]
    preview = document("reply:sync-preview")
    require(preview["data"]["preview"]["operation_id"] == ids["sync"], "Original sync ID differs")
    project, candidate = frozen["project"], frozen["candidate-seven"]
    require(
        project["kind"] == candidate["kind"] == "tree"
        and Path(project["root"]) == old / "project"
        and Path(candidate["root"]) == old / "candidate-seven"
        and set(project["files"]) == set(candidate["files"]) == set(core.PROJECT_FILES),
        "Original exact seven-file set is incomplete",
    )
    for name in core.PROJECT_FILES:
        require(
            project["files"][name]["sha256"] == candidate["files"][name]["sha256"]
            and project["files"][name]["size"] == candidate["files"][name]["size"],
            "Saved seven-file bytes differ from the exact candidate snapshot",
        )
    require(
        project["files"][STEM + ".kicad_pcb"]["sha256"]
        == frozen["review:candidate"]["identity"]["sha256"],
        "Saved PCB differs from the published candidate",
    )
    saved_receipt = document("reply:sync-confirm")["data"]["receipt"]["snapshot_after"]
    require(
        saved_receipt["revision"] == project["files"][STEM + ".kicad_pcb"]["sha256"]
        and Path(saved_receipt["extensions"]["path"]) == old / "project" / (STEM + ".kicad_pcb"),
        "Successful original receipt does not bind the saved PCB",
    )
    request = document("review-request:candidate")
    validate_acknowledgment(
        request,
        frozen["review-request:candidate"]["identity"]["sha256"],
        document("human-ack:candidate"),
    )
    log = old / "logs/formal/repeat-confirm-negative"
    records = [(path, load(path)) for path in (old / "state").glob("formal-stop-*.json")]
    records = [(path, row) for path, row in records if row.get("out") == str(log)]
    require(len(records) == 1, "Expected one exact original repeat-method stop record")
    stop_path, stop = records[0]
    require(
        stop.get("code") == "E_TRIAL_NATIVE_STOP"
        and stop.get("invocation_attempted") is True
        and stop.get("inputs_before") == frozen
        and stop.get("automatic_retry") is False,
        "Original stop is outside the finite observation scope",
    )
    policy = load(Path(stop["execution_policy"]))
    declaration = {
        "code": "E_CONFLICT",
        "exit_code": 6,
        "reason": "token_used",
        "operation_id": ids["sync"],
    }
    require(
        policy.get("expected_public_refusal") == declaration
        and policy.get("expected_exit_codes") == [6]
        and policy.get("inputs_before") == frozen,
        "Original refusal policy/inputs differ",
    )
    expected_argv = [
        ready.record["dependencies"]["paths"]["product_exe"],
        "project",
        "sync",
        "--board",
        str(old / "project" / (STEM + ".kicad_pcb")),
        "--schematic",
        str(old / "project" / (STEM + ".kicad_sch")),
        "--candidate-out",
        str(old / "review" / (STEM + ".kicad_pcb")),
        "--confirm",
        preview["data"]["confirm_token"],
        "--compact",
    ]
    require(policy["argv"] == expected_argv, "Original refusal arguments target another project")
    trace, generic, reply = (
        load(log / "TRACE.json"),
        load(log / "GENERIC-CLASSIFICATION.json"),
        load(log / "stdout"),
    )
    adapter = stop["details"].get("public_refusal_adapter", {})
    require(
        adapter.get("admitted") is False and adapter.get("issues") == ["undeclared_extra_child"],
        "Only the original undeclared-helper method stop is supported",
    )
    details = reply.get("error", {}).get("details", {})
    require(
        reply.get("ok") is False
        and reply["error"].get("code") == "E_CONFLICT"
        and reply["error"].get("retryable") is False
        and details.get("conflict") == "token_used"
        and details.get("dispatch_state") == "not_started"
        and details.get("operation_id") == ids["sync"],
        "Original public refusal is not exact token_used/not_started",
    )
    require(
        stop["details"].get("trace") == trace
        and stop["details"].get("generic_classification") == generic
        and stop["details"].get("trace_sha256") == ident(log / "TRACE.json")["sha256"]
        and stop["details"].get("stdout_sha256")
        == ident(log / "stdout")["sha256"]
        == trace["stdout_sha256"],
        "Original trace/stdout stop bindings are incomplete",
    )
    verify_known_refusal_trace(trace, generic, policy["argv"], ready, new_ready)
    return {
        "old_work": str(old),
        "context": context,
        "ready": ready.as_dict(),
        "operation_ids": ids,
        # New origin metadata bindings do not replace any original expected
        # engineering/configuration inventory in context.inputs.
        "source_metadata": {
            "context_document_sha256": digest((old / "method-state/CONTEXT.json").read_bytes()),
            "ready_document_sha256": digest((old / "READY.json").read_bytes()),
            "stop_record": ident(stop_path),
            "policy": ident(stop["execution_policy"]),
            "trace": ident(log / "TRACE.json"),
            "stdout": ident(log / "stdout"),
        },
        "legacy_stop_preserved": True,
        "legacy_source_gaps_restored": False,
        "legacy_preprobe_snapshot_available": "repeat-confirm-before" in frozen,
        "legacy_source_limits": "Old seven-provider omissions and _MEI scope remain unchanged.",
        "scope": "Fresh read-only observations only; not CAD recovery or old READY upgrade",
    }


class SavedObservation:
    def __init__(self, work, *, _runner=None):
        self.ready = core.load_ready(permitted_old_path(work))
        require(
            not self.ready.record["synthetic"] or _runner is not None,
            "Synthetic READY cannot enter real saved observations",
        )
        self.synthetic = self.ready.record["synthetic"]
        self.work, self.package = self.ready.work_root, self.ready.package_root
        require(
            not (self.work / "method-state").exists(),
            "Observation destination must be a new prepared root without a CAD method context",
        )
        method = str(Path(__file__).absolute())
        require(
            method in self.ready.record["methods"]["files"]
            and file_identity(method) == self.ready.record["methods"]["files"][method],
            "Observation method was not frozen in the new destination READY",
        )
        self.state = self.work / "observation-state"
        self._runner, self.context = _runner, None

    def save(self):
        path = self.state / "CONTEXT.json"
        temporary = path.with_suffix(".tmp")
        write_new(temporary, self.context.as_dict())
        os.replace(temporary, path)

    def reopen(self):
        self.context = core.FrozenContext.from_dict(load(self.state / "CONTEXT.json"))
        plan = load(self.work / "intent/PLAN.json")
        origin = qualify_original(plan["origin"]["old_work"], self.ready)
        require(origin == plan["origin"], "Original stop/READY/context observation changed")
        return plan

    def bind(self, label, path):
        core.verify_ready(self.ready, inputs=self.context.inputs)
        item = capture(path)  # New owned artifacts only.
        require(
            Path(path).is_relative_to(self.work),
            "Cannot adopt original data as a new expected input",
        )
        if label in self.context.inputs:
            require(item == self.context.inputs[label], "New observation input changed")
        self.context.inputs[label] = item
        self.save()

    def plan(self, old_work):
        require(
            not self.state.exists() and not (self.work / "intent").exists(),
            "Use a new prepared observation root",
        )
        current_method = str(Path(__file__).absolute())
        require(
            current_method in self.ready.record["methods"]["files"]
            and file_identity(current_method)
            == self.ready.record["methods"]["files"][current_method],
            "Current observation method was not frozen before new preparation",
        )
        criteria = self.package / "windows-internal-trial/READONLY_CONTINUATION_CRITERIA.md"
        current_records = {
            **self.ready.record["methods"]["files"],
            **self.ready.record["dependencies"]["files"],
        }
        require(
            str(criteria) in current_records
            and file_identity(criteria) == current_records[str(criteria)],
            "New read-only criteria were not frozen before preparation",
        )
        overlay = self.ready.record["request"]["dependencies"].get("prospective_provider_errata")
        require(
            isinstance(overlay, dict)
            and len(overlay.get("files", {})) == 7
            and overlay.get("manifest")
            == ident(self.package / "trial_tools/resources/TRIAL-PROVIDER-ERRATA.json"),
            "New observation READY lacks the prospectively frozen seven-provider overlay",
        )
        for row in overlay["files"].values():
            require(
                row["path"] in self.ready.record["dependencies"]["files"]
                and ident(row["path"]) == row,
                "New observation provider overlay changed",
            )
        origin = qualify_original(old_work, self.ready)
        project = self.work / "observed-project"
        project.mkdir()
        source = Path(origin["context"]["inputs"]["project"]["root"])
        migrations = []
        for name in core.PROJECT_FILES:
            target = project / name
            with target.open("xb") as stream:
                stream.write((source / name).read_bytes())
            expected = origin["context"]["inputs"]["project"]["files"][name]
            require(
                ident(target)["sha256"] == expected["sha256"], "Owned saved-copy migration differs"
            )
            migrations.append(
                {
                    "source": str(source / name),
                    "original_sha256": expected["sha256"],
                    "copy": ident(target),
                }
            )
        plan = {
            "schema": SCHEMA,
            "synthetic": self.synthetic,
            "origin": origin,
            "migrations": migrations,
            "new_method_inventory": self.ready.record["methods"],
            "new_provider_inventory": self.ready.record["dependencies"],
            "old_provider_inventory": origin["ready"]["dependencies"],
            "provider_errata": ident(
                self.package / "trial_tools/resources/TRIAL-PROVIDER-ERRATA.json"
            ),
            "new_only_provider_overlay": overlay,
            "readonly_criteria": ident(criteria),
            "legacy_missing_providers_retroactively_declared": False,
            "read_only_commands": "Existing Readers checks + 11 PCB/3 SCH views; fixed argv only",
            "formal_cad_writes": 0,
            "product_executable_calls": 0,
        }
        self.state.mkdir()
        write_new(self.work / "intent/PLAN.json", plan)
        fresh = core.freeze_context(
            self.ready,
            inputs={
                "new-project": project,
                "new-plan": self.work / "intent",
                "new-methods": self.package / "trial_tools",
                "new-physical-methods": self.package / "physical-footprint-eco/methods",
                "new-readonly-criteria": criteria,
            },
        )
        fresh.inputs.update(
            {
                "original:" + key: copy.deepcopy(value)
                for key, value in origin["context"]["inputs"].items()
            }
        )
        core.verify_ready(self.ready, inputs=fresh.inputs)
        self.context = fresh
        self.save()
        return {
            "status": "new_readonly_plan_frozen",
            "synthetic": self.synthetic,
            "plan": ident(self.work / "intent/PLAN.json"),
            "legacy_stop_preserved": True,
            "formal_cad_writes": 0,
        }

    def run(self, name, argv, *, profile="consumer", expected_outputs=(), **kwargs):
        require(
            not kwargs and profile == "consumer", "Only fixed consumer observations are allowed"
        )
        self.reopen()
        require_readonly_argv(self.ready, self.work, name, argv, expected_outputs)
        out = self.work / "logs/saved-observation" / name
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = core.guarded_run(
                argv,
                context=self.context,
                profile="consumer",
                cwd=self.work,
                out=out,
                expected_outputs=expected_outputs,
                _runner=self._runner,
            )
        finally:
            self.save()
        for path in expected_outputs:
            self.bind(str(path), Path(path))
        result["stdout_path"] = str(out / "stdout")
        return result

    def observe(self):
        plan = self.reopen()
        if (self.state / "COMPLETE.json").exists():
            return {
                "status": "observed_pending_actual_views",
                "synthetic": self.synthetic,
                "new_processes": 0,
                "request": ident(self.work / "output/observation/REVIEW-REQUEST.json"),
            }
        require(
            not (self.state / "ATTEMPT.json").exists(), "Interrupted observations cannot replay"
        )
        write_new(
            self.state / "ATTEMPT.json",
            {"schema": SCHEMA, "synthetic": self.synthetic, "automatic_retry": False},
        )
        deps = dict(self.ready.record["dependencies"]["paths"])
        readers = Readers(
            self.package,
            self.work,
            deps,
            self.ready.record["request"]["config"]["renderer_runtime"],
            self.run,
            self.bind,
            python_flags=self.ready.python_flags("consumer"),
        )
        project = self.work / "observed-project"
        checks = readers.checks("observation", project)
        images = readers.pcb_images("observation", project, final=True)
        images += readers.schematic_images("observation", project)
        request = {
            "schema": "trial-actual-view-request/v1",
            "synthetic": self.synthetic,
            "stage": "fresh-saved-observation",
            "operation_id": plan["origin"]["operation_ids"]["sync"],
            "candidate": ident(project / (STEM + ".kicad_pcb")),
            "checks": checks,
            "images": images,
            "actual_view_performed": False,
            "statement_required": (
                "I viewed every listed complete image and accept this exact snapshot "
                "within the stated finite scope."
            ),
        }
        path = self.work / "output/observation/REVIEW-REQUEST.json"
        write_new(path, request)
        self.bind("new-review-request", path)
        write_new(
            self.state / "COMPLETE.json",
            {
                "schema": SCHEMA,
                "synthetic": self.synthetic,
                "request": ident(path),
                "legacy_stop_preserved": True,
                "legacy_source_gaps_restored": False,
                "formal_cad_writes": 0,
            },
        )
        self.bind("new-observation-complete", self.state / "COMPLETE.json")
        return {
            "status": "observed_pending_actual_views",
            "synthetic": self.synthetic,
            "request": ident(path),
            "images": images,
            "legacy_source_gaps_restored": False,
            "formal_cad_writes": 0,
        }

    def review(self, ack=None, output=None):
        self.reopen()
        path = self.work / "output/observation/REVIEW-REQUEST.json"
        request = load(path)
        if output:
            target = Path(output).absolute()
            require(target.is_relative_to(self.work), "Review draft must stay in new owned work")
            write_new(
                target,
                {
                    "schema": "trial-actual-view-ack/v1",
                    "request_sha256": ident(path)["sha256"],
                    "operation_id": request["operation_id"],
                    "reviewer": "",
                    "statement": request["statement_required"],
                    "images": [
                        {"path": row["path"], "sha256": row["sha256"], "actual_viewed": False}
                        for row in request["images"]
                    ],
                    "verdict": "pending",
                },
            )
            return {
                "status": "pending_unviewed",
                "draft": ident(target),
                "synthetic": self.synthetic,
            }
        target = ordinary_path(ack)
        require(target.is_relative_to(self.work), "Acknowledgment must stay in new owned work")
        validate_acknowledgment(request, ident(path)["sha256"], load(target))
        record = self.state / "ACTUAL-VIEWS.json"
        require(not record.exists(), "An actual-view record already exists")
        write_new(
            record,
            {
                "schema": SCHEMA,
                "synthetic": self.synthetic,
                "request": ident(path),
                "acknowledgment": ident(target),
                "actual_view_performed": True,
                "legacy_source_gaps_restored": False,
            },
        )
        self.bind("new-view-ack", target)
        self.bind("new-actual-views", record)
        return {
            "status": "fresh_saved_views_recorded",
            "synthetic": self.synthetic,
            "record": ident(record),
            "legacy_stop_preserved": True,
            "legacy_source_gaps_restored": False,
        }


def require_readonly_argv(ready, work, name, argv, outputs):
    """Closed argv inventory for the existing Readers; no product or free command."""
    deps = ready.record["dependencies"]["paths"]
    package, project = ready.package_root, work / "observed-project"
    methods, reading = (
        package / "physical-footprint-eco/methods",
        package / "trial_tools/resources/reading-tools",
    )
    out = work / "output/observation"
    checks, pcb, sch = out / "checks", out / "pcb", out / "schematic"
    board, root = project / (STEM + ".kicad_pcb"), project / (STEM + ".kicad_sch")
    flags, native, host, official = (
        ready.python_flags("consumer"),
        deps["native_python"],
        deps["host_python"],
        deps["official_cli"],
    )
    allowed = {
        "native": (
            [
                native,
                *flags,
                methods / "saved_board_native_read.py",
                board,
                methods / "requirements/BASELINE-INPUTS.json",
                methods / "requirements/PHYSICAL-PIN-INTENT42.json",
            ],
            [],
        ),
        "xml": (
            [
                official,
                "sch",
                "export",
                "netlist",
                "--format",
                "kicadxml",
                "--output",
                checks / "official.xml",
                root,
            ],
            [checks / "official.xml"],
        ),
        "erc": (
            [
                official,
                "sch",
                "erc",
                "--format",
                "json",
                "--severity-all",
                "--output",
                checks / "erc.json",
                root,
            ],
            [checks / "erc.json"],
        ),
        "drc": (
            [
                official,
                "pcb",
                "drc",
                "--format",
                "json",
                "--severity-all",
                "--schematic-parity",
                "--output",
                checks / "drc.json",
                board,
            ],
            [checks / "drc.json"],
        ),
        "hole": (
            [
                native,
                *flags,
                package / "trial_tools/resources/hole/native_hole_read_02.py",
                "--board",
                board,
                "--intent",
                checks / "HOLE-INTENT.json",
            ],
            [],
        ),
        "host-check": (
            [
                host,
                *flags,
                methods / "check_footprint_eco.py",
                "--current-dir",
                project,
                "--native",
                checks / "native.json",
                "--xml",
                checks / "official.xml",
                "--erc",
                checks / "erc.json",
                "--drc",
                checks / "drc.json",
                "--output",
                checks / "CHECK.json",
            ],
            [checks / "CHECK.json"],
        ),
        "sch-svg": ([official, "sch", "export", "svg", "--output", sch / "native", root], []),
        "page-identity": (
            [
                host,
                *flags,
                reading / "svg_page_identity.py",
                "--spec",
                reading / "hier_dual_rc.json",
                "--intent",
                reading / "FROZEN-INTENT-01.json",
                "--project",
                project,
                "--svgs",
                sch / "native",
                "--out",
                sch / "PAGE-IDENTITY.json",
            ],
            [sch / "PAGE-IDENTITY.json"],
        ),
    }
    for view, layers, mirrored in VIEWS:
        raw, derivative = pcb / "native" / (view + ".svg"), pcb / "derivative" / (view + ".svg")
        proof, derive, checked = (
            pcb / "derivative" / (view + suffix)
            for suffix in ("-proof.json", "-derive.json", "-check.json")
        )
        words = [
            official,
            "pcb",
            "export",
            "svg",
            "--output",
            raw,
            "--layers",
            ",".join(layers),
            "--mode-single",
            "--page-size-mode",
            "1",
            "--exclude-drawing-sheet",
        ]
        allowed["svg-" + view] = ([*words, *(["--mirror"] if mirrored else []), board], [raw])
        common = ["--raw", raw, "--derivative", derivative, "--proof", proof]
        allowed["derive-" + view] = (
            [host, *flags, reading / "svg_reading.py", "derive", *common, "--out", derive],
            [derivative, proof, derive],
        )
        allowed["geometry-" + view] = (
            [host, *flags, reading / "svg_reading.py", "check", *common, "--out", checked],
            [checked],
        )
    for phase, directory, script, mode, images in (
        (
            "pcb-render",
            pcb,
            "render_pcb.cjs",
            "render-bound",
            [view + ".png" for view, _, _ in VIEWS],
        ),
        (
            "page-render",
            sch,
            "render_pages.cjs",
            "render-pages",
            [page + ".png" for page in ("root", "A", "B")],
        ),
    ):
        plan = directory / "PLAN.json"
        if name == "observation-" + phase:
            node = ready.record["request"]["config"]["renderer_runtime"]["node"]["path"]
            allowed[phase] = (
                [node, reading / script, plan, ident(plan)["sha256"], mode],
                [
                    *[directory / "render/images" / image for image in images],
                    directory / "render/RESULT.json",
                ],
            )
    key = name.removeprefix("observation-")
    require(
        name.startswith("observation-") and key in allowed,
        "Command is outside fixed read-only Readers",
    )
    expected, expected_outputs = allowed[key]
    require(
        list(map(str, argv)) == list(map(str, expected))
        and list(map(str, outputs)) == list(map(str, expected_outputs)),
        "Read-only argv or outputs differ from the frozen method",
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="phase", required=True)
    for phase in ("plan", "observe", "review-template", "review-record"):
        item = sub.add_parser(phase)
        item.add_argument("--work", required=True, help="New READY from the current public prepare")
        if phase == "plan":
            item.add_argument("--old-work", required=True)
        if phase == "review-template":
            item.add_argument("--output", required=True)
        if phase == "review-record":
            item.add_argument("--ack", required=True)
    args = parser.parse_args(argv)
    observation = None
    try:
        observation = SavedObservation(args.work)
        result = (
            observation.plan(args.old_work)
            if args.phase == "plan"
            else observation.observe()
            if args.phase == "observe"
            else observation.review(output=args.output)
            if args.phase == "review-template"
            else observation.review(ack=args.ack)
        )
        envelope, code = {"ok": True, "schema_version": "1.0", "data": result}, 0
    except Exception as error:
        evidence = None
        work = Path(args.work)
        if observation is not None and work.is_absolute() and (work / "OWNERSHIP.json").is_file():
            evidence = work / "state" / ("readonly-refusal-" + uuid.uuid4().hex + ".json")
            write_new(
                evidence,
                {
                    "type": type(error).__name__,
                    "reason": str(error),
                    "details": getattr(error, "details", None),
                    "legacy_modified": False,
                },
            )
        envelope, code = (
            {
                "ok": False,
                "schema_version": "1.0",
                "error": {
                    "code": getattr(error, "code", "E_READONLY_REJECTED"),
                    "message": "Read-only observation refused; preserve old and new evidence.",
                    "retryable": False,
                    "details": {
                        "error_type": type(error).__name__,
                        "formal_cad_writes": 0,
                        "private_evidence": str(evidence) if evidence else None,
                    },
                },
            },
            1,
        )
    print(json.dumps(envelope, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
