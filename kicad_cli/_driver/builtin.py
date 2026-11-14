"""The built-in commands (DCLI-030 to DCLI-036): reference, context, doctor, changelog,
operation get, list and resolve, --help and --version. All are declared ``observe``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import knowledge, update as self_update
from .args import Meta
from .errors import DriverError, usage
from .output import Result, page
from .records import parse_utc, public, utc_now
from .registry import Command, Flag
from .runtime import Run
from .update import SEMVER, newer
from .writes import read_back, same, shorten_record, slim_receipt

if TYPE_CHECKING:
    from .tool import Tool



def commands(tool: Tool) -> list[Command]:
    from .standard import make

    states = tuple(tool.contract["operation"]["states"]) + ("all",)
    return [
        make(tool, "reference", "Describe the commands: the index, one command, or every command written to a file.",
             [Flag("--command", "string", "The path of one command."), Flag("--all", "boolean", "Every command; needs --output."),
              Flag("--output", "path", "With --all, the file to write.")], reference, kind="builtin",
             schema="reference.schema.json", capabilities=[], examples=["reference", "reference --command context"]),
        make(tool, "context", "Local state: versions, state directory, authorization, application, sessions, knowledge.", [],
             context, kind="builtin", schema="context.schema.json", capabilities=[], examples=["context"]),
        make(tool, "doctor", "Health checks, each with a fix.", [], doctor, kind="builtin", schema="doctor.schema.json",
             capabilities=[], examples=["doctor"]),
        make(tool, "changelog", "Release notes from the bundled CHANGELOG.", [Flag("--since", "string", "Only versions newer than this one.")],
             changelog, kind="builtin", schema="changelog.schema.json", capabilities=[], examples=["changelog --since 1.0.0"]),
        make(tool, "operation get", "One operation record.", [Flag("--id", "string", "The operation ID.", required=True),
             Flag("--output", "path", "Write the whole record, with every produced file, to this file.")],
             operation_get, kind="builtin", schema="operation.schema.json", capabilities=[], examples=["operation get --id op_7mq2xk4v"]),
        make(tool, "operation list", "Operation records, newest first.",
             [Flag("--state", "string", "Only records in this state; all for every state.", choices=states),
              Flag("--batch", "string", "Only the items of this batch operation."),
              Flag("--limit", "integer", "Items per page; default 20, at most 100.", maximum=100, default=20),
              Flag("--offset", "integer", "Start position of the page; default 0.", default=0)],
             operation_list, kind="builtin", schema="operation.schema.json", capabilities=[],
             examples=["operation list --state unknown"]),
        make(tool, "operation resolve", "Settle an outcome-unknown write by reading the target back, or record the outcome found by other means.",
             [Flag("--id", "string", "The operation ID.", required=True),
              Flag("--outcome", "string", "applied, not_applied or partial; only when the target cannot be read back.",
                   choices=tuple(tool.contract["operation"]["resolve_outcomes"])),
              Flag("--evidence", "string", "How the outcome was found; with --outcome.")],
             operation_resolve, kind="builtin", schema="operation.schema.json", capabilities=["read_back"],
             examples=["operation resolve --id op_7mq2xk4v"]),
        make(tool, "update", "Update the tool and its installed Skills from npm.",
             [Flag("--check", "boolean", "Only look for a newer version; install nothing."),
              Flag("--target-version", "string", "The version to install; default the latest."),
              Flag("--allow-downgrade", "boolean", "With --target-version, allow an older version.")],
             self_update.update, kind="builtin", schema="update.schema.json", capabilities=[],
             examples=["update --check", "update"]),
    ]


# ------------------------------------------------------------------ --help and --version


def meta(tool: Tool, what: Meta) -> Result:
    if what.what == "version":
        return Result({"tool": tool.name, "version": tool.version})
    lines = [f"{tool.name} {tool.version}: drives {tool.application}.", "",
             f"Usage: {tool.name} <command> [flags]", "",
             "Global flags: " + ", ".join(tool.contract.global_flags), ""]
    if what.path and what.path in tool.commands:
        command = tool.commands[what.path]
        lines = [f"{tool.name} {command.path}: {command.summary}", ""]
        for flag in command.flags:
            lines.append(f"  {flag.name}  {flag.summary}")
    else:
        lines.append("Commands:")
        for path, command in tool.commands.items():
            lines.append(f"  {path:<22} {command.summary}")
        lines += ["", f"Run `{tool.name} reference` for the machine-readable description."]
    return Result({"usage": "\n".join(lines)})


# ------------------------------------------------------------------ reference


def tier_of(tool: Tool, command: Command) -> str | dict[str, str]:
    if not command.writes:
        return "none"
    return tool.tiers.per_mode(command)


def describe(tool: Tool, command: Command, full: bool = False) -> dict[str, Any]:
    """One command as ``reference`` shows it (DCLI-031). ``full``: for ``--all``, with the adapter
    capabilities and evidence the project scripts check."""
    flags = []
    names = {f.name for f in command.flags}
    for flag in command.flags:
        entry: dict[str, Any] = {"name": flag.name, "summary": flag.summary}
        # A value --input, or for --id also --ids, can give is required only without them (DCLI-064).
        without = [] if flag.name in ("--session", "--doc", "--device") or flag.runtime else \
            [n for n in ("--ids", "--input") if n in names and (n == "--input" or flag.name == "--id")]
        if flag.required and without:
            entry["required_without"] = without
        elif flag.required:
            entry["required"] = True
        entry["type"] = "|".join(flag.choices) if flag.choices else flag.type
        if flag.quantity:
            unit = tool.contract.units[flag.quantity]
            entry["unit"] = unit["unit"]
            entry["summary"] = (flag.summary + " " if flag.summary else "") + f"Give a unit: {', '.join(unit['accepts'])}."
        if flag.default is not None:
            entry["default"] = flag.default
        if flag.maximum is not None:
            entry["maximum"] = flag.maximum
        if flag.secret:
            entry["secret"] = True
        if flag.coordinate_system:
            entry["coordinate_system"] = flag.coordinate_system
        if flag.schema is not None:
            entry["schema"] = flag.schema
        flags.append(entry)
    if not command.writes and not full:
        # A shared flag every read takes; the file of --all, which the project scripts read, lists
        # only what the command registers (DCLI-007).
        flags.append({"name": "--fields", "summary": "Only these fields, comma-separated; a.b is b inside a; all is every field.",
                      "type": "string"})
    out: dict[str, Any] = {"path": command.path, "summary": command.summary, "flags": flags,
                           "output_schema": command.output_schema, "examples": command.examples,
                           "effects": command.effects, "tier": tier_of(tool, command)}
    if command.writes and command.tier_when:
        out["tier_when"] = command.tier_when
    out.update(maturity=command.maturity, modes=command.modes)
    if full:
        out.update(evidence=command.evidence, capabilities=command.adapter_capabilities)
    if command.default_fields:
        out["default_fields"] = command.default_fields
    if command.related_knowledge:
        out["related_knowledge"] = command.related_knowledge
    out["formats"] = command.formats
    if command.output_coordinate_system:
        out["output_coordinate_system"] = command.output_coordinate_system
    if command.doc_kinds:
        out["doc_kinds"] = command.doc_kinds
    if command.long or command.jobs:
        out["long"] = True  # it returns a job, whoever runs it (DCLI-065)
    if command.verify_after_save:
        out["verify_after_save"] = True
    return out


def driver_block(tool: Tool) -> dict[str, Any]:
    return {"modes": tool.modes, "application": tool.application, "versions": tool.versions, "host_component": tool.host_component}


def reference(run: Run) -> Result:
    tool, call = run.tool, run.call
    head = {"tool": tool.name, "version": tool.version, "spec_version": tool.contract.version, "driver": driver_block(tool)}
    if call.values.get("--all"):
        if not call.has("--output"):
            raise usage("reference --all writes every command to a file; add --output <path>.", "--output")
        full = {**head, "commands": [describe(tool, c, full=True) for c in tool.commands.values()]}
        text = json.dumps(full, ensure_ascii=False, indent=1)
        path = call.values["--output"]
        try:
            Path(path).write_text(text, encoding="utf-8")
        except OSError as exc:
            raise DriverError("E_IO", f"Cannot write {path}: {exc.strerror}.") from None
        return Result({"path": path, "count": len(full["commands"]), "bytes": len(text.encode("utf-8"))})
    if call.has("--output"):
        raise usage("--output works only with --all.", "--output")
    if call.has("--command"):
        path = call.values["--command"]
        if path not in tool.commands:
            raise usage(f"There is no command {path!r}.", "--command", f"Run `{tool.name} reference` to list the commands.")
        return Result(describe(tool, tool.commands[path]))
    index = [{"path": c.path, "summary": c.summary} for c in tool.commands.values()]
    # When the index and the notices of the call do not fit, the paths alone stay: one short call
    # still lists everything the tool offers (DCLI-031).
    shorter = {**head, "index": [{"path": c.path} for c in tool.commands.values()],
               "summaries": f"Left out to keep the index short; `{tool.name} reference --command \"<path>\"` describes one command."}
    return Result({**head, "index": index}, shorter=shorter)


# ------------------------------------------------------------------ context and doctor


def _probe(run: Run) -> dict[str, Any]:
    result = dict(run.capability("probe", {}, 5000))
    if run.fake:
        result["path"] = f"fake host at {run.fake}"
    return result


def _plugin(run: Run) -> dict[str, Any] | None:
    if not run.tool.host_component:
        return None
    return run.capability("plugin_status", {}, 5000)


def context(run: Run) -> Result:
    tool, state = run.tool, run.state
    try:
        state.ensure()
    except DriverError:
        pass
    data: dict[str, Any] = {
        "tool": tool.name, "version": tool.version, "spec_version": tool.contract.version,
        "state_dir": {"path": str(state.path), "usable": state.usable(), "protection": state.protection()},
        "authorization": run.scope.summary(),
    }
    try:
        data["application"] = _probe(run)
    except DriverError as exc:
        data["application"] = {"found": False}
        run.notices.append({"type": "application", "severity": "warning", "message": exc.message})
    sessions: dict[str, Any] = {"count": 0, "ids": []}
    if run.has_sessions():
        try:
            items = run.sessions()["items"]
            sessions = {"count": len(items), "ids": [s["id"] for s in items]}
        except DriverError as exc:
            run.notices.append({"type": "sessions", "severity": "warning", "message": exc.message})
    data["sessions"] = sessions
    components: dict[str, Any] = {"cli": tool.version}
    if tool.host_component:
        try:
            plugin = _plugin(run) or {}
            components["plugin_on_disk"] = plugin.get("version_on_disk")
            components["plugin_loaded"] = plugin.get("loaded", [])
        except DriverError as exc:
            components["plugin_on_disk"] = None
            run.notices.append({"type": "plugin", "severity": "warning", "message": exc.message})
    data["components"] = components
    kb = knowledge.Base(run)
    team = kb.team_entries(strict=False)
    data["knowledge"] = {"builtin_count": len(kb.builtin()),
                         "team": [{"id": e["id"], "title": e["title"], "kind": e["kind"], "url": e["url"]} for e in team]}
    return Result(data, [".knowledge.team[*].url", ".knowledge.team[*].title"])


def _check(name: str, status: str, message: str, fix: str | None = None) -> dict[str, Any]:
    out = {"check": name, "status": status, "message": message}
    if status != "pass":
        out["fix"] = fix or "See the message."
    return out


def doctor(run: Run) -> Result:
    tool, state = run.tool, run.state
    checks = []
    try:
        state.ensure()
    except DriverError:
        pass
    protection = state.protection()
    if not state.usable():
        checks.append(_check("state_store", "fail", f"The state directory {state.path} cannot be used.",
                             f"Make {state.path} a directory the current user can read and write."))
    elif protection == "none":
        checks.append(_check("state_store", "fail", f"Other users may be able to read or write {state.path}.",
                             f"Remove {state.path}; the tool creates it again readable by you alone."))
    else:
        checks.append(_check("state_store", "pass", f"{state.path} is usable and protected ({protection})."))
    scope = run.scope
    status = {"granted": "pass", "missing": "warn", "expired": "warn", "invalid": "fail"}[scope.status]
    message = {"granted": f"Scope {scope.scope_id} grants {', '.join(scope.data.get('effects', []))} until {scope.data.get('expires_at')}.",
               "missing": f"There is no scope file at {scope.path}; reads work, writes are refused.",
               "expired": f"The scope {scope.scope_id} expired at {scope.data.get('expires_at')}; writes are refused.",
               "invalid": f"The scope file {scope.path} is invalid: {scope.problem}."}[scope.status]
    checks.append(_check("authorization", status, message, f"Writes need a grant: the person writes the scope file at {scope.path}, or grants targets in their own message for you to write."))
    checks.append(_skills_version(run))
    checks.append(_update_check(run))
    probe: dict[str, Any] | None = None
    try:
        probe = _probe(run)
        if run.fake:
            checks.append(_check("application", "pass", f"The application is the fake host at {run.fake}; results are simulated."))
        elif probe.get("found"):
            checks.append(_check("application", "pass", f"{tool.application} {probe.get('version', '')} at {probe.get('path', '?')}."))
        else:
            checks.append(_check("application", "fail", f"{tool.application} was not found.", f"Install {tool.application} {tool.versions}."))
    except DriverError as exc:
        checks.append(_check("application", "fail", exc.message, f"Install {tool.application} {tool.versions}."))
    license_state = (probe or {}).get("license", "unknown")
    checks.append(_check("license", {"valid": "pass", "not_required": "pass", "unknown": "warn"}.get(license_state, "fail"),
                         f"The license is {license_state}.", f"Activate the {tool.application} license."))
    checks.append(_plugin_check(run))
    checks.append(_sessions_check(run))
    checks.append(knowledge.doctor_check(run))
    return Result({"checks": checks})


def _skills_version(run: Run) -> dict[str, Any]:
    """The installed copies of the tool's Skills against the CLI version (DSKL-031)."""
    shipped = run.tool.root / "skills"
    names = sorted(p.parent.name for p in shipped.glob("*/SKILL.md")) if shipped.is_dir() else []
    versions = {}
    for folder in self_update.skill_dirs(run):
        for name in names:
            skill = folder / name / "SKILL.md"
            try:
                text = skill.read_text(encoding="utf-8")
            except OSError:
                continue
            match = re.search(r"^\s+version:\s*['\"]?([^'\"\s]+)", text, re.MULTILINE)
            versions[str(skill.parent)] = match.group(1) if match else None
    if not versions:
        return _check("version", "warn", f"No Skill of {run.tool.name} is installed in an Agent's Skill folder.",
                      "Copy the Skills from the installed npm package, as the Install section of the README shows.")
    wrong = {k: v for k, v in versions.items() if v != run.tool.version}
    if wrong:
        links = [k for k in wrong if self_update._is_link(Path(k))]
        fix = f"Run `{run.tool.name} update` to sync the Skills."
        if links:
            fix += (f" {', '.join(links)} {'is a link' if len(links) == 1 else 'are links'}, which update leaves alone: "
                    "update the folder a link points to, or copy the Skill from the installed npm package.")
        return _check("version", "fail", f"Installed Skills at another version than the CLI {run.tool.version}: {wrong}.", fix)
    return _check("version", "pass", f"The CLI and {len(versions)} installed Skill copies are at {run.tool.version}.")


