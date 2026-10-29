"""Pure/fake observer regressions: no debuggee, native API or real process starts.

The full producer is driven by fake kernel/Job/process/CIM/Debugger objects.
Classifier cases are synthetic data; persistence cases write only tmp evidence.
This file does not open any product operation state, database or old incident.
"""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.native_observation import classify_trace, inspect_trace
from bench.persistence_acceptance import process_tree as producer

ROOT_PID, REUSED_PID = 42, 31400
ROOT_BIRTH = 134000000000000000
OLD_BIRTH, NEW_BIRTH = ROOT_BIRTH + 10_000_000, ROOT_BIRTH + 30_000_000


def instance(pid, birth):
    return producer.process_instance_id(pid, birth)


def cim_birth(birth):
    value = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=birth // 10)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def identity(pid, birth, index, image, parent=None):
    return {
        "pid": pid,
        "creation_filetime": birth,
        "process_instance_id": instance(pid, birth),
        "create_event_index": index,
        "identity_status": "bound",
        "image": image,
        "image_sha256": hashlib.sha256(image.encode()).hexdigest(),
        "owned": True,
        "metadata_creation_filetime_matches": True,
        "metadata": {
            "ProcessId": pid,
            "ParentProcessId": ROOT_PID if parent else 999,
            "CreationDate": cim_birth(birth),
            "CommandLine": image + " harmless-fake-arguments",
        },
        "parent_process_instance_id": parent,
        "parent_binding": "observed-parent" if parent else "external-observer",
        "exit": 0,
    }


def schema3_trace():
    root = instance(ROOT_PID, ROOT_BIRTH)
    before, after = instance(REUSED_PID, OLD_BIRTH), instance(REUSED_PID, NEW_BIRTH)
    events, exceptions = [], []

    def event(code, pid, birth, **fields):
        row = {
            "index": len(events),
            "code": code,
            "pid": pid,
            "thread_id": pid + 1,
            "process_instance_id": instance(pid, birth),
            "creation_filetime": birth,
            "continued": True,
            "cleanup": False,
            "continuation": "0x10002",
        }
        row.update(fields)
        if code == 3:
            row.update(create_event_index=row["index"], identity_status="bound")
        events.append(row)
        return row

    event(3, ROOT_PID, ROOT_BIRTH)
    for birth in (OLD_BIRTH, NEW_BIRTH):
        event(3, REUSED_PID, birth)
        event(6, REUSED_PID, birth)
        event(
            1,
            REUSED_PID,
            birth,
            exception_code="0x80000003",
            first_chance=True,
            bootstrap_breakpoint=True,
        )
        current = event(
            1,
            REUSED_PID,
            birth,
            exception_code="0xe06d7363",
            first_chance=True,
            bootstrap_breakpoint=False,
            continuation="0x80010001",
        )
        exceptions.append(
            {
                "pid": REUSED_PID,
                "thread_id": REUSED_PID + 1,
                "code": "0xe06d7363",
                "process_instance_id": current["process_instance_id"],
                "creation_filetime": birth,
                "event_index": current["index"],
                "first_chance": True,
                "continuation": "0x80010001",
            }
        )
        event(5, REUSED_PID, birth, exit_code=0)
    event(5, ROOT_PID, ROOT_BIRTH, exit_code=0)
    rows = [
        identity(ROOT_PID, ROOT_BIRTH, 0, "owned-root.exe"),
        identity(REUSED_PID, OLD_BIRTH, 1, "owned-native.exe", root),
        identity(REUSED_PID, NEW_BIRTH, 6, "owned-console.exe", root),
    ]
    empty = {"members": [], "all_members_attributed": True}
    return {
        "observation_schema": 3,
        "complete_tree": True,
        "timed_out": False,
        "root_pid": ROOT_PID,
        "root_process_instance_id": root,
        "root_identity": {
            "pid": ROOT_PID,
            "creation_filetime": ROOT_BIRTH,
            "process_instance_id": root,
            "image": "owned-root.exe",
            "assigned_before_resume": True,
            "owned_job_member": True,
        },
        "exit": 0,
        "events": events,
        "processes": rows,
        "exceptions": exceptions,
        "module_inventory": {root: [], before: [], after: []},
        "job_active_before_close": 0,
        "job_finalization": {
            "natural_tree_complete": True,
            "controlled_termination": False,
            "control_events": [],
            "before_close": copy.deepcopy(empty),
            "after_cleanup": copy.deepcopy(empty),
        },
    }


