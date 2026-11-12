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

A run in a live record counts as evidence until the command's registration or the adapter code that run used changes; a new version number alone changes nothing. Records never hold secrets, and arguments that carry secrets show as `***`.
<!-- driver:spec-procedure:end -->

`python tests/live/run_all.py --targets <listed folder> [command ...]` runs every matrix the way the live records were made. Rows that need a prepared situation run alone with `--row`. A `read_back_mismatch` row runs with `--tamper`, which changes the result while kicad-cli waits before its read-back: `--tamper "{python} tests/live/disturb.py delete"` deletes the files a create, export or render made, and `--tamper "{python} tests/live/disturb.py restore <pristine copy>"` puts the original bytes back over a file a change wrote, as another program writing the same file would; `{python}` is the interpreter `run_matrix` runs on. A `conflict` row that expects `E_CONFLICT` with `file_open_in_session` runs while the file is open in KiCad, so that KiCad's lock file is beside it. An `ambiguous` row of a schematic or board command runs with `--set doc=` naming the practice copy that has a duplicated reference.

## Evidence Records

The live records in `docs/evidence/`, generated by `python scripts/spec.py generate`:

<!-- driver:evidence:start -->
| Record | Recorded at | Application | Platform | Result |
|---|---|---|---|---|
| [2026-11-12-122657-zone-delete.json](evidence/2026-11-12-122657-zone-delete.json) | 2026-11-12T12:26:57Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122645-zone-delete.json](evidence/2026-11-12-122645-zone-delete.json) | 2026-11-12T12:26:45Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122632-zone-delete.json](evidence/2026-11-12-122632-zone-delete.json) | 2026-11-12T12:26:32Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122612-zone-delete.json](evidence/2026-11-12-122612-zone-delete.json) | 2026-11-12T12:26:12Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-122556-zone-create.json](evidence/2026-11-12-122556-zone-create.json) | 2026-11-12T12:25:56Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122542-zone-create.json](evidence/2026-11-12-122542-zone-create.json) | 2026-11-12T12:25:42Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122534-zone-create.json](evidence/2026-11-12-122534-zone-create.json) | 2026-11-12T12:25:34Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122515-zone-create.json](evidence/2026-11-12-122515-zone-create.json) | 2026-11-12T12:25:15Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-122501-zone-list.json](evidence/2026-11-12-122501-zone-list.json) | 2026-11-12T12:25:01Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-122456-via-create.json](evidence/2026-11-12-122456-via-create.json) | 2026-11-12T12:24:56Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122432-via-create.json](evidence/2026-11-12-122432-via-create.json) | 2026-11-12T12:24:32Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122413-via-create.json](evidence/2026-11-12-122413-via-create.json) | 2026-11-12T12:24:13Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122343-via-create.json](evidence/2026-11-12-122343-via-create.json) | 2026-11-12T12:23:43Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-122316-footprint-update.json](evidence/2026-11-12-122316-footprint-update.json) | 2026-11-12T12:23:16Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122313-footprint-update.json](evidence/2026-11-12-122313-footprint-update.json) | 2026-11-12T12:23:13Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122310-footprint-update.json](evidence/2026-11-12-122310-footprint-update.json) | 2026-11-12T12:23:10Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122303-footprint-update.json](evidence/2026-11-12-122303-footprint-update.json) | 2026-11-12T12:23:03Z | KiCad 10.0.6 | windows | 5 of 5 runs passed |
| [2026-11-12-122256-board-update.json](evidence/2026-11-12-122256-board-update.json) | 2026-11-12T12:22:56Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122241-board-update.json](evidence/2026-11-12-122241-board-update.json) | 2026-11-12T12:22:41Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122233-board-update.json](evidence/2026-11-12-122233-board-update.json) | 2026-11-12T12:22:33Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122215-board-update.json](evidence/2026-11-12-122215-board-update.json) | 2026-11-12T12:22:15Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-122200-track-delete.json](evidence/2026-11-12-122200-track-delete.json) | 2026-11-12T12:22:00Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122157-track-delete.json](evidence/2026-11-12-122157-track-delete.json) | 2026-11-12T12:21:57Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122154-track-delete.json](evidence/2026-11-12-122154-track-delete.json) | 2026-11-12T12:21:54Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122146-track-delete.json](evidence/2026-11-12-122146-track-delete.json) | 2026-11-12T12:21:46Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-122142-track-create.json](evidence/2026-11-12-122142-track-create.json) | 2026-11-12T12:21:42Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122128-track-create.json](evidence/2026-11-12-122128-track-create.json) | 2026-11-12T12:21:28Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122120-track-create.json](evidence/2026-11-12-122120-track-create.json) | 2026-11-12T12:21:20Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122101-track-create.json](evidence/2026-11-12-122101-track-create.json) | 2026-11-12T12:21:01Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-122046-footprint-move.json](evidence/2026-11-12-122046-footprint-move.json) | 2026-11-12T12:20:46Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122032-footprint-move.json](evidence/2026-11-12-122032-footprint-move.json) | 2026-11-12T12:20:32Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122024-footprint-move.json](evidence/2026-11-12-122024-footprint-move.json) | 2026-11-12T12:20:24Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-122012-footprint-move.json](evidence/2026-11-12-122012-footprint-move.json) | 2026-11-12T12:20:12Z | KiCad 10.0.6 | windows | 5 of 5 runs passed |
| [2026-11-12-121954-board-get.json](evidence/2026-11-12-121954-board-get.json) | 2026-11-12T12:19:54Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-121951-netlist-import.json](evidence/2026-11-12-121951-netlist-import.json) | 2026-11-12T12:19:51Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121946-netlist-import.json](evidence/2026-11-12-121946-netlist-import.json) | 2026-11-12T12:19:46Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121943-netlist-import.json](evidence/2026-11-12-121943-netlist-import.json) | 2026-11-12T12:19:43Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121933-netlist-import.json](evidence/2026-11-12-121933-netlist-import.json) | 2026-11-12T12:19:33Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121926-footprint-create.json](evidence/2026-11-12-121926-footprint-create.json) | 2026-11-12T12:19:26Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121923-footprint-create.json](evidence/2026-11-12-121923-footprint-create.json) | 2026-11-12T12:19:23Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121921-footprint-create.json](evidence/2026-11-12-121921-footprint-create.json) | 2026-11-12T12:19:21Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121917-board-render.json](evidence/2026-11-12-121917-board-render.json) | 2026-11-12T12:19:17Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121913-board-render.json](evidence/2026-11-12-121913-board-render.json) | 2026-11-12T12:19:13Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121911-board-render.json](evidence/2026-11-12-121911-board-render.json) | 2026-11-12T12:19:11Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-121907-board-export.json](evidence/2026-11-12-121907-board-export.json) | 2026-11-12T12:19:07Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121903-board-export.json](evidence/2026-11-12-121903-board-export.json) | 2026-11-12T12:19:03Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121901-board-export.json](evidence/2026-11-12-121901-board-export.json) | 2026-11-12T12:19:01Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-121857-track-list.json](evidence/2026-11-12-121857-track-list.json) | 2026-11-12T12:18:57Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121852-footprint-list.json](evidence/2026-11-12-121852-footprint-list.json) | 2026-11-12T12:18:52Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121848-board-check.json](evidence/2026-11-12-121848-board-check.json) | 2026-11-12T12:18:48Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121836-board-create.json](evidence/2026-11-12-121836-board-create.json) | 2026-11-12T12:18:36Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121831-board-create.json](evidence/2026-11-12-121831-board-create.json) | 2026-11-12T12:18:31Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121828-board-create.json](evidence/2026-11-12-121828-board-create.json) | 2026-11-12T12:18:28Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121807-net-update.json](evidence/2026-11-12-121807-net-update.json) | 2026-11-12T12:18:07Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121803-net-update.json](evidence/2026-11-12-121803-net-update.json) | 2026-11-12T12:18:03Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121800-net-update.json](evidence/2026-11-12-121800-net-update.json) | 2026-11-12T12:18:00Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121754-net-update.json](evidence/2026-11-12-121754-net-update.json) | 2026-11-12T12:17:54Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-121749-pin-update.json](evidence/2026-11-12-121749-pin-update.json) | 2026-11-12T12:17:49Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121745-pin-update.json](evidence/2026-11-12-121745-pin-update.json) | 2026-11-12T12:17:45Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121742-pin-update.json](evidence/2026-11-12-121742-pin-update.json) | 2026-11-12T12:17:42Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121736-pin-update.json](evidence/2026-11-12-121736-pin-update.json) | 2026-11-12T12:17:36Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121734-pin-update.json](evidence/2026-11-12-121734-pin-update.json) | 2026-11-12T12:17:34Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121729-symbol-delete.json](evidence/2026-11-12-121729-symbol-delete.json) | 2026-11-12T12:17:29Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121725-symbol-delete.json](evidence/2026-11-12-121725-symbol-delete.json) | 2026-11-12T12:17:25Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121722-symbol-delete.json](evidence/2026-11-12-121722-symbol-delete.json) | 2026-11-12T12:17:22Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121716-symbol-delete.json](evidence/2026-11-12-121716-symbol-delete.json) | 2026-11-12T12:17:16Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121715-symbol-delete.json](evidence/2026-11-12-121715-symbol-delete.json) | 2026-11-12T12:17:15Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121709-symbol-move.json](evidence/2026-11-12-121709-symbol-move.json) | 2026-11-12T12:17:09Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121706-symbol-move.json](evidence/2026-11-12-121706-symbol-move.json) | 2026-11-12T12:17:06Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121703-symbol-move.json](evidence/2026-11-12-121703-symbol-move.json) | 2026-11-12T12:17:03Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121657-symbol-move.json](evidence/2026-11-12-121657-symbol-move.json) | 2026-11-12T12:16:57Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121656-symbol-move.json](evidence/2026-11-12-121656-symbol-move.json) | 2026-11-12T12:16:56Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121651-symbol-update.json](evidence/2026-11-12-121651-symbol-update.json) | 2026-11-12T12:16:51Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121647-symbol-update.json](evidence/2026-11-12-121647-symbol-update.json) | 2026-11-12T12:16:47Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121644-symbol-update.json](evidence/2026-11-12-121644-symbol-update.json) | 2026-11-12T12:16:44Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121638-symbol-update.json](evidence/2026-11-12-121638-symbol-update.json) | 2026-11-12T12:16:38Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121636-symbol-update.json](evidence/2026-11-12-121636-symbol-update.json) | 2026-11-12T12:16:36Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121631-symbol-create.json](evidence/2026-11-12-121631-symbol-create.json) | 2026-11-12T12:16:31Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121628-symbol-create.json](evidence/2026-11-12-121628-symbol-create.json) | 2026-11-12T12:16:28Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121625-symbol-create.json](evidence/2026-11-12-121625-symbol-create.json) | 2026-11-12T12:16:25Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121618-symbol-create.json](evidence/2026-11-12-121618-symbol-create.json) | 2026-11-12T12:16:18Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-121613-schematic-create.json](evidence/2026-11-12-121613-schematic-create.json) | 2026-11-12T12:16:13Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121610-schematic-create.json](evidence/2026-11-12-121610-schematic-create.json) | 2026-11-12T12:16:10Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121608-schematic-create.json](evidence/2026-11-12-121608-schematic-create.json) | 2026-11-12T12:16:08Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121604-schematic-render.json](evidence/2026-11-12-121604-schematic-render.json) | 2026-11-12T12:16:04Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121600-schematic-render.json](evidence/2026-11-12-121600-schematic-render.json) | 2026-11-12T12:16:00Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121558-schematic-render.json](evidence/2026-11-12-121558-schematic-render.json) | 2026-11-12T12:15:58Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-121553-schematic-export.json](evidence/2026-11-12-121553-schematic-export.json) | 2026-11-12T12:15:53Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121550-schematic-export.json](evidence/2026-11-12-121550-schematic-export.json) | 2026-11-12T12:15:50Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121548-schematic-export.json](evidence/2026-11-12-121548-schematic-export.json) | 2026-11-12T12:15:48Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-121544-symbol-get.json](evidence/2026-11-12-121544-symbol-get.json) | 2026-11-12T12:15:44Z | KiCad 10.0.6 | windows | 1 of 1 runs passed |
| [2026-11-12-121542-symbol-get.json](evidence/2026-11-12-121542-symbol-get.json) | 2026-11-12T12:15:42Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-121538-net-list.json](evidence/2026-11-12-121538-net-list.json) | 2026-11-12T12:15:38Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121534-symbol-list.json](evidence/2026-11-12-121534-symbol-list.json) | 2026-11-12T12:15:34Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121529-schematic-check.json](evidence/2026-11-12-121529-schematic-check.json) | 2026-11-12T12:15:29Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121518-doc-list.json](evidence/2026-11-12-121518-doc-list.json) | 2026-11-12T12:15:18Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-121514-doc-get.json](evidence/2026-11-12-121514-doc-get.json) | 2026-11-12T12:15:14Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
| [2026-11-12-121511-library-get.json](evidence/2026-11-12-121511-library-get.json) | 2026-11-12T12:15:11Z | KiCad 10.0.6 | windows | 5 of 5 runs passed |
| [2026-11-12-121504-library-list.json](evidence/2026-11-12-121504-library-list.json) | 2026-11-12T12:15:04Z | KiCad 10.0.6 | windows | 4 of 4 runs passed |
| [2026-11-12-121459-project-get.json](evidence/2026-11-12-121459-project-get.json) | 2026-11-12T12:14:59Z | KiCad 10.0.6 | windows | 3 of 3 runs passed |
<!-- driver:evidence:end -->
