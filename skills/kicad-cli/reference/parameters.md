# Accepted values and defaults

`kicad-cli reference` declares each parameter's **name**, type and whether it is
required. It does not carry values, defaults or enums, so those live here.

**Names come from `reference`. Values and defaults come from this file.** If the
two ever disagree about a name, `reference` wins — it is generated from the
dispatch table, this file is written by hand.

An option a command does not declare is refused with `E_USAGE` (exit 2), and
`error.details.accepted` lists what it does take. That refusal exists because
`board rewidth --nets VSYS` used to be accepted and silently ignored: `rewidth`
has no `--nets`, so the tool issued a confirm token for a plan that re-routed
the default netclasses instead.

## Board writes

| Command | Option | Values | Default |
|---|---|---|---|
| `board route` | `--mode` | `repair` · `full` · `rewidth` | `repair` |
| | `--nets` | comma-separated net names | all unconnected |
| | `--classes` | comma-separated netclass names | `PWR_MAIN,BTL_OUT,SWITCH` |
| | `--neck` | mm, the narrowest width routing may fall back to | `0.20` |
| | `--ripup` | flag: tear up and re-route rather than only filling gaps | off |
| `board stitch` | `--net` | net name of the pour | `GND` |
| | `--min-area` | mm², islands smaller than this are treated as dead copper | `0.5` |
| | `--bridge` | flag: also bridge islands across layers | off |
| `board rewidth` | `--classes` | comma-separated netclass names | `PWR_MAIN,BTL_OUT,SWITCH` |
| | `--neck` | mm | `0.20` |
| `board widen` | `--oz` | copper weight, oz | `1.0` |
| `board move` | `--moves` | `"U1:120.5,60.0; C3:118,62"` — millimetres, board coordinates | required |

`--classes` defaults name the netclasses of the board this tool was built
against. **On any other board they are almost certainly wrong — pass them
explicitly.** Read the real ones from `board audit`'s `width_compliance`.

Every board write also takes `--ignore-lock` (see the checkpoint in `SKILL.md`),
and `board route` / `board stitch` / `board widen` take `--no-verify`
(and `board route` additionally `--no-restore`) — never pass those unasked.

## Reads

| Command | Option | Values | Default |
|---|---|---|---|
| `board audit` | `--oz` | copper weight, oz | `1.0` |
| | `--dt` | permitted temperature rise, K | `10` |
| `board plane` | `--step` | mm between samples along a track | `0.5` |
| `sch audit` | `--schematic` | path | `<board>.kicad_sch` |
| `sch sync-preview` | `--schematic` | path | `<board>.kicad_sch` |

`--oz` and `--dt` are **assumptions, not measurements**. Every ampacity number
downstream depends on them. The output echoes `copper_oz` and `delta_t_c` —
check them against the real stackup before quoting any current figure.

## Fabrication

| Command | Option | Values | Default |
|---|---|---|---|
| `fab gerber` / `pdf` / `svg` / `dxf` | `--layers` | comma-separated layer names, one string | project's plot set |
| | `--out` | directory | `<board dir>/fab` |
| `fab drill` | `--map` | `pdf` · `gerber` · `svg` · `none` | `pdf` |
| | `--merge` | flag: one file for PTH and NPTH | off (separate) |
| | `--inch` | flag | off (metric) |
| | `--aux-origin` | flag: coordinates from the drill origin | off (absolute) |

Plotting reuses the project's own plot settings unchanged. Drill settings are
not stored anywhere readable, so the values above are the tool's, and it echoes
them in `settings` — quote from there, not from here.

## Status values

Also absent from `reference`:

| Schema | `status` |
|---|---|
| `sch_link`, `sch_audit`, `board_plane`, `board_parity` | `PASS` · `FAIL` |
| `sch_relink` | `PASS` · `PARTIAL` · `NOOP` |
| `sch_sync_preview` | `CLEAN` · `DESTRUCTIVE` |

`board_sync_preview`'s `CLEAN` means no footprint is added or removed. It does
**not** mean nothing happens — field updates are counted separately.