def _plugin_check(run: Run) -> dict[str, Any]:
    if not run.tool.host_component:
        return _check("plugin", "pass", "This tool has no host component.")
    try:
        plugin = _plugin(run) or {}
    except DriverError as exc:
        return _check("plugin", "fail", exc.message, f"Run `{run.tool.name} plugin install`.")
    on_disk = plugin.get("version_on_disk")
    if not on_disk:
        return _check("plugin", "warn", "The host component is not installed.", f"Run `{run.tool.name} plugin install`.")
    # An instance running another version than the one on disk refuses writes (DSEC-053): that
    # is a failure, and it comes before the softer note that the CLI brings a newer version.
    stale = [f"{i['session']} has {i['version']}" for i in plugin.get("loaded", []) if i["version"] != on_disk]
    if stale:
        return _check("plugin", "fail", f"Version {on_disk} is on disk, but {'; '.join(stale)} loaded.",
                      "Ask the person to restart or reload those instances.")
    bundled = bundled_plugin_version(run.tool)
    if bundled and bundled != on_disk:
        return _check("plugin", "warn", f"Version {on_disk} of the host component is installed, but this CLI brings {bundled}.",
                      f"Run `{run.tool.name} plugin install`.")
    return _check("plugin", "pass", f"Version {on_disk} is on disk and loaded in every instance.")


