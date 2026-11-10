"""The tool: its definition, its command table and one call from argv to exit code.

A tool's entry point builds one ``Tool`` and calls ``main()``::

    TOOL = Tool(root=ROOT, adapter=KicadAdapter, commands=COMMANDS, application="KiCad",
                versions=">=9.0 <11", modes=["file", "session"], doc_kinds=["schematic", "board"])

    def main() -> None:
        TOOL.main()

``root`` holds ``package.json`` (the npm package name, the version and the command name),
``CHANGELOG.md``, ``knowledge/`` and ``contract/driver-contract.json``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping, TextIO

from . import builtin, knowledge, standard
from .args import Call, Meta, Parser, command_line
from .contract import Contract
from .errors import DriverError, usage
from .output import Envelopes, Result, dumps, render_text
from .registry import Command, RegistrationError, Tiers, domain_command
from .runtime import Run
from .writes import DomainWrite, Engine


class Tool:
    def __init__(
        self,
        *,
        root: str | os.PathLike[str],
        adapter: type,
        commands: list[dict[str, Any]],
        application: str,
        versions: str,
        modes: list[str],
        host_component: bool = False,
        contract: str | os.PathLike[str] | None = None,
        maturity: dict[str, dict[str, Any]] | None = None,
        plugin_manifest: str | os.PathLike[str] | None = None,
        long_commands: list[str] | None = None,
        doc_kinds: list[str] | None = None,
    ) -> None:
        self.root = Path(root)
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))
        bins = package.get("bin")
        if not isinstance(bins, dict) or len(bins) != 1:
            raise RegistrationError("package.json must name exactly one command in bin")
        self.name = next(iter(bins))
        self.package = package["name"]
        self.version = package["version"]
        self.prefix = self.name.upper().replace("-", "_")
        self.contract = Contract(contract or self.root / "contract" / "driver-contract.json")
        modes_known = [m["mode"] for m in self.contract["modes"]]
        if not modes or any(m not in modes_known for m in modes):
            raise RegistrationError(f"modes must be among {modes_known}")
        self.adapter_class = adapter
        self.application = application
        self.versions = versions
        self.modes = list(modes)
        self.host_component = host_component
        self.plugin_manifest = Path(plugin_manifest) if plugin_manifest else None
        # How a long job's worker starts this tool again; None finds it from the running program (DCLI-065).
        self.worker_command: list[str] | None = None
        self.tiers = Tiers(self.contract)
        self.commands: dict[str, Command] = {}
        for command in builtin.commands(self) + standard.commands(self) + knowledge.commands(self):
            self._add(command)
        self.domain: list[Command] = []
        for reg in commands:
            command = domain_command(reg, self.contract, self.modes)
            standard.add_target_flags(self, command)
            command.handler = _domain_handler(command)
            self._add(command)
            self.domain.append(command)
        for path in long_commands or []:
            # A standard write that takes the application over a minute, such as opening a large
            # project, runs as a long job in the tool's own worker (DCLI-065).
            if path not in self.contract["jobs"]["standard_long"] or path not in self.commands:
                raise RegistrationError(f"long_commands names {path!r}; it takes the offered commands of jobs.standard_long")
            self.commands[path].long = True
        # The kinds of document the application has, when it has several: --kind takes only these,
        # so a wrong kind is refused before anything starts (DCLI-210).
        self.doc_kinds = list(doc_kinds or [])
        if len(set(self.doc_kinds)) != len(self.doc_kinds) or not all(
                isinstance(k, str) and re.fullmatch(r"[a-z][a-z0-9-]*", k) for k in self.doc_kinds):
            raise RegistrationError("doc_kinds is a list of distinct lowercase document kinds (DCLI-210)")
        if self.doc_kinds and not ({"file", "session"} & set(self.modes)):
            raise RegistrationError("doc_kinds needs a mode with documents")
        for command in self.domain:
            other = [k for k in command.doc_kinds if self.doc_kinds and k not in self.doc_kinds]
            if other:
                raise RegistrationError(f"{command.path}: doc_kinds lists {other[0]!r}, which is not among the tool's doc_kinds {self.doc_kinds}")
        for path in ("session open", "doc create") if self.doc_kinds else ():
            for flag in self.commands[path].flags if path in self.commands else []:
                if flag.name == "--kind":
                    flag.choices = tuple(self.doc_kinds)
        for path, level in (maturity or {}).items():
            if path not in self.commands or self.commands[path].kind != "standard":
                raise RegistrationError(f"maturity is declared only for standard commands, not {path!r}")
            self.commands[path].maturity = level["maturity"]
            self.commands[path].evidence = list(level.get("evidence", []))
        groups = {" ".join(p.split(" ")[:i]) for p in self.commands for i in range(1, len(p.split(" ")))}
        clash = sorted(groups & set(self.commands))
        if clash:
            raise RegistrationError(f"{clash[0]!r} is both a group and a command (DCLI-021)")
        self.parser = Parser(self.contract, self.commands, self.name)
        self.envelopes = Envelopes(self.contract)

    def _hang(self, run: Run, cap: str, deadline_ms: int, out: TextIO, fmt: str, pretty: bool, started: float) -> None:
        """The watchdog of an in-process adapter call: report what is known, then end the
        process, because the call on the main thread cannot be interrupted (DCLI-278)."""
        code = 1
        try:
            code = self._report_hang(run, cap, deadline_ms, out, fmt, pretty)
        finally:
            os._exit(code)

    def _report_hang(self, run: Run, cap: str, deadline_ms: int, out: TextIO, fmt: str, pretty: bool) -> int:
        env_out = None
        settle, run.settle_hang = run.settle_hang, None
        if settle is not None:
            # apply replied; only a read after it hangs, so the outcome is known.
            note = f"The application did not answer {cap} within {deadline_ms} ms, so the read-back could not finish."
            try:
                settled = settle(note)
                hint = too_long_hint(self.name, run.call.command if run.call else None, settled.data if settled else None)
                env_out = self.envelopes.success(settled, run.notices, hint) if settled is not None else (
                    self.envelopes.failure(DriverError("E_VERIFY_INCOMPLETE", note), run.notices))
            except DriverError as exc:
                env_out = self.envelopes.failure(exc, run.notices)
            except Exception:  # noqa: BLE001 - the record could not be settled; report the hang
                env_out = None
        if env_out is None:
            env_out = self.envelopes.failure(_hang_error(run, cap, deadline_ms), run.notices)
        text = (render_text(env_out) if fmt == "text" else dumps(env_out, pretty)) + "\n"
        if hasattr(out, "buffer"):
            out.buffer.write(text.encode("utf-8"))
            out.buffer.flush()
        else:
            out.write(text)
            out.flush()
        return self.envelopes.exit_code(env_out)

    def _add(self, command: Command) -> None:
        if command.path in self.commands:
            raise RegistrationError(f"command {command.path!r} is registered twice")
        self.commands[command.path] = command

    def registration(self) -> dict[str, Any]:
        """``contract/commands.json``: the domain commands as registered (DCLI-029, DREPO-031)."""
        return {"tool": self.name, "version": self.version, "commands": [c.registration() for c in self.domain]}

    def risk(self) -> str:
        """The tool's risk tier (DSEC-002)."""
        risks = [self.tiers.risk(c) for c in self.commands.values() if c.counts_for_risk]
        return max(risks, key=self.contract.risk_rank)

    # ------------------------------------------------------------------ one call

    def main(self, argv: list[str] | None = None) -> None:
        sys.exit(self.run(sys.argv[1:] if argv is None else argv))

    def run(self, argv: list[str], env: Mapping[str, str] | None = None, stdout: TextIO | None = None) -> int:
        started = time.monotonic()
        env = os.environ if env is None else env
        out = stdout or sys.stdout
        fmt, pretty = _early_format(argv)
        call: Call | None = None
        run: Run | None = None
        result: Result | None = None
        error: DriverError | None = None
        try:
            parsed = self.parser.parse(argv)
            if isinstance(parsed, Meta):
                fmt, pretty = parsed.format, parsed.pretty
                result = builtin.meta(self, parsed)
            else:
                call = parsed
                fmt, pretty = call.format, call.pretty
                run = Run(self, call, env)
                run.on_hang = lambda cap, ms, r=run: self._hang(r, cap, ms, out, fmt, pretty, started)
                _required(call)
                result = call.command.handler(run)
                if not call.command.writes:
                    result = _fields(result, call)
        except DriverError as exc:
            error = _after_dispatch(run, exc) if run and run.dispatching else exc
        except KeyboardInterrupt:
            error = DriverError("E_INTERRUPTED", "The call was interrupted before anything was sent.")
            if run and run.dispatching:
                error = _after_dispatch(run, error)
        except Exception as exc:  # noqa: BLE001 - every failure still prints one envelope
            error = DriverError("E_UNKNOWN", f"Internal error: {type(exc).__name__}: {exc}")
            if run and run.dispatching:
                error = _after_dispatch(run, error)
        finally:
            notices = run.notices if run else []
            if run is not None:
                if call is not None and call.command.path not in ("update", "doctor"):
                    builtin.update_notice(run)
                run.close()
        if error is None:
            assert result is not None
            if fmt == "raw" and result.raw is not None:
                out.buffer.write(result.raw) if hasattr(out, "buffer") else out.write(result.raw.decode("utf-8", "replace"))
                return 0
            try:
                env_out = self.envelopes.success(result, notices, too_long_hint(self.name, call.command if call else None, result.data))
            except DriverError as exc:
                env_out = self.envelopes.failure(exc, notices)
        else:
            env_out = self.envelopes.failure(error, notices)
        text = (render_text(env_out) if fmt == "text" else dumps(env_out, pretty)) + "\n"
        if hasattr(out, "buffer"):
            out.flush()
            out.buffer.write(text.encode("utf-8"))  # UTF-8 whatever the console code page (DCLI-001)
            out.buffer.flush()
        else:
            out.write(text)
            out.flush()
        return self.envelopes.exit_code(env_out)


