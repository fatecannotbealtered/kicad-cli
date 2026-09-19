"""Build a schematic from a declarative specification.

The other end of this tool. Every other command starts from a design that
already exists; this one starts from a description of one, which is where an
agent's work actually begins -- a requirement, not a `.kicad_pcb`.

The input is JSON, not Python. An agent that has to emit code to use a tool is
an agent running code it wrote, and the review surface for that is the whole
language. A parts-and-nets document is reviewable in the dry run, refusable on
a typo, and cannot do anything but describe a circuit.

SKiDL turns the description into a `.kicad_sch` and a netlist. That is an
implementation detail and it is deliberately not the contract: what this
module promises is the spec shape and the validation, so the generator can be
replaced -- and it will need to be, because SKiDL's automatic placement is
poor enough that the output is for machines to consume rather than for people
to read. See `docs/SCHEMATIC.md`.

What this module adds over calling SKiDL directly, and the reason the command
exists at all: every symbol is resolved against the real libraries and every
pin reference against the real symbol *before* anything is written, so a
misspelled pin fails as `E_VALIDATION` naming the pin and listing the ones
that exist, rather than as a traceback or, worse, a schematic that is missing
a connection nobody notices.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import tempfile
import traceback
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from . import envelope, kicad_env, netlist

PIN_REF = re.compile(r"\A(?P<ref>[A-Za-z_][A-Za-z0-9_]*)\.(?P<pin>.+)\Z")


def _libraries() -> tuple[str, str]:
    """Where KiCad keeps its symbols and footprints on this machine.

    SKiDL reads the library files; it does not import anything from KiCad. So
    the interpreter running this does not need to be KiCad's, and this command
    -- unlike every write command that touches a board -- does not need pcbnew.
    """
    root = kicad_env.find_kicad_root()
    if root is None:
        envelope.fail(
            "E_CONFIG",
            "could not find the KiCad installation, so its symbol libraries cannot be read",
            {
                "hint": f"set {kicad_env.ENV_ROOT} to the KiCad installation directory; "
                "run: kicad-cli doctor"
            },
        )
    symbols = Path(root) / "share" / "kicad" / "symbols"
    footprints = Path(root) / "share" / "kicad" / "footprints"
    if not symbols.is_dir():
        envelope.fail(
            "E_CONFIG",
            "the KiCad installation has no symbol library directory",
            {"expected": str(symbols)},
        )
    return str(symbols), str(footprints)


def _release_log_files() -> None:
    """Close the generator's log handlers so the scratch directory can go.

    It opens a log and an ERC report named after the top-level script when it
    is imported, and keeps them open. Nothing closes them, so on Windows the
    directory holding them cannot be removed while the process lives.
    """
    for logger in logging.Logger.manager.loggerDict.values():
        if not isinstance(logger, logging.Logger):
            continue
        for handler in list(getattr(logger, "handlers", [])):
            if isinstance(handler, logging.FileHandler):
                try:
                    handler.close()
                    logger.removeHandler(handler)
                except (OSError, ValueError):  # noqa: PERF203 - best effort
                    continue


@contextmanager
def _scratch():
    """Confine everything the generator does to a directory that gets deleted.

    It writes a log file named after the top-level script, in the current
    working directory, *at import time* -- so a plain `sch create` left
    `main.log` wherever it was run from. It also shells out to KiCad to check
    its own output, which rewrites the caller's `kicad_common.json`; that is the
    contention `netlist` already isolates against, and it caused an intermittent
    failure in the full suite.

    Both are the same shape of problem -- a library treating the caller's
    working directory and settings as its own -- so both get the same answer,
    applied before the import rather than after it.
    """
    previous_cwd = os.getcwd()
    previous_config = os.environ.get("KICAD_CONFIG_HOME")
    # `ignore_cleanup_errors` because the generator holds its log files open:
    # on Windows a still-open handle makes the directory undeletable, and a
    # command must not fail over its own scratch space. `_release_log_files`
    # closes what it can first, so the usual case leaves nothing behind.
    with tempfile.TemporaryDirectory(prefix="kicadcli-sch-", ignore_cleanup_errors=True) as scratch:
        try:
            os.environ["KICAD_CONFIG_HOME"] = netlist.isolated_config_home()
            os.chdir(scratch)
            yield Path(scratch)
        finally:
            _release_log_files()
            os.chdir(previous_cwd)
            if previous_config is None:
                os.environ.pop("KICAD_CONFIG_HOME", None)
            else:
                os.environ["KICAD_CONFIG_HOME"] = previous_config


def _import_skidl():
    """SKiDL is an optional dependency, and says so when it is missing.

    Same shape as the IPC client in `board live`: one command needs one more
    thing, and the error says which thing rather than reporting the tool as
    broken.

    The library search paths have to be in the environment *before* the import,
    because SKiDL reads them at module scope and complains to stderr about the
    ones it cannot find. Setting them afterwards left six warnings on a stream
    that is supposed to carry progress, for an answer that was already correct.
    """
    symbols, footprints = _libraries()
    for variable, value in (
        ("KICAD10_SYMBOL_DIR", symbols),
        ("KICAD10_FOOTPRINT_DIR", footprints),
        ("KICAD_SYMBOL_DIR", symbols),
    ):
        os.environ.setdefault(variable, value)
    noise = io.StringIO()
    try:
        with redirect_stdout(noise), redirect_stderr(noise):
            import skidl  # noqa: PLC0415
    except ImportError as exc:
        envelope.fail(
            "E_CONFIG",
            "the schematic generator is not installed",
            {
                "import_error": str(exc),
                "fix": "pip install skidl",
                "note": "only `sch create` needs it. Every other command works without it.",
            },
        )
    return skidl


def load_spec(path: str) -> dict[str, Any]:
    """Read and shape-check the specification. No libraries touched yet."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        envelope.fail("E_NOT_FOUND", "cannot read the circuit specification", {"path": path})
        raise AssertionError from exc  # pragma: no cover - fail() exits
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        envelope.fail(
            "E_VALIDATION",
            "the circuit specification is not valid JSON",
            {"path": path, "reason": str(exc)[:200]},
        )
        raise AssertionError from exc  # pragma: no cover
    if not isinstance(spec, dict):
        envelope.fail("E_VALIDATION", "the specification must be a JSON object", {"path": path})
    problems: list[dict[str, Any]] = []
    parts = spec.get("parts")
    nets = spec.get("nets")
    if not isinstance(parts, list) or not parts:
        problems.append({"field": "parts", "problem": "a non-empty array is required"})
    if not isinstance(nets, list) or not nets:
        problems.append({"field": "nets", "problem": "a non-empty array is required"})
    if not problems:
        problems = _structural_problems(parts, nets)
    if problems:
        envelope.fail(
            "E_VALIDATION",
            "the specification is missing what a circuit needs",
            {"problems": problems[:40], "problem_count": len(problems), "shape": SHAPE},
        )
    return spec