def bundled_plugin_version(tool: Tool) -> str | None:
    """The host component version this CLI installs, from its plugin manifest."""
    if tool.plugin_manifest is None:
        return None
    try:
        version = json.loads(tool.plugin_manifest.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) else None


def _sessions_check(run: Run) -> dict[str, Any]:
    if not run.has_sessions():
        return _check("sessions", "pass", "This tool does not drive running instances.")
    try:
        items = run.sessions()["items"]
    except DriverError as exc:
        return _check("sessions", "warn", exc.message, "Start the application, then run doctor again.")
    trouble = [f"{s['id']} is {s['state']}" for s in items if s["state"] in ("blocked", "lost", "unresponsive")]
    if trouble:
        return _check("sessions", "warn", "; ".join(trouble) + ".", "Ask the person to look at those instances.")
    return _check("sessions", "pass", f"{len(items)} instances are running.")


# ------------------------------------------------------------------ update notices (DCLI-079)


def _cache_path(run: Run) -> Path:
    return run.state.path / "cache" / "update.json"


def _update_check(run: Run) -> dict[str, Any]:
    """Ask the registry through npm, waiting at most the contract's time; a failure is a warning (DCLI-079)."""
    npm = self_update.find_npm(run.env)
    if npm is None:
        return _check("update", "warn", "npm was not found on PATH, so newer versions cannot be looked up.",
                      "Install Node.js with npm.")
    try:
        info = self_update.query(run, npm, run.contract["update"]["doctor_registry_timeout_ms"] / 1000)
    except DriverError as exc:
        return _check("update", "warn", exc.message, f"Check the network and npm's registry setting, then run "
                      f"`{run.tool.name} update --check`.")
    latest = info["latest"]
    self_update.write_cache(run, info, latest)
    if latest and newer(latest, run.tool.version):
        return _check("update", "warn", f"Version {latest} is available; this is {run.tool.version}.", f"Run `{run.tool.name} update`.")
    if info["deprecated"]:
        return _check("update", "warn", f"Version {run.tool.version} is deprecated on npm.", f"Run `{run.tool.name} update`.")
    return _check("update", "pass", f"{run.tool.version} is the latest version.")


