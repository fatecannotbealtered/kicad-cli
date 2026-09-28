# Chain benchmark

`chain.py` runs the Skill's own recipe -- "Building a board from nothing" --
on a circuit specification and records what came out: every step with its
outcome and time, and the finished board as the tool's read commands measure
it. Every write goes dry run, then confirm, exactly as an agent would.

```bash
export KICAD_CLI_ROOT=/path/to/kicad      # if KiCad is not where it usually is
python bench/chain.py bench/specs/atmega328p.json --runs 5 \
    --work /tmp/bench --out result.json
```

The work directory is kept, one subdirectory per run, so the boards can be
opened and looked at. Do that: two of the findings below came from a plot of
the copper, not from any number.

## Why it exists

The figures this project quoted for a realistic board came from a 15-part
ATmega328P whose specification was never checked in, so none of them could be
made again, and a change that made the chain worse had nothing to be measured
against. `specs/atmega328p.json` is a reconstruction from that description:
ATmega328P in TQFP-32, AMS1117-5.0, 16 MHz crystal with load capacitors, ICSP
header, power input, reset pull-up, an LED, decoupling -- 15 parts, 46 pads in
11 nets. It is not the same board, so the old figures are not comparable.
These are.

It is also the yardstick for moving the engine off SWIG (see
`docs/DEVELOPMENT_STATUS.md`): the new implementation runs the same recipe on
the same specification and is compared against `baseline/atmega328p-swig.json`.

## Baseline: SWIG engine, KiCad 10.0.6, Windows, commit 8984ec22c7a5

Five runs of one specification:

| | range over 5 runs |
|---|---|
| DRC errors | 0 in every run |
| Connections left open | 0 or 1 -- `ok_to_fabricate` in 2 of 5 |
| Power class at its 0.5 mm target | 0 to 52%; `board rewidth` rolled back (`E_INTEGRITY`) in 2 of 5 |
| `backed_fraction` (`board plane`) | 0.31 to 0.41, status FAIL |
| Copper | 263 to 306 mm |
| Vias | 24 to 28 |

`board route --mode full --use-planes` raised the DRC error count and was
rolled back in every run; the Skill's instruction is to drop the flag and
route again, and that is the route recorded.

**Five runs, five boards.** The grid router breaks ties between equal-length
paths in an order that follows object identity, so the geometry differs from
run to run -- and on this board that decides whether `board rewidth` succeeds.
`PYTHONHASHSEED=0` makes routing repeat and leaves the silkscreen step
varying. A benchmark that moves between runs cannot tell a regression from
noise, so the replacement engine has to be deterministic, and this one is
reported as a range.

**What the numbers miss.** Plotted, every part sits in one corner of an
outline sized for the grid layout that `board place` replaced -- nothing
shrinks it. And the silkscreen is legible but points the wrong way: measured
from the centre of each reference designator to every courtyard, 9 or 10 of
the 15 end nearer another part than their own in every run, `C7` printed
inside `U1`'s courtyard among them. `ok_to_fabricate: true` is DRC's view of a
board a person would misassemble.

**The schematic is a deliverable too, and it is further off.** In every run
the generator's wire router failed and `sch create` fell back to drawing
high-fanout nets as labels; in one run of five that failed as well
(`KeyError: 'pop from an empty set'`) and only the netlist was written. The
four drawings differ from each other -- symbol positions, in one the
microcontroller's rotation. In the one inspected closely, the crystal, both
load capacitors and the LED circuit are drawn on top of the
microcontroller's pins and of each other, labels overprinted. KiCad's ERC
finds the same 23 errors in all four: 19 unused pins with no no-connect
marker, 4 power or input pins undriven, and 15 to 18 warnings.

## Acceptance for the replacement engine

Same specification, same recipe:

- one run, because the output is the same every time;
- no metric worse than the worst run above;
- the silkscreen attribution and the outline measured by the tool itself,
  so the next regression of that kind is a number;
- the schematic measured as well: ERC, and symbols or text drawn over one
  another. The goal in `docs/DEVELOPMENT_STATUS.md` is a schematic a person
  reads, not one only the netlist step consumes.
