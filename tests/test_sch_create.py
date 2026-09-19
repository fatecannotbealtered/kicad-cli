"""The command that starts from a description instead of a design.

Everything else here takes a `.kicad_pcb` that already exists. This one takes a
requirement, which is where an agent's work actually starts, so the tests are
about the thing that makes it usable to one: a wrong symbol or a wrong pin has
to fail with the name, in the dry run, before a file is written. A generator
that writes a schematic missing a connection nobody asked about is worse than
one that refuses.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]

# A real circuit, small enough to read: 5V in, 3V3 out, a power LED.
SPEC = {
    "title": "5V to 3V3 LDO",
    "parts": [
        {
            "ref": "J1",
            "symbol": "Connector_Generic:Conn_01x02",
            "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
        },
        {
            "ref": "U1",
            "symbol": "Regulator_Linear:AMS1117-3.3",
            "footprint": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
        },
        {
            "ref": "C1",
            "symbol": "Device:C",
            "value": "10uF",
            "footprint": "Capacitor_SMD:C_0805_2012Metric",
        },
        {
            "ref": "R1",
            "symbol": "Device:R",
            "value": "1k",
            "footprint": "Resistor_SMD:R_0805_2012Metric",
        },
        {"ref": "D1", "symbol": "Device:LED", "footprint": "LED_SMD:LED_0805_2012Metric"},
    ],
    "nets": [
        {"name": "+5V", "connect": ["J1.1", "U1.VI", "C1.1"]},
        {"name": "+3V3", "connect": ["U1.VO", "R1.1"]},
        {"name": "LED_A", "connect": ["R1.2", "D1.1"]},
        {"name": "GND", "connect": ["J1.2", "U1.GND", "C1.2", "D1.2"]},
    ],
}


def _skidl_available() -> bool:
    """Look without importing.

    Importing the generator opens a log file named after the running script, in
    the current working directory, and keeps it open -- so a collection-time
    import here drops `test_sch_create.log` into the repository root. Asking the
    import system whether the module exists does not execute it.
    """
    return importlib.util.find_spec("skidl") is not None


needs_generator = pytest.mark.skipif(
    not _skidl_available() or not DEMOS.exists(),
    reason=f"needs the schematic generator and KiCad's libraries ({SKIP_REASON})",
)


def run(argv: list[str], cwd: Path) -> tuple[dict | None, int]:
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=900,
    )
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith("{"):
            return json.loads(line), proc.returncode
    return None, proc.returncode


def write_spec(directory: Path, spec, name: str = "circuit.json") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(
        spec if isinstance(spec, str) else json.dumps(spec, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


@needs_generator
def test_a_dry_run_resolves_the_whole_circuit_and_writes_nothing(tmp_path):
    spec = write_spec(tmp_path, SPEC)
    doc, code = run(["sch", "create", "--spec", str(spec), "--dry-run"], tmp_path)
    assert doc is not None and doc["ok"] is False
    assert doc["error"]["code"] == "E_CONFIRMATION_REQUIRED", doc
    preview = doc["error"]["details"]["preview"]
    assert preview["parts"] == len(SPEC["parts"])
    assert preview["nets"] == len(SPEC["nets"])
    assert preview["unconnected_parts"] == []
    # The dry run resolved against the real libraries; it must not have left a
    # schematic behind while doing it.
    assert list(tmp_path.glob("*.kicad_sch")) == []


@needs_generator
def test_a_confirmed_run_produces_a_schematic_kicad_itself_accepts(tmp_path):
    """The only proof that matters: KiCad reads it back and finds the parts."""
    from kicad_cli import kicad_env  # noqa: PLC0415

    spec = write_spec(tmp_path, SPEC)
    plan, _ = run(["sch", "create", "--spec", str(spec), "--dry-run"], tmp_path)
    token = plan["error"]["details"]["confirm_token"]
    doc, code = run(["sch", "create", "--spec", str(spec), "--confirm", token], tmp_path)
    assert doc is not None and doc["ok"] is True, doc
    written = doc["data"]["written"]
    schematic = Path(written["schematic"])
    assert schematic.is_file() and Path(written["netlist"]).is_file()

    official = kicad_env.find_official_cli()
    assert official, "the official binary is needed to check what we produced"
    netlist = tmp_path / "readback.net"
    export = subprocess.run(
        [official, "sch", "export", "netlist", "-o", str(netlist), str(schematic)],
        capture_output=True,
        timeout=900,
    )
    assert export.returncode == 0, export.stderr.decode("utf-8", "replace")[:400]
    text = netlist.read_text(encoding="utf-8")
    for part in SPEC["parts"]:
        assert f'(ref "{part["ref"]}")' in text or f'(ref "{part["ref"]}"' in text, part["ref"]


@needs_generator
def test_a_simple_circuit_is_drawn_without_needing_the_fallback(tmp_path):
    """`drawing` reports which of the two routes produced the picture.

    Plain routing is tried first precisely so that a circuit which draws
    correctly today keeps coming out the same way. If this starts reporting
    `auto_stub`, the order was changed and every existing schematic's
    appearance changed with it.
    """
    spec = write_spec(tmp_path, SPEC)
    plan, _ = run(["sch", "create", "--spec", str(spec), "--dry-run"], tmp_path)
    token = plan["error"]["details"]["confirm_token"]
    doc, _ = run(["sch", "create", "--spec", str(spec), "--confirm", token], tmp_path)
    drawing = doc["data"]["drawing"]
    assert drawing["status"] == "drawn", drawing
    assert drawing["style"] == "routed", drawing
    assert drawing["reason"] is None


@needs_generator
def test_a_circuit_the_wire_router_cannot_draw_still_yields_its_netlist(tmp_path):
    """The netlist is what the chain consumes; the drawing is for a person.

    A 15-part ATmega328P failed here outright: `generate_netlist` had already
    written a correct netlist, then the wire router could not lay out a 14-pin
    ground net, and the whole command failed and threw the netlist away.

    Rather than reconstruct that board, this asserts the contract directly --
    whatever happens to the drawing, a netlist comes back and `drawing` says
    which. Both outcomes are legitimate; silently returning neither is not.
    """
    spec = write_spec(tmp_path, SPEC)
    plan, _ = run(["sch", "create", "--spec", str(spec), "--dry-run"], tmp_path)
    token = plan["error"]["details"]["confirm_token"]
    doc, _ = run(["sch", "create", "--spec", str(spec), "--confirm", token], tmp_path)
    assert doc["ok"] is True, doc

    written, drawing = doc["data"]["written"], doc["data"]["drawing"]
    assert written["netlist"] and Path(written["netlist"]).is_file()
    assert drawing["status"] in ("drawn", "failed"), drawing
    if drawing["status"] == "failed":
        assert written["schematic"] is None
        assert "NO SCHEMATIC DRAWING" in doc["data"]["note"]
        assert drawing["reason"] and drawing["reason_auto_stub"]
    else:
        assert written["schematic"] and Path(written["schematic"]).is_file()


@needs_generator
def test_a_symbol_that_does_not_exist_fails_by_name(tmp_path):
    spec = json.loads(json.dumps(SPEC))
    spec["parts"][1]["symbol"] = "Regulator_Linear:NoSuchPartXYZ"
    doc, code = run(
        ["sch", "create", "--spec", str(write_spec(tmp_path, spec)), "--dry-run"], tmp_path
    )
    assert doc["error"]["code"] == "E_VALIDATION", doc
    problems = json.dumps(doc["error"]["details"]["problems"], ensure_ascii=False)
    assert "NoSuchPartXYZ" in problems
    assert code == 2


@needs_generator
def test_a_pin_that_does_not_exist_lists_the_pins_that_do(tmp_path):
    """A refused pin is usually a near miss, and the tool knows the real names.

    Handing back "no such pin" alone makes the caller go and read the symbol;
    handing back the list makes the next attempt the right one.
    """
    spec = json.loads(json.dumps(SPEC))
    spec["nets"][0]["connect"] = ["J1.1", "U1.VIN", "C1.1"]  # the pin is VI
    doc, _ = run(
        ["sch", "create", "--spec", str(write_spec(tmp_path, spec)), "--dry-run"], tmp_path
    )
    assert doc["error"]["code"] == "E_VALIDATION", doc
    problem = next(p for p in doc["error"]["details"]["problems"] if p.get("pin") == "U1.VIN")
    assert "VI" in problem["available"], problem


@pytest.mark.parametrize(
    ("spec", "expect"),
    [
        ("not json at all", "not valid JSON"),
        ({"parts": [], "nets": []}, "non-empty array"),
        (
            {"parts": [{"ref": "R1"}], "nets": [{"name": "N", "connect": ["R1.1", "R1.2"]}]},
            "symbol",
        ),
        (
            {"parts": [{"ref": "R1", "symbol": "Device:R"}], "nets": [{"name": "N"}]},
            "at least two connections",
        ),
    ],
)
def test_a_malformed_specification_is_refused_before_any_library_is_read(spec, expect, tmp_path):
    """Shape errors do not need KiCad, so they must not wait for it."""
    doc, code = run(
        ["sch", "create", "--spec", str(write_spec(tmp_path, spec)), "--dry-run"], tmp_path
    )
    assert doc is not None and doc["ok"] is False
    assert doc["error"]["code"] == "E_VALIDATION", doc
    # Message and details together: a caller reads the whole error object, and
    # which half carries the phrase is not a promise worth freezing in a test.
    assert expect in json.dumps(doc["error"], ensure_ascii=False), doc
    assert code == 2


def test_a_missing_specification_is_not_found_rather_than_a_traceback(tmp_path):
    doc, code = run(
        ["sch", "create", "--spec", str(tmp_path / "absent.json"), "--dry-run"], tmp_path
    )
    assert doc["error"]["code"] == "E_NOT_FOUND", doc
    assert code == 3


@pytest.mark.parametrize(
    ("spec", "expect"),
    [
        (
            {
                "parts": [{"ref": "R1", "symbol": "NoColon"}],
                "nets": [{"name": "N", "connect": ["R1.1", "R1.2"]}],
            },
            "Library:Name",
        ),
        (
            {
                "parts": [{"ref": "R1", "symbol": "Device:R"}, {"ref": "R1", "symbol": "Device:R"}],
                "nets": [{"name": "N", "connect": ["R1.1", "R1.2"]}],
            },
            "duplicate ref",
        ),
        (
            {
                "parts": [{"ref": "R1", "symbol": "Device:R"}],
                "nets": [{"name": "N", "connect": ["R1.1", "R9.1"]}],
            },
            "no such part",
        ),
        (
            {
                "parts": [{"ref": "R1", "symbol": "Device:R"}],
                "nets": [{"name": "N", "connect": ["R1.1", "justtext"]}],
            },
            "REF.PIN",
        ),
    ],
)
def test_structure_is_judged_without_needing_kicad(spec, expect, tmp_path, monkeypatch):
    """A missing field is not a question for the symbol libraries.

    CI has no KiCad, and answering "install KiCad" when the real problem is a
    part with no symbol is the wrong answer to the wrong question. Pointing the
    installation lookup at nothing proves the judgement did not consult it.
    """
    monkeypatch.setenv("KICAD_CLI_ROOT", str(tmp_path / "no-kicad-here"))
    doc, code = run(
        ["sch", "create", "--spec", str(write_spec(tmp_path, spec)), "--dry-run"], tmp_path
    )
    assert doc is not None and doc["error"]["code"] == "E_VALIDATION", doc
    assert expect in json.dumps(doc["error"], ensure_ascii=False), doc
    assert code == 2
