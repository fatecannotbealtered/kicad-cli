# AGENTS.md

English | [中文](AGENTS_zh.md)

This file is for coding Agents that work on the kicad-cli project. Read it before changing anything.

## Bare minimum to obey

1. Never edit a pinned copy: `.agent/`, `contract/driver-contract.json`, `kicad_cli/_driver/`, `licenses/`, and `scripts/spec.py`, `build.py`, `pack.py`, `platforms.py` and `release_gate.py`. They come unchanged from one tool-driver-cli-spec release. To move to another release, run `python scripts/spec.py sync <version>`, which replaces them all together.
2. Never edit generated content by hand: `contract/commands.json`, the command maps in the README and the Skills, the badge row, the info card, the risk block, the support matrix, the evidence index and the text between `<!-- driver:spec-...:start -->` and `:end -->` markers, which comes from the specification release; write this tool's own text outside those markers. Change their source, then run `python scripts/spec.py generate`.
3. Register every domain command in `kicad_cli/commands/` with all its effects, its modes, the unit quantity of every physical parameter, and its target type. Mark a change the application cannot take back as `irreversible`. Never lower a confirmation tier.
4. Give every domain command a behavior matrix in `tests/matrix/`. The `normal` row of a write command is its sample.
5. Before every commit, run `python -m ruff check .`, `python scripts/spec.py check`, `python -m unittest discover -s tests` and `python scripts/spec.py suites`. All four must pass. Never skip, weaken or edit a conformance test.
6. Run live tests on the real application only on the targets listed under Authorized Targets in `docs/E2E.md`, as its Authorization Rules say. Add a target only when the person's own message grants it, and tell them the line you added. Inside the listed targets, go ahead without asking: make practice copies, start the instances you test on and close only those, and write your own scope files.
7. Tests set `KICAD_CLI_STATE_DIR` to a new temporary directory and never touch the real state directory.
8. Never commit secrets, license files or keys, or customer and project files you are not allowed to share, not even inside evidence.
9. Scripts and tests that start subprocesses on Windows pass `CREATE_NO_WINDOW`.
10. Add an entry under `[Unreleased]` in `CHANGELOG.md` for every change.
11. Write code, code comments, commit messages, command output and error messages in English.

## This project

kicad-cli drives KiCad, versions >=10.0.6 <11, in these modes:

- `file`: reads and writes KiCad files directly.

Where the code lives:

- Domain commands: `kicad_cli/commands/`
- Adapter layer: `kicad_cli/adapter/`
- Driver runtime, a pinned copy: `kicad_cli/_driver/`

How the adapter reaches KiCad:

- **KiCad's files** (`kicad_cli/adapter/fileformat/`): every change is made by editing the file's own s-expression text, so the rest of the file stays as KiCad wrote it. Handlers per command group live in `kicad_cli/adapter/handlers/`, with the engines they use beside them (`sch_edit_*`, `pcb_read_*`, `pcb_edit_*`).
- **KiCad's own command-line program** (`kicad_cli/adapter/kicad_tools.py`): ERC, DRC, exports and fresh netlists. It has the same name as this tool; `kicad_cli/adapter/install.py` finds it inside a KiCad installation, never through PATH alone. Runs are bounded (25 s for reads, 40 s for writes) so that every call returns in time.
- File mode only: nothing talks to a running KiCad. A write refuses a file with KiCad's lock file `~<name>.lck` beside it (`base.refuse_if_open`).
- An adapter raises only the host error codes of DCLI-279; use the helpers in `kicad_cli/adapter/base.py`: `ambiguous` (E_AMBIGUOUS with candidates), `refuse_if_open` (E_CONFLICT, `file_open_in_session`), `kicad` (E_BACKEND_UNAVAILABLE when KiCad is missing). A KiCad run that takes too long is E_TIMEOUT for a read.
- Unit tests never run KiCad: they mock `kicad_tools`, point `KICAD_CONFIG_HOME` at an empty folder and patch `base._found`.

The common commands are listed in the Commands section of `CONTRIBUTING.md`. Live tests use only the targets under Authorized Targets in `docs/E2E.md`; this file lists none.
