# Changelog

All notable product changes are recorded here. CLI, executable, six Skills and delivery packages use one version.

## [Unreleased]

## [1.0.0] - 2026-10-29

### Added

- Six independently installable engineering Skills: environment and routing, project-local libraries, schematics, PCB layout, review and fabrication export.
- Native packages for Windows x64, macOS arm64/x64 and Linux x64, with bundled Python, validators and fixed SDK. KiCad itself remains a separate user installation.
- Source-bound single-unit symbols and rectangular SMD footprints, project library registration, wired schematic construction, scalar hierarchy and saved/reopened editing.
- First-board creation, SCH/PCB identity and field synchronization, bounded front-SMD movement, explicit straight-copper patches and exact-duplicate subsets.
- Saved-file checks, single-root schematic exports and basic Gerber, drill and board plot delivery.
- Provider-neutral file/URL knowledge indexes and self-contained resources in each Skill.

### Fixed

- Resolve installed symbol and footprint resources without shadowing the environment helper during schematic editing.
- Preserve project rules, source identity, scoped previews and operation status across edits and reloads.

### Licensing and distribution

- License original product code, documentation, Skills and fixed driver SDK assets under Apache-2.0, retaining inherited MIT and other third-party notices.
- Keep company deployment endpoints, recipient identifiers and private publication receipts outside the product repository.
- Bind delivered packages to their source commit, OS/CPU, file hashes and native acceptance results. Public npm publication remains disabled.

### Security

- Block the stable publishing workflow unless exact artifact-bound FCC and required live evidence pass. The bounded initial package profile does not promote missing whole-product evidence.
- Preserve scoped previews, confirmation checks, fixed SDK integrity and honest unknown/partial operation status.

### Scope

- Initial bounded engineering release. Automatic routing, dense escape, advanced library geometry, general recovery and manufacturing signoff are not included.
- Basic package installation and library/SCH/PCB save/reopen acceptance are recorded per artifact. Full-product FCC, Agent evaluation and hardware validation remain separate; the runtime reports their actual status.