def _hang_error(run: Run, cap: str, deadline_ms: int) -> DriverError:
    cause = DriverError("E_TIMEOUT", f"the adapter did not finish {cap} within {deadline_ms} ms")
    if run.dispatching:
        return _after_dispatch(run, cause)
    return DriverError("E_BUSY", f"The application did not answer {cap} within {deadline_ms} ms; nothing was changed.",
                       retry_after_ms=2000)


def _after_dispatch(run: Run, cause: DriverError) -> DriverError:
    """A failure while a change may be in flight is an unknown outcome: the record says so
    and the target stays locked until ``operation resolve`` (DCLI-063)."""
    operation_id = run.dispatching
    assert operation_id is not None
    batch = False
    try:
        record = run.records.load(operation_id)
        batch = bool(record and record.get("_batch"))
        with run.mutex.hold():
            run.locks.abandon(operation_id, (record or {}).get("_keys", []))
    except Exception:  # noqa: BLE001 - the error below still tells the Agent to look the outcome up
        pass
    if batch:
        lookup = run.cmd("operation list", [("--state", "unknown"), ("--batch", operation_id)])
    else:
        lookup = run.cmd("operation resolve", [("--id", operation_id)])
    return DriverError("E_OUTCOME_UNKNOWN", f"The call stopped after the change was sent ({cause.message}); whether it took effect is not known.",
                       dispatch_state="unknown", lookup=lookup,
                       next_action=f"Run `{lookup}` before anything else; never send the write again.")


