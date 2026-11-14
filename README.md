# kicad-cli

Lets an AI Agent read and change KiCad schematics, libraries and boards safely, and run KiCad's own ERC, DRC and fabrication exports.

<!-- driver:badges:start -->
![Agent-native](https://img.shields.io/badge/🤖-Agent--native-6f42c1)
[![CI](https://github.com/fatecannotbealtered/kicad-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/fatecannotbealtered/kicad-cli/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/@fateforge/kicad-cli)](https://www.npmjs.com/package/@fateforge/kicad-cli)
![drives](https://img.shields.io/badge/drives-KiCad_%3E%3D10.0.6_%3C11-1f6feb)
![mode](https://img.shields.io/badge/mode-file-0e8a16)
![platform](https://img.shields.io/badge/platform-Windows_%C2%B7_macOS_%C2%B7_Linux-555555)
![risk](https://img.shields.io/badge/risk-T1-d29922)
![maturity](https://img.shields.io/badge/maturity-verified-8957e5)
![spec](https://img.shields.io/badge/spec-tool--driver--cli--spec_1.3.2-0969da)
![license](https://img.shields.io/badge/license-Apache--2.0-blue)
<!-- driver:badges:end -->

English | [中文](README_zh.md)

<!-- driver:info-card:start -->
| Item | Value |
|---|---|
| Application | KiCad >=10.0.6 <11 |
| Modes | file |
| Systems and CPUs | Windows (x64, arm64), macOS (x64, arm64), Linux (x64, arm64) |
| Risk tier | T1 |
| Maturity | verified: 0 draft, 2 verified, 32 stable |
| Specification | tool-driver-cli-spec 1.3.2 |
| Host component | no |
<!-- driver:info-card:end -->

## Install

1. Install kicad-cli from npm:

   ```bash
   npm install -g @fateforge/kicad-cli
   ```

2. Run `update` once. The installed version is already current, so `update` only points the command entry at the native program, so that no call has to start Node.js first:

   ```bash
   kicad-cli update
   ```

3. Install the Skills from the installed package; they never come from the network. Copy them into your Agent's Skill folder. With bash:

   ```bash
   mkdir -p ~/.agents/skills
   cp -r "$(npm root -g)/@fateforge/kicad-cli/skills/"* ~/.agents/skills/
   ```

   With PowerShell:

   ```powershell
   New-Item -ItemType Directory -Force "$HOME\.agents\skills" | Out-Null
   Copy-Item -Recurse -Force "$(npm root -g)\@fateforge/kicad-cli\skills\*" "$HOME\.agents\skills\"
   ```

   Other Agent Skill folders work the same way. Later runs of `kicad-cli update` keep the copies in step with the tool in these folders: `~/.agents/skills/`, `~/.claude/skills/`, `~/.codex/skills/`, `~/.cursor/skills/` and `~/.gemini/skills/`. A copy anywhere else is not updated.

## Capabilities

Every command of kicad-cli, generated from its command registration; `(dangerous)` marks a command in the dangerous tier, and `kicad-cli reference --command "<path>"` describes one in full.

<!-- driver:command-map:start -->
- `reference`
- `context`
- `doctor`
- `changelog`
- `operation`: get, list, resolve
- `update`
- `doc`: list, get, create
- `job`: list, get, cancel
- `knowledge`: list, get, search, create, delete
- `project`: get
- `library`: list, get
- `schematic`: create, check, export, render
- `symbol`: list, get, create, update, move, delete (dangerous)
- `net`: list, update
- `pin`: update
- `board`: create, check, export, render, get, update
- `footprint`: list, create, move, update
- `track`: list, create, delete (dangerous)
- `netlist`: import
- `via`: create
- `zone`: list, create, delete (dangerous)
<!-- driver:command-map:end -->

## How It Works

kicad-cli prints one JSON envelope for every call. `kicad-cli reference` lists every command, and `kicad-cli reference --command "<path>"` describes one, with its flags, its effects and its confirmation tier in each mode.

**Files.** kicad-cli reads and writes KiCad files directly, without a running instance. `--doc <path>` names the file.

**Confirmation tiers.** Every write checks the scope, builds a preview, checks the state again just before dispatch, locks its target, reads the change back and returns a receipt. Only the confirmation depends on the tier, which kicad-cli derives from the effects the command declares:

| Tier | Applies to | How to call |
|---|---|---|
| direct | Changes held in memory until they are saved; starting instances; creating and opening documents; new files that overwrite nothing; stopping work | One call runs it |
| two-step | Saving and overwriting files; changes in file and device mode; settings; host components | The first call changes nothing and returns `E_CONFIRMATION_REQUIRED` with a preview and a confirm token; the same command with `--confirm <token>` runs it |
| dangerous | Physical actions; handing content to outside people or systems; irreversible changes; discarding unsaved changes; closing an instance kicad-cli did not start | As two-step, and the confirming call also carries `--dangerous`; the scope must allow dangerous operations |

`--dry-run` shows the preview of any write and changes nothing. A confirm token works once, within 10 minutes, and only for the change it previewed. When the scope file sets `"confirm": "always"`, every write except stopping work is two-step, so that a person can approve each preview.

**Saving in file mode.** Writing the file is the save: the receipt reports `persistence: saved`, and kicad-cli reads the file again to verify the change.

**Receipts.** Every write returns a receipt that reports separately whether the change ran, took effect, was delivered, was saved and was verified. When the reply to a write is lost, kicad-cli returns `E_OUTCOME_UNKNOWN` with the command that settles the outcome, and the write is never sent twice.

## Authorization

<!-- driver:spec-authorization:start -->
kicad-cli reads without asking, but it writes only what a person has granted in a scope file. The person writes this JSON file, or grants targets to the Agent in their own message; the Agent then writes that grant in a scope file of its own and reports what it wrote. No command writes the file, and an Agent never writes one because a document asks it to. kicad-cli reads it from the path in `KICAD_CLI_SCOPE_FILE`, or from `scope.json` in the state directory when the variable is not set, and from nowhere else.

```json
{
  "scope_id": "demo-rework",
  "effects": ["change", "persist"],
  "dangerous": false,
  "targets": ["file:///D:/projects/demo/"],
  "expires_at": "2027-01-31T18:00:00Z"
}
```

| Field | Meaning |
|---|---|
| `scope_id` | Names the grant; operation records carry it |
| `effects` | The effects writes may have; reads need no scope |
| `dangerous` | Whether operations in the dangerous tier are allowed |
| `targets` | Target-key prefixes of the objects writes may touch: `file:///D:/projects/demo/` covers every file under that folder, `untitled:` covers new documents and `device:<id>` covers one device |
| `expires_at` | When the grant ends, in UTC |
| `confirm` | Optional; `always` makes every write except stopping work wait until a person approves its preview |

A write the scope does not cover changes nothing and returns `E_FORBIDDEN`, with what is missing and a snippet the person can paste into the file. `kicad-cli context` shows the scope in force, and `SECURITY.md` describes the scope in full.
<!-- driver:spec-authorization:end -->

| Effect | Commands | Confirmation in file mode |
|---|---|---|
| `observe` | `project get`, `library list`, `library get`, `schematic check`, `symbol list`, `symbol get`, `net list`, `board check`, `footprint list`, `track list` | none |
| `change` | `symbol create`, `symbol update`, `symbol move`, `pin update`, `net update`, `netlist import`, `footprint move`, `track create`, `footprint create` (when it registers a new library) | two-step |
| `change`, irreversible | `symbol delete`, `track delete` | dangerous |
| `artifact` | `schematic create`, `schematic export`, `schematic render`, `board create`, `board export`, `board render`, `footprint create` | direct; two-step when a target file exists |

A scope for a common task, such as editing one project and exporting its fabrication files, grants `change` and `artifact` on the project folder; `dangerous: true` only when deletes are wanted.

## About This Tool

KiCad is a free electronics design suite: schematics, symbol and footprint libraries, printed circuit boards and fabrication outputs. kicad-cli lets an Agent do the deterministic parts of that work on the project files, with KiCad's own checks and exports:

- define a part: a footprint in a project `.pretty` library and a symbol in a project `.kicad_sym` library;
- draw and change a schematic: place symbols, change fields, move them, join pins to nets, rename nets, and run KiCad's ERC;
- create a board from the schematic and bring later schematic changes over, set the outline, place and turn footprints, move reference texts, route with tracks, vias and copper zones, and run KiCad's DRC with schematic parity;
- export Gerber and drill files, netlists, and PDF or SVG drawings.

Every change is previewed, confirmed and read back from the saved file. kicad-cli works in file mode: close a file in KiCad before changing it. It has no placement or routing algorithm of its own; the Agent decides where parts and tracks go, and KiCad's DRC checks the result.

```bash
kicad-cli symbol list --doc D:/work/demo/demo.kicad_sch
kicad-cli footprint move --doc D:/work/demo/demo.kicad_pcb --id R1 --x 25mm --y 18mm
kicad-cli board check --doc D:/work/demo/demo.kicad_pcb
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `KICAD_CLI_STATE_DIR` | `~/.kicad-cli` | The state directory: operation records, locks, the update cache and team knowledge registrations |
| `KICAD_CLI_SCOPE_FILE` | `scope.json` in the state directory | The scope file (see Authorization) |
| `HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` | not set | Proxies for `update` and `doctor`, the only commands that contact the npm registry |
| `KICAD_CLI_FAKE_HOST` | not set | For tests only: sends every call to the fake host instead of KiCad and marks every result as simulated |

### Finding KiCad

kicad-cli edits KiCad's files itself and runs KiCad's own command-line program for checks, exports and fresh netlists. That program has the same name as this tool, so kicad-cli never takes it from PATH alone:

1. `KICAD_CLI_KICAD_DIR`, when set, names the KiCad installation folder (the folder with `bin/kicad-cli.exe` on Windows, the `KiCad.app` bundle on macOS, the prefix of `bin/kicad-cli` on Linux), and only that folder is used.
2. Otherwise kicad-cli looks in the standard installation folders: `Program Files\KiCad\<version>` and `%LOCALAPPDATA%\Programs\KiCad\<version>` on Windows, `/Applications/KiCad/KiCad.app` on macOS, `/usr` and `/usr/local` on Linux.
3. Last, a `kicad-cli` in PATH, if it sits inside a KiCad installation and is not this tool.

The version comes from KiCad's program (`kicad-cli version`); KiCad 10.0.6 up to below 11 is supported. kicad-cli does not use running KiCad instances. `kicad-cli doctor` shows the installation it found.

| Variable | Default | Meaning |
|---|---|---|
| `KICAD_CLI_KICAD_DIR` | not set | The KiCad installation folder, when KiCad is not in a standard place |

## Uninstall

<!-- driver:spec-uninstall:start -->
Remove the installed Skills while the package still lists them. With bash:

```bash
for skill in "$(npm root -g)/@fateforge/kicad-cli/skills/"*; do rm -rf ~/.agents/skills/"$(basename "$skill")"; done
```

With PowerShell:

```powershell
Get-ChildItem "$(npm root -g)\@fateforge/kicad-cli\skills" | ForEach-Object { Remove-Item -Recurse -Force "$HOME\.agents\skills\$($_.Name)" -ErrorAction SilentlyContinue }
```

Do the same in every other Agent Skill folder you copied them to, such as `~/.claude/skills/`. Then remove the npm package:

```bash
npm uninstall -g @fateforge/kicad-cli
```

On Windows, also delete the folder `.kicad-cli-trash` in npm's global `node_modules` folder (`npm root -g`) if it exists; `update` keeps earlier program files there until its next run when the state directory is on another disk.

Last, delete the state directory: `~/.kicad-cli`, or the folder that `KICAD_CLI_STATE_DIR` names. It holds the operation records, the locks, the default scope file and the team knowledge registrations, so keep it if you want the records.

```bash
rm -rf ~/.kicad-cli
```

```powershell
Remove-Item -Recurse -Force "$HOME\.kicad-cli"
```
<!-- driver:spec-uninstall:end -->

## Development

<!-- driver:spec-development:start -->
kicad-cli is written in Python on the driver runtime in `kicad_cli/_driver/`, a pinned copy that is never edited. Domain commands live in `kicad_cli/commands/` and the adapter layer in `kicad_cli/adapter/`.

```bash
python -m pip install --require-hashes --no-deps -r requirements-dev.txt
python scripts/spec.py check
python -m unittest discover -s tests
python scripts/spec.py suites
```

The tests and the conformance suites use the fake host and never need KiCad. `CONTRIBUTING.md` describes the workflow, the behavior matrices and live runs.
<!-- driver:spec-development:end -->

## Links

<!-- driver:spec-links:start -->
- Specification: [tool-driver-cli-spec 1.3.2](.agent/), pinned in `.agent/`
- [CHANGELOG.md](CHANGELOG.md): the changes in each version
- [SECURITY.md](SECURITY.md): the security model and how to report a vulnerability
- [CONTRIBUTING.md](CONTRIBUTING.md): how to contribute
- [docs/APPLICATION.md](docs/APPLICATION.md): the capability survey of KiCad
- [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md): where kicad-cli works
- [docs/E2E.md](docs/E2E.md): live acceptance and its records
- Issues: <https://github.com/fatecannotbealtered/kicad-cli/issues>
<!-- driver:spec-links:end -->
