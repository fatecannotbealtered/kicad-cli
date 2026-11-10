# Live Acceptance

English | [中文](E2E_zh.md)

Live runs execute the behavior matrices of kicad-cli on the real KiCad, with the real adapter. They make real changes, so they use only the targets authorized below.

## Authorization Rules

Authorized Targets in this file is the only list of the files, folders and devices that live runs, and evaluations on the real KiCad, may use. A target is added only by a person, or by a coding Agent when the person's own message grants it; the Agent then tells the person the line it added, and keeps the descriptions up to date itself. Each target is one list item that starts with the target in backticks, written the way the scope file writes targets: a `file://` URL of a file or folder, or `device:<id>` for a device, followed by a colon and a description of the target. A listed folder is a work folder: inside it, the coding Agent makes practice copies, output folders and evaluation workspaces without asking again. An instance of KiCad has no lasting ID, so live runs use only instances started for the run, by a person or by the coding Agent, with documents from the listed files and folders; the coding Agent closes only the instances it started. Live runs use no other target.

Scope files for live runs grant only listed targets and the new documents of the instances started for the run. The matrix and evaluation runners write the scope files of their runs; for other live work, such as trying a command or installing the host component, the coding Agent writes its own without asking. A target that an evaluation question's `SCOPE` names is listed too.

## Authorized Targets

- `file:///D:/driver-work/kicad-cli/targets/`: the work folder of the live runs; `tests/live/run_all.py` makes the practice copies there from `tests/live/targets`: a Schmitt-trigger project (schmitt, schmitt-sch, schmitt-dup, schmitt-eco) and a board of about 15 parts with through-hole connectors (layout-practice); live runs of the behavior matrices change them.
- `file:///D:/driver-work/kicad-cli/eval/`: the folder for the evaluation workspaces.

## Procedure

Live runs are made on these machines:

- One Windows 11 x64 machine with KiCad 10.0.6, in file mode. macOS and Linux, and arm64 on every system, have no live runs yet; the support matrix marks them untested.

<!-- driver:spec-procedure:start -->
To run a behavior matrix on the real KiCad:

1. Prepare the targets, such as a copy of a project made for testing in a listed folder, an instance started for the run or a device set up for testing. A target outside the listed ones is added under Authorized Targets first, as Authorization Rules says.
2. Set `KICAD_CLI_STATE_DIR` to a separate directory for live runs.
3. Run `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>`, with one `--set` for each placeholder the matrix uses, named without braces, such as `--set doc=<file>`: `{session}` takes an instance started for the run, `{doc}` an authorized document or file, `{device}` an authorized device, and `{dir}` a new empty directory inside an authorized folder. `{job}` takes a job ID; without it, the runner first runs the `normal` row of the long command that `--job-from` names, or of the only long command, and uses the job it starts. `{inputs}` needs no `--set`: it is `tests/inputs/`, whose files commands only read, so they need no authorization.
4. The runner runs the rows that apply, writes a live record to `docs/evidence/` and fills in the actual result and the `live:` evidence reference of each row. A row whose actual result differs from the expected one has failed.
5. A row that cannot be caused on demand on the real KiCad, such as `disconnect`, may be marked `unrunnable` with the reason; the `normal`, `dialog` and `read_back_mismatch` rows never may. For `read_back_mismatch`, run the matrix with `--row read_back_mismatch`: the tool waits before its read-back, and in that time change what it just wrote in KiCad, or delete the file it made.
6. Run `python scripts/spec.py generate` to update the support matrix and the index below, then commit the records with the matrices.

To run the evaluation questions on the real KiCad, run `python scripts/spec.py eval --workspace-root <folder>` with a folder inside a listed folder. The runner writes each question's scope file and writes the evaluation report to `docs/evidence/`.

A run in a live record counts as evidence until the command's registration or the adapter's code changes; a new version number alone changes nothing. Records never hold secrets, and arguments that carry secrets show as `***`.
<!-- driver:spec-procedure:end -->

`python tests/live/run_all.py --targets <listed folder> [command ...]` runs every matrix the way the live records were made. Rows that need a prepared situation run alone with `--row`. A `read_back_mismatch` row runs with `--tamper`, which changes the result while kicad-cli waits before its read-back: `--tamper "{python} tests/live/disturb.py delete"` deletes the files a create, export or render made, and `--tamper "{python} tests/live/disturb.py restore <pristine copy>"` puts the original bytes back over a file a change wrote, as another program writing the same file would; `{python}` is the interpreter `run_matrix` runs on. A `conflict` row that expects `E_CONFLICT` with `file_open_in_session` runs while the file is open in KiCad, so that KiCad's lock file is beside it. An `ambiguous` row of a schematic or board command runs with `--set doc=` naming the practice copy that has a duplicated reference.