def _read_cache(run: Run) -> dict[str, Any]:
    try:
        data = json.loads(_cache_path(run).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(run: Run, data: dict[str, Any]) -> None:
    try:
        path = run.state.sub("cache") / "update.json"
        path.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, DriverError):
        pass


def update_notice(run: Run) -> None:
    """At most once every 24 hours, from the cache only (DCLI-079)."""
    if run.fake:
        run.notices.append({"type": "fake_host", "severity": "warning",
                            "message": f"{run.tool.prefix}_FAKE_HOST is set: the application is the fake host at {run.fake}, and results are simulated."})
    cache = _read_cache(run)
    latest = cache.get("latest")
    deprecated = cache.get("deprecated_version") == run.tool.version
    if not (latest and newer(latest, run.tool.version)) and not deprecated:
        return
    last = cache.get("notified_at")
    hours = run.contract["update"]["notice_interval_hours"]
    if last and parse_utc(last) > datetime.now(timezone.utc) - timedelta(hours=hours):
        return
    available = bool(latest and newer(latest, run.tool.version))
    major = available and latest.split(".")[0] != run.tool.version.split(".")[0]
    severity = "warning" if major or deprecated else "info"
    what = f"Version {latest} is available" if available else f"Version {run.tool.version} is deprecated"
    run.notices.append({"type": run.contract["update"]["notice_type"], "severity": severity,
                        "message": f"{what}; run `{run.tool.name} update`."})
    cache["notified_at"] = utc_now()
    _write_cache(run, cache)


