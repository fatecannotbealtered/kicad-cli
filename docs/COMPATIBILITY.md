# Compatibility

[English](COMPATIBILITY.md) · [中文](COMPATIBILITY_zh.md)

`kicad-cli` talks to KiCad three different ways, and they do not have the same
lifetime. This page records which versions have actually been exercised, and
which of the three routes each command depends on.

## Verified backend versions

| KiCad | Status | Notes |
|-------|--------|-------|
| 10.0.6 | Verified | Every command in `reference` is run against this version by the test suite, on Windows. |
| 10.0.x (other patches) | Expected to work | Same file format and same `pcbnew` API surface. Not exercised. |
| 9.x | Unverified | The board file format differs. `sch relink` deliberately edits the board as text rather than saving through `pcbnew`, because saving a KiCad 9 file through a KiCad 10 `pcbnew` silently migrates it to the 10 format — a change the caller did not ask for. |
| 11.x | Will break | See below. |

The version actually in use is reported by `kicad-cli context`, which resolves
the interpreter and the official binary and prints both. Do not infer it.

## The three routes, and which one each command uses

| Route | How | Used by | Lifetime |
|-------|-----|---------|----------|
| File format | Read and write the `.kicad_pcb` / `.kicad_sch` text directly | `sch link`, `sch relink` | Stable. Independent of any KiCad API. |
| `pcbnew` (SWIG) | Run under KiCad's bundled Python | `board audit`, `board plane`, `board parity`, `board route`, `board rewidth`, `board widen`, `board stitch`, `board move`, `fab *` | **Scheduled for removal in KiCad 11.** |
| Official binary | Shell out to KiCad's own `kicad-cli` | ERC and DRC oracles behind `sch audit`, `sch sync-preview`, and every write command's self-verification | Stable. |
| IPC API | `kipy` over KiCad's API server | `board live` | Young. Requires KiCad running with the API enabled under Preferences → KiCad API. |

The SWIG row is the exposure. Most commands depend on it, and KiCad has
announced its removal. When that lands, those commands need porting to the IPC
API — which today cannot create or open a document, read DRC results, or place
a footprint from a library, so the port is not a straight substitution.

## Known upstream gaps

Recorded here because each one looks like a bug in this tool and is not:

- **No IPC command creates or opens a document.** None of the registered
  handlers do it. `board live` acts on a board the user already has open.
- **`ParseAndCreateItemsFromString` is registered and not implemented** in
  10.0.6. It accepts s-expression text, returns success, and creates nothing.
  So does `UpdateBoardStackup`. Verify by reading back, not by checking status.
- **No IPC command reads DRC results.** DRC verification goes through the
  official binary instead.
- **"Update PCB from Schematic" has no headless entry point.** KiCad implements
  it, SWIG does not bind it, and the official CLI has no subcommand for it.
  Predicting its outcome is pure computation, which is what `sch sync-preview`
  does; performing it on an *existing* board still needs the GUI. Building a
  board from a netlist does not: `board from-netlist` loads footprints, places
  them and joins pads into nets through pcbnew directly. What remains missing
  is the incremental case -- reconciling a board that already has placement and
  routing against a changed schematic, which is what the dialog is really for.
- **`pcb drc --schematic-parity` matches by reference designator**, while the
  update dialog matches by uuid path. A board whose links are all broken but
  whose designators are all correct reads as perfectly in sync.

## Platforms

| Platform | Status |
|----------|--------|
| Windows | Fully verified. The development machine: the whole suite runs there against a real KiCad, and the frozen binary is smoke-tested. |
| macOS, Linux | Partly verified. CI runs on `ubuntu-latest` and `macos-latest` across Python 3.10, 3.11 and 3.12: 54 tests pass, 47 skip. What passes is everything that does not need KiCad — the envelope, the argument gate, the schemas, the exit codes, and the whole substituted-upstream suite. What skips is every test that needs a real KiCad, because the runners have none. So the tool starts, parses and answers correctly on all three platforms; whether `pcbnew` behaves the same there is still unproven. |