def schema2_trace():
    root = identity(ROOT_PID, ROOT_BIRTH, 0, "owned-root.exe")
    for key in (
        "process_instance_id",
        "create_event_index",
        "identity_status",
        "metadata_creation_filetime_matches",
        "parent_process_instance_id",
        "parent_binding",
    ):
        root.pop(key)
    events = [
        {"code": 3},
        {
            "code": 1,
            "exception_code": "0xe06d7363",
            "first_chance": True,
            "bootstrap_breakpoint": False,
        },
        {"code": 5, "exit_code": 0},
    ]
    for index, event in enumerate(events):
        event.update(
            index=index,
            pid=ROOT_PID,
            thread_id=ROOT_PID + 1,
            cleanup=False,
            continued=True,
            continuation="0x80010001" if event["code"] == 1 else "0x10002",
        )
    result = schema3_trace()
    result.update(
        observation_schema=2,
        events=events,
        processes=[root],
        exceptions=[
            {
                "pid": ROOT_PID,
                "thread_id": ROOT_PID + 1,
                "code": "0xe06d7363",
                "event_index": 1,
                "first_chance": True,
                "continuation": "0x80010001",
            }
        ],
        module_inventory={str(ROOT_PID): []},
    )
    result.pop("root_process_instance_id")
    result["root_identity"].pop("process_instance_id")
    return result


def persisted(tmp_path, trace):
    trace = copy.deepcopy(trace)
    for row in trace["processes"] if trace["observation_schema"] == 3 else []:
        snapshot = {
            key: value for key, value in row.items() if key not in {"creation_record", "exited_at"}
        }
        snapshot["exit"] = None
        path = tmp_path / ("process-" + row["process_instance_id"] + ".json")
        raw = (json.dumps(snapshot, indent=2) + "\n").encode()
        path.write_bytes(raw)
        row["creation_record"] = {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}
    for name, raw in (("stdout", b"synthetic response\n"), ("stderr", b"")):
        (tmp_path / name).write_bytes(raw)
        trace[name + "_sha256"] = hashlib.sha256(raw).hexdigest()
    pairs = []
    for event in trace["events"]:
        received = copy.deepcopy(event)
        received["continued"] = False
        received.pop("continuation", None)
        pairs.extend(
            [{"kind": "received", "event": received}, {"kind": "continued", "event": event}]
        )
    raw = b"".join((json.dumps(row) + "\n").encode() for row in pairs)
    (tmp_path / "events.jsonl").write_bytes(raw)
    trace["event_journal_sha256"] = hashlib.sha256(raw).hexdigest()
    (tmp_path / "TRACE.json").write_text(json.dumps(trace), encoding="utf-8")
    return trace


@pytest.mark.parametrize("same_image", [False, True], ids=["different-image", "same-image"])
def test_registry_keeps_both_births_and_exclusive_metadata_paths(tmp_path, same_image):
    registry = producer.ProcessInstances()
    image = "native.exe"
    old = registry.create(REUSED_PID, OLD_BIRTH, 1, image=image)
    old_id = old["process_instance_id"]
    registry.finish(REUSED_PID, old_id, 0, exited_at="old-finished")
    frozen_old = copy.deepcopy(old)
    new = registry.create(REUSED_PID, NEW_BIRTH, 9, image=image if same_image else "console.exe")
    new_id = new["process_instance_id"]
    assert old_id != new_id and registry.current(REUSED_PID) is new
    assert registry.rows[old_id] == frozen_old and len(registry.rows) == 2
    paths = [tmp_path / ("process-" + registry.stem(value) + ".json") for value in (old_id, new_id)]
    for path, row in zip(paths, (old, new), strict=True):
        with path.open("x", encoding="utf-8") as stream:
            json.dump(row, stream)
    assert paths[0] != paths[1]
    before = paths[0].read_bytes()
    with pytest.raises(FileExistsError):
        paths[0].open("x").close()
    assert paths[0].read_bytes() == before
    registry.finish(REUSED_PID, new_id, 0)
    assert not registry.active and registry.rows[old_id] == frozen_old
    assert registry.rows[new_id]["exit"] == 0