# ------------------------------------------------------------------ changelog


def changelog(run: Run) -> Result:
    since = run.call.values.get("--since")
    if since and not SEMVER.match(since):
        raise usage(f"--since takes a version such as 1.2.0, not {since!r}.", "--since")
    path = run.tool.root / "CHANGELOG.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        raise DriverError("E_CONFIG", f"The bundled CHANGELOG is missing at {path}.") from None
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    group: str | None = None
    groups = {g.capitalize(): g for g in run.contract["changelog_groups"]}
    for line in text.splitlines():
        head = re.match(r"^## \[?([^\]\s]+)\]?(?:\s*-\s*([0-9]{4}-[0-9]{2}-[0-9]{2}))?", line)
        if head:
            name = head.group(1)
            current = None if name.lower() == "unreleased" else {"version": name, "date": head.group(2), "changes": {}}
            if current:
                entries.append(current)
            group = None
            continue
        sub = re.match(r"^### (\w+)", line)
        if sub:
            group = groups.get(sub.group(1))
            continue
        item = re.match(r"^[-*] (.+)", line)
        if item and current is not None and group:
            current["changes"].setdefault(group, []).append(item.group(1).strip())
    if since:
        entries = [e for e in entries if newer(e["version"], since)]
    return Result({"current_version": run.tool.version, "entries": entries})