def _structural_problems(parts: list, nets: list) -> list[dict[str, Any]]:
    """Everything wrong with a specification that the libraries cannot answer.

    Whether `Device:R` exists needs KiCad; whether a part was given a symbol at
    all does not. Keeping the two apart is what lets a broken specification be
    refused on a machine with no KiCad installed -- CI is one, and being told to
    install KiCad when the real problem is a missing field is a bad answer.
    """
    problems: list[dict[str, Any]] = []
    refs: set[str] = set()
    for index, item in enumerate(parts):
        where = {"index": index, "ref": item.get("ref") if isinstance(item, dict) else None}
        if not isinstance(item, dict):
            problems.append({**where, "problem": "each part must be an object"})
            continue
        if not item.get("ref"):
            problems.append({**where, "problem": "each part needs a ref"})
            continue
        if not item.get("symbol"):
            problems.append({**where, "problem": "each part needs a symbol"})
            continue
        if ":" not in str(item["symbol"]):
            problems.append(
                {**where, "problem": "symbol must be 'Library:Name'", "got": item["symbol"]}
            )
            continue
        if str(item["ref"]) in refs:
            problems.append({**where, "problem": "duplicate ref"})
            continue
        refs.add(str(item["ref"]))

    for index, item in enumerate(nets):
        where = {"index": index, "net": item.get("name") if isinstance(item, dict) else None}
        if not isinstance(item, dict) or not item.get("name"):
            problems.append({**where, "problem": "each net needs a name"})
            continue
        connect = item.get("connect")
        if not isinstance(connect, list) or len(connect) < 2:
            problems.append({**where, "problem": "a net needs at least two connections"})
            continue
        for entry in connect:
            match = PIN_REF.match(str(entry))
            if match is None:
                problems.append({**where, "problem": "connection must be 'REF.PIN'", "got": entry})
            elif refs and match.group("ref") not in refs:
                problems.append({**where, "problem": "no such part", "ref": match.group("ref")})
    return problems


