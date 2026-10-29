# kicad-cli 1.0.1

[中文完整使用入口](README_zh.md).

kicad-cli lets AI assistants work on KiCad projects through a structured command-line interface. It supports project-local libraries, schematic editing, PCB creation and local edits, read-only checks, and Gerber, drill, SVG and PDF export.

## Installation

Download the complete package for your OS and CPU from [Releases](https://github.com/fatecannotbealtered/kicad-cli/releases/latest). Install KiCad 10.0.6 or a newer 10.0 patch, including its symbol and footprint libraries, separately.

| Platform | Package target | System baseline |
| --- | --- | --- |
| Windows | x64 | KiCad 10.0.6 or newer 10.0 patch |
| macOS, Apple Silicon | arm64 | macOS 14 |
| macOS, Intel | x64 | macOS 15 |
| Linux | x64 | Ubuntu 24.04 / glibc 2.39 |

The direct executable bundles Python and its runtime dependencies; no development Python, Node, compiler or SDK installation is needed. The optional Node installation wrapper additionally requires Node. Set the installed KiCad path and an owned profile directory in `runtime-config.json`, then run `--check-environment --compact`. See the [installation and upgrade guide](docs/INSTALLATION.md) for commands. Windows ARM64 and Linux ARM64 packages are not provided.

## Six task Skills

| Skill | Use it for |
| --- | --- |
| `kicad-cli` | Environment checks, command discovery and task routing |
| `kicad-library` | Project-local symbols, SMD footprints and pin/pad mappings |
| `kicad-sch` | Schematics, connections, fields, scalar hierarchy and single-root exports |
| `kicad-layout` | PCB creation, synchronization, supported placement and explicit local track edits |
| `kicad-review` | Read-only schematic/PCB checks, parity and copper/path diagnostics |
| `kicad-fabrication` | Gerber, drill, PCB SVG/PDF export and output checks |

The complete native package includes all six modules. Each Skill can also be installed independently with its resources. For example:

```sh
npx skills add fatecannotbealtered/kicad-cli --skill kicad-cli -y -g
```

Start with `reference --index`, then `reference --command "<path>"`; use `modules`, `context` and `doctor` to check the installed capabilities and environment. The PCB Skill is named `kicad-layout`; its CLI module ID is `kicad-pcb`.

## Working scope

Supported operations include single-unit symbols and rectangular SMD footprints, wired schematic editing, scalar hierarchy/reuse, single-root netlist/SVG/PDF export, first-board creation and field synchronization, zero-degree front-SMD translation, explicit straight-track edits and exact-duplicate cleanup, save/reopen, checks and fabrication export.

Automatic routing, dense escape, push/shove, new vias or arc/via removal, arbitrary outline/stackup editing, advanced/THT/custom-pad libraries, general placed-instance refresh, hierarchical schematic export and assembly BOM/placement output are not supported by this workflow. On macOS/Linux, board sync with `candidate-out` and incremental hierarchy batches that publish new schematic source files are unavailable; see [compatibility](docs/COMPATIBILITY.md).

Writes use scoped previews and confirmation. After a partial or unknown result, inspect the operation and actual files before retrying. Multi-file saves are not atomic. Custom checks differ from official ERC/DRC, and successful exports or zero findings do not establish manufacturing approval.

## Knowledge and repository layout

An AI assistant can read an explicitly configured file/URL knowledge index for project or team rules. Existing knowledge paths are retained during an upgrade. See [knowledge configuration](docs/KNOWLEDGE.md).

Product code lives in kicad_cli; maintained Skill sources in docs/modules; generated
installable modules in skills. tests and bench retain reproducible regression inputs,
not installation requirements. See [maintenance](docs/MAINTENANCE.md).

## License

Original code and Skills use [Apache-2.0](LICENSE). See [NOTICE.md](NOTICE.md) for fixed SDK, inherited seed and other third-party terms.