@pytest.mark.parametrize(
    "mode", ["same-active-birth", "different-active-birth", "same-retired-birth"]
)
def test_registry_rejects_duplicate_or_live_pid_without_overwriting_history(mode):
    registry = producer.ProcessInstances()
    old = registry.create(REUSED_PID, OLD_BIRTH, 1, image="native.exe")
    if mode == "same-retired-birth":
        registry.finish(REUSED_PID, old["process_instance_id"], 0)
    before, active = copy.deepcopy(registry.rows), dict(registry.active)
    birth = NEW_BIRTH if mode == "different-active-birth" else OLD_BIRTH
    with pytest.raises(ValueError):
        registry.create(REUSED_PID, birth, 9, image="console.exe")
    assert registry.rows == before and registry.active == active


@pytest.mark.parametrize("mode", ["unknown-pid", "wrong-instance", "already-exited"])
def test_registry_finish_requires_the_exact_current_instance(mode):
    registry = producer.ProcessInstances()
    row = registry.create(REUSED_PID, OLD_BIRTH, 1)
    if mode == "already-exited":
        registry.finish(REUSED_PID, row["process_instance_id"], 0)
    before = copy.deepcopy(registry.rows)
    with pytest.raises(ValueError):
        registry.finish(
            REUSED_PID + 1 if mode == "unknown-pid" else REUSED_PID,
            instance(REUSED_PID, NEW_BIRTH)
            if mode == "wrong-instance"
            else row["process_instance_id"],
            0,
        )
    assert registry.rows == before


def test_loader_allowance_is_once_per_instance_not_once_per_pid():
    old, new = instance(REUSED_PID, OLD_BIRTH), instance(REUSED_PID, NEW_BIRTH)
    seen = set()
    assert producer.bootstrap_breakpoint(0x80000003, True, old, seen)
    assert not producer.bootstrap_breakpoint(0x80000003, True, old, seen)
    assert producer.bootstrap_breakpoint(0x80000003, True, new, seen)
    assert not producer.bootstrap_breakpoint(0xE06D7363, True, new, seen)
    assert not producer.bootstrap_breakpoint(0x80000003, False, new, seen)
    assert seen == {old, new}


