# KiCad Capability Survey

English | [中文](APPLICATION_zh.md)

This survey records what KiCad can do and how it can be automated. It is filled in before commands are designed, and every capability has a disposition: `provided` when a command of kicad-cli offers it.

## Application

- Name: KiCad
- Vendor: The KiCad developers (https://www.kicad.org); KiCad is free software under GPL-3.0-or-later
- Versions surveyed: KiCad 10.0.6 on Windows x64 (the installed program's help and version output) and the KiCad 10.0 source branch up to 10.0.8; kicad-cli supports >=10.0.6 <11

The operating systems and CPU architectures on which KiCad runs, or in device mode the device driver or SDK; the platform packages of kicad-cli follow from this table:

| Operating system | CPU architectures |
|---|---|
| Windows | x64, arm64 |
| macOS | x64, arm64 |
| Linux | x64, arm64 |

Source: https://www.kicad.org/download/windows/ (x86_64 and arm64 installers for Windows 10 and 11), https://www.kicad.org/download/macos/ (one universal disk image, macOS 12 or later), https://www.kicad.org/download/linux/ (distribution packages and the Ubuntu PPA for amd64 and arm64; Flathub for x86_64 and aarch64), checked 2026-11-04.

## Automation Interfaces

Every interface KiCad offers, and where code that calls it must run. An interface the CLI can call is used from the adapter layer in `kicad_cli/adapter/`. An interface that works only inside the process of KiCad or on its main thread needs a host component in `plugin/`.

| Interface | Called from | Notes |
|---|---|---|
| KiCad's file formats: `.kicad_sch`, `.kicad_sym`, `.kicad_pcb`, `.kicad_mod` (s-expressions), `.kicad_pro` (JSON), `sym-lib-table`, `fp-lib-table`, `.kicad_dru` | the CLI | Documented at https://dev-docs.kicad.org/en/file-formats/. KiCad 10.0 writes `.kicad_pcb`/`.kicad_mod` version 20260206 and `.kicad_pro` schema 3 (source: `pcb_io_kicad_sexpr.h`, `sch_file_versions.h`, `project_file.cpp` on the 10.0 branch). kicad-cli reads and edits them itself (`kicad_cli/adapter/fileformat/`); this is how every change is made. |
| KiCad's own command-line program `kicad-cli` (found inside the KiCad installation, never through PATH alone) | the CLI | `sch erc`, `pcb drc` (JSON reports; `--schematic-parity`, `--refill-zones`), `sch export netlist/pdf/svg/bom`, `pcb export gerbers/drill/pdf/svg/dxf/pos/step/...`, `version` (https://docs.kicad.org/10.0/en/cli/cli.html). The exports refill zones in memory with `--check-zones`. It has the same name as this tool. kicad-cli uses it for every check and export. |
| IPC API (protobuf over NNG; client library kicad-python `kipy`, MIT) | the CLI | Talks to a running KiCad. Off by default (Preferences > Plugins > Enable KiCad API). In KiCad 10 it covers the PCB Editor only; the schematic handler answers only `GetOpenDocuments`. It returns `AS_BUSY` both for a modal dialog and for a busy editor. Sources: https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/, `pcbnew/api/api_handler_pcb.cpp`, `eeschema/api/api_handler_sch.cpp` on the 10.0 branch; kipy 0.8.0. Not used by kicad-cli 1.0, which has no session mode. |
| pcbnew Python API (SWIG), run under KiCad's own Python | the CLI | Deprecated since 9.0 and removed in KiCad 11 (https://dev-docs.kicad.org/en/apis-and-binding/pcbnew/); runs only in KiCad's bundled interpreter. Not used: KiCad's own program does the plotting, drill files and zone refills it was used for. |
| Action plugins inside the PCB Editor | only inside the process | Built on the deprecated SWIG API; would need a host component. Not used. |

## Object Model

From the KiCad 10.0 manuals and file formats; these names are the command nouns.

- Project: the `.kicad_pro` file with its root schematic, its board, its project library tables (`sym-lib-table`, `fp-lib-table`), design rules (`.kicad_dru`) and net classes.
- Schematic: one or more sheets (`.kicad_sch`) in a hierarchy; a sheet holds symbols, wires, junctions, labels (local, global, hierarchical), no-connect flags, sheet symbols, text and graphics.
- Symbol: a placed instance of a library symbol, with a reference designator (`R1`), fields (Value, Footprint, Datasheet, user fields), units and pins; or, in a symbol library, the symbol's definition.
- Pin: a connection point of a symbol, by number, with a name and an electrical type.
- Net: a set of connected pins, named by labels or power symbols, or generated from a pin.
- Library: a symbol library (`.kicad_sym`, one file with many symbols) or a footprint library (`.pretty` folder, one `.kicad_mod` per footprint), registered by nickname in the user's or the project's library table.
- Board (PCB): the `.kicad_pcb` file with layers, outline (Edge.Cuts), footprints, tracks, arcs, vias, zones, text and graphics.
- Netlist: the connections of a schematic as a file, used to create and update the board.
- Footprint: a placed footprint on the board (reference, position, rotation, side, lock, pads with nets), or a footprint in a library.
- Track: a straight track segment or an arc on a copper layer, with a width and a net; a via joins layers.
- Zone: a copper zone with an outline, a net and stored fill polygons.

## Units and Coordinates

- Boards and footprints: internal unit 1 nm; files store millimetres with up to 6 decimals. Schematics and symbols: internal unit 100 nm; files store millimetres with up to 4 decimals. Source: `include/base_units.h`, https://dev-docs.kicad.org/en/file-formats/sexpr-intro/.
- The origin is the top-left corner of the page; x grows to the right and y grows downwards. Symbol library graphics are stored y-up. Plots and drill files can use the drill/place (auxiliary) origin.
- Angles are in degrees, counter-clockwise on screen; symbol rotations are 0, 90, 180 or 270.
- The schematic connection grid is 1.27 mm; a pin or wire end off this grid does not connect.
- Display units (mm, mil, inch) are a user setting and never appear in the files; kicad-cli takes units in every physical value and reports millimetres and degrees (DCLI-080 to DCLI-085).

## Threading and Blocking

- kicad-cli works on files: nothing runs on KiCad's main thread and no KiCad window is involved.
- KiCad's own program runs to completion and exits. ERC and DRC on small and medium designs take seconds; a large board can take minutes. kicad-cli gives a check 25 seconds and an export 40 seconds, so that every call returns within 60 seconds (DCLI-065), and reports `E_NATIVE_FAILURE` otherwise.
- KiCad's program shows no dialogs. A missing user configuration (KiCad never started) leaves the global library tables empty; checks then report library warnings.

## Read-back

- A change is read back by parsing the saved file again and comparing every changed field with the preview: board coordinates in whole nanometres (tolerance 1 nm), schematic coordinates on the 100 nm resolution of the file, angles and text exactly.
- ERC and DRC results are KiCad's JSON reports, identified by type, severity, item UUIDs and position. Exit code 0 only means the check ran.
- Exports: the produced files exist; the runtime records their size and SHA-256. Their content is KiCad's and is not interpreted.

## Save and Undo

- Writing the file is saving (DCLI-213). kicad-cli replaces each file whole (a temporary file renamed over it). A change that writes several files (a library and its table, several sheets) writes them one after another, not atomically.
- KiCad offers no undo for a file written outside its editors. kicad-cli has no undo; a move or a field change is undone by the same command with the old value.
- There is no save-as. Exports write new files.

## Instances and Concurrency

- Several KiCad instances can run. An editor that has a file open keeps a lock file `~<name>.lck` beside it (KiCad 10.0 source `include/lockfile.h`).
- A file written on disk while an editor has it open is overwritten at the editor's next save. kicad-cli therefore refuses to write a file with a lock file (`E_CONFLICT` with `file_open_in_session`: a person closes it first); reading it is allowed.
- A person can work in KiCad on other files at the same time.

## Licensing

- KiCad is free software under GPL-3.0-or-later. Automation needs no license and KiCad shows no license dialog.
- kicad-cli runs KiCad's own program as a separate process and does not link KiCad code.

## Capabilities

Each capability has one row with an ID `CAP-nnn`, unique in this survey, and one disposition: `provided` (a domain command offers it), `deferred` (worth offering, left for a later version), `excluded` (will not be offered, for example because it is unsafe or cannot be automated reliably) or `unknown` (not yet known whether KiCad allows it). Every row names the interface that reaches the capability; a row other than `provided` gives the reason in one line.

| ID | Capability | Interface | Disposition | Reason |
|---|---|---|---|---|
| CAP-001 | Read a project: its files, library tables, design rules and net classes | file formats | provided | - |
| CAP-002 | Read schematic symbols: reference, fields, pins, units, sheet | file formats | provided | - |
| CAP-003 | Read connectivity: nets and the pins or pads on them | file formats | provided | - |
| CAP-004 | Create an empty schematic (and its project file when absent) | file formats | provided | - |
| CAP-005 | Place a library symbol on a sheet | file formats | provided | - |
| CAP-006 | Change a symbol's reference, value, footprint or other fields | file formats | provided | - |
| CAP-007 | Move or rotate a symbol and keep its connections | file formats | provided | - |
| CAP-008 | Delete a symbol | file formats | provided | - |
| CAP-009 | Connect a pin to a net with a wire and a label, or mark it unused | file formats | provided | - |
| CAP-010 | Build hierarchical sheets and reuse a sheet | file formats | deferred | The previous implementation is incomplete |
| CAP-011 | Electrical rules check with KiCad's own checker | KiCad's `kicad-cli sch erc` | provided | - |
| CAP-012 | Export a schematic as a netlist, PDF or SVG | KiCad's `kicad-cli sch export` | provided | - |
| CAP-013 | Export a bill of materials | KiCad's `kicad-cli sch export bom` | deferred | Not offered yet; needs its own live verification |
| CAP-014 | List the libraries a project sees and what they contain | file formats | provided | - |
| CAP-015 | Create a project library and register it in the project's library table | file formats | provided | - |
| CAP-016 | Define a library symbol: one unit, rectangular body, explicit pins | file formats | provided | - |
| CAP-017 | Define a library footprint: front-side rectangular SMD pads, body and courtyard | file formats | provided | - |
| CAP-018 | Create a board from a fresh netlist with footprints from the libraries | KiCad's `kicad-cli sch export netlist` + file formats | provided | - |
| CAP-019 | Update the board from the schematic: footprints, values, nets, links | KiCad's `kicad-cli sch export netlist` + file formats | provided | - |
| CAP-020 | Read the footprints on a board: position, rotation, side, lock, courtyard, pads with their type and size, reference text | file formats | provided | - |
| CAP-021 | Move and turn footprints, checked by KiCad's DRC before the write | file formats + KiCad's `kicad-cli pcb drc` | provided | - |
| CAP-022 | Read tracks and vias | file formats | provided | - |
| CAP-023 | Add straight tracks and paths of them, and through vias, checked by KiCad's DRC before the write; remove straight segments and vias | file formats + KiCad's `kicad-cli pcb drc` | provided | - |
| CAP-024 | Design rules check with KiCad's own checker | KiCad's `kicad-cli pcb drc` | provided | - |
| CAP-025 | Export Gerber and drill files | KiCad's `kicad-cli pcb export gerbers/drill` | provided | - |
| CAP-026 | Plot a board as PDF or SVG | KiCad's `kicad-cli pcb export pdf/svg` | provided | - |
| CAP-027 | Change net classes and their net assignments | file formats (`.kicad_pro`) | deferred | Not offered yet |
| CAP-028 | Compare schematic and board | KiCad's `kicad-cli pcb drc --schematic-parity` | provided | - |
| CAP-029 | Refill copper zones in memory before checks and plots | KiCad's `kicad-cli` (`--refill-zones`, `--check-zones`) | provided | - |
| CAP-030 | List, create and delete copper zone outlines; KiCad fills them in memory for checks and plots | file formats + KiCad's `kicad-cli pcb drc` | provided | - |
| CAP-031 | Automatic footprint placement | the previous tool's own algorithm | excluded | Not a KiCad function; the Agent places parts with footprint move |
| CAP-032 | Automatic routing (grid router, Freerouting) | the previous tool's own router; Freerouting | excluded | Not a KiCad function; rolled back in most benchmark runs |
| CAP-033 | Track widening and neck-down | the previous tool's own algorithm | excluded | Not a KiCad function; never verified |
| CAP-034 | Automatic silkscreen reference placement | the previous tool's own algorithm | excluded | References landed nearer another part in 9 to 10 of 15 benchmark runs |
| CAP-035 | Ground stitching vias | the previous tool's own algorithm | excluded | Not a KiCad 10 function; never verified |
| CAP-036 | Current-capacity and reference-plane estimates | own calculations | excluded | Engineering estimates, not KiCad capabilities |
| CAP-037 | List running PCB Editors and their open boards; save a board | IPC API | deferred | kicad-cli has no session mode yet |
| CAP-038 | Change items of an open board, one undo step per change | IPC API | deferred | kicad-cli has no session mode yet |
| CAP-039 | Read or change an open schematic in a running Schematic Editor | IPC API | excluded | Not available in KiCad 10 |
| CAP-040 | Open or close a document in a running KiCad | IPC API | excluded | No such API command in KiCad 10 |
| CAP-041 | Start or close a KiCad instance | process launch | deferred | Not needed in file mode |
| CAP-042 | Undo the tool's last change in a running KiCad | IPC API | excluded | The API has no undo command |
| CAP-043 | Position files, STEP, DXF and other outputs | KiCad's `kicad-cli pcb export` | deferred | Not offered yet; each needs its own live verification |
| CAP-044 | Upgrade files to the current format | KiCad's `kicad-cli sch/pcb upgrade` | excluded | Rewrites user files in place; KiCad upgrades files when a person saves |
| CAP-045 | Edit global (non-project) libraries | file formats | excluded | Global libraries are shared by every project; only project libraries are changed |
| CAP-046 | Rename a net | file formats | provided | - |
| CAP-047 | Find duplicate or overlapping track segments | the previous tool's own analysis | excluded | Not a KiCad check; KiCad's DRC reports dangling tracks |
| CAP-049 | Read the board outline and set it to a rectangle, checked by KiCad's DRC before the write | file formats + KiCad's `kicad-cli pcb drc` | provided | - |
| CAP-050 | Move and turn footprints' reference texts | file formats | provided | - |
| CAP-051 | Flip a footprint to the other side | file formats | deferred | No task needs it yet; flipping mirrors every pad, drawing and text |
| CAP-048 | Refill zones and save the fills in the board file | KiCad's `kicad-cli pcb drc --refill-zones --save-board` | deferred | Not needed for checks and exports, which refill in memory |

## Risks

Each risk or limit found in the survey becomes an entry in `knowledge/`, or gives here the reason it is left out.

- This tool and KiCad's own program are both called `kicad-cli`. → `kicad-cli-name-clash`
- KiCad installed outside its standard folders is not found without a setting. → `kicad-not-found`
- A file written while KiCad has it open is lost at KiCad's next save. → `files-open-in-kicad`
- Changing a library does not refresh the symbols and footprints already placed. → `library-changes-do-not-refresh-instances`
- A change that writes several files is not atomic. → `multi-file-writes-are-not-atomic`
- Stored zone fills can be out of date. → `zone-fills`
- Checks need the project folder, its rules and library tables. → `run-checks-in-the-project-folder`
- A check or export on a large design can exceed the time a call may take. → `checks-time-limit`
- Design rules live in `.kicad_pro` and `.kicad_dru`, not in the board. → `design-rules-files`
- Net names come from labels and are local to a sheet unless global. → `net-names-and-labels`
- A pin or wire end off the 1.27 mm grid does not connect. → `schematic-grid`
- The plot origin decides where artwork and holes land. → `plot-origin`
- KiCad 10.0.6 reports `starved_thermal` when spokes reach pad-only islands. → `starved-thermal-in-10-0-6`
- Clean checks and successful exports are not a manufacturing approval. → `checks-are-not-sign-off`
- KiCad 11 is not supported. → `kicad-11-not-supported`
- A Flatpak KiCad is not found on Linux. → `flatpak-kicad`
- The IPC API is off by default, and `AS_BUSY` covers both dialogs and a busy editor: left out; kicad-cli does not use the IPC API.
- pcbnew assert dialogs and console noise: left out; kicad-cli does not use the pcbnew API.
- Live runs on real projects could damage them: left out; `docs/E2E.md` allows only authorized practice targets.
