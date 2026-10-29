"""Observe a new owned Windows process tree, including every child creation event."""

import ctypes as C
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from observer02_support import now, put, sha

from observer02_native_observation import process_instance_id
from observer02_metadata import PROVIDER, MetadataIncomplete, observe_metadata
from observer02_owned_process import WindowsJob
from observer02_win_debugging import Debugger, Event


def implementation_sources(*, require_current=False):
    """Report captured bytes actually executed by integration, plus current bytes."""
    result = {}
    for name in (
        "observer02_support",
        "observer02_metadata",
        "observer02_owned_process",
        "observer02_win_debugging",
        "observer02_native_observation",
        "observer02_process_tree",
    ):
        module = sys.modules[name]
        loaded = dict(getattr(module, "_observer02_loaded_source", {}))
        path = Path(module.__file__).resolve()
        current = sha(path)
        stable = loaded == {"path": str(path), "sha256": current}
        if require_current and not stable:
            raise RuntimeError(
                "Observer must use integration captured source bytes unchanged"
            )
        result[name] = dict(loaded, current_sha256=current, unchanged_since_load=stable)
    return result


class ProcessInstances:
    """Immutable lifecycle identities plus a PID route for only the currently alive instance."""

    def __init__(self):
        self.rows, self.active = {}, {}

    def create(self, pid, birth, create_event_index, **metadata):
        identity = process_instance_id(pid, birth)
        if type(create_event_index) is not int or create_event_index < 0:
            raise ValueError("A CREATE event index is required")
        if pid in self.active or identity in self.rows:
            raise ValueError("Duplicate or overlapping process instance creation")
        if set(metadata) & {
            "pid",
            "creation_filetime",
            "process_instance_id",
            "create_event_index",
            "exit",
        }:
            raise ValueError("Process instance identity cannot be overridden")
        row = dict(
            metadata,
            pid=pid,
            creation_filetime=birth,
            process_instance_id=identity,
            create_event_index=create_event_index,
            exit=None,
        )
        self.rows[identity] = row
        self.active[pid] = identity
        return row

    def current(self, pid):
        if pid not in self.active:
            raise ValueError("Event has no active process instance")
        return self.rows[self.active[pid]]

    def finish(self, pid, instance_id, exit_code, **metadata):
        row = self.current(pid)
        if row["process_instance_id"] != instance_id or row["exit"] is not None:
            raise ValueError("EXIT does not belong to the active process instance")
        if set(metadata) & {
            "pid",
            "creation_filetime",
            "process_instance_id",
            "create_event_index",
        }:
            raise ValueError("An EXIT cannot replace process identity")
        row.update(metadata, exit=exit_code)
        del self.active[pid]
        return row

    def stem(self, instance_id):
        if instance_id not in self.rows:
            raise ValueError("Unknown process instance")
        return instance_id


def bootstrap_breakpoint(code, first_chance, instance_id, seen):
    """Only the first first-chance loader breakpoint is consumed by the observer."""
    if code == 0x80000003 and first_chance and instance_id not in seen:
        seen.add(instance_id)
        return True
    return False


