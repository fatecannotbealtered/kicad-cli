"""``board route --engine freerouting`` moved off pcbnew: pcbnew's own
Specctra export and import are the reference.

The DSN this tool writes must be the one pcbnew writes, as Freerouting reads
it (`specctra_reference` says what that leaves out), on KiCad's demo boards;
the session it reads back must lay down on a board the very tracks and vias
pcbnew's import lays down. Freerouting itself is stood in for by
`fake_freerouting`, so the command around it can be tested here, where
Freerouting is not installed -- with KiCad's binary and interpreter both
replaced by stubs that fail when called.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import fake_upstream
import pytest
from payload_reference import DEMO_BOARDS, legacy_port, needs_payload
from specctra_reference import copper, differences, ours, pcbnew, session_of

from kicad_cli.fileformat import specctra
from kicad_cli.fileformat.drc.settings import Settings
from kicad_cli.native import freeroute, route

REPO = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent

# Selected exportable demos; RoyalBlue54L-Feather and tinytapeout-demo are excluded,
# on which its export stops for a dialog nobody can answer, and the NFC
# antenna, whose outline has an arc meeting a line 3.5 um short: pcbnew
# bends the arc to meet it, this tool joins the two where they are.
DSN_BOARDS = [
    b
    for b in DEMO_BOARDS
    if b.stem
    in (
        "ecc83-pp",
        "ecc83-pp_v2",
        "interf_u",
        "pic_programmer",
        "microwave",
        "video",
        "complex_hierarchy",
        "kit-dev-coldfire-xilinx_5213",
        "multichannel_mixer",
        "multichannel_mixer-unrouted",
        "One-Air-Max",
        "CM5_MINIMA_3",
        "sonde xilinx",
        "StickHub",
        "vme-wren",
    )
]
SES_BOARDS = [
    b for b in DEMO_BOARDS if b.stem in ("ecc83-pp", "kit-dev-coldfire-xilinx_5213", "CM5_MINIMA_3")
]


def copy_project(board: Path, into: Path) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    for sibling in board.parent.glob(board.stem + ".kicad_*"):
        if sibling.suffix in (".kicad_pcb", ".kicad_pro", ".kicad_dru"):
            shutil.copy2(sibling, into / sibling.name)
    return into / board.name


def strip(board: Path) -> None:
    from kicad_cli.fileformat.board import Board  # noqa: PLC0415

    b = Board.load(board)
    for node in [n for n in b.root.lists() if n.head in ("segment", "arc", "via")]:
        b.root.remove(node)
    b.document.save(board)


# -- the DSN ---------------------------------------------------------------------------


@needs_payload
@pytest.mark.parametrize("board", DSN_BOARDS, ids=lambda p: p.stem)
def test_the_dsn_is_pcbnews_as_freerouting_reads_it(board, tmp_path):
    copy = copy_project(board, tmp_path)
    theirs = tmp_path / f"{board.stem}.pcbnew.dsn"
    assert pcbnew("export", str(copy), str(theirs)) == 0, "pcbnew did not export"
    text = theirs.read_text(encoding="utf-8")
    path = text.split("\n", 1)[0][len("(pcb ") :].strip('"')
    version = re.search(r'\(host_version "([^"]*)"\)', text).group(1)
    assert differences(text, ours(copy, path, version)) == []


# -- the session -----------------------------------------------------------------------


@needs_payload
@pytest.mark.parametrize("board", SES_BOARDS, ids=lambda p: p.stem)
def test_a_session_goes_onto_the_board_as_pcbnews_import_put_it(board, tmp_path):
    """The board's own tracks and vias as a session, imported into the board
    with its tracks taken out: by pcbnew, and by this tool."""
    base = copy_project(board, tmp_path / "base")
    ses = tmp_path / "board.ses"
    ses.write_text(session_of(base), encoding="utf-8")
    theirs = copy_project(board, tmp_path / "theirs")
    mine = copy_project(board, tmp_path / "ours")
    strip(theirs)
    strip(mine)
    imported = tmp_path / "theirs" / "imported.kicad_pcb"
    assert pcbnew("import", str(theirs), str(ses), str(imported)) == 0, "pcbnew did not import"

    layout = route.Layout(str(mine))
    dsn = specctra.Dsn(layout.board, Settings.of(mine.with_suffix(".kicad_pro")))
    dsn.text("board.dsn", "10.0")
    session = specctra.read_session(ses.read_text(encoding="utf-8"))
    freeroute.apply_session(layout, session, {name: layer for layer, name in dsn.names.items()})
    layout.apply().document.save(mine)
    assert copper(imported) == copper(mine)


def test_a_session_is_read_as_freerouting_writes_one():
    text = """(session "x.ses"
  (base_design "x.dsn")
  (placement
    (resolution um 10)
    (component "Lib:R"
      (place R1 1000000 -500000 front 90)
    )
  )
  (routes
    (resolution um 10)
    (library_out
      (padstack "Via[0-1]_600:300_um"
        (shape
          (circle F.Cu 6000 0 0)
        )
        (attach off)
      )
    )
    (network_out
      (net "Net-(R1-Pad1)"
        (wire
          (path F.Cu 2500
            1000000 -500000
            1010000 -500000
            1010000 -510000
          )
        )
        (via
          "Via[0-1]_600:300_um" 1010000 -510000
        )
      )
    )
  )
)
"""
    s = specctra.read_session(text)
    assert s.wires == [
        (
            "Net-(R1-Pad1)",
            "F.Cu",
            250_000,
            [(100_000_000, 50_000_000), (101_000_000, 50_000_000), (101_000_000, 51_000_000)],
        )
    ]
    assert s.vias == [
        ("Net-(R1-Pad1)", "Via[0-1]_600:300_um", (101_000_000, 51_000_000), 600_000, 300_000)
    ]
    assert s.parts == [("R1", (100_000_000, 50_000_000), "front", 90.0)]


def test_a_file_that_is_no_session_is_said_to_be_none():
    with pytest.raises(specctra.SessionError):
        specctra.read_session("(pcb x)")
    with pytest.raises(specctra.SessionError):
        specctra.read_session("(session x (routes")


# -- what was measured on pcbnew's output, kept -------------------------------------------


@pytest.mark.parametrize(
    ("word", "written"),
    [
        ("a b", '"a b"'),
        ("a%b", '"a%b"'),
        ("a(b", '"a(b"'),
        ("a{b", '"a{b"'),
        ("a-b", '"a-b"'),
        ("ab-", '"ab-"'),
        ("-ab", "-ab"),
        ("-VAA", "-VAA"),
        ("a#b", "a#b"),
        ("a+b", "a+b"),
        ("a/b", "a/b"),
        ("a:b", "a:b"),
        ("a[b", "a[b"),
        ("a\\b", "a\\b"),
        ('a"b', "a''b"),
        ("", '""'),
    ],
)
def test_words_are_quoted_where_pcbnew_quotes_them(word, written):
    assert specctra.quote(word) == written


def test_numbers_are_six_digits_and_points_wrap_where_pcbnew_wraps_them():
    assert [specctra.g(v) for v in (81201.3, 100060.0, -0.0, 63.5, 2271.13)] == [
        "81201.3",
        "100060",
        "0",
        "63.5",
        "2271.13",
    ]
    pts = [
        (194945, -133350),
        (172085, -133350),
        (172085, -142494),
        (90805, -142494),
        (90805, -140970),
        (90805, -133350),
        (79375, -133350),
        (79375, -34290),
        (194945, -34290),
        (194945, -133350),
    ]
    assert specctra.point_list("path pcb 0", pts) == (
        "(path pcb 0  194945 -133350  172085 -133350  172085 -142494  90805 -142494\n"
        "            90805 -140970  90805 -133350  79375 -133350  79375 -34290  194945 -34290\n"
        "            194945 -133350)"
    )


@pytest.mark.parametrize(
    ("radius_mm", "sweep", "pieces"),
    [
        (0.03, 20, 0),
        (0.03, 45, 0),
        (0.03, 90, 2),
        (0.03, 180, 4),
        (0.064, 20, 0),
        (0.064, 45, 2),
        (0.064, 90, 2),
        (0.064, 180, 4),
        (0.1, 20, 0),
        (0.1, 45, 2),
        (0.1, 90, 2),
        (0.1, 180, 5),
        (0.2, 20, 2),
        (0.2, 45, 2),
        (0.2, 90, 4),
        (0.2, 180, 7),
        (0.4, 20, 2),
        (0.4, 45, 2),
        (0.4, 90, 5),
        (0.4, 180, 10),
        (0.5, 90, 6),
        (1, 90, 8),
        (2, 90, 11),
        (5, 90, 18),
        (10, 90, 25),
        (20, 90, 35),
        (10, 180, 50),
    ],
)
def test_an_arc_takes_as_many_pieces_as_pcbnew_gives_it(radius_mm, sweep, pieces):
    assert specctra.segments(radius_mm * 1_000_000, sweep, 5000) == pieces


@pytest.mark.parametrize(
    ("radius", "outer", "turn"),
    [
        (50_000, 50_190, 16),
        (250_000, 250_951, 16),
        (300_000, 301_142, 17),
        (500_000, 501_903, 22),
        (1_000_000, 1_003_805, 31),
        (2_000_000, 2_007_611, 45),
        (4_000_000, 4_015_221, 63),
    ],
)
def test_a_rounded_pads_corners_sit_where_pcbnews_do(radius, outer, turn):
    assert specctra._round(radius * specctra.ROUNDING) == outer
    assert specctra.corner_turn(outer, 5000) == turn


# -- the command, Freerouting stood in for ------------------------------------------------


def engine(tmp: Path, session: Path) -> dict:
    """An environment in which `java -jar freerouting-2.1.0.jar` hands back
    `session`, and KiCad's binary and interpreter fail when called."""
    env = fake_upstream.env(tmp / "fake", "launch_fail")
    bin_dir = tmp / "engine"
    bin_dir.mkdir(parents=True, exist_ok=True)
    jar = bin_dir / "freerouting-2.1.0.jar"
    jar.write_bytes(b"")
    java = fake_upstream.make_launcher(bin_dir, "java", HERE / "fake_freerouting.py")
    env.update(
        {
            "KICAD_CLI_FREEROUTING": str(jar),
            "KICAD_CLI_JAVA": str(java),
            "FAKE_FREEROUTING_SES": str(session),
            "FAKE_FREEROUTING_DSN": str(tmp / "given.dsn"),
            "PYTHONIOENCODING": "utf-8",
            "KICAD_CLI_STRICT": "1",
        }
    )
    return env