@pytest.mark.parametrize("encoding", ["iso", "windows-json"])
@pytest.mark.parametrize(
    "delta,expected",
    [(0, True), (9999, True), (10000, False)],
    ids=["exact-ms", "same-ms-subtick", "wrong-next-ms"],
)
def test_metadata_birth_uses_declared_millisecond_precision(encoding, delta, expected):
    date = (
        cim_birth(OLD_BIRTH)
        if encoding == "iso"
        else ("/Date(" + str((OLD_BIRTH - 116444736000000000) // 10000) + ")/")
    )
    assert producer.metadata_birth_matches({"CreationDate": date}, OLD_BIRTH + delta) is expected


def test_same_pid_complete_instances_forward_first_chance_without_claiming_functional_success():
    result = classify_trace(schema3_trace())
    assert result["status"] == "completed_with_forwarded_first_chance"
    assert result["observation_complete"] and result["may_validate_functional_output"]
    assert result["functional_output_verified"] is False
    assert result["first_chance_notifications"] == [4, 9]
    assert result["second_chance_notifications"] == []
    assert result["stack_limitations"] == [4, 9]


MUTATIONS = [
    "missing-event-instance",
    "wrong-event-instance",
    "missing-event-birth",
    "wrong-event-birth",
    "wrong-create-index",
    "unbound-create",
    "missing-create",
    "missing-exit",
    "exit-wrong-instance",
    "exception-wrong-instance",
    "exception-wrong-birth",
    "parent-wrong-instance",
    "parent-already-exited",
    "module-unknown-instance",
    "root-wrong-instance",
    "duplicate-instance-row",
    "second-loader-same-instance",
    "continuation-swallowed",
    "late-old-instance-event",
    "new-instance-event-before-create",
    "missing-process-birth",
    "invalid-process-birth",
    "metadata-birth-mismatch",
    "metadata-match-flag-missing",
]


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_schema3_refuses_missing_out_of_order_or_cross_instance_evidence(mutation):
    trace = schema3_trace()
    old, new = trace["processes"][1:]
    if mutation == "missing-event-instance":
        trace["events"][4].pop("process_instance_id")
    elif mutation == "wrong-event-instance":
        trace["events"][4]["process_instance_id"] = new["process_instance_id"]
    elif mutation == "missing-event-birth":
        trace["events"][4].pop("creation_filetime")
    elif mutation == "wrong-event-birth":
        trace["events"][4]["creation_filetime"] = NEW_BIRTH
    elif mutation == "wrong-create-index":
        old["create_event_index"] = 2
    elif mutation == "unbound-create":
        trace["events"][1]["identity_status"] = "pending"
    elif mutation in {"missing-create", "missing-exit"}:
        # Keep every other event index untouched: the missing notification is
        # critical even if unrelated events still happen to be contiguous.
        trace["events"][1 if mutation == "missing-create" else 5]["code"] = 2
    elif mutation == "exit-wrong-instance":
        trace["events"][5]["process_instance_id"] = new["process_instance_id"]
        trace["events"][5]["creation_filetime"] = NEW_BIRTH
    elif mutation == "exception-wrong-instance":
        trace["exceptions"][0]["process_instance_id"] = new["process_instance_id"]
    elif mutation == "exception-wrong-birth":
        trace["exceptions"][0]["creation_filetime"] = NEW_BIRTH
    elif mutation == "parent-wrong-instance":
        new["parent_process_instance_id"] = "missing-parent"
    elif mutation == "parent-already-exited":
        new["metadata"]["ParentProcessId"] = REUSED_PID
        new["parent_process_instance_id"] = old["process_instance_id"]
    elif mutation == "module-unknown-instance":
        trace["module_inventory"]["unobserved-instance"] = []
    elif mutation == "root-wrong-instance":
        trace["root_process_instance_id"] = old["process_instance_id"]
    elif mutation == "duplicate-instance-row":
        trace["processes"].append(copy.deepcopy(old))
    elif mutation == "second-loader-same-instance":
        trace["events"][4].update(
            exception_code="0x80000003", bootstrap_breakpoint=True, continuation="0x10002"
        )
        trace["exceptions"].pop(0)
    elif mutation == "continuation-swallowed":
        trace["events"][4]["continuation"] = "0x10002"
        trace["exceptions"][0]["continuation"] = "0x10002"
    elif mutation == "late-old-instance-event":
        trace["events"][7].update(
            process_instance_id=old["process_instance_id"], creation_filetime=OLD_BIRTH
        )
    elif mutation == "new-instance-event-before-create":
        trace["events"][2].update(
            process_instance_id=new["process_instance_id"], creation_filetime=NEW_BIRTH
        )
    elif mutation == "missing-process-birth":
        old.pop("creation_filetime")
    elif mutation == "invalid-process-birth":
        old["creation_filetime"] = True
    elif mutation == "metadata-birth-mismatch":
        old["metadata"]["CreationDate"] = cim_birth(NEW_BIRTH)
    else:
        old.pop("metadata_creation_filetime_matches")
    result = classify_trace(trace)
    assert result["status"] == "observation_incomplete"
    assert not result["may_validate_functional_output"] and result["stop_required"]
    assert result["functional_output_verified"] is False


@pytest.mark.parametrize("schema", [2, 3], ids=["legacy2-unique", "schema3-reused-pid"])
def test_persisted_complete_instances_and_received_continued_pairs_are_accepted(tmp_path, schema):
    persisted(tmp_path, schema2_trace() if schema == 2 else schema3_trace())
    result = inspect_trace(tmp_path)
    assert result["may_validate_functional_output"] and not result["stop_required"]
    assert result["functional_output_verified"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "record-missing",
        "record-replaced",
        "record-wrong-instance",
        "journal-pair",
        "exception-instance",
        "stderr",
    ],
)
def test_persisted_record_or_journal_tampering_is_incomplete(tmp_path, mutation):
    trace = persisted(tmp_path, schema3_trace())
    record = tmp_path / trace["processes"][1]["creation_record"]["path"]
    if mutation == "record-missing":
        record.unlink()
    elif mutation == "record-replaced":
        record.write_bytes(b"{}")
    elif mutation == "record-wrong-instance":
        # Even an updated byte digest must not authenticate another birth.
        row = json.loads(record.read_bytes())
        row["creation_filetime"] = NEW_BIRTH
        raw = json.dumps(row).encode()
        record.write_bytes(raw)
        trace["processes"][1]["creation_record"]["sha256"] = hashlib.sha256(raw).hexdigest()
    elif mutation == "journal-pair":
        path = tmp_path / "events.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[9]["event"]["process_instance_id"] = trace["processes"][2]["process_instance_id"]
        raw = b"".join((json.dumps(row) + "\n").encode() for row in rows)
        path.write_bytes(raw)
        trace["event_journal_sha256"] = hashlib.sha256(raw).hexdigest()
    elif mutation == "exception-instance":
        trace["exceptions"][0]["process_instance_id"] = trace["processes"][2]["process_instance_id"]
    else:
        (tmp_path / "stderr").write_bytes(b"changed output")
    (tmp_path / "TRACE.json").write_text(json.dumps(trace), encoding="utf-8")
    result = inspect_trace(tmp_path)
    assert not result["may_validate_functional_output"] and result["stop_required"]
    assert result["status"] == "observation_incomplete"


def test_schema2_duplicate_create_is_not_repaired_into_two_instances():
    trace = schema2_trace()
    duplicate = copy.deepcopy(trace["events"][0])
    trace["events"].insert(1, duplicate)
    for index, event in enumerate(trace["events"]):
        event["index"] = index
    trace["exceptions"][0]["event_index"] = 2
    result = classify_trace(trace)
    assert not result["may_validate_functional_output"] and result["stop_required"]
    assert result["status"] == "observation_incomplete"


class FakeFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


@pytest.mark.parametrize("same_image", [False, True], ids=["different-image", "same-image"])
@pytest.mark.parametrize("case_mode", ["complete", "metadata-birth-mismatch"])
def test_producer_fake_api_preserves_reused_pid_modules_loader_and_exceptions(
    tmp_path, monkeypatch, same_image, case_mode
):
    """Exercise observe itself, replacing every OS/process/diagnostic entry."""
    fixture = schema3_trace()
    input_root = tmp_path / "inert-images"
    input_root.mkdir()
    images = []
    for name in ("root", "native", "console"):
        path = input_root / (name + ".exe")
        path.write_bytes(("inert-" + name).encode())
        images.append(path)
    if same_image:
        images[2] = images[1]
    rows = fixture["processes"]
    by_instance = {
        row["process_instance_id"]: (row, image) for row, image in zip(rows, images, strict=True)
    }
    handles = {
        index + 100: (row, image)
        for index, (row, image) in enumerate(zip(rows, images, strict=True))
    }
    handle_by_instance = {
        row["process_instance_id"]: handle for handle, (row, _image) in handles.items()
    }
    queue = iter(fixture["events"])
    current, metadata_calls, continuations, diagnostics = {}, [], [], []
    control = {"exited": False, "termination_requests": 0}
    fake_process = SimpleNamespace(pid=ROOT_PID, returncode=None, _handle=100)
    fake_process.poll = lambda: fake_process.returncode if control["exited"] else None
    fake_process.wait = lambda **unused: fake_process.returncode

    def receive(pointer, _timeout):
        source = next(queue)
        current.clear()
        current.update(source)
        event = ctypes.cast(pointer, ctypes.POINTER(producer.Event)).contents
        event.code, event.pid, event.tid = source["code"], source["pid"], source["thread_id"]
        if source["code"] == 3:
            event.data.create.process = handle_by_instance[source["process_instance_id"]]
            event.data.create.file = 1
            event.data.create.base = source["creation_filetime"]
        elif source["code"] == 6:
            event.data.load.file, event.data.load.base = 2, source["creation_filetime"] + 1
        elif source["code"] == 1:
            event.data.exception.record.code = int(source["exception_code"], 16)
            event.data.exception.first_chance = 1
        else:
            event.data.exit_code = 0xE0000001 if control["termination_requests"] else 0
            if source["pid"] == ROOT_PID:
                control["exited"] = True
                fake_process.returncode = int(event.data.exit_code)
        return True

    def times(handle, birth, *rest):
        result = ctypes.cast(birth, ctypes.POINTER(ctypes.c_ulonglong)).contents
        result.value = handles[handle][0]["creation_filetime"]
        return True

    def image_name(handle, _flags, name, _size):
        name.value = str(handles[handle][1])
        return True

    def member(_handle, _job, value):
        ctypes.cast(value, ctypes.POINTER(ctypes.c_int)).contents.value = 1
        return True

    def continue_event(pid, tid, status):
        continuations.append((pid, tid, status))
        return True

    kernel = SimpleNamespace(
        WaitForDebugEvent=FakeFunction(receive),
        GetProcessTimes=FakeFunction(times),
        QueryFullProcessImageNameW=FakeFunction(image_name),
        IsProcessInJob=FakeFunction(member),
        ContinueDebugEvent=FakeFunction(continue_event),
        CloseHandle=FakeFunction(lambda handle: True),
    )

    class FakeJob:
        def __init__(self):
            self.handle, self.root_identity = 7, {}

        def assign_and_resume(self, process):
            self.root_identity = dict(fixture["root_identity"], image=str(images[0]))

        def active(self):
            return 0 if control["exited"] else 1

        def terminate(self):
            assert case_mode == "metadata-birth-mismatch"
            control["termination_requests"] += 1

        def close(self):
            value = copy.deepcopy(fixture["job_finalization"])
            if control["termination_requests"]:
                value.update(
                    natural_tree_complete=False,
                    controlled_termination=True,
                    control_events=[{"action": "fake-owned-job-stop"}],
                )
            return value

    class FakeDebugger:
        def __init__(self, process, directory):
            self.pid, self.directory = process.pid, Path(directory)
            self.directory.mkdir(exist_ok=False)
            self.modules, self.exceptions = [], []
            diagnostics.append(self)

        def module(self, file, base):
            self.modules.append({"fake_file": int(file), "base": int(base)})

        def address(self, value):
            return {"fake_address": value}

        def context_and_dump(self, event):
            raise AssertionError("First-chance notification is not a crash capture request")

    def popen(*args, **kwargs):
        return fake_process

    def metadata(*args, **kwargs):
        row, image = by_instance[current["process_instance_id"]]
        metadata_calls.append(row["process_instance_id"])
        data = dict(row["metadata"], CommandLine=str(image) + " harmless-fake-arguments")
        if case_mode == "metadata-birth-mismatch" and row["creation_filetime"] == NEW_BIRTH:
            data["CreationDate"] = cim_birth(OLD_BIRTH)
        return SimpleNamespace(returncode=0, stdout=json.dumps(data).encode(), stderr=b"")

    monkeypatch.setattr(producer, "WindowsJob", FakeJob)
    monkeypatch.setattr(producer, "Debugger", FakeDebugger)
    monkeypatch.setattr(producer.C, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(producer.subprocess, "Popen", popen)
    monkeypatch.setattr(producer.subprocess, "run", metadata)
    monkeypatch.setenv("SystemRoot", str(tmp_path / "fake-windows"))
    output = tmp_path / "observer"
    if case_mode == "metadata-birth-mismatch":
        with pytest.raises(RuntimeError, match="CIM process identity"):
            producer.observe([str(images[0]), "harmless-fake-arguments"], tmp_path, {}, output)
        trace = json.loads((output / "TRACE.json").read_bytes())
        old_id, new_id = instance(REUSED_PID, OLD_BIRTH), instance(REUSED_PID, NEW_BIRTH)
        rows_by_id = {row["process_instance_id"]: row for row in trace["processes"]}
        assert len(rows_by_id) == 3 and rows_by_id[old_id]["exit"] == 0
        assert rows_by_id[old_id]["metadata"]["CreationDate"] == cim_birth(OLD_BIRTH)
        old_record = output / ("process-" + old_id + ".json")
        assert (
            hashlib.sha256(old_record.read_bytes()).hexdigest()
            == (rows_by_id[old_id]["creation_record"]["sha256"])
        )
        assert rows_by_id[new_id]["identity_status"] == "kernel_bound_metadata_pending"
        assert rows_by_id[new_id]["exit"] == 0xE0000001
        assert "creation_record" not in rows_by_id[new_id]
        assert not (output / ("process-" + new_id + ".json")).exists()
        assert len(diagnostics) == 2 and new_id not in trace["module_inventory"]
        assert trace["complete_tree"] is False and trace["controlled_cleanup_tree_complete"]
        assert control["termination_requests"] == 1
        assert trace["job_finalization"]["controlled_termination"] is True
        assert trace["job_finalization"]["natural_tree_complete"] is False
        assert trace["events"][6]["process_instance_id"] == new_id
        cleanup = [event for event in trace["events"] if event["cleanup"]]
        cleanup_exceptions = [event for event in cleanup if event["code"] == 1]
        assert len(cleanup_exceptions) == 2
        assert all(event["process_instance_id"] == new_id for event in cleanup_exceptions)
        assert all(event["continuation"] == "0x80010001" for event in cleanup_exceptions)
        journal = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
        assert len(journal) == 2 * len(trace["events"]) == 24
        assert journal[12]["kind"] == "received"
        assert journal[12]["event"]["received_identity_binding"] == "pending_create"
        assert metadata_calls == [row["process_instance_id"] for row in rows]
        verdict = inspect_trace(output)
        assert verdict["status"] == "observation_incomplete" and verdict["stop_required"]
        assert not verdict["may_validate_functional_output"]
        return
    trace = producer.observe([str(images[0]), "harmless-fake-arguments"], tmp_path, {}, output)
    ids = {row["process_instance_id"] for row in trace["processes"]}
    assert ids == set(by_instance) and len(trace["processes"]) == 3
    reused = [row for row in trace["processes"] if row["pid"] == REUSED_PID]
    assert [row["creation_filetime"] for row in reused] == [OLD_BIRTH, NEW_BIRTH]
    assert all(row["exit"] == 0 for row in reused)
    assert set(trace["module_inventory"]) == ids and len(diagnostics) == 3
    assert len({value.directory for value in diagnostics}) == 3
    for identifier in ids:
        record = output / ("process-" + identifier + ".json")
        assert record.is_file()
        saved = json.loads(record.read_bytes())
        assert saved["process_instance_id"] == identifier and saved["exit"] is None
        diagnostic = next(
            item for item in diagnostics if item.directory.name == "local-diagnostics-" + identifier
        )
        assert trace["module_inventory"][identifier] == diagnostic.modules
        birth = by_instance[identifier][0]["creation_filetime"]
        expected_bases = (
            {birth} if identifier == instance(ROOT_PID, ROOT_BIRTH) else {birth, birth + 1}
        )
        assert {row["base"] for row in diagnostic.modules} == expected_bases
        assert [row["process_instance_id"] for row in diagnostic.exceptions] == (
            [] if identifier == instance(ROOT_PID, ROOT_BIRTH) else [identifier]
        )
    assert [row["process_instance_id"] for row in trace["exceptions"]] == [
        instance(REUSED_PID, OLD_BIRTH),
        instance(REUSED_PID, NEW_BIRTH),
    ]
    loaders = [event for event in trace["events"] if event.get("bootstrap_breakpoint")]
    assert len(loaders) == 2 and len({event["process_instance_id"] for event in loaders}) == 2
    assert all(
        status
        == (
            0x80010001
            if source["code"] == 1 and not source.get("bootstrap_breakpoint")
            else 0x10002
        )
        for (_pid, _tid, status), source in zip(continuations, fixture["events"], strict=True)
    )
    assert metadata_calls == [row["process_instance_id"] for row in rows]
    verdict = inspect_trace(output)
    assert verdict["status"] == "completed_with_forwarded_first_chance"
    assert verdict["may_validate_functional_output"] and not verdict["stop_required"]
    assert verdict["functional_output_verified"] is False