# ------------------------------------------------------------------ operation records


def _record(run: Run, operation_id: str) -> dict[str, Any]:
    record = run.records.load(operation_id)
    if record is None:
        raise DriverError("E_NOT_FOUND", f"There is no operation {operation_id}.",
                          next_action=f"Run `{run.tool.name} operation list` to see the operations.")
    return record


def operation_get(run: Run) -> Result:
    record = _record(run, run.call.values["--id"])
    data = public(record)
    path = run.call.values.get("--output")
    if path:
        # The whole record, however many files the write produced, and a long job's result go
        # to a new file (DCLI-008).
        if os.path.exists(path):
            raise usage(f"{path} exists; --output writes a new file and never replaces one.", "--output")
        full = {**data, "result": record["_job"]["result"]} if "result" in (record.get("_job") or {}) else data
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(full, ensure_ascii=False, indent=1))
        except OSError as exc:
            raise DriverError("E_IO", f"Cannot write {path}: {exc.strerror}.") from None
    if isinstance(data.get("receipt"), dict):
        data["receipt"] = slim_receipt(run, data["receipt"])
    # A record too long to show is shortened rather than refused: the call that wrote a file
    # succeeds, and one without --output still shows the state and the outcome (DCLI-035).
    if shorten_record(run, data, _room(run)) and not path:
        command = run.cmd("operation get", [("--id", record["operation_id"])]) + " --output <file>"
        run.notices.append({"type": "shortened", "severity": "info",
                            "message": f"Shortened to fit the response. `{command}` writes the whole record."})
    if path:
        data["output"] = path
    return Result(data, _record_untrusted())


def _room(run: Run) -> int:
    """The characters one record or job may take, leaving room for the notices of the call."""
    return int(run.contract["envelope"]["limits"]["success_chars"]) * 3 // 4


def _record_untrusted() -> list[str]:
    return [".preview.action", ".preview.changes[*].before", ".preview.changes[*].after", ".receipt.notes",
            ".target.label", ".preview.target.label", ".resolution.evidence"]


def operation_list(run: Run) -> Result:
    call = run.call
    state = call.values.get("--state", "all")
    batch = call.values.get("--batch")
    records = [r for r in run.records.all() if (state == "all" or r.get("state") == state) and (not batch or r.get("batch") == batch)]
    data, offset = page([public(r) for r in records], call.values.get("--offset", 0), call.values.get("--limit", 20))
    # A record too long for a page on its own is shortened, so that paging goes on past it (DCLI-035).
    shortened = [item["operation_id"] for item in data["items"] if shorten_record(run, item, _room(run))]
    if shortened:
        command = f"{run.tool.name} operation get --id <id> --output <file>"
        run.notices.append({"type": "shortened", "severity": "info",
                            "message": f"Shortened to fit the page: {', '.join(shortened)}. `{command}` writes one whole."})
    return Result(data, [".items[*]" + p for p in _record_untrusted()], offset=offset)