def run(argv: list[str], env: dict) -> dict:
    # This tests the retained domain router with a fake Java engine. It is not
    # an invocation of the public driver or actual installed Freerouting.
    return legacy_port(*argv, environment=env)


@needs_payload
def test_routing_through_freerouting_needs_no_kicad(tmp_path):
    board = copy_project(next(b for b in DEMO_BOARDS if b.stem == "ecc83-pp"), tmp_path / "board")
    session = tmp_path / "back.ses"
    session.write_text(session_of(board), encoding="utf-8")
    before = copper(board)
    env = engine(tmp_path, session)
    argv = ["board", "route", "rebuild", "--board", str(board), "--engine", "freerouting"]
    asked = run([*argv, "--dry-run"], env)
    assert asked["error"]["code"] == "E_CONFIRMATION_REQUIRED", asked
    assert asked["error"]["details"]["preview"]["tracks_before"] == 59
    done = run([*argv, "--confirm", asked["error"]["details"]["confirm_token"]], env)
    assert done["ok"] is True, done
    data = done["data"]
    assert (data["engine"], data["tracks"], data["vias"], data["unconnected_after"]) == (
        "freerouting",
        59,
        0,
        0,
    )
    assert data["verify"]["oracle"] == "engine"
    assert data["verify"]["errors_final"] <= data["verify"]["errors_baseline"]
    assert (data["freerouting_unrouted"], data["freerouting_violations"]) == (0, 0)
    assert (tmp_path / "given.dsn").read_text(encoding="utf-8").startswith("(pcb ")
    # the board's own tracks, put on the session's tenth of a micrometre
    assert sum(copper(board)[0].values()) == sum(before[0].values())
    assert fake_upstream.attempts(tmp_path / "fake") == 0, "KiCad was started after all"