SHAPE = {
    "title": "optional string",
    "parts": [
        {"ref": "U1", "symbol": "Library:Symbol", "value": "optional", "footprint": "optional"}
    ],
    "nets": [{"name": "+5V", "connect": ["U1.VI", "C1.1"]}],
}


def _resolve_parts(skidl, spec: dict[str, Any]) -> tuple[dict, list[dict]]:
    """Instantiate every part, collecting failures instead of raising on the first."""
    built: dict[str, Any] = {}
    problems: list[dict[str, Any]] = []
    for index, item in enumerate(spec["parts"]):
        where = {"index": index, "ref": item.get("ref")}
        if not isinstance(item, dict) or not item.get("ref") or not item.get("symbol"):
            problems.append({**where, "problem": "each part needs a ref and a symbol"})
            continue
        ref = str(item["ref"])
        if ref in built:
            problems.append({**where, "problem": "duplicate ref"})
            continue
        symbol = str(item["symbol"])
        if ":" not in symbol:
            problems.append({**where, "problem": "symbol must be 'Library:Name'", "got": symbol})
            continue
        lib, name = symbol.split(":", 1)
        try:
            part = skidl.Part(lib, name, ref=ref)
        except Exception as exc:  # noqa: BLE001 - SKiDL raises several types
            problems.append(
                {
                    **where,
                    "problem": "symbol not found in the KiCad libraries",
                    "symbol": symbol,
                    "reason": str(exc)[:160],
                }
            )
            continue
        if item.get("value"):
            part.value = str(item["value"])
        if item.get("footprint"):
            part.footprint = str(item["footprint"])
        built[ref] = part
    return built, problems


