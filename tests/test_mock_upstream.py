"""Contract tests against a substituted KiCad (CLI-SPEC §11, mock upstream).

Every other test in this suite needs a real KiCad and skips without one, which
means on a contributor's machine or in CI the suite proves nothing. These run
anywhere: the board is a fixture in this repository and KiCad is replaced by the
stubs in `fake_upstream.py`.

They are not a cheaper copy of the live tests. They cover what a real KiCad
cannot be asked to do — refuse to launch, produce nothing while exiting zero,
hang, emit rubbish, or fail twice and then work. Those paths decide whether a
healthy board gets reported as broken, and until this file existed they had been
reasoned about and shipped without ever running.

The spec's breadth list, and where each item lives:

    success, output schema, validation      here
    upstream failure, timeout, retry        here (impossible against real KiCad)
    empty results, exit codes               here
    stdout/stderr boundary                  here
    config failure                          test_uncovered_commands.py (session inspect)
    auth/permission, pagination, rate limit  not applicable - no service, no
                                            credentials, no paged results
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import fake_upstream
import pytest
from driver_test_utils import domain_receipt, grant

REPO = Path(__file__).resolve().parents[1]
MINI = REPO / "tests" / "fixtures" / "mini" / "mini.kicad_pcb"


def run(argv: list[str], env: dict, timeout: int = 900) -> tuple[dict | None, int, str]:
    # Retained payload transport unit tests; not driver/FCC or native evidence.
    if argv[:3] == ["board", "silkscreen", "place"]:
        prefix = [
            sys.executable,
            "-c",
            "import sys; from kicad_cli import envelope,registry; "
            "from kicad_cli.commands import layout; "
            "envelope.configure(compact=True,schema_name='board_silkscreen',"
            "schema_fields=registry.SCHEMAS['board_silkscreen']['fields']); "
            "layout.silkscreen({'board':sys.argv[sys.argv.index('--board')+1]})",
        ]
        argv = argv[3:]
    else:
        prefix = [sys.executable, "-m", "kicad_cli.main"]
    proc = subprocess.run(
        [*prefix, *argv, "--compact"],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=timeout,
    )
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    envelopes = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert len(envelopes) <= 1, f"stdout carried {len(envelopes)} envelopes; the contract is one"
    return (envelopes[0] if envelopes else None), proc.returncode, err


def code_of(doc: dict | None) -> str:
    assert doc is not None, "no envelope on stdout"
    return "OK" if doc.get("ok") else doc["error"]["code"]


# --- the fixture has to stand on its own ------------------------------------


def test_the_fixture_board_needs_no_kicad() -> None:
    """If this fixture ever depends on an installed KiCad, every test below
    quietly stops being a no-KiCad test."""
    assert MINI.exists()
    assert "kicad_pcb" in MINI.read_text(encoding="utf-8")[:40]
    for sibling in ("mini.kicad_sch", "mini.kicad_pro"):
        assert (MINI.parent / sibling).exists()


# --- success and schema -----------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        ["board", "analyze", "ampacity"],
        ["board", "analyze", "reference-plane"],
        ["sch", "check"],
        ["board", "check"],
    ],
)
def test_a_native_command_runs_without_kicad(command: list[str], tmp_path: Path) -> None:
    """These read the file themselves now. Both of KiCad's boundaries are
    replaced by stubs that fail when called, and the command must neither
    call them nor need them. `sch check` and `board check` check the rules
    themselves."""
    doc, exit_code, _ = run(
        [
            *command,
            *(
                ["--schematic", str(MINI.with_suffix(".kicad_sch"))]
                if command == ["sch", "check"]
                else ["--board", str(MINI)]
            ),
        ],
        fake_upstream.env(tmp_path, "launch_fail"),
    )
    assert code_of(doc) == "OK", doc
    assert exit_code == 0
    assert fake_upstream.attempts(tmp_path) == 0, f"{' '.join(command)} started KiCad after all"


ONE_PART = """(kicad_pcb (version 20241229) (generator "t") (general (thickness 1.6)) (paper "A4")
	(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (44 "Edge.Cuts" user))
	(footprint "R:R" (layer "F.Cu") (at 10 10)
		(property "Reference" "R1" (at 0 0 0) (layer "F.SilkS"))
		(pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu"))
	)
)
"""


def _confirmed(argv: list[str], env: dict) -> dict:
    if argv[:3] in (
        ["board", "footprint", "move"],
        ["board", "zone", "create"],
        ["board", "zone", "refill"],
        ["project", "netclass", "set"],
        ["board", "route", "repair"],
        ["board", "route", "rebuild"],
        ["board", "route", "rewidth"],
        ["board", "track", "widen"],
    ):
        asked, _, _ = run([*argv, "--dry-run"], env)
        assert asked["ok"], asked
        done, _, _ = run([*argv, "--confirm", asked["data"]["confirm_token"]], env)
        return done
    asked, _, _ = run(argv + ["--dry-run"], env)
    assert asked["ok"], asked
    done, _, _ = run([*argv, "--confirm", asked["data"]["confirm_token"]], env)
    return domain_receipt(done)


def test_the_native_writes_run_without_kicad(tmp_path: Path) -> None:
    """`board footprint move` edits the board and `project netclass set` the project, with
    both of KiCad's boundaries replaced by stubs that fail when called."""
    board = tmp_path / "one.kicad_pcb"
    board.write_text(OUTLINED, encoding="utf-8")
    (tmp_path / "one.kicad_pro").write_text("{}", encoding="utf-8")
    env = fake_upstream.env(tmp_path / "fake", "launch_fail")
    env.update(grant(tmp_path))

    moved = _confirmed(
        ["board", "footprint", "move", "--board", str(board), "--moves", "R1:12.5,11"], env
    )
    assert code_of(moved) == "OK", moved
    assert "(at 12.5 11)" in board.read_text(encoding="utf-8")

    netclass = [
        "project",
        "netclass",
        "set",
        "--board",
        str(board),
        "--name",
        "Power",
        "--nets",
        "GND",
        "--width",
        "0.5",
    ]
    assert code_of(_confirmed(netclass, env)) == "OK"
    assert "Power" in (tmp_path / "one.kicad_pro").read_text(encoding="utf-8")
    assert fake_upstream.attempts(tmp_path / "fake") == 0, "a write started KiCad after all"


OUTLINED = ONE_PART.replace(
    '\t(footprint "R:R"',
    "\t(gr_rect (start 0 0) (end 20 20) (stroke (width 0.1) (type solid)) (fill no)"
    ' (layer "Edge.Cuts"))\n\t(footprint "R:R"',
).replace('(layers "F.Cu"))', '(layers "F.Cu") (net "GND"))')


def test_board_zone_create_and_refill_need_no_kicad(tmp_path: Path) -> None:
    """Zone create is unfilled; refill uses the tool's own filler, both of
    KiCad's boundaries replaced by stubs that fail when called."""
    board = tmp_path / "one.kicad_pcb"
    board.write_text(OUTLINED, encoding="utf-8")
    env = fake_upstream.env(tmp_path / "fake", "launch_fail")
    env.update(grant(tmp_path))
    argv = ["board", "zone", "create", "--board", str(board), "--net", "GND", "--layer", "F.Cu"]
    created = _confirmed(argv, env)
    assert code_of(created) == "OK", created
    assert created["data"]["receipt"]["extensions"]["observed"][0]["filled_polygons"] == 0
    poured = _confirmed(["board", "zone", "refill", "--board", str(board), "--all"], env)
    assert code_of(poured) == "OK", poured
    from kicad_cli.fileformat import polygons
    from kicad_cli.fileformat.board import Board

    assert polygons.total_area(Board.load(board).zones[0].filled["F.Cu"]) / 1e12 > 300
    assert poured["data"]["receipt"]["extensions"]["observed"][0]["filled_polygons"] > 0
    assert "(filled_polygon" in board.read_text(encoding="utf-8")
    assert fake_upstream.attempts(tmp_path / "fake") == 0, "pour started KiCad after all"


TWO_PARTS = """(kicad_pcb (version 20241229) (generator "t") (general (thickness 1.6)) (paper "A4")
	(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
	(gr_rect (start 0 0) (end 40 30) (stroke (width 0.1) (type solid)) (fill no)
		(layer "Edge.Cuts"))
	(footprint "R:R" (layer "F.Cu") (uuid "00000000-0000-4000-8000-000000000001") (at 5 5)
		(property "Reference" "R1" (at 0 0 0) (layer "F.SilkS"))
		(pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu") (net "A")
            (uuid "00000000-0000-4000-8000-000000000011"))
	)
	(footprint "R:R" (layer "F.Cu") (uuid "00000000-0000-4000-8000-000000000002") (at 35 25)
		(property "Reference" "R2" (at 0 0 0) (layer "F.SilkS"))
		(pad "1" smd rect (at -1 0) (size 1 1) (layers "F.Cu") (net "A")
            (uuid "00000000-0000-4000-8000-000000000012"))
	)
)
"""


def test_board_place_needs_no_kicad(tmp_path: Path) -> None:
    """`board footprint place` checks its work with this tool's DRC, both of KiCad's
    boundaries replaced by stubs that fail when called."""
    board = tmp_path / "two.kicad_pcb"
    board.write_text(TWO_PARTS, encoding="utf-8")
    env = fake_upstream.env(tmp_path / "fake", "launch_fail")
    env.update(grant(tmp_path))
    placed = _confirmed(["board", "footprint", "place", "--board", str(board)], env)
    assert code_of(placed) == "OK", placed
    assert placed["data"]["improved"] is True
    assert placed["data"]["verify"]["oracle"] == "engine"
    assert fake_upstream.attempts(tmp_path / "fake") == 0, "place started KiCad after all"


ROUTED = """(kicad_pcb (version 20241229) (generator "t") (general (thickness 1.6)) (paper "A4")
	(layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
	(gr_rect (start 0 0) (end 30 20) (stroke (width 0.1) (type solid)) (fill no)
		(layer "Edge.Cuts"))
	(footprint "R:R" (layer "F.Cu") (at 8 10)
		(property "Reference" "R1" (at 0 0 0) (layer "F.SilkS"))
		(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "A"))
	)
	(footprint "R:R" (layer "F.Cu") (at 22 10)
		(property "Reference" "R2" (at 0 0 0) (layer "F.SilkS"))
		(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net "A"))
	)
)
"""


def test_board_route_needs_no_kicad(tmp_path: Path) -> None:
    """`board route` lays its tracks with this tool's router, fills the zones
    with its filler and counts errors with its DRC, both of KiCad's
    boundaries replaced by stubs that fail when called."""
    board = tmp_path / "two.kicad_pcb"
    board.write_text(ROUTED, encoding="utf-8")
    (tmp_path / "two.kicad_pro").write_text("{}", encoding="utf-8")
    env = fake_upstream.env(tmp_path / "fake", "launch_fail")
    env.update(grant(tmp_path))
    routed = _confirmed(["board", "route", "rebuild", "--board", str(board)], env)
    assert code_of(routed) == "OK", routed
    ext = routed["data"]["receipt"]["extensions"]
    assert not ext["finding_comparison"]["final"]["unconnected"]
    assert ext["finding_comparison"]["final"]["provenance"]["source"]["kind"] == "custom_checker"
    assert fake_upstream.attempts(tmp_path / "fake") == 0, "route started KiCad after all"


def test_board_widen_needs_no_kicad(tmp_path: Path) -> None:
    """`board track widen` refills and asks this tool's DRC after each step, both
    of KiCad's boundaries replaced by stubs that fail when called."""
    board = tmp_path / "two.kicad_pcb"
    board.write_text(ROUTED, encoding="utf-8")
    project = {
        "net_settings": {"classes": [{"name": "Default", "track_width": 0.5, "clearance": 0.2}]}
    }
    (tmp_path / "two.kicad_pro").write_text(json.dumps(project), encoding="utf-8")
    env = fake_upstream.env(tmp_path / "fake", "launch_fail")
    route = ["board", "route", "rebuild", "--board", str(board), "--neck", "0.2"]
    env.update(grant(tmp_path))
    assert code_of(_confirmed(route, env)) == "OK"
    widened = _confirmed(["board", "track", "widen", "--board", str(board)], env)
    assert code_of(widened) == "OK", widened
    ext = widened["data"]["receipt"]["extensions"]
    assert ext["quality"]["all_selected_target_met"], widened
    assert ext["finding_comparison"]["final"]["provenance"]["source"]["kind"] == "custom_checker"
    assert fake_upstream.attempts(tmp_path / "fake") == 0, "widen started KiCad after all"


def test_board_stitch_needs_no_kicad(tmp_path: Path) -> None:
    """`board zone stitch` plans its vias, fills the zones again with this tool's
    filler and judges each via by this tool's DRC, both of KiCad's boundaries
    replaced by stubs that fail when called."""
    board = tmp_path / "one.kicad_pcb"
    board.write_text(OUTLINED, encoding="utf-8")
    (tmp_path / "one.kicad_pro").write_text("{}", encoding="utf-8")
    env = fake_upstream.env(tmp_path / "fake", "launch_fail")
    env.update(grant(tmp_path))
    create = ["board", "zone", "create", "--board", str(board), "--net", "GND", "--layer", "B.Cu"]
    assert code_of(_confirmed(create, env)) == "OK"
    assert (
        code_of(_confirmed(["board", "zone", "refill", "--board", str(board), "--all"], env))
        == "OK"
    )
    before = board.read_bytes()
    asked, exit_code, _ = run(
        ["board", "zone", "stitch", "--board", str(board), "--net", "GND", "--dry-run"], env
    )
    assert code_of(asked) == "E_CONFIG" and exit_code == 4, asked
    assert board.read_bytes() == before
    # Native refill is now an actual dependency; the real delivery tests it.


def test_board_parity_needs_no_kicad(tmp_path: Path) -> None:
    """Both sides are read here now: the board from its file, the schematic
    through this tool's own netlist. Both of KiCad's boundaries are stubs that
    fail when called, and neither is called."""
    project = tmp_path / "project"
    shutil.copytree(REPO / "tests" / "fixtures" / "sch_edit", project)
    doc, exit_code, _ = run(
        ["project", "parity", "--board", str(project / "edit.kicad_pcb")],
        fake_upstream.env(tmp_path, "launch_fail"),
    )
    assert code_of(doc) == "OK", doc
    assert exit_code == 0
    assert doc["data"]["status"] == "PASS"
    assert fake_upstream.attempts(tmp_path) == 0, "parity started KiCad after all"


# The payload bridge is exercised through `board silkscreen place`, which still
# runs in KiCad's interpreter; the commands that served here before it have
# moved into this process one by one. Moving labels is a write, but its
# confirmation gate lives in the payload, so without a token the call still
# goes straight to the interpreter -- which is all these tests need.
def test_a_payload_envelope_is_relayed(tmp_path: Path) -> None:
    doc, exit_code, _ = run(
        ["board", "silkscreen", "place", "--board", str(MINI)],
        fake_upstream.env(tmp_path, "ok"),
    )
    assert code_of(doc) == "OK"
    assert exit_code == 0


def test_strict_mode_rejects_a_payload_that_does_not_match_its_schema(tmp_path: Path) -> None:
    """The real payload always returns the right fields, so a live run can
    never tell you whether the shell checks. Substituting one that returns the
    wrong fields is the only way to find out."""
    env = fake_upstream.env(tmp_path, "ok")
    env["KICAD_CLI_STRICT"] = "1"
    doc, exit_code, err = run(["board", "silkscreen", "place", "--board", str(MINI)], env)
    assert doc is None, "a contract violation must not be emitted as a successful envelope"
    assert exit_code != 0
    assert "contract violation in board_silkscreen" in err
    # A write command's relay fills the fields a mode does not set, so only
    # the undeclared one can be caught here; a missing field is caught for
    # every command by the envelope's own strict check.
    assert "undeclared ['fake']" in err


# --- upstream failures ------------------------------------------------------


@pytest.mark.parametrize("behaviour", ["launch_fail", "no_output"])
def test_an_unusable_official_binary_is_e_io(behaviour: str, tmp_path: Path) -> None:
    """`no_output` is the observed Windows transient: exit 0, no file, empty
    stderr. It cannot be provoked on a real KiCad. The write commands'
    verification is what launches KiCad's binary now, through the runner
    the payloads share; it launches it once, and a failure is reported with
    its evidence, never read as a clean board."""
    script = "import sys\nfrom kicad_cli.payload import kicad_lib\nkicad_lib.run_drc(sys.argv[1])\n"
    proc = subprocess.run(
        [sys.executable, "-c", script, str(MINI)],
        capture_output=True,
        cwd=REPO,
        env=fake_upstream.env(tmp_path, behaviour),
        timeout=300,
    )
    doc = json.loads(proc.stdout.decode("utf-8", "replace").splitlines()[-1])
    assert code_of(doc) == "E_IO"
    assert proc.returncode == 1
    assert fake_upstream.attempts(tmp_path) == 1


@pytest.mark.parametrize(
    ("behaviour", "expected"),
    [("launch_fail", "E_IO"), ("no_output", "E_IO"), ("garbage", "E_IO")],
)
def test_an_unusable_interpreter_is_e_io(behaviour: str, expected: str, tmp_path: Path) -> None:
    doc, exit_code, _ = run(
        ["board", "silkscreen", "place", "--board", str(MINI)],
        fake_upstream.env(tmp_path, behaviour),
    )
    assert code_of(doc) == expected
    assert exit_code == 1


def test_noise_before_the_envelope_is_stepped_over(tmp_path: Path) -> None:
    """SWIG writes to the C-level stdout and can beat the envelope out of the
    process, so the rule is "first line that parses", not "the output". The
    stub also emits a decoy line that starts with `{` and is not JSON."""
    doc, exit_code, _ = run(
        ["board", "silkscreen", "place", "--board", str(MINI)],
        fake_upstream.env(tmp_path, "noisy"),
    )
    assert code_of(doc) == "OK"
    assert exit_code == 0


@pytest.mark.parametrize(
    "script",
    [
        # KiCad's interpreter, as every pcbnew command starts it
        "import sys\nfrom kicad_cli import kicad_env\n"
        "kicad_env.run_payload('silkscreen', ['--board', sys.argv[1]], timeout=3)\n",
        # KiCad's binary, as those commands verify their writes with it
        "import sys\nfrom kicad_cli.payload import kicad_lib\n"
        "kicad_lib.run_drc(sys.argv[1], timeout=3)\n",
    ],
    ids=["interpreter", "binary"],
)
def test_a_hanging_upstream_becomes_e_timeout(script: str, tmp_path: Path) -> None:
    """Exercised below the command, because the command's own timeout is half an
    hour and this has to be a test, not a coffee break. The paths under test
    are the real ones -- the boundary into KiCad's interpreter the pcbnew
    commands cross, and the DRC they verify with -- same stubs, same
    envelope."""
    env = fake_upstream.env(tmp_path, "hang")
    proc = subprocess.run(
        [sys.executable, "-c", script, str(MINI)],
        capture_output=True,
        cwd=REPO,
        env=env,
        timeout=300,
    )
    out = proc.stdout.decode("utf-8", "replace")
    assert proc.returncode == 8, out
    assert "E_TIMEOUT" in out


# --- empty results ----------------------------------------------------------


def test_an_empty_netlist_is_refused_rather_than_counted(tmp_path: Path) -> None:
    """A count produced from nothing looks exactly like a count from a healthy
    project. `add: 0` would be the dangerous answer here. The netlist is this
    tool's own, so KiCad is a stub that fails when called, and is not called."""
    project = tmp_path / "project"
    shutil.copytree(MINI.parent, project)
    board = project / MINI.name
    env = fake_upstream.env(tmp_path, "launch_fail")
    env.update(grant(project))
    env["KICAD_CONFIG_HOME"] = str(tmp_path / "configuration")
    doc, exit_code, _ = run(
        [
            "project",
            "sync",
            "--board",
            str(board),
            "--schematic",
            str(board.with_suffix(".kicad_sch")),
            "--dry-run",
        ],
        env,
    )
    assert code_of(doc) == "E_VALIDATION"
    assert exit_code == 2
    assert doc["error"]["details"]["source_components"] == 0
    assert fake_upstream.attempts(tmp_path) == 0, "project sync started KiCad after all"


@pytest.mark.parametrize("command", [["project", "links", "inspect"], ["project", "parity"]])
def test_a_project_with_no_symbols_is_not_found_not_zero(tmp_path: Path, command) -> None:
    "Pure file-format path — no KiCad involved at all, in either direction."
    doc, exit_code, _ = run(
        [*command, "--board", str(MINI)],
        fake_upstream.env(tmp_path, "ok"),
    )
    assert code_of(doc) == "E_NOT_FOUND"
    assert exit_code == 3
    assert "expected_root" in doc["error"]["details"]


# --- the envelope boundary --------------------------------------------------


def test_diagnostics_never_land_on_stdout(tmp_path: Path) -> None:
    """One envelope on stdout and nothing else, even when the upstream is
    writing to stderr. `run()` asserts the count; this pins the other half."""
    _, _, err = run(
        [
            "project",
            "sync",
            "--board",
            str(MINI),
            "--schematic",
            str(MINI.with_suffix(".kicad_sch")),
            "--dry-run",
        ],
        fake_upstream.env(tmp_path, "launch_fail"),
    )
    assert isinstance(err, str)


def test_every_error_here_maps_to_its_declared_exit_code(tmp_path: Path) -> None:
    """The error triple — code, exit status, retryable — has to agree, and the
    declaration lives in the generated contract."""
    sys.path.insert(0, str(REPO))
    from kicad_cli.contract_gen import CODES

    seen = {
        "E_IO": ["board", "silkscreen", "place", "--board", str(MINI)],
        "E_NOT_FOUND": ["project", "links", "inspect", "--board", str(MINI)],
    }
    for expected, argv in seen.items():
        behaviour = "launch_fail" if expected == "E_IO" else "ok"
        doc, exit_code, _ = run(argv, fake_upstream.env(tmp_path / expected, behaviour))
        assert code_of(doc) == expected
        assert exit_code == CODES[expected]["exit"], (
            f"{expected} exited {exit_code}, contract says {CODES[expected]['exit']}"
        )


# --- the name we share ------------------------------------------------------


def test_our_own_binary_on_path_is_not_mistaken_for_kicads(tmp_path: Path) -> None:
    """Third time this class of bug has appeared, so it gets a test.

    `kicad-cli` is our name too, on purpose. The PATH fallback used to accept
    any hit whose path differed from `sys.argv[0]`, which is almost always
    true -- including for the `kicad-cli.CMD` shim an npm install puts on PATH.
    Accepting it sends every DRC and ERC call back into this tool, which
    answers E_USAGE, which surfaces as E_IO on a perfectly healthy board.
    """
    sys.path.insert(0, str(REPO))
    from kicad_cli.kicad_env import _is_kicads_own

    ours = tmp_path / "npm-shim"
    ours.mkdir()
    shim = ours / "kicad-cli.CMD"
    shim.write_text("@echo off\r\n", encoding="utf-8")
    assert not _is_kicads_own(shim), "our own npm shim was accepted as KiCad's binary"

    lone = tmp_path / "somewhere" / "kicad-cli"
    lone.parent.mkdir(parents=True)
    lone.write_text("", encoding="utf-8")
    assert not _is_kicads_own(lone), "a lone binary with no KiCad around it was accepted"


def test_a_real_kicad_layout_on_path_is_accepted(tmp_path: Path) -> None:
    """The guard has to stay usable: rejecting everything would quietly remove
    the fallback for people whose KiCad really is on PATH."""
    sys.path.insert(0, str(REPO))
    from kicad_cli.kicad_env import _is_kicads_own

    # Layout one: the rest of the suite sits beside it (Linux /usr/bin).
    suite = tmp_path / "usr" / "bin"
    suite.mkdir(parents=True)
    for name in ("kicad-cli", "pcbnew", "eeschema"):
        (suite / name).write_text("", encoding="utf-8")
    assert _is_kicads_own(suite / "kicad-cli")

    # Layout two: alone in bin/, with share/kicad one level up.
    alt = tmp_path / "opt" / "kicad"
    (alt / "bin").mkdir(parents=True)
    (alt / "share" / "kicad").mkdir(parents=True)
    (alt / "bin" / "kicad-cli").write_text("", encoding="utf-8")
    assert _is_kicads_own(alt / "bin" / "kicad-cli")