def operation_resolve(run: Run) -> Result:
    """Settle an outcome-unknown write (DCLI-035, DCLI-063, DSEC-017)."""
    call = run.call
    record = _record(run, call.values["--id"])
    operation_id = record["operation_id"]
    if record.get("state") != "unknown":
        raise DriverError("E_VALIDATION", f"Operation {operation_id} is {record.get('state')}, not outcome unknown; there is nothing to resolve.")
    keys = _record_keys(record)
    missing = run.scope.missing(record["effects"], False, keys)
    if missing is not None:
        raise DriverError("E_FORBIDDEN", f"Resolving {operation_id} needs a scope that covers its effects and targets ({run.scope.status}).",
                          scope=missing)
    found, observed, why = _read_outcome(run, record)
    outcome = call.values.get("--outcome")
    by_hand = (f"`{run.tool.name} operation resolve --id {operation_id} --outcome applied|not_applied|partial "
               f"--evidence '<how you found it>'`")
    if outcome:
        if found is not None:
            raise DriverError("E_VALIDATION", f"The target of {operation_id} can be read back; run the command without --outcome.",
                              next_action=f"Run `{run.tool.name} operation resolve --id {operation_id}`.")
        evidence = call.values.get("--evidence")
        if not evidence:
            raise usage("--outcome needs --evidence: how the outcome was found.", "--evidence")
        resolution = {"outcome": outcome, "resolved_by": "agent", "evidence": evidence, "resolved_at": utc_now()}
    else:
        if call.has("--evidence"):
            raise usage("--evidence goes with --outcome.", "--evidence")
        if found is None:
            if why == "busy":
                raise DriverError("E_BUSY", f"The target of {operation_id} could not be read back now; nothing changed.",
                                  retry_after_ms=2000,
                                  next_action=f"Try again later; if it keeps failing, find the outcome in the application and run {by_hand}.")
            raise DriverError("E_UNSUPPORTED", f"The outcome of {operation_id} cannot be settled by reading the target back: {why}.",
                              next_action=f"Find the outcome in the application, then run {by_hand}.")
        resolution = {"outcome": found, "resolved_by": "read_back", "observed": observed, "resolved_at": utc_now()}
    record["resolution"] = resolution
    record["state"] = "failed" if resolution["outcome"] == "not_applied" else "completed"
    record["finished_at"] = record.get("finished_at") or utc_now()
    with run.mutex.hold():  # the outcome is recorded and the target unlocked in one step
        run.records.save(record)
        run.locks._remove(operation_id)
    # The outcome is recorded: a record too long to show is shortened, never refused, since a
    # refusal reads as a failure (DCLI-035).
    data = public(record)
    if isinstance(data.get("receipt"), dict):
        data["receipt"] = slim_receipt(run, data["receipt"])
    if shorten_record(run, data, _room(run)):
        command = run.cmd("operation get", [("--id", operation_id)]) + " --output <file>"
        run.notices.append({"type": "shortened", "severity": "info",
                            "message": f"Settled; shortened to fit the response. `{command}` writes the whole record."})
    return Result(data, _record_untrusted())


def _read_outcome(run: Run, record: dict[str, Any]) -> tuple[str | None, dict[str, Any], str]:
    """Read the target back and compare each change with its values before and after
    (DCLI-035). Returns the outcome, or ``None`` with the reason it cannot be told."""
    from .standard import settle

    settled = settle(run, record)
    if settled is not None:
        return settled  # a standard write, settled from the instances, documents or files it changes
    command = run.tool.commands.get(record["command"])
    secret = {run.key(f) for f in command.flags if f.secret} if command else set()
    stored = record.get("_changes", (record.get("preview") or {}).get("changes", []))
    changes = [c for c in stored if c.get("origin", "tool") == "tool" and "field" in c and c["field"] not in secret]
    request = record.get("_request")
    if not request or not changes or not run.backend.has("read_back", request.get("session")):
        return None, {}, "the operation has no change the adapter can read back"
    request = {**request, "operation_id": record["operation_id"], "targets": sorted({c["target"] for c in changes}),
               "fields": sorted({c["field"] for c in changes})}
    after = read_back(run, request, changes, record.get("_tolerances", {}))
    if after.human is not None:
        # A dialog is still open: a person deals with it, then this command runs again (DCLI-066).
        raise run.host_error(after.human, "not_started")
    if after.call_failed:
        return None, {}, "busy"
    tolerances = record.get("_tolerances", {})
    states = []
    for change in changes:
        values = after.observed.get(change["target"])
        if not isinstance(values, dict) or change["field"] not in values:
            # A field that cannot be read tells nothing; it never counts as "not applied".
            return None, after.observed, f"{change['field']} of {change['target']} cannot be read back"
        value = values.get(change["field"])
        if same(change.get("after"), value, tolerances.get(change["field"])):
            states.append("after")
        elif same(change.get("before"), value, tolerances.get(change["field"])):
            states.append("before")
        else:
            return None, after.observed, f"{change['field']} of {change['target']} matches neither the value before nor the preview"
    if all(s == "after" for s in states):
        return "applied", after.observed, ""
    if all(s == "before" for s in states):
        return "not_applied", after.observed, ""
    return "partial", after.observed, ""


def _record_keys(record: dict[str, Any]) -> list[str]:
    target = record.get("target")
    targets = target if isinstance(target, list) else ([target] if target else [])
    keys = [t["key"] for t in targets]
    for change in (record.get("preview") or {}).get("changes", []):
        if change["target"] not in keys:
            keys.append(change["target"])
    return keys


def lark_cli_on_path() -> bool:
    return shutil.which("lark-cli") is not None