## Evidence Records

The live records in `docs/evidence/`, generated by `python scripts/spec.py generate`:

<!-- driver:evidence:start -->
| Record | Recorded at | Application | Platform | Result |
|---|---|---|---|---|
| [2026-11-09-183140-via-create.json](evidence/2026-11-09-183140-via-create.json) | 2026-11-09T18:31:40Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-183108-via-create.json](evidence/2026-11-09-183108-via-create.json) | 2026-11-09T18:31:08Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-183038-board-update.json](evidence/2026-11-09-183038-board-update.json) | 2026-11-09T18:30:38Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182938-zone-delete.json](evidence/2026-11-09-182938-zone-delete.json) | 2026-11-09T18:29:38Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182923-zone-delete.json](evidence/2026-11-09-182923-zone-delete.json) | 2026-11-09T18:29:23Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182907-zone-delete.json](evidence/2026-11-09-182907-zone-delete.json) | 2026-11-09T18:29:07Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182845-zone-delete.json](evidence/2026-11-09-182845-zone-delete.json) | 2026-11-09T18:28:45Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-182826-zone-create.json](evidence/2026-11-09-182826-zone-create.json) | 2026-11-09T18:28:26Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182811-zone-create.json](evidence/2026-11-09-182811-zone-create.json) | 2026-11-09T18:28:11Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182803-zone-create.json](evidence/2026-11-09-182803-zone-create.json) | 2026-11-09T18:28:03Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182741-zone-create.json](evidence/2026-11-09-182741-zone-create.json) | 2026-11-09T18:27:41Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-09-182725-zone-list.json](evidence/2026-11-09-182725-zone-list.json) | 2026-11-09T18:27:25Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-182713-via-create.json](evidence/2026-11-09-182713-via-create.json) | 2026-11-09T18:27:13Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182705-via-create.json](evidence/2026-11-09-182705-via-create.json) | 2026-11-09T18:27:05Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182647-via-create.json](evidence/2026-11-09-182647-via-create.json) | 2026-11-09T18:26:47Z | KiCad 10.0.6 | windows | 3 of 4 runs passed |
| [2026-11-09-182637-footprint-update.json](evidence/2026-11-09-182637-footprint-update.json) | 2026-11-09T18:26:37Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182634-footprint-update.json](evidence/2026-11-09-182634-footprint-update.json) | 2026-11-09T18:26:34Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182631-footprint-update.json](evidence/2026-11-09-182631-footprint-update.json) | 2026-11-09T18:26:31Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182619-footprint-update.json](evidence/2026-11-09-182619-footprint-update.json) | 2026-11-09T18:26:19Z | KiCad 10.0.6 | windows | 5 of 5 runs passed |
| [2026-11-09-182615-board-update.json](evidence/2026-11-09-182615-board-update.json) | 2026-11-09T18:26:15Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182600-board-update.json](evidence/2026-11-09-182600-board-update.json) | 2026-11-09T18:26:00Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182550-board-update.json](evidence/2026-11-09-182550-board-update.json) | 2026-11-09T18:25:50Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182522-board-update.json](evidence/2026-11-09-182522-board-update.json) | 2026-11-09T18:25:22Z | KiCad 10.0.6 | windows | 3 of 4 runs passed |
| [2026-11-09-182506-track-delete.json](evidence/2026-11-09-182506-track-delete.json) | 2026-11-09T18:25:06Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182503-track-delete.json](evidence/2026-11-09-182503-track-delete.json) | 2026-11-09T18:25:03Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182500-track-delete.json](evidence/2026-11-09-182500-track-delete.json) | 2026-11-09T18:25:00Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182449-track-delete.json](evidence/2026-11-09-182449-track-delete.json) | 2026-11-09T18:24:49Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-182446-track-create.json](evidence/2026-11-09-182446-track-create.json) | 2026-11-09T18:24:46Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182429-track-create.json](evidence/2026-11-09-182429-track-create.json) | 2026-11-09T18:24:29Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182420-track-create.json](evidence/2026-11-09-182420-track-create.json) | 2026-11-09T18:24:20Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182353-track-create.json](evidence/2026-11-09-182353-track-create.json) | 2026-11-09T18:23:53Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-09-182336-footprint-move.json](evidence/2026-11-09-182336-footprint-move.json) | 2026-11-09T18:23:36Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182317-footprint-move.json](evidence/2026-11-09-182317-footprint-move.json) | 2026-11-09T18:23:17Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182309-footprint-move.json](evidence/2026-11-09-182309-footprint-move.json) | 2026-11-09T18:23:09Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182254-footprint-move.json](evidence/2026-11-09-182254-footprint-move.json) | 2026-11-09T18:22:54Z | KiCad 10.0.6 | windows | 5 of 5 runs passed |
| [2026-11-09-182231-board-get.json](evidence/2026-11-09-182231-board-get.json) | 2026-11-09T18:22:31Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-09-182228-netlist-import.json](evidence/2026-11-09-182228-netlist-import.json) | 2026-11-09T18:22:28Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182223-netlist-import.json](evidence/2026-11-09-182223-netlist-import.json) | 2026-11-09T18:22:23Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182220-netlist-import.json](evidence/2026-11-09-182220-netlist-import.json) | 2026-11-09T18:22:20Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182210-netlist-import.json](evidence/2026-11-09-182210-netlist-import.json) | 2026-11-09T18:22:10Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-182205-footprint-create.json](evidence/2026-11-09-182205-footprint-create.json) | 2026-11-09T18:22:05Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182202-footprint-create.json](evidence/2026-11-09-182202-footprint-create.json) | 2026-11-09T18:22:02Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182200-footprint-create.json](evidence/2026-11-09-182200-footprint-create.json) | 2026-11-09T18:22:00Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-182157-board-render.json](evidence/2026-11-09-182157-board-render.json) | 2026-11-09T18:21:57Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182154-board-render.json](evidence/2026-11-09-182154-board-render.json) | 2026-11-09T18:21:54Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182152-board-render.json](evidence/2026-11-09-182152-board-render.json) | 2026-11-09T18:21:52Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-09-182149-board-export.json](evidence/2026-11-09-182149-board-export.json) | 2026-11-09T18:21:49Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182146-board-export.json](evidence/2026-11-09-182146-board-export.json) | 2026-11-09T18:21:46Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182144-board-export.json](evidence/2026-11-09-182144-board-export.json) | 2026-11-09T18:21:44Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-09-182141-track-list.json](evidence/2026-11-09-182141-track-list.json) | 2026-11-09T18:21:41Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-182139-footprint-list.json](evidence/2026-11-09-182139-footprint-list.json) | 2026-11-09T18:21:39Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-182136-board-check.json](evidence/2026-11-09-182136-board-check.json) | 2026-11-09T18:21:36Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-182126-board-create.json](evidence/2026-11-09-182126-board-create.json) | 2026-11-09T18:21:26Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182122-board-create.json](evidence/2026-11-09-182122-board-create.json) | 2026-11-09T18:21:22Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182117-board-create.json](evidence/2026-11-09-182117-board-create.json) | 2026-11-09T18:21:17Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-182104-schematic-render.json](evidence/2026-11-09-182104-schematic-render.json) | 2026-11-09T18:21:04Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182059-net-update.json](evidence/2026-11-09-182059-net-update.json) | 2026-11-09T18:20:59Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182056-symbol-delete.json](evidence/2026-11-09-182056-symbol-delete.json) | 2026-11-09T18:20:56Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182054-symbol-create.json](evidence/2026-11-09-182054-symbol-create.json) | 2026-11-09T18:20:54Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182052-schematic-create.json](evidence/2026-11-09-182052-schematic-create.json) | 2026-11-09T18:20:52Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182009-net-update.json](evidence/2026-11-09-182009-net-update.json) | 2026-11-09T18:20:09Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-182004-net-update.json](evidence/2026-11-09-182004-net-update.json) | 2026-11-09T18:20:04Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181958-net-update.json](evidence/2026-11-09-181958-net-update.json) | 2026-11-09T18:19:58Z | KiCad 10.0.6 | windows | 3 of 4 runs passed |
| [2026-11-09-181954-pin-update.json](evidence/2026-11-09-181954-pin-update.json) | 2026-11-09T18:19:54Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181949-pin-update.json](evidence/2026-11-09-181949-pin-update.json) | 2026-11-09T18:19:49Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181944-pin-update.json](evidence/2026-11-09-181944-pin-update.json) | 2026-11-09T18:19:44Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181942-pin-update.json](evidence/2026-11-09-181942-pin-update.json) | 2026-11-09T18:19:42Z | KiCad 10.0.6 | windows | 5 of 5 runs passed |
| [2026-11-09-181939-symbol-delete.json](evidence/2026-11-09-181939-symbol-delete.json) | 2026-11-09T18:19:39Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181935-symbol-delete.json](evidence/2026-11-09-181935-symbol-delete.json) | 2026-11-09T18:19:35Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181930-symbol-delete.json](evidence/2026-11-09-181930-symbol-delete.json) | 2026-11-09T18:19:30Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181928-symbol-delete.json](evidence/2026-11-09-181928-symbol-delete.json) | 2026-11-09T18:19:28Z | KiCad 10.0.6 | windows | 4 of 5 runs passed |
| [2026-11-09-181923-symbol-move.json](evidence/2026-11-09-181923-symbol-move.json) | 2026-11-09T18:19:23Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181918-symbol-move.json](evidence/2026-11-09-181918-symbol-move.json) | 2026-11-09T18:19:18Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181912-symbol-move.json](evidence/2026-11-09-181912-symbol-move.json) | 2026-11-09T18:19:12Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181910-symbol-move.json](evidence/2026-11-09-181910-symbol-move.json) | 2026-11-09T18:19:10Z | KiCad 10.0.6 | windows | 5 of 5 runs passed |
| [2026-11-09-181906-symbol-update.json](evidence/2026-11-09-181906-symbol-update.json) | 2026-11-09T18:19:06Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181901-symbol-update.json](evidence/2026-11-09-181901-symbol-update.json) | 2026-11-09T18:19:01Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181854-symbol-update.json](evidence/2026-11-09-181854-symbol-update.json) | 2026-11-09T18:18:54Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181853-symbol-update.json](evidence/2026-11-09-181853-symbol-update.json) | 2026-11-09T18:18:53Z | KiCad 10.0.6 | windows | 5 of 5 runs passed |
| [2026-11-09-181848-symbol-create.json](evidence/2026-11-09-181848-symbol-create.json) | 2026-11-09T18:18:48Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181843-symbol-create.json](evidence/2026-11-09-181843-symbol-create.json) | 2026-11-09T18:18:43Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181835-symbol-create.json](evidence/2026-11-09-181835-symbol-create.json) | 2026-11-09T18:18:35Z | KiCad 10.0.6 | windows | 3 of 4 runs passed |
| [2026-11-09-181830-schematic-create.json](evidence/2026-11-09-181830-schematic-create.json) | 2026-11-09T18:18:30Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181827-schematic-create.json](evidence/2026-11-09-181827-schematic-create.json) | 2026-11-09T18:18:27Z | KiCad 10.0.6 | windows | 4 of 5 runs passed |
| [2026-11-09-181819-schematic-render.json](evidence/2026-11-09-181819-schematic-render.json) | 2026-11-09T18:18:19Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-181814-schematic-export.json](evidence/2026-11-09-181814-schematic-export.json) | 2026-11-09T18:18:14Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181810-schematic-export.json](evidence/2026-11-09-181810-schematic-export.json) | 2026-11-09T18:18:10Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-181755-symbol-get.json](evidence/2026-11-09-181755-symbol-get.json) | 2026-11-09T18:17:55Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-09-181753-symbol-get.json](evidence/2026-11-09-181753-symbol-get.json) | 2026-11-09T18:17:53Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-09-181750-net-list.json](evidence/2026-11-09-181750-net-list.json) | 2026-11-09T18:17:50Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-181746-symbol-list.json](evidence/2026-11-09-181746-symbol-list.json) | 2026-11-09T18:17:46Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-181741-schematic-check.json](evidence/2026-11-09-181741-schematic-check.json) | 2026-11-09T18:17:41Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-181721-doc-list.json](evidence/2026-11-09-181721-doc-list.json) | 2026-11-09T18:17:21Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-09-181718-doc-get.json](evidence/2026-11-09-181718-doc-get.json) | 2026-11-09T18:17:18Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-09-181714-library-get.json](evidence/2026-11-09-181714-library-get.json) | 2026-11-09T18:17:14Z | KiCad 10.0.6 | windows | 5 of 5 runs passed |
| [2026-11-09-181710-library-list.json](evidence/2026-11-09-181710-library-list.json) | 2026-11-09T18:17:10Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-09-181706-project-get.json](evidence/2026-11-09-181706-project-get.json) | 2026-11-09T18:17:06Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
<!-- driver:evidence:end -->
