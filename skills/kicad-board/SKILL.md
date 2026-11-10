---
name: kicad-board
description: >-
  Works on KiCad boards with kicad-cli: creates a board from the schematic,
  brings schematic changes over, reads and sets the board outline, places and
  turns footprints, moves reference texts, routes with tracks, vias and copper
  zones, defines footprints in a project library, runs KiCad's DRC with
  schematic parity, and exports Gerber and drill files and PDF or SVG plots.
  Not for drawing schematics; use kicad-schematic, and kicad-cli first for
  every task. kicad-cli has no placement or routing algorithm: the Agent
  decides where parts and tracks go.
  布板相关：从原理图新建 PCB、同步原理图改动、读取和设置板框、摆放和旋转封装、
  移动丝印位号、用走线、过孔和铺铜布线、在工程库里定义封装、运行带原理图一致性
  检查的 KiCad DRC、导出 Gerber、钻孔文件和 PDF、SVG 图纸。不负责画原理图，请用
  kicad-schematic；任何任务都先读 kicad-cli。kicad-cli 没有自动布局布线，器件和
  走线放在哪里由 Agent 决定。
metadata:
  version: 1.1.0
  commands: [board create, board get, board update, board check, board export, board render, netlist import, footprint list, footprint create, footprint move, footprint update, track list, track create, track delete, via create, zone list, zone create, zone delete]
---

# kicad-board

Read the entry Skill `kicad-cli` first and follow its write recipe, STOP checkpoints and report. If it is not installed, stop and ask the user to reinstall the Skills of kicad-cli.

## Commands

<!-- driver:command-map:start -->
- `board`: create, get, update, check, export, render
- `netlist`: import
- `footprint`: list, create, move, update
- `track`: list, create, delete (dangerous)
- `via`: create
- `zone`: list, create, delete (dangerous)
<!-- driver:command-map:end -->

## Knowledge

- Before a check or an export, read `kicad-cli knowledge get --id run-checks-in-the-project-folder` and `kicad-cli knowledge get --id zone-fills`.
- Before fabrication outputs, read `kicad-cli knowledge get --id plot-origin` and `kicad-cli knowledge get --id checks-are-not-sign-off`.
- For anything else, run `kicad-cli knowledge list --applies-to <command>`.

## Playbooks

### Create the board

1. The schematic is saved and checked (`schematic check`).
2. Run `board create --doc <schematic>`; it takes a fresh netlist from KiCad and writes the board beside the schematic, or to `--output`. It places the footprints on a grid inside a rectangular outline (`--width`, `--height`). KiCad pairs a board with the schematic of the same name: with another `--output` name the board is not the project's, and `netlist import` then needs `--schematic`.
3. Read the result with `footprint list`.

### Lay out a board

Read `kicad-cli knowledge get --id placement-method` and follow it: place the parts, fit the outline, check.

### Route a board

Read `kicad-cli knowledge get --id routing-method` and follow it until `board check` reports no errors and no unconnected items. `track delete` and `zone delete` are dangerous: use them only when the user asked for it, or to undo copper you added in this task.

### Tidy reference texts

Follow step 7 of `kicad-cli knowledge get --id placement-method`.

### Fabrication outputs

1. Run `board check` and report every error to the person before exporting.
2. Export Gerber and drill files into one folder: `board export --file-type gerber --output <folder>` and `board export --file-type drill --output <folder>`.
3. Plot drawings with `board render --file-type pdf --output <file.pdf>`.