def _connect(parts: dict, spec: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Wire the nets, reporting every bad pin reference rather than the first."""
    problems: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    from skidl import Net  # noqa: PLC0415

    for index, item in enumerate(spec["nets"]):
        where = {"index": index, "net": item.get("name") if isinstance(item, dict) else None}
        if not isinstance(item, dict) or not item.get("name"):
            problems.append({**where, "problem": "each net needs a name"})
            continue
        connect = item.get("connect")
        if not isinstance(connect, list) or len(connect) < 2:
            problems.append({**where, "problem": "a net needs at least two connections"})
            continue
        net = Net(str(item["name"]))
        joined = 0
        for spec_pin in connect:
            match = PIN_REF.match(str(spec_pin))
            if match is None:
                problems.append(
                    {**where, "problem": "connection must be 'REF.PIN'", "got": spec_pin}
                )
                continue
            ref, pin = match.group("ref"), match.group("pin")
            part = parts.get(ref)
            if part is None:
                problems.append({**where, "problem": "no such part", "ref": ref})
                continue
            try:
                net += part[pin]
            except Exception:  # noqa: BLE001 - SKiDL raises several types
                problems.append(
                    {
                        **where,
                        "problem": "the symbol has no such pin",
                        "pin": f"{ref}.{pin}",
                        # The list is the useful half: a pin name is usually a
                        # near miss, and guessing from a bare refusal is work
                        # the tool can do instead.
                        "available": sorted(str(p.name or p.num) for p in part.pins)[:40],
                    }
                )
                continue
            joined += 1
        summary.append({"net": str(item["name"]), "connections": joined})
    return summary, problems


def plan(spec_path: str) -> dict[str, Any]:
    """Resolve everything against the real libraries. Writes nothing.

    This is the dry run's whole value: a spec that would fail at generation
    time fails here instead, with the pin or the symbol named.
    """
    spec = load_spec(spec_path)
    symbols, _ = _libraries()

    # SKiDL narrates to stdout and stderr. Ours is a single JSON document, so
    # its narration is captured and dropped rather than allowed to interleave.
    noise = io.StringIO()
    with _scratch(), redirect_stdout(noise), redirect_stderr(noise):
        skidl = _import_skidl()
        skidl.reset()
        parts, part_problems = _resolve_parts(skidl, spec)
        nets, net_problems = ([], []) if part_problems else _connect(parts, spec)

    problems = part_problems + net_problems
    if problems:
        envelope.fail(
            "E_VALIDATION",
            "the circuit specification does not resolve against the KiCad libraries",
            {"problems": problems[:40], "problem_count": len(problems)},
        )
    unconnected = sorted(
        ref for ref, part in parts.items() if not any(p.is_connected() for p in part.pins)
    )
    return {
        "title": str(spec.get("title") or "kicad-cli generated"),
        "parts": [
            {
                # `lib.filename` is the library name SKiDL resolved this from,
                # which is what the spec asked for and what a caller can look
                # up again. `lib.name` does not exist.
                "ref": ref,
                "symbol": f"{part.lib.filename}:{part.name}",
                "value": str(part.value or ""),
            }
            for ref, part in sorted(parts.items())
        ],
        "nets": nets,
        "unconnected_parts": unconnected,
        "symbol_dir": symbols,
    }


def _draw(skidl: Any, title: str) -> dict[str, Any]:
    """Draw the schematic, and treat failing to draw it as survivable.

    The netlist and the drawing are separate outputs of the same generator, and
    only one of them is load-bearing. `board from-netlist` reads the netlist;
    the `.kicad_sch` is for a person who opens it. Until now a drawing failure
    failed the whole command and discarded a netlist that had already been
    written and was perfectly good -- which on the first realistic board tried
    here, a 15-part ATmega328P, blocked the entire chain on the wire router's
    inability to lay out a 14-pin ground net.

    So: draw it; on failure draw it again with auto-stubbing, which turns
    high-fanout nets into global labels and power symbols -- how an engineer
    would have drawn a ground net anyway; and if that also fails, say so and
    let the caller have the netlist. The order matters: plain first, so no
    circuit that draws correctly today starts coming out differently.
    """
    try:
        skidl.generate_schematic(title=title)
        return {"status": "drawn", "style": "routed", "reason": None}
    except Exception as exc:  # noqa: BLE001 - the router raises several types
        first = f"{type(exc).__name__}: {str(exc)[:160] or '(no message)'}"

    try:
        skidl.generate_schematic(title=title, auto_stub=True)
        return {"status": "drawn", "style": "auto_stub", "reason": first}
    except Exception as exc:  # noqa: BLE001 - the router raises several types
        return {
            "status": "failed",
            "style": None,
            "reason": first,
            "reason_auto_stub": f"{type(exc).__name__}: {str(exc)[:160] or '(no message)'}",
        }


def generate(spec_path: str, out_dir: str) -> dict[str, Any]:
    """Write the schematic and its netlist. Call only after `plan` succeeded.

    The generator writes to filenames of its own choosing in the working
    directory rather than to a path it is handed, so it works inside a scratch
    directory and the results are copied out. Writing into the caller's
    directory and tidying up afterwards would leave debris behind on any
    failure -- and there is debris either way, because it also opens a log file
    at import time.
    """
    import shutil  # noqa: PLC0415

    summary = plan(spec_path)
    spec = load_spec(spec_path)
    destination = Path(out_dir)
    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        envelope.fail("E_IO", "cannot create the output directory", {"out": str(destination)})
        raise AssertionError from exc  # pragma: no cover

    noise = io.StringIO()
    with _scratch() as scratch:
        try:
            with redirect_stdout(noise), redirect_stderr(noise):
                skidl = _import_skidl()
                skidl.reset()
                parts, _ = _resolve_parts(skidl, spec)
                _connect(parts, spec)
                # `track_abs_path` keeps the generator from recording each
                # part's source line as a path relative to the directory it was
                # imported from. That relpath raises outright when the two are
                # on different Windows drives, which is the ordinary case here.
                skidl.generate_netlist(file_="circuit.net", track_abs_path=True)
                drawing = _draw(skidl, summary["title"])
        except Exception as exc:  # noqa: BLE001 - the generator raises several types
            # The type and the traceback tail, not just str(exc): it raises some
            # exceptions with an empty message, and "failed" with an empty
            # reason is the least actionable thing a tool can say.
            envelope.fail(
                "E_IO",
                "the schematic generator failed",
                {
                    "exception": type(exc).__name__,
                    "reason": str(exc)[:300] or "(the generator raised without a message)",
                    "traceback": traceback.format_exc()[-900:],
                    "generator_output": noise.getvalue()[-900:],
                },
            )

        produced = {"schematic": None, "netlist": None}
        for pattern, name in (("*.kicad_sch", "schematic"), ("circuit.net", "netlist")):
            found = sorted(scratch.glob(pattern))
            if not found:
                # The netlist is the deliverable: it is what `board from-netlist`
                # reads and what the rest of the chain is built on. A missing
                # drawing is reported by `drawing` and survivable; a missing
                # netlist is not.
                if name == "netlist":
                    envelope.fail(
                        "E_IO",
                        "the generator produced no netlist",
                        {"expected": pattern, "generator_output": noise.getvalue()[-900:]},
                    )
                continue
            target = destination / (Path(spec_path).stem + found[0].suffix)
            shutil.copy2(found[0], target)
            produced[name] = str(target)

    return {**summary, "written": produced, "drawing": drawing}
