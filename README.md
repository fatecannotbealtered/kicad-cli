# kicad-cli 1.0.0

[中文完整使用入口](README_zh.md).

The CLI, executable, six Skills and delivery metadata share product version **1.0.0**. Native packages target Windows x64, macOS arm64/x64 and Linux x64, with an external legal KiCad 10.0 installation (patch 6 or later). Exact candidate acceptance and publication state are recorded in [the delivery record](docs/modules/DELIVERY.md); version metadata alone does not prove acceptance.

Obtain the matched complete package. It includes the native executable, Python, validators and authorized fixed SDK. The direct executable needs no development Python/SDK; the optional Node installation wrapper also needs Node. It does not fetch public optional dependencies. Unpack or install into a new owned prefix, create UTF-8 `runtime-config.json` with `format_version: 1`, `kicad_root` and `profile_dir`, select it with `KICAD_CLI_RUNTIME_CONFIG`, and run the absolute executable with `--check-environment --compact`. See [installation and migration](docs/INSTALLATION.md).

Choose the needed Skill: `kicad-cli`, `kicad-library`, `kicad-sch`, `kicad-layout`, `kicad-review` or `kicad-fabrication`. The logical PCB module remains `kicad-pcb`. Each complete module folder carries its generated shared resources, manifest and exact binding. Begin discovery with `reference --index`, then `reference --command "<path>"`; check `context`, `doctor` and the changelog before engineering work.

Supported scope includes source-bound single-unit rectangular/SMD local assets, real wired schematic capture and editing, scalar hierarchy/reuse, single-root netlist/SVG/PDF export, first-board creation and field synchronization, bounded zero-degree front-SMD translation, explicit straight-copper patches and duplicate subsets, saved/reopened editing, read-only checks and basic fabrication artifacts. [PROFILE](release/PROFILE.json) and runtime `modules` identify the exact supported subset.

Automatic routing, dense escape, push/shove, new vias or arc/via removal, arbitrary outlines/stackups, advanced/THT/custom-pad libraries, general placed-instance refresh, hierarchical schematic export and new assembly BOM/placement output are outside this delivery. Custom checks are not official ERC/DRC. Multi-file saves are not atomic. Preserve partial/unknown operation state and inspect actual files before further writes.

Whole-product `release_readiness` remains `unpublishable` while FCC is missing. `reference.extensions.internal_delivery`, `modules` and `doctor` report the bounded internal profile separately. Basic native package installation and library/SCH/PCB smoke are recorded per artifact. Broader CAD, recovery, powered operation, simulation, SI/EMI and manufacturing work remain separate. Public npm publication stays disabled; a version tag does not publish a public package or bypass stable-release admission.

## Knowledge and repository layout

Knowledge is an explicit, provider-neutral file/URL index read by the Agent, not a
built-in Feishu integration. See [configuration](docs/KNOWLEDGE.md).

Product code lives in kicad_cli; maintained Skill sources in docs/modules; generated
installable modules in skills. tests and bench retain reproducible regression inputs,
not installation requirements. See [maintenance](docs/MAINTENANCE.md).

## License

Original code and Skills use [Apache-2.0](LICENSE). See [NOTICE.md](NOTICE.md) for fixed SDK, inherited seed and other third-party terms.