@needs_payload
def test_a_session_that_makes_the_board_worse_is_not_kept(tmp_path):
    """A wire across a pad of another net: the DRC finds a short the board
    did not have, and the board goes back as it was."""
    board = copy_project(next(b for b in DEMO_BOARDS if b.stem == "ecc83-pp"), tmp_path / "board")
    text = session_of(board)
    head = '      (net "Net-(P3-P1)"\n'
    # from C1's pad 1 (Net-(P3-P1)) straight through its pad 2 (GND)
    across = (
        "        (wire\n          (path bottom_cu 8000\n            1416050 -996950\n"
        "            1416050 -946950\n          )\n        )\n"
    )
    session = tmp_path / "worse.ses"
    session.write_text(text.replace(head, head + across, 1), encoding="utf-8")
    original = board.read_bytes()
    env = engine(tmp_path, session)
    argv = ["board", "route", "rebuild", "--board", str(board), "--engine", "freerouting"]
    asked = run([*argv, "--dry-run"], env)
    done = run([*argv, "--confirm", asked["error"]["details"]["confirm_token"]], env)
    assert done["ok"] is False and done["error"]["code"] == "E_INTEGRITY", done
    assert done["error"]["details"]["write_state"] == "rolled_back"
    assert board.read_bytes() == original