def _required(call: Any) -> None:
    """A required flag left out is a usage error (DCLI-013). A domain command checks its own,
    because --ids or an --input item may carry the value instead."""
    command = call.command
    for flag in command.flags:
        own = command.kind == "domain" and flag.name not in ("--session", "--doc", "--device")
        if flag.required and flag.name not in call.given and not own:
            raise usage(f"{command.path} needs {flag.name}.", flag.name)


def _domain_handler(command: Command) -> Any:
    if command.writes:
        return lambda run: Engine(run).execute(DomainWrite(run, command))
    return lambda run: standard.domain_read(run, command)


def too_long_hint(tool: str, command: Command | None, data: Any) -> str:
    """What a result over the size limit can ask for instead: only what this command offers (DCLI-008)."""
    operation = data.get("operation_id") if isinstance(data, dict) else None
    record = command_line(tool, "operation get", [("--id", operation), ("--output", "<file>")]) if isinstance(operation, str) else None
    if command is not None and command.writes:
        if record and "preview" not in data:
            # The write ran: it must not be sent again (DCLI-063).
            if isinstance(data.get("items"), list):
                items = command_line(tool, "operation list", [("--batch", operation)])
                return f"The batch ran as operation {operation}; do not send it again. `{items}` lists the operation of each item."
            return (f"The write ran as operation {operation}; do not send it again. Its whole record, with the receipt, "
                    f"goes to a file with `{record}`.")
        return "Name fewer objects in one call; nothing was changed."  # a dry run, its changes already shortened
    names = {f.name for f in command.flags} if command is not None else set()
    ways = ["fewer fields with --fields"]
    if "--limit" in names:
        ways.append("a smaller page with --limit")
    if "--output" in names:
        ways.append("the full result in a file with --output")
    elif record:
        ways.append(f"the whole operation record in a file with `{record}`")
    listed = ways[0] if len(ways) == 1 else ", ".join(ways[:-1]) + (", or " if len(ways) > 2 else " or ") + ways[-1]
    return f"Ask for {listed}."


def _early_format(argv: list[str]) -> tuple[str, bool]:
    """The format to use when the command line cannot be parsed."""
    fmt = "json"
    for i, token in enumerate(argv):
        if token == "--format" and i + 1 < len(argv) and argv[i + 1] in ("json", "text"):
            fmt = argv[i + 1]
        elif token in ("--format=text", "--format=json"):
            fmt = token.split("=", 1)[1]
    return fmt, "--pretty" in argv


def _fields(result: Result, call: Call) -> Result:
    """``--fields`` and the default fields of a list (DCLI-007)."""
    wanted = call.fields
    if wanted is None and call.command.default_fields:
        wanted = call.command.default_fields
    if wanted is None or wanted == "all" or not isinstance(result.data, dict):
        return result
    data = result.data
    if isinstance(data.get("items"), list):
        data = {**data, "items": [{k: v for k, v in i.items() if k in wanted} if isinstance(i, dict) else i for i in data["items"]]}
    elif call.fields is not None:
        data = {k: v for k, v in data.items() if k in wanted}
    return Result(data, result.untrusted, result.offset, result.raw)
