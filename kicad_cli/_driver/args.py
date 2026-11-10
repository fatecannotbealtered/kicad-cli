"""Command-line parsing (DCLI-020 to DCLI-027, DCLI-082).

``<tool> [global flags] <command path> [flags]``. Global flags may appear anywhere. A flag
takes its value as ``--name value`` or ``--name=value``; the token after a valued flag is
its value even when it starts with ``-``, so ``--x -5mm`` works. Every mistake is
``E_USAGE`` with ``problem`` and the suggested correct form (DCLI-012).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any

from .contract import Contract
from .errors import DriverError, usage
from .registry import Command, Flag
from .units import convert, duration_seconds

BOOLEAN = {"boolean"}


@dataclass
class Call:
    command: Command
    values: dict[str, Any] = field(default_factory=dict)  # flag name -> converted value
    given: list[str] = field(default_factory=list)  # flag names in the order given
    raw: dict[str, str] = field(default_factory=dict)  # flag name -> text as given
    format: str = "json"
    pretty: bool = False
    fields: list[str] | str | None = None  # None: the default fields; "all": every field
    quiet: bool = False
    dry_run: bool = False
    confirm: str | None = None
    dangerous: bool = False

    def has(self, name: str) -> bool:
        return name in self.values


@dataclass
class Meta:
    """``--help`` or ``--version``, which run without a command."""

    what: str
    format: str = "json"
    pretty: bool = False
    path: str | None = None


def quote(arg: str) -> str:
    """Quote an argument that holds anything but plain characters or starts with ``-``
    (DCLI-011). Single quotes keep ``$(...)`` and backticks literal in bash and PowerShell."""
    if arg and re.fullmatch(r"[A-Za-z0-9_./:@%+=,#~^-]+", arg) and not arg.startswith("-"):
        return arg
    # A quote inside is closed, given in double quotes, and reopened: '"'"' keeps every part
    # literal in bash and in PowerShell, so a generated command never runs embedded code.
    return "'" + arg.replace("'", "'\"'\"'") + "'"


def command_line(tool: str, path: str, args: list[tuple[str, Any]]) -> str:
    """A complete command for the Agent to run later (DCLI-011)."""
    parts = [tool, path]
    for name, value in args:
        if value is True:
            parts.append(name)
        elif value is not None and value is not False:
            parts += [name, quote(str(value))]
    return " ".join(parts)


class Parser:
    def __init__(self, contract: Contract, commands: dict[str, Command], tool: str) -> None:
        self.contract = contract
        self.commands = commands
        self.tool = tool
        self.groups = {" ".join(p.split(" ")[:i]) for p in commands for i in range(1, len(p.split(" ")))}

    # ------------------------------------------------------------------ tokens

    def parse(self, argv: list[str]) -> Call | Meta:
        g: dict[str, Any] = {}
        words: list[str] = []
        flags: list[tuple[str, str | None, int]] = []
        i = 0
        while i < len(argv):
            token = argv[i]
            if token in ("-h", "--help"):
                g["help"] = True
            elif token == "--version":
                g["version"] = True
            elif token.startswith("--"):
                name, eq, inline = token.partition("=")
                flags.append((name, inline if eq else None, i))
                if not eq and self._takes_value(name, words) and i + 1 < len(argv):
                    i += 1
                    flags[-1] = (name, argv[i], i)
            elif token.startswith("-") and len(token) > 1 and not flags:
                raise usage(f"Unknown option {token}; flags are written --name.", token,
                            f"Run `{self.tool} --help` to see the usage.")
            elif flags:
                raise usage(f"Unexpected argument {token!r}; every value belongs to a flag.", None,
                            self._hint(" ".join(words)))
            else:
                words.append(token)
            i += 1
        for name, value, _ in flags:
            if name in self.contract.global_flags:
                self._global(g, name, value)
        if g.get("dry_run") and g.get("confirm"):
            raise usage("--dry-run changes nothing and --confirm runs the write; give one of them.", "--dry-run")
        fmt = g.get("format", "json")
        if g.get("version"):
            return Meta("version", fmt, g.get("pretty", False))
        path = " ".join(words)
        if g.get("help") or not words:
            if not words and not g.get("help"):
                raise usage("No command given.", None, f"Run `{self.tool} reference` to list the commands.")
            return Meta("help", fmt, g.get("pretty", False), path or None)
        command = self._command(words)
        call = Call(command=command, format=fmt, pretty=g.get("pretty", False), fields=g.get("fields"),
                    quiet=g.get("quiet", False), dry_run=g.get("dry_run", False), confirm=g.get("confirm"),
                    dangerous=g.get("dangerous", False))
        for name, value, _ in flags:
            if name in self.contract.global_flags:
                continue
            flag = command.flag(name)
            if flag is None:
                raise self._unknown_flag(command, name)
            if name in call.values:
                raise usage(f"{name} is given twice.", name, self._hint(command.path))
            if value is None and flag.type not in BOOLEAN:
                raise usage(f"{name} needs a value.", name, self._hint(command.path))
            call.values[name] = self.convert(flag, value)
            call.raw[name] = value if value is not None else ""
            call.given.append(name)
        if call.format not in command.formats:
            raise usage(f"{command.path} does not support --format {call.format}; it supports {', '.join(command.formats)}.",
                        "--format", self._hint(command.path))
        return call

    def _takes_value(self, name: str, words: list[str]) -> bool:
        if name in self.contract.global_flags:
            return self.contract.global_flags[name] is not None
        command = self.commands.get(" ".join(words))
        flag = command.flag(name) if command else None
        if flag is not None:
            return flag.type not in BOOLEAN
        return True

    def _global(self, g: dict[str, Any], name: str, value: str | None) -> None:
        key = name[2:].replace("-", "_")
        if key in g:
            raise usage(f"{name} is given twice.", name)
        if name == "--json":
            if value is not None:
                raise usage("--json takes no value.", name)
            g["format"] = "json"
            return
        spec = self.contract.global_flags[name]
        if spec is None:
            if value is not None and value not in ("true", "false"):
                raise usage(f"{name} takes no value.", name)
            g[key] = value != "false"
            return
        if value is None:
            raise usage(f"{name} needs a value: {spec}.", name)
        if name == "--format":
            if value not in self.contract["envelope"]["formats"]:
                raise usage(f"--format takes json, text or raw, not {value!r}.", name)
            g["format"] = value
        elif name == "--fields":
            names = [f.strip() for f in value.split(",") if f.strip()]
            if not names:
                raise usage("--fields needs field names separated by commas, or all.", name)
            g["fields"] = "all" if names == ["all"] else names
        else:
            g[key] = value

    # ------------------------------------------------------------------ commands

    def _command(self, words: list[str]) -> Command:
        for n in range(len(words), 0, -1):
            path = " ".join(words[:n])
            if path in self.commands:
                if n < len(words):
                    raise usage(f"Unexpected argument {words[n]!r} after {path}.", None, self._hint(path))
                return self.commands[path]
            if path in self.groups and n == len(words):
                children = sorted(p for p in self.commands if p.startswith(path + " "))
                raise usage(f"{path} is a group, not a command; its commands are {', '.join(children)}.", None,
                            f"Run one of them, such as `{self.tool} {children[0]}`.")
        path = " ".join(words)
        close = difflib.get_close_matches(path, list(self.commands), n=1, cutoff=0.6)
        hint = f"Did you mean `{self.tool} {close[0]}`?" if close else f"Run `{self.tool} reference` to list the commands."
        raise usage(f"Unknown command {path!r}.", None, hint)

    def _unknown_flag(self, command: Command, name: str) -> DriverError:
        flags = f"Run `{self.tool} reference --command {quote(command.path)}` for its flags."
        mode = {"--session": "session", "--device": "device"}.get(name)
        if mode and mode not in command.modes:
            # The flag of a mode the command does not work in: a near name would mislead (DCLI-201).
            doc = "file" in command.modes and any(f.name == "--doc" for f in command.flags)
            return usage(f"{command.path} has no flag {name}: it works only in {' and '.join(command.modes)} mode.", name,
                         ("It works on the file itself: name it with --doc <path>. " if doc else "") + flags)
        known = [f.name for f in command.flags] + list(self.contract.global_flags)
        close = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
        return usage(f"{command.path} has no flag {name}.", name, f"Did you mean {close[0]}? {flags}" if close else flags)

    def _hint(self, path: str) -> str:
        if path in self.commands:
            return f"Run `{self.tool} reference --command {quote(path)}` for its flags."
        return f"Run `{self.tool} reference` to list the commands."

    # ------------------------------------------------------------------ values

    def convert(self, flag: Flag, value: str | None) -> Any:
        if not flag.secret:
            return self._convert(flag, value)
        try:
            return self._convert(flag, value)
        except DriverError:
            raise usage(f"{flag.name} has a value of the wrong form.", flag.name) from None  # never echo a secret

    def _convert(self, flag: Flag, value: str | None) -> Any:
        name = flag.name
        if flag.type == "boolean":
            if name == "--continue-on-error":
                if value not in ("true", "false"):
                    raise usage("--continue-on-error takes true or false.", name)
                return value == "true"
            if value not in (None, "true", "false"):
                raise usage(f"{name} takes no value.", name)
            return value != "false"
        assert value is not None
        if flag.choices is not None:
            if value not in flag.choices:
                raise usage(f"{name} takes one of {', '.join(flag.choices)}, not {value!r}.", name)
            return value
        if flag.type == "string":
            return value
        if flag.type == "integer":
            if not re.fullmatch(r"-?[0-9]+", value):
                raise usage(f"{name} takes a whole number, not {value!r}.", name)
            number = int(value)
            if name in ("--limit", "--offset") and number < 0:
                raise usage(f"{name} cannot be negative.", name)
            if flag.maximum is not None and number > flag.maximum:
                raise usage(f"{name} is at most {flag.maximum}; it is never lowered silently.", name)
            if name == "--limit" and number < 1:
                raise usage("--limit is at least 1.", name)
            return number
        if flag.type == "number":
            try:
                number = float(value)
            except ValueError:
                raise usage(f"{name} takes a number, not {value!r}.", name) from None
            if number != number or number in (float("inf"), float("-inf")):
                raise usage(f"{name} takes a finite number.", name)
            return number
        if flag.type == "quantity":
            assert flag.quantity is not None
            return convert(value, flag.quantity, self.contract.units[flag.quantity]["accepts"], name)
        if flag.type == "duration":
            return duration_seconds(value, name, self.contract["duration_suffixes"])
        if flag.type == "path":
            if not value:
                raise usage(f"{name} needs a path.", name)
            return os.path.abspath(value)
        if flag.type == "list":
            items: list[str] = []
            for item in (v.strip() for v in value.split(",")):
                if item and item not in items:
                    items.append(item)
            if not items:
                raise usage(f"{name} needs at least one ID; an empty list does nothing.", name)
            return items
        if flag.type == "json":
            # Inline JSON, or the path of a file that holds it (DCLI-026).
            text = value
            if not value.lstrip().startswith(("{", "[")):
                try:
                    with open(value, encoding="utf-8") as handle:
                        text = handle.read()
                except OSError as exc:
                    raise usage(f"{name} cannot read {value}: {exc.strerror}.", name) from None
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise usage(f"{name} is not valid JSON: {exc}.", name) from None
            from .runtime import json_value

            return json_value(flag, data, name)
        if flag.type == "input":
            return self._input(value, name)
        raise usage(f"{name} has the unknown type {flag.type}.", name)

    @staticmethod
    def _input(source: str, name: str) -> dict[str, Any]:
        try:
            if source == "-":
                text = sys.stdin.read()
            else:
                with open(source, encoding="utf-8") as handle:
                    text = handle.read()
        except OSError as exc:
            raise usage(f"{name} cannot read {source}: {exc.strerror}.", name) from None
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise usage(f"{name} is not valid JSON: {exc}.", name) from None
        if not isinstance(data, dict):
            raise usage(f"{name} must hold one JSON object.", name)
        return data
