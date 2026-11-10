---
name: kicad-schematic
description: >-
  Draws and changes KiCad schematics with kicad-cli: creates a schematic,
  places and edits symbols, joins pins to nets, renames nets, defines symbols
  in a project library, runs KiCad's electrical rules check and exports a
  netlist or PDF and SVG drawings. Not for boards, DRC or fabrication files;
  use kicad-board for those, and kicad-cli first for every task.
  画原理图、改原理图：新建原理图、放置和修改符号、把引脚接到网络、给网络改名、
  在工程库里定义符号、运行 KiCad 的 ERC、导出网表和 PDF、SVG 图纸。
  不负责 PCB、DRC 和投板文件，这些请用 kicad-board；任何任务都先读 kicad-cli。
metadata:
  version: 1.1.0
  commands: [schematic create, schematic check, schematic export, schematic render, symbol list, symbol get, symbol create, symbol update, symbol move, symbol delete, pin update, net list, net update]
---

# kicad-schematic

Read the entry Skill `kicad-cli` first and follow its write recipe, STOP checkpoints and report. If it is not installed, stop and ask the user to reinstall the Skills of kicad-cli.

## Commands

<!-- driver:command-map:start -->
- `schematic`: create, check, export, render
- `symbol`: list, get, create, update, move, delete (dangerous)
- `pin`: update
- `net`: list, update
<!-- driver:command-map:end -->

## Knowledge

- Before placing or moving symbols, read `kicad-cli knowledge get --id schematic-grid`.
- Before joining pins or renaming nets, read `kicad-cli knowledge get --id net-names-and-labels`.
- Before reporting a check, read `kicad-cli knowledge get --id checks-are-not-sign-off`.
- For anything else, run `kicad-cli knowledge list --applies-to <command>`.

## Playbooks

### Draw a circuit

1. The person closes the schematic in KiCad; kicad-cli changes files only.
2. For a new design, run `schematic create --doc <project .kicad_pro>`; it makes the project file when missing, and `--output` names another sheet file.
3. Find each part with `library get --id <library> --query <text>` and place it with `symbol create`, on the 1.27 mm grid. Place several parts in one call with `--input`.
4. Join pins to nets with `pin update --id <ref>.<pin> --net <name>`, several pins in one call with `--input`; mark pins that stay open with `--no-connect`.
5. Run `schematic check` and fix what it reports, or report the findings to the person.

### Change parts

1. Find them with `symbol list --query <text>` and read one with `symbol get --id <ref>`.
2. Change fields with `symbol update`, the same values for several parts with `--ids`, or per-part values with `--input`.
3. Read them back with `symbol get`; when footprints changed, bring the change to the board with the playbook of `kicad-cli`.

### Export

1. `schematic export --file-type netlist --output <file.net>` writes KiCad's netlist.
2. `schematic render --file-type pdf --output <file.pdf>` writes a PDF of every sheet; `--file-type svg` writes one SVG per sheet into a folder.