def observe(argv, cwd, env, output, *, timeout=120):
    loaded_sources = implementation_sources(require_current=True)
    output = Path(output)
    output.mkdir(exist_ok=False)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    handle, number = C.c_void_p, C.c_uint32
    kernel.WaitForDebugEvent.argtypes = [C.POINTER(Event), number]
    kernel.ContinueDebugEvent.argtypes = [number, number, number]
    kernel.GetProcessTimes.argtypes = [handle, handle, handle, handle, handle]
    kernel.QueryFullProcessImageNameW.argtypes = [
        handle,
        number,
        C.c_wchar_p,
        C.POINTER(number),
    ]
    kernel.IsProcessInJob.argtypes = [handle, handle, C.POINTER(C.c_int)]
    kernel.CloseHandle.argtypes = [handle]
    job, process, held = WindowsJob(), None, None
    instances = ProcessInstances()
    rows, active = instances.rows, instances.active
    initial, exceptions = set(), []
    events = []
    diagnostics = {}
    result = {
        "started_at": now(),
        "method": "DEBUG_PROCESS child creation events; owned job before execution",
        "observation_schema": 4,
        "metadata_provider": PROVIDER,
        "timed_out": False,
    }

    journal = (output / "events.jsonl").open("x", encoding="utf-8", newline="\n")

    def received(event, *, cleanup=False):
        row = {
            "index": len(events),
            "code": int(event.code),
            "pid": int(event.pid),
            "thread_id": int(event.tid),
            "at": now(),
            "cleanup": cleanup,
            "continued": False,
        }
        if event.code != 3 and event.pid in active:
            current = instances.current(event.pid)
            row.update(
                process_instance_id=current["process_instance_id"],
                creation_filetime=current["creation_filetime"],
            )
        else:
            row["received_identity_binding"] = (
                "pending_create" if event.code == 3 else "unbound"
            )
        if event.code == 1:
            row.update(
                exception_code=hex(event.data.exception.record.code),
                first_chance=bool(event.data.exception.first_chance),
            )
        elif event.code in (4, 5):
            row["exit_code"] = int(event.data.exit_code)
        events.append(row)
        journal.write(json.dumps({"kind": "received", "event": row}) + "\n")
        journal.flush()
        return row

    def continued(event, row, status):
        checked(kernel.ContinueDebugEvent(event.pid, event.tid, status))
        row.update(continued=True, continuation=hex(status))
        journal.write(json.dumps({"kind": "continued", "event": row}) + "\n")
        journal.flush()

    def checked(ok):
        if not ok:
            raise C.WinError(C.get_last_error())

    try:
        with (
            (output / "stdout").open("xb") as stdout,
            (output / "stderr").open("xb") as stderr,
        ):
            process = subprocess.Popen(
                list(map(str, argv)),
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=stderr,
                creationflags=0x08000000
                | 0x4
                | 0x1,  # DEBUG_PROCESS, not ONLY_THIS_PROCESS
            )
            job.assign_and_resume(process)
            result["root_pid"] = process.pid
            result["root_identity"] = job.root_identity
            root_id = process_instance_id(
                process.pid, job.root_identity["creation_filetime"]
            )
            result["root_process_instance_id"] = root_id
            result["root_identity"]["process_instance_id"] = root_id
            deadline = time.monotonic() + timeout
            while True:
                if time.monotonic() > deadline:
                    result["timed_out"] = True
                    raise TimeoutError(
                        "Owned public command exceeded the observation bound"
                    )
                event = Event()
                if not kernel.WaitForDebugEvent(C.byref(event), 100):
                    if C.get_last_error() not in (0, 121):
                        raise C.WinError(C.get_last_error())
                    continue
                continuation = 0x00010002
                held = event
                event_row = received(event)
                if event.code == 3:
                    info = event.data.create
                    member = C.c_int()
                    checked(
                        kernel.IsProcessInJob(info.process, job.handle, C.byref(member))
                    )
                    if not member.value:
                        raise RuntimeError("Unowned debug event; refuse to classify")
                    times = [C.c_ulonglong() for _ in range(4)]
                    checked(
                        kernel.GetProcessTimes(
                            info.process, *[C.byref(t) for t in times]
                        )
                    )
                    size, name = number(32768), C.create_unicode_buffer(32768)
                    checked(
                        kernel.QueryFullProcessImageNameW(
                            info.process, 0, name, C.byref(size)
                        )
                    )
                    row = instances.create(
                        event.pid,
                        times[0].value,
                        event_row["index"],
                        image=name.value,
                        image_sha256=sha(name.value),
                        owned=True,
                        observed_at=now(),
                        identity_status="kernel_bound_metadata_pending",
                    )
                    instance_id = row["process_instance_id"]
                    event_row.update(
                        process_instance_id=instance_id,
                        creation_filetime=times[0].value,
                        create_event_index=event_row["index"],
                    )
                    metadata = observe_metadata(
                        info.process,
                        job.handle,
                        int(event.pid),
                        times[0].value,
                        name.value,
                        budget_seconds=1.0,
                    )
                    row["metadata"] = metadata
                    row["metadata_kernel_identity_rechecked"] = True
                    row["metadata_birth_precision_ticks"] = 1
                    parent_pid = metadata.get("ParentProcessId")
                    if instance_id == root_id:
                        row.update(
                            parent_process_instance_id=None,
                            parent_binding="external-observer",
                        )
                    elif parent_pid in active and active[parent_pid] != instance_id:
                        parent = instances.current(parent_pid)
                        if parent["creation_filetime"] > row["creation_filetime"]:
                            raise RuntimeError(
                                "Parent process birth is later than child birth"
                            )
                        row.update(
                            parent_process_instance_id=parent["process_instance_id"],
                            parent_binding="observed-parent",
                        )
                    else:
                        raise RuntimeError(
                            "Child parent instance is not observed active at CREATE"
                        )
                    diagnostics[instance_id] = Debugger(
                        SimpleNamespace(pid=event.pid, _handle=info.process),
                        output / ("local-diagnostics-" + instances.stem(instance_id)),
                    )
                    diagnostics[instance_id].module(info.file, info.base)
                    row["identity_status"] = "bound"
                    event_row["identity_status"] = "bound"
                    metadata_path = output / (
                        "process-" + instances.stem(instance_id) + ".json"
                    )
                    put(metadata_path, row)
                    row["creation_record"] = {
                        "path": metadata_path.name,
                        "sha256": sha(metadata_path),
                    }
                elif event.code == 6:
                    instance_id = instances.current(event.pid)["process_instance_id"]
                    diagnostics[instance_id].module(
                        event.data.load.file, event.data.load.base
                    )
                elif event.code == 1:
                    info = event.data.exception
                    bootstrap = bootstrap_breakpoint(
                        info.record.code,
                        bool(info.first_chance),
                        instances.current(event.pid)["process_instance_id"],
                        initial,
                    )
                    event_row["bootstrap_breakpoint"] = bootstrap
                    if not bootstrap:
                        current = instances.current(event.pid)
                        diagnostic = diagnostics[current["process_instance_id"]]
                        row = {
                            "pid": event.pid,
                            "process_instance_id": current["process_instance_id"],
                            "creation_filetime": current["creation_filetime"],
                            "thread_id": event.tid,
                            "code": hex(info.record.code),
                            "first_chance": bool(info.first_chance),
                            "flags": int(info.record.flags),
                            "parameters": [
                                int(info.record.information[i])
                                for i in range(min(info.record.count, 15))
                            ],
                            "at": now(),
                            "fault": diagnostic.address(int(info.record.address or 0)),
                            "event_index": event_row["index"],
                            "continuation": "0x80010001",
                            "call_stack": "not_captured_for_first_chance_notification",
                        }
                        if not info.first_chance or info.record.code in (
                            0xC0000409,
                            0xC0000602,
                        ):
                            try:
                                row["local_only_scene"] = diagnostic.context_and_dump(
                                    event
                                )
                            except (OSError, ValueError, RuntimeError) as capture_error:
                                row["capture_failure"] = str(capture_error)
                        diagnostic.exceptions.append(row)
                        exceptions.append(row)
                        continuation = 0x80010001
                elif event.code == 5:
                    instance_id = instances.current(event.pid)["process_instance_id"]
                    instances.finish(
                        event.pid,
                        instance_id,
                        int(event.data.exit_code),
                        exited_at=now(),
                    )
                continued(event, event_row, continuation)
                held = None
                if event.code == 5 and not active:
                    break
            process.wait(timeout=5)
            result.update(exit=process.returncode, complete_tree=True)
            accounting_deadline = time.monotonic() + 1
            while job.active() and time.monotonic() < accounting_deadline:
                time.sleep(0.01)
            if job.active():
                raise RuntimeError(
                    "Owned processes remain after the last observed exit"
                )
    except BaseException as exc:
        result.update(complete_tree=False, failure=type(exc).__name__ + ": " + str(exc))
        if isinstance(exc, MetadataIncomplete):
            result["metadata_failure"] = {
                "provider": PROVIDER,
                "stage": exc.stage,
                "detail": exc.detail,
            }

        # No retry. Closing this owned job bounds even a controller failure.
        job.terminate()
        if held is not None:
            # Even during a controlled stop, do not claim to handle an application
            # exception. The owned Job is already being terminated.
            status = 0x80010001 if held.code == 1 else 0x00010002
            continued(held, events[-1], status)
        deadline = time.monotonic() + 5
        while process is not None and time.monotonic() < deadline:
            event = Event()
            if kernel.WaitForDebugEvent(C.byref(event), 100):
                event_row = received(event, cleanup=True)
                if event.code == 6 and event.data.load.file:
                    kernel.CloseHandle(event.data.load.file)
                elif event.code == 5 and event.pid in active:
                    instance_id = instances.current(event.pid)["process_instance_id"]
                    instances.finish(
                        event.pid,
                        instance_id,
                        int(event.data.exit_code),
                        exited_at=now(),
                    )
                status = 0x80010001 if event.code == 1 else 0x00010002
                continued(event, event_row, status)
            if process.poll() is not None and not job.active():
                break
        if process is not None and process.poll() is not None:
            result["exit"] = process.returncode
        result["controlled_cleanup_tree_complete"] = bool(
            rows
            and all(row["exit"] is not None for row in rows.values())
            and not job.active()
        )
    finally:
        result["job_active_before_close"] = job.active()
        result["job_finalization"] = job.close()
        journal.close()
        result["events"] = events
        result["event_journal_sha256"] = sha(output / "events.jsonl")
        result["module_inventory"] = {
            identity: diagnostic.modules for identity, diagnostic in diagnostics.items()
        }
        result.update(
            processes=list(rows.values()), exceptions=exceptions, finished_at=now()
        )
        for stream in ("stdout", "stderr"):
            if (output / stream).exists():
                result[stream + "_sha256"] = sha(output / stream)
        result["observer_implementation"] = implementation_sources()
        result["observer_implementation_before"] = loaded_sources
        if result["observer_implementation"] != loaded_sources:
            result["complete_tree"] = False
            result["failure"] = "Observer source changed after captured load"
        put(output / "TRACE.json", result)
    if not result.get("complete_tree"):
        raise RuntimeError(result.get("failure", "Incomplete process observation"))
    if any(p["exit"] >= 0xC0000000 for p in rows.values()):
        raise RuntimeError(
            "Spontaneous process exception; stop this public acceptance cycle"
        )
    return result


if __name__ == "__main__":
    raise SystemExit(
        "Use the parameterized public acceptance entry, not arbitrary process attachment"
    )
