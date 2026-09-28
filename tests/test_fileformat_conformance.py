"""The lossless layer, held against every kind of file KiCad ships.

`test_fileformat_sexpr.py` states the two promises on a small board; this
holds them on KiCad's own demo projects and libraries:

- an untouched document is written back byte for byte, and deferring the
  parse builds exactly the tree a whole parse does;
- a document whose every list is re-rendered is identical to what KiCad
  saved. That is the claim the layout rules in `fileformat/sexpr.py` rest
  on, and it was measured here before it was written there.

Which KiCad saved a file is in its ``generator_version``. The rules hold for
everything 9.0 and later wrote, except two boards where KiCad 9.0's own
writer broke its layout (named below). Files older than 9.0, and footprints
the library team's scripts generated rather than KiCad, are only required to
round-trip: their layout is somebody else's.

The whole corpus is about 16,000 files and several minutes; the complete
checks run on all of what KiCad 9.99 and 10.0 wrote and on a fixed sample of
the libraries, and the cheap round-trip on all of it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from kicad_demos import DEMOS, SKIP_REASON

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli.fileformat import sexpr  # noqa: E402

SHARE = DEMOS.parent
GENERATOR = re.compile(r'\(generator_version "([^"]*)"\)')

# Written by KiCad 9.0, and not in its own layout: a missing line break
# between two teardrop settings, `(curved_edges no)(filter_ratio 0.9)`, and a
# line break inside an (xy ...) of a text's render cache. Both are defects of
# that writer, and they are exactly what an untouched list keeps.
KICAD_9_0_LAYOUT_DEFECTS = {"RoyalBlue54L-Feather.kicad_pcb", "tinytapeout-demo.kicad_pcb"}

# A fixed sample: the libraries the chain uses, plus a large one.
SYMBOL_SAMPLE = (
    "Device", "power", "Connector_Generic", "Regulator_Linear",
    "MCU_Microchip_ATmega", "Interface_USB", "FPGA_Xilinx_Artix7",
)  # fmt: skip
FOOTPRINT_EVERY = 10  # every tenth footprint KiCad itself saved

pytestmark = pytest.mark.skipif(not DEMOS.exists(), reason=SKIP_REASON)


def generator(source: str) -> str | None:
    match = GENERATOR.search(source, 0, 4000)
    return match.group(1) if match else None


def written_by_kicad_9_or_later(version: str | None) -> bool:
    if version is None:
        return False
    major, _, minor = version.partition(".")
    return (int(major), int(minor or 0)) >= (9, 0)


def same_tree(a: sexpr.List, b: sexpr.List) -> bool:
    stack = [(a, b)]
    while stack:
        x, y = stack.pop()
        if (x.start, x.end) != (y.start, y.end) or len(x.items) != len(y.items):
            return False
        for i, j in zip(x.items, y.items, strict=True):
            if isinstance(i, str) or isinstance(j, str):
                if i != j:
                    return False
            else:
                stack.append((i, j))
    return True


def first_difference(a: str, b: str) -> str:
    index = next((k for k, (x, y) in enumerate(zip(a, b, strict=False)) if x != y), None)
    if index is None:
        index = min(len(a), len(b))
    return f"KiCad {a[index - 60 : index + 40]!r}\n   ours {b[index - 60 : index + 40]!r}"


def read(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def demo_files() -> list[Path]:
    return sorted(p for pattern in ("*.kicad_pcb", "*.kicad_sch") for p in DEMOS.rglob(pattern))


def complete_check(path: Path, require_layout: bool) -> list[str]:
    source = read(path)
    problems = []
    lazy = sexpr.Document.parse(source)
    if lazy.dumps() != source:
        problems.append(f"{path.name}: deferred parse did not round-trip")
    whole = sexpr.Document.parse(source, lazy=False)
    if whole.dumps() != source:
        problems.append(f"{path.name}: whole parse did not round-trip")
    if not same_tree(lazy.root, whole.root):
        problems.append(f"{path.name}: deferred parse built a different tree")
    if require_layout:
        sexpr.mark_all(whole.root)
        rendered = whole.dumps()
        if rendered != source:
            problems.append(
                f"{path.name} [{generator(source)}]: re-rendered text differs\n   "
                + first_difference(source, rendered)
            )
    return problems


def test_every_demo_file_round_trips_untouched():
    """Cheap and total: including the 71 and 89 MB boards."""
    files = demo_files()
    assert len(files) > 100, f"expected KiCad's demo projects under {DEMOS}"
    broken = []
    for path in files:
        source = read(path)
        if sexpr.Document.parse(source).dumps() != source:
            broken.append(path.name)
    assert broken == []


def test_what_kicad_9_and_later_wrote_is_reproduced_exactly():
    problems = []
    checked = 0
    for path in demo_files():
        if path.stat().st_size > 8_000_000:
            continue  # round-tripped above; a whole parse of these is a minute each
        version = generator(read(path))
        if not written_by_kicad_9_or_later(version):
            continue
        require_layout = path.name not in KICAD_9_0_LAYOUT_DEFECTS
        problems += complete_check(path, require_layout)
        checked += 1
    assert checked > 100, "too few files written by KiCad 9 or later were found"
    assert problems == [], "\n".join(problems)


def test_the_symbol_libraries_are_reproduced_exactly():
    problems = []
    for name in SYMBOL_SAMPLE:
        path = SHARE / "symbols" / f"{name}.kicad_sym"
        assert path.is_file(), f"missing symbol library {path}"
        problems += complete_check(path, require_layout=True)
    assert problems == [], "\n".join(problems)


def test_footprints_kicad_saved_are_reproduced_and_the_rest_round_trip():
    problems = []
    kicad_saved = 0
    for index, path in enumerate(sorted((SHARE / "footprints").glob("*.pretty/*.kicad_mod"))):
        source = read(path)
        if generator(source) is not None:
            kicad_saved += 1
            if kicad_saved % FOOTPRINT_EVERY == 0:
                problems += complete_check(path, require_layout=True)
        elif index % FOOTPRINT_EVERY == 0:
            # Generated by the library's scripts: their layout is theirs, and
            # only the promise about untouched text applies.
            problems += complete_check(path, require_layout=False)
    assert kicad_saved > 1000, "expected thousands of footprints KiCad saved itself"
    assert problems == [], "\n".join(problems)
