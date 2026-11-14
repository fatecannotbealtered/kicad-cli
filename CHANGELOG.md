# Changelog

All notable changes to kicad-cli are recorded here, in the Keep a Changelog format; versions follow Semantic Versioning.

## [Unreleased]

### Changed

- Pinned to tool-driver-cli-spec 1.3.2: a write's reply that is too long is shortened rather than refused, `--fields` takes dotted names such as `receipt.outcome`, and an adapter value outside the contract is named in an `adapter_defect` notice. `doc undo` is not offered, since KiCad's files keep no undo history, so its new `undoes` needs no check.

## [1.1.0] - 2026-11-09

### Added

- `board get` reads a board's outline, copper layers and thickness, the box around all footprints' courtyards, the margin from it to each side of the outline and the footprints that stick out.
- `board update` sets the board outline to a rectangle after a KiCad DRC check; it replaces only an unlocked outline that is one rectangle, or none.
- `footprint update` moves and turns footprints' reference texts, given where they go on the board; its result names the courtyard nearest to each text.
- `track create --points` draws a path of straight segments through its corners, and `--input` draws several tracks with one DRC check.
- `via create` adds through vias after a KiCad DRC check, with the net class's size unless given, and says what each via's copper meets on each layer.
- `zone list`, `zone create` and `zone delete`: copper zones as outlines with their holes, which KiCad fills in memory for checks and exports. `zone create` uses KiCad's default zone settings, which its `--points` help lists.
- `footprint move --rotation` turns a footprint about its origin, as KiCad does: the pads and texts turn with it.
- `footprint list` gives each pad's type and the box its copper takes, and each footprint's reference text.
- Knowledge entries `placement-method` and `routing-method`.
- `tests/live/run_all.py` runs every behavior matrix on the real KiCad the way the live records were made, from the practice projects in `tests/live/targets`.

### Changed

- Pinned to tool-driver-cli-spec 1.3.0. Each item of a batch write (`--input`, `--ids`) carries its own result, as a single call returns it. When the person's own message grants what a write lacks, the Agent writes a scope file holding only that grant and says what it wrote; a request to do a task is not a grant. `docs/E2E.md` has the specification's fixed Authorization Rules section, and Authorized Targets holds only the list of targets.
- The practice targets in `docs/E2E.md` include layout-practice, a board of about 15 parts with through-hole connectors.
- `footprint move` moves any unlocked footprint with pads: through-hole, turned and back-side ones too. It refuses a footprint that holds a zone, a text box or a dimension, which KiCad writes where they lie on the board and which would stay behind.
- `track delete` deletes vias as well as straight segments; a via is named `/vias/<uuid>` in targets, as `via create` names it.
- A new track's UUID follows from its net, layer, width and points, so the items of a batch keep the UUIDs their preview showed; when the board already holds that UUID, because the same track was drawn before and then moved in KiCad, the track takes the next UUID that follows from them. A path that runs over one of its own segments, or two items that draw the same segment, are refused.
- `board check` lists by default the items of each finding, such as the two pads an unconnected item joins, with their positions.
- The preview of a write checked with KiCad's DRC says how many unconnected items are left on the board in all.
- `--input` on board commands is a JSON file or `-` for stdin; its help says so.

### Fixed

- `pin update` joins a pin to a net that a local label on its sheet already names when the net is given by its bare name, such as `--net SIG` for `/SIG`, on the placement of the sheet the pin is on; before, it refused, saying the edit would join other nets.

## [1.0.0] - 2026-11-06

### Added

- The kicad-cli project, generated from tool-driver-cli-spec 1.0.0 and pinned to tool-driver-cli-spec 1.0.5.
- The adapter: finds KiCad, reads and writes its files, and runs KiCad's own command-line program for checks and exports.
- `project get`, `library list` and `library get`.
- Schematic commands: `schematic create`, `schematic check` (KiCad's ERC), `schematic export`, `schematic render`, `symbol list`, `symbol get`, `symbol create`, `symbol update`, `symbol move`, `symbol delete`, `pin update`, `net list` and `net update`.
- Board commands: `board create`, `board check` (KiCad's DRC with schematic parity), `board export` (Gerber and drill), `board render`, `netlist import`, `footprint list`, `footprint create`, `footprint move`, `track list`, `track create` and `track delete`.
- Builtin knowledge entries, the Skills `kicad-cli`, `kicad-schematic` and `kicad-board`, the behavior matrices of every command and the evaluation questions.
- The result of each write describes what it made, read again from the written file: a schematic edit gives each symbol's fields, position and the net of each pin, and what else changed, such as wires removed with a symbol; `track create` gives the pads, tracks, vias and zones each end touches and which ends are left unconnected; `footprint move` gives the tracks each pad touches and those the move left behind; `track delete` gives what each end of a deleted segment touched; `footprint create` gives the pads as written.
- `footprint create` and `symbol create` take `--definition` as inline JSON or a JSON file; `reference` shows its JSON Schema.
- `footprint create` and `symbol create` declare `artifact` as well as `change`: `footprint create` always makes a new `.kicad_mod` file, and `symbol create` makes a new `.kicad_sym` file when it defines a symbol in a library that does not exist yet.
- `board create` writes only the board. When its name differs from the schematic's, the preview says that KiCad does not pair it with the schematic, so `netlist import` needs `--schematic` and `board check` runs without schematic parity.
- An export into a folder, `board export` or `schematic render --file-type svg`, is read back as the kind of files the folder holds, and the write flow checks each file it reports.
- Paths stay as given: a file reached through a symbolic link, a junction or a short name keeps that path in results and target keys, so it matches `--doc` and the scope.
- A read, check or export of a file that KiCad has open warns in `meta.notices` that it used the file on disk and that KiCad may hold unsaved changes.
- KiCad's own program runs on a temporary copy of the person's KiCad settings, because it rewrites `kicad_common.json` on every run; a read that KiCad's program crashes on runs once more.
- Every command passed its behavior matrix on KiCad 10.0.6 on Windows; 26 of them are stable after the real evaluation, in which Claude Code passed all 14 questions.
- Live runs on the practice copies listed in `docs/E2E.md`, with `tests/live/disturb.py` as the `--tamper` command of the read-back rows.
- Every list, domain lists included, pages with `--limit` (default 20, at most 100) and `--offset`; a required flag left out returns `E_USAGE`.
