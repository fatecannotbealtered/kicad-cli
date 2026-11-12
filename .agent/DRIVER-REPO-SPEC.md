# DRIVER-REPO-SPEC

English | [中文](DRIVER-REPO-SPEC_zh.md)

> Version 1.3.0 · Clauses DREPO-001 to DREPO-062

## How to Read

This specification says how a driver tool project is laid out, documented, labeled, versioned and released. Each rule is a clause with a fixed ID, such as `DREPO-020`. A clause ends with two lines. `Why:` gives the reason. `Check:` says how the rule is checked: 🤖 means a script or a test checks it, 👀 means a person checks it in review.

The normative words mean:

- MUST and MUST NOT: required. A tool that breaks the rule does not conform.
- SHOULD and SHOULD NOT: the expected choice. Depart from it only for a reason you can state.
- MAY: allowed, not required.

The machine contract `contract/driver-contract.json` holds the file names, section names, badges and labels of this specification as data. Tables that repeat these values carry a contract marker. When such a table and the contract disagree, the contract wins.

Placeholders: `<tool>` is the tool's command name, `<package>` its npm package name, `<pkg>` its source package directory and `<PREFIX>` its environment variable prefix (DREPO-053). `X.Y.Z` stands for a version number.

## Scope

This specification applies to every driver tool project, whatever the application, the modes or the implementation language. It covers:

- the files and directories of the project
- the sections of the project's documents, in English and Chinese
- the badge row, the info card and the status labels
- the pinned copies of the specification, the contract, the driver runtime and the shared scripts
- versions, CI and releases
- distribution through npm
- the capability survey and the steps that bring a new application on board

The template creates a project that already meets these rules. The tool stays conforming by passing `check_project` on every merge.

Command behavior, security, Skills and verification have their own clauses. This specification cites them by ID where a project rule depends on them.

## Terms

| Term | Meaning |
|---|---|
| driver tool | A command-line tool that lets an AI Agent operate a professional desktop application or a device |
| tool project | The repository of one driver tool |
| template | The project template that comes with this specification. It generates a new tool project |
| pinned copy | A file copied unchanged from one specification release: the specification, the contract, the driver runtime, their licenses and the scripts every tool shares |
| sync script | `sync_spec`, the script that copies the pinned copies into a tool project and replaces them on upgrade |
| project check | `check_project`, the script that checks a tool project against this specification |
| driver runtime | The shared code in `<pkg>/_driver/` that implements the built-in and standard commands, the write flow and self-update |
| host component | Code the tool installs into the application's process, for interfaces that work only from inside the application (DCLI-262) |
| native program | The tool built as one standalone executable for one operating system and CPU architecture |
| platform package | An npm package that holds the native program for one operating system and CPU architecture |
| launcher | The small Node.js script in the main npm package that finds and starts the native program |
| command entry | The file npm puts on PATH so that typing `<tool>` runs the tool |
| shipped platform | An operating system for which the tool publishes platform packages |
| provenance | The npm attestation that a package was built by the tool's own release workflow |
| capability survey | `docs/APPLICATION.md`: what the application can do and how it can be automated |
| capability ID | The ID `CAP-nnn` of one capability in the survey |
| disposition | What the tool does with a capability: `provided`, `deferred`, `excluded` or `unknown` |
| badge row | The row of badges at the top of the README |
| info card | The table under the README title area that gives the details behind the badges |
| support matrix | The table in `docs/COMPATIBILITY.md`, generated from the live records and the platform packages, that shows where the tool works (DREPO-016) |
| compatibility label | The status mark in a row of the support matrix, such as ✅ verified |
| live record | The JSON record of one run on the real application, kept in `docs/evidence/` (DVER-042) |
| merge gate | The checks every change passes before it merges, and every release before it publishes |
| platform delivery | The release step that installs each platform package on its own operating system and CPU architecture and runs one key operation |
| delivery record | The package digest and the result that platform delivery records for one platform |

## Runtime and Tool Responsibilities

| Area | The template, the sync script and the checks provide | The tool author does |
|---|---|---|
| Project skeleton | Every required file and directory, with all fixed headings in both languages and the fixed text: How It Works, Bare minimum to obey, the install block, the five fixed SECURITY sections and Authorization Rules of `docs/E2E.md` | Fills in the tool's own sections and removes every placeholder |
| Pinned copies | `sync_spec` copies the specification, the contract, the runtime, the licenses and the shared scripts, and replaces them as a whole on upgrade | Runs `sync_spec` to upgrade and never edits a pinned copy |
| Generated content | Scripts that generate `contract/commands.json`, the command map, the badge row, the info card and the support matrix from data, and write the text every tool shares from the release (DREPO-036) | Registers commands and keeps the evidence and the other sections of `docs/COMPATIBILITY.md` true |
| Checks | `check_project`: layout, root files, sections, language pairs, placeholders, hashes, badges, labels and capability tracing | Fixes everything `check_project` reports |
| CI and release | The workflows `ci.yml` and `release.yml` with the merge gate, platform delivery and publishing with provenance | Keeps both workflows passing and picks the key operation for platform delivery |
| Distribution | Packaging scripts for the native program, the platform packages and the launcher; `update` and the direct command entry in the runtime (DCLI-070, DCLI-075) | Sets the package name, command name and platform packages in `package.json` |
| Survey and onboarding | The skeleton of `docs/APPLICATION.md` and the development Skill `build-driver-cli` | Surveys the application and makes the decisions of each onboarding step |

## Project Layout

### DREPO-001 Project layout

The tree below shows a typical tool project. It illustrates the layout and is not itself the rule: the required files and paths are those of DREPO-002 and DREPO-004, and other clauses add paths inside them, such as `tests/matrix/` (DVER-030). The example shows a Python tool; a tool built with another build system replaces `pyproject.toml` with its own build configuration.

```
<tool>/
├── README.md / README_zh.md        title area, info card and nine fixed sections
├── AGENTS.md / AGENTS_zh.md
├── CHANGELOG.md
├── CONTRIBUTING.md / CONTRIBUTING_zh.md
├── SECURITY.md / SECURITY_zh.md
├── CODE_OF_CONDUCT.md
├── LICENSE
├── NOTICE.md                       the driven application and its trademarks, third-party licenses
├── package.json                    npm package: name, version, command name, platform packages
├── pyproject.toml
├── .agent/                         this specification, copied unchanged
│   └── SPEC_VERSION
├── contract/                       driver-contract.json (copied unchanged), commands.json (generated from command registration)
├── licenses/                       licenses of this specification and of the runtime
├── <pkg>/
│   ├── _driver/                    driver runtime, copied unchanged
│   ├── commands/                   domain commands (written by the tool author)
│   └── adapter/                    adapter layer (written by the tool author)
├── plugin/                         host component, in a language the application supports (only when needed); source/ holds code that is built and not installed
├── skills/
│   ├── <tool>/                     entry Skill
│   └── <product>-<area>/           domain Skills
├── knowledge/                      builtin knowledge
├── docs/
│   ├── APPLICATION.md / _zh        capability survey
│   ├── COMPATIBILITY.md / _zh      support matrix
│   ├── E2E.md / _zh                live acceptance and the list of authorized targets
│   └── evidence/                   live records and eval reports (JSON)
├── scripts/                        build, npm platform packages, smoke test, checks
├── tests/                          the tool's tests and conformance runs
│   ├── matrix/                     behavior matrices, one JSON file per command
│   ├── inputs/                     files that samples only read, as {inputs} (DVER-002)
│   └── eval/                       evaluation questions (required before a command is marked stable)
└── .github/                        workflows ci.yml and release.yml, issue and pull request templates
```

Domain commands MUST live in `<pkg>/commands/` and adapter code in `<pkg>/adapter/`. The host component MUST live in `plugin/`, with code that is built into its installed files in `plugin/source/` (DCLI-240), and `plugin/` exists only when the tool has a host component (DCLI-262).

Why: When every tool puts the same thing in the same place, people and Agents who know one tool find their way in the next.
Check: 🤖 `check_project` checks the paths of DREPO-002 and DREPO-004; 👀 review checks where the code sits.

### DREPO-002 Root files

The root of every tool project MUST hold each of these files:

<!-- contract:root_files -->
| File | Requirement |
|---|---|
| `README.md` | Title area, info card and the nine fixed sections (DREPO-011) |
| `README_zh.md` | The Chinese README (DREPO-010) |
| `AGENTS.md` | Rules for coding Agents that work on the project, in two fixed sections (DREPO-012) |
| `AGENTS_zh.md` | The Chinese AGENTS file |
| `CHANGELOG.md` | Keep a Changelog format, English only (DREPO-018) |
| `CONTRIBUTING.md` | How to contribute, in fixed sections (DREPO-014) |
| `CONTRIBUTING_zh.md` | The Chinese CONTRIBUTING file |
| `SECURITY.md` | Security model and vulnerability reports, in fixed sections (DREPO-013) |
| `SECURITY_zh.md` | The Chinese SECURITY file |
| `CODE_OF_CONDUCT.md` | The code of conduct for everyone who takes part |
| `LICENSE` | The tool's license |
| `NOTICE.md` | The application the tool drives and its trademarks, and the licenses of third-party components |
| `package.json` | The npm package: name, version, command name and platform packages (DREPO-053) |

Why: These files answer the first questions anyone has about a tool: what it is, how to install it, whether it is safe and how to help.
Check: 🤖 `check_project`.

### DREPO-003 Nothing else at the root

Besides the files of DREPO-002, the root MAY hold only two kinds of files:

- build configuration, such as `pyproject.toml` and the lock files of DSEC-070
- tool configuration, such as `.gitignore` and `.editorconfig`

Every script MUST live in `scripts/`. No other file MAY sit at the root.

Why: A tidy root shows the same few files in every tool, so a reader sees at once what matters.
Check: 🤖 `check_project` rejects any other file at the root.

### DREPO-004 Required paths

Every tool project MUST have these paths. This table and the conditional paths below are the normative list; the tree of DREPO-001 only illustrates it.

<!-- contract:required_paths -->
| Path | Content |
|---|---|
| `.agent/` | The pinned specification (DREPO-030) |
| `.agent/SPEC_VERSION` | The version of the pinned specification (DREPO-030) |
| `contract/driver-contract.json` | The pinned contract (DREPO-031) |
| `contract/commands.json` | The tool's commands, generated from command registration (DCLI-029) and validated by `contract/schemas/commands.schema.json` (DREPO-031) |
| `licenses/` | The licenses of the specification and the runtime (DREPO-033) |
| `knowledge/` | Builtin knowledge, with at least the entries DSKL-126 requires |
| `skills/` | The entry Skill and the domain Skills (DSKL-022, DSKL-023) |
| `docs/APPLICATION.md` | The capability survey (DREPO-015, DREPO-060) |
| `docs/APPLICATION_zh.md` | The Chinese capability survey |
| `docs/COMPATIBILITY.md` | The support matrix (DREPO-016) |
| `docs/COMPATIBILITY_zh.md` | The Chinese support matrix |
| `docs/E2E.md` | Live acceptance, with the only list of authorized targets (DREPO-017, DVER-051) |
| `docs/E2E_zh.md` | The Chinese live acceptance document |
| `docs/evidence/` | Live records and eval reports (DVER-042, DSKL-043) |
| `scripts/` | Build, packaging, smoke test and check scripts; some are pinned copies (DREPO-035) |
| `tests/` | The tool's tests, including the conformance runs and the behavior matrices (DVER-001, DVER-030) |
| `tests/matrix/` | One behavior matrix per command (DVER-030) |
| `.github/workflows/ci.yml` | The merge gate (DREPO-041) |
| `.github/workflows/release.yml` | The release workflow, run only by a tag (DREPO-043) |

These paths MUST also exist, each under its condition:

- `<pkg>/_driver/` with `MANIFEST.json`, in every tool (DREPO-032)
- `plugin/`, when the tool has a host component
- `tests/eval/`, when any command is marked stable (DSKL-043)

Why: The project check, the workflows and Agents look for these paths by name.
Check: 🤖 `check_project`.

## Document Skeletons

### DREPO-010 Bilingual documents

Each of these documents MUST exist in English and in Chinese. The English file is the main file. The Chinese file has the same name with `_zh` before `.md`.

<!-- contract:bilingual_files -->
| English file | Chinese file |
|---|---|
| `README.md` | `README_zh.md` |
| `AGENTS.md` | `AGENTS_zh.md` |
| `SECURITY.md` | `SECURITY_zh.md` |
| `CONTRIBUTING.md` | `CONTRIBUTING_zh.md` |
| `docs/APPLICATION.md` | `docs/APPLICATION_zh.md` |
| `docs/COMPATIBILITY.md` | `docs/COMPATIBILITY_zh.md` |
| `docs/E2E.md` | `docs/E2E_zh.md` |

- The two files of a pair MUST say the same thing. The Chinese file is natural Chinese, not a word-for-word translation.
- The two files MUST have the same `##` sections in the same order, and the same number of table rows in each section.
- Section headings are English in the English file and Chinese in the Chinese file. The contract lists the Chinese heading of every fixed section under `project.sections_zh`, and the Chinese edition of this specification shows it next to the English name.
- Each file MUST link to its pair with a language switch line: `English | [中文](<name>_zh.md)` in the English file and `[English](<name>.md) | 中文` in the Chinese file. In the README the line is part of the title area (DREPO-011); in the other files it is the first line after the title.
- These stay English only: `CHANGELOG.md` (DREPO-018), command output and error messages, code and code comments.
- Skills are written once, with an English body and a `description` in both languages (DSKL-001).
- Builtin knowledge entries need not be bilingual. Each entry states its language in `lang` (DSKL-120).

Why: Readers of either language get the same facts, and a change made on one side only is caught at once.
Check: 🤖 `check_project` compares the sections, the table rows and the language switch lines of each pair; 👀 review checks that the Chinese reads naturally.

### DREPO-011 README sections

The README MUST start with the title area, in this order: the `#` title with the tool name, a one-line pitch, the badge row (DREPO-020) and the language switch line. The info card (DREPO-021) follows. Then come these `##` sections, with these names and in this order:

<!-- contract:readme_sections -->
| Section | Content |
|---|---|
| `Install` | The install block (DREPO-054) |
| `Capabilities` | The command map of all commands, generated from command registration by the same generator as the Skills' maps (DSKL-006) |
| `How It Works` | Choosing the instance and the document, the confirmation tiers and saving, in fixed text from the template |
| `Authorization` | How a person grants writes with the scope file (DSEC-010 to DSEC-018), and which effects the tool's commands need |
| `About This Tool` | The free section: the domain, typical tasks, screenshots, whatever shows the tool's character |
| `Configuration` | Environment variables, such as `<PREFIX>_STATE_DIR` (DSEC-030) and `<PREFIX>_SCOPE_FILE` (DSEC-010), and how the tool finds the application |
| `Uninstall` | How to remove the host component when there is one, the npm package, the installed Skills and the state directory |
| `Development` | How to build and test the tool, with the details in `CONTRIBUTING.md` |
| `Links` | The specification version, `CHANGELOG.md`, `SECURITY.md`, the documents in `docs/` and the issue tracker |

About This Tool is the only free section. Every other section MUST keep its name and position and hold the content above. The tool MUST NOT change the fixed text of How It Works. With a host component, Install adds the `plugin install` step (DREPO-054).

Why: A reader who knows one tool's README finds everything in the same place in the next; the free section keeps each tool recognizable.
Check: 🤖 `check_project` checks the title area, the info card, the section order and the fixed text; 👀 review reads About This Tool.

### DREPO-012 AGENTS sections

`AGENTS.md` is for coding Agents that work on the tool project. It MUST have these `##` sections, in this order:

<!-- contract:agents_sections -->
| Section | Content |
|---|---|
| `Bare minimum to obey` | The fixed rules from the template |
| `This project` | The application the tool drives, its modes, where the adapter code lives and the common commands. For live tests it points to Authorized Targets in `docs/E2E.md` and lists no target itself (DVER-050) |

The tool MUST NOT change the text of Bare minimum to obey.

Why: A coding Agent opening any tool project meets the same ground rules first, then the facts of this project.
Check: 🤖 `check_project` checks the sections and compares Bare minimum to obey with the template.

### DREPO-013 SECURITY sections

`SECURITY.md` MUST have these `##` sections, in this order:

<!-- contract:security_sections -->
| Section | Content |
|---|---|
| `Supported Versions` | Which versions receive security fixes; a version with a known vulnerability is deprecated on npm, so update notices warn its users (DCLI-079) |
| `Reporting a Vulnerability` | How to report a vulnerability privately |
| `Risk Tier` | The tool's risk tier and the commands that set it (DSEC-002) |
| `Authorization` | The scope file: where it is, who writes it and what it grants (DSEC-010 to DSEC-018) |
| `Write Confirmation` | Fixed text: confirmation tiers, confirm tokens and the confirm-time review (DCLI-052 to DCLI-056) |
| `Host Component` | What runs inside the application, how it is installed and how it connects (DSEC-050 to DSEC-053) |
| `Physical Effects` | How physical effects are guarded (DSEC-061 and DSEC-062) |
| `State Directory` | Fixed text: location, contents and access protection of the state directory (DSEC-030 to DSEC-032) |
| `Credentials` | The secrets the tool handles, how they are masked, and the addresses the tool connects to (DSEC-040 to DSEC-043) |
| `Untrusted Content` | Fixed text: what content is untrusted and how it is marked (DSEC-020 to DSEC-022) |
| `Supply Chain` | Fixed text: locked dependencies, releases with provenance, no install scripts, Skills from the installed package (DSEC-070 to DSEC-073) |
| `Self-update` | Fixed text: what `update` changes and checks (DCLI-070 to DCLI-079) |

Write Confirmation, State Directory, Untrusted Content, Supply Chain and Self-update describe runtime behavior that is the same in every tool. Their text is fixed and comes from the template, like How It Works, and the tool MUST NOT change it.

Two sections depend on the tool:

- Host Component MUST appear when the tool has a host component and MUST NOT appear otherwise.
- Physical Effects MUST appear when the tool supports device mode and MUST NOT appear otherwise.

All other sections MUST always appear.

Why: A person deciding whether to grant a scope reads the same safety facts, in the same order, for every tool; text that describes the shared runtime is written once and cannot drift from it.
Check: 🤖 `check_project` checks the sections, compares the five fixed sections with the template, and checks the two conditional ones against `reference`.

### DREPO-014 CONTRIBUTING sections

`CONTRIBUTING.md` MUST have these `##` sections, in this order:

<!-- contract:contributing_sections -->
| Section | Content |
|---|---|
| `Development Setup` | What to install and how to prepare a working copy |
| `Commands` | The commands developers run to build, test and check the tool |
| `Branch and Commit Convention` | How branches are named and commit messages are written |
| `CI Checks` | The merge gate (DREPO-041) and how to run it locally |
| `Tests and Evidence` | Conformance tests, behavior matrices, live records, eval reports, and how evidence sets maturity (DVER-001, DVER-030, DVER-040) |
| `Adding or Changing a Command` | Registration with effects, units and target type (DCLI-029), the capability's disposition in the survey (DREPO-060), the behavior matrix, whose `normal` row is the write sample (DVER-002, DVER-030), the Skill and the CHANGELOG entry |
| `Pull Requests` | What a pull request contains and how it is reviewed |
| `Security` | Where to report vulnerabilities instead of opening a public issue (`SECURITY.md`) |

Why: A contributor, human or Agent, learns the same workflow once and applies it to every tool.
Check: 🤖 `check_project`.

### DREPO-015 Capability survey sections

`docs/APPLICATION.md` MUST have these `##` sections, in this order:

<!-- contract:application_sections -->
| Section | Content |
|---|---|
| `Application` | Name, vendor, the versions surveyed, and the operating systems and CPU architectures on which the application, or in device mode the device driver or SDK, runs (DREPO-052) |
| `Automation Interfaces` | Every interface the application offers, and whether the CLI can call it or it works only in-process or on the main thread (DCLI-262) |
| `Object Model` | The application's objects and their names, which become command nouns (DCLI-023) |
| `Units and Coordinates` | Internal and display units, origin and axis directions (DCLI-080 to DCLI-084) |
| `Threading and Blocking` | Main-thread rules, busy states and dialogs that block automation |
| `Read-back` | How each kind of change can be read back, and how exactly |
| `Save and Undo` | How saving works, how unsaved changes show, and whether undo exists (DCLI-058, DCLI-067) |
| `Instances and Concurrency` | Running several instances, telling them apart, and a person working at the same time |
| `Licensing` | License types, what automation needs, and license dialogs |
| `Capabilities` | The capability table with IDs and dispositions (DREPO-060) |
| `Risks` | Risks and limits found in the survey; each becomes a knowledge entry or has a written reason (DSKL-126) |

Why: The survey forces the hard questions about an application to be answered before any command is designed, in an order every reviewer knows.
Check: 🤖 `check_project`.

### DREPO-016 Compatibility sections

`docs/COMPATIBILITY.md` MUST have these `##` sections, in this order:

<!-- contract:compatibility_sections -->
| Section | Content |
|---|---|
| `Support Matrix` | One row per combination of application version (major and minor, or the supported range while no live record names a version), operating system and mode, with its compatibility label (DREPO-022); generated, never typed by hand |
| `Known Limitations` | Known gaps and failures, with the versions they affect |

The Support Matrix section MUST be generated by the template's script from the live records in `docs/evidence/` and the platform packages in `package.json`, and MUST NOT be edited by hand. A support matrix looks like this:

| Application version | Operating system | Mode | Status |
|---|---|---|---|
| 10.0 | Windows | session | ✅ verified |
| 10.0 | Linux | session | ⏳ untested |
| 9.0 | macOS | file | ⛔ unsupported |

Why: A person sees at a glance where the tool works and how sure that is, before installing it. A matrix generated from the records cannot claim more than they show.
Check: 🤖 `check_project` checks the sections, regenerates the support matrix and compares.

### DREPO-017 Acceptance sections

`docs/E2E.md` MUST have these `##` sections, in this order:

<!-- contract:e2e_sections -->
| Section | Content |
|---|---|
| `Authorization Rules` | Fixed text: who adds a target to Authorized Targets, how a target is written, and who writes the scope files for live runs and for evaluations on the real application (DVER-050) |
| `Authorized Targets` | The only list of the files, folders and devices allowed for live runs, one list item per target, and no other text (DVER-050) |
| `Procedure` | The machines, operating systems, application versions and modes used for live runs, and how to prepare and run the behavior matrices on the real application (DVER-030, DVER-031) |
| `Evidence Records` | An index of the live records in `docs/evidence/` (DVER-042) |

The tool MUST NOT change the text of Authorization Rules. The results of live runs are not repeated here: the support matrix shows them, generated from the same records (DREPO-016).

Why: A reviewer, or the next maintainer, needs one place that says which targets may be used, how live runs are done and where their records are. The rules for authorizing targets are the same in every tool, so a change to them reaches every tool with the template.
Check: 🤖 `check_project` checks the sections and compares Authorization Rules with the template.

### DREPO-018 CHANGELOG

`CHANGELOG.md` MUST follow the Keep a Changelog format:

- `## [Unreleased]` is always the first section.
- Each release is a section `## [X.Y.Z] - YYYY-MM-DD`, newest first.
- Inside a section, changes sit under `###` subsections. Only the six below MAY be used, in this order. Empty subsections are left out.
- The file is English only.

<!-- contract:changelog_sections -->
| Subsection | What goes in it |
|---|---|
| `Added` | New commands, flags, fields or capabilities |
| `Changed` | Changes to existing behavior |
| `Deprecated` | Features that a later version will remove |
| `Removed` | Features removed in this version |
| `Fixed` | Bug fixes |
| `Security` | Fixes for vulnerabilities |

The `changelog` command reads this file (DCLI-034), and release notes are generated from it (DREPO-040).

Why: One format lets the `changelog` command, the release notes and the update notices read every tool's history the same way.
Check: 🤖 `check_project` checks the headings, the subsections and that the file has no Chinese text.

### DREPO-019 No template leftovers

A tool project MUST NOT keep any placeholder the template inserted. `check_project` looks for template placeholders in every document of DREPO-010 to DREPO-018, together with the file, section and language-pair checks those clauses set.

Why: A leftover placeholder tells the reader that nobody finished the document, and the tool loses the trust its badges claim.
Check: 🤖 `check_project`.

## Badges and Labels

### DREPO-020 Badge row

The README MUST show these badges, in this order, in one row under the pitch:

<!-- contract:badges -->
| Badge | Shows | Source |
|---|---|---|
| `agent_native` | 🤖 Agent-native, the series badge, identical on every tool | The template |
| `ci` | The status of the CI workflow on the main branch | CI |
| `npm` | The latest published npm version | The npm registry |
| `drives` | The application and its version range, such as drives KiCad 10.0 | The driver block of `reference` (DCLI-031) |
| `mode` | The supported modes: file, session, device | The driver block of `reference` |
| `platform` | The shipped platforms: Windows, macOS, Linux | The platform packages in `package.json` (DREPO-043) |
| `risk` | The tool's risk tier, T0 to T2 (DSEC-002) | `reference` |
| `maturity` | The lowest maturity among the tool's domain commands (DVER-040) | `reference` |
| `spec` | The pinned specification version, such as tool-driver-cli-spec 1.3.0 | `.agent/SPEC_VERSION` |
| `license` | The tool's license | `package.json` |

A badge row for a tool that drives KiCad 10.0 in file and session mode looks like this; the template defines the exact style:

```markdown
![Agent-native](https://img.shields.io/badge/🤖-Agent--native-6f42c1)
[![CI](https://github.com/<owner>/<repo>/actions/workflows/ci.yml/badge.svg)](https://github.com/<owner>/<repo>/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/<package>)](https://www.npmjs.com/package/<package>)
![drives](https://img.shields.io/badge/drives-KiCad_10.0-1f6feb)
![mode](https://img.shields.io/badge/mode-file_·_session-0e8a16)
![platform](https://img.shields.io/badge/platform-Windows_·_macOS_·_Linux-555555)
![risk](https://img.shields.io/badge/risk-T1-d29922)
![maturity](https://img.shields.io/badge/maturity-verified-8957e5)
![spec](https://img.shields.io/badge/spec-tool--driver--cli--spec_1.3.0-0969da)
![license](https://img.shields.io/badge/license-Apache--2.0-blue)
```

Why: The same row on every tool marks it as part of the series and answers the first questions at a glance: what it drives, where it runs, how risky and how proven it is.
Check: 🤖 `check_project` regenerates the badge row from its sources and compares.

### DREPO-021 Info card

The info card is a table right after the title area. It MUST have these rows, in this order, and MUST take each value from the same source as the matching badge:

| Row | Content | Source |
|---|---|---|
| Application | The application and the supported version range | The driver block of `reference` |
| Modes | Each supported mode | The driver block of `reference` |
| Systems and CPUs | Each shipped platform with its CPU architectures | The platform packages in `package.json` |
| Risk tier | The tool's risk tier | `reference` |
| Maturity | The badge level and the number of domain commands at each level | `reference` |
| Specification | The pinned specification version | `.agent/SPEC_VERSION` |
| Host component | Whether the tool needs one | `reference`: the tool has `plugin` commands (DCLI-240) |

Why: The badges stay short; the card gives the details a person needs before installing, without a second source that could disagree.
Check: 🤖 `check_project` regenerates the info card and compares.

### DREPO-022 Compatibility labels

Each row of the support matrix (DREPO-016) MUST carry exactly one of these labels, written as the mark followed by the label, such as ✅ verified:

<!-- contract:compatibility_labels -->
| Label | Mark | Meaning | Evidence needed |
|---|---|---|---|
| `verified` | ✅ | Tested on the real application | A current live record for this application version, operating system and mode (DVER-042, DVER-043) |
| `untested` | ⏳ | Not tested on the real application yet | None |
| `unsupported` | ⛔ | The tool does not support this combination | None |

Why: Three marks that mean the same thing in every tool let a person read any support matrix without a legend.
Check: 🤖 `check_project` checks every label and its evidence.

### DREPO-023 No overstatement

Badges, the info card and compatibility labels MUST NOT claim more than the data shows:

- The badge row, the info card and the support matrix MUST be generated from the sources named in DREPO-016, DREPO-020 and DREPO-021, never typed by hand.
- The maturity badge MUST equal the level derived from `reference`.
- ✅ verified MUST rest on a current live record (DVER-043). A row backed only by the fake host is ⏳ untested (DVER-021).
- A platform MUST appear as supported only when it was delivered: `package.json` lists a platform package for it, and the release workflow published that package only after its platform delivery passed (DREPO-042, DREPO-043). This holds everywhere: in the badge row, the info card, `docs/COMPATIBILITY.md` and the release notes.

Why: Not run is not passed and simulated is not real; a label that claims more than its evidence misleads exactly the person who trusts it.
Check: 🤖 `check_project` regenerates the badges, the info card and the support matrix and checks every ✅ against the live records.

### DREPO-024 No invented labels

A tool MUST use only the badges of DREPO-020, the info card rows of DREPO-021 and the labels of DREPO-022. It MUST NOT add other badges or status labels to its documents, such as a "beta" or "production ready" badge. Colors, icons and order come from the template and MUST NOT be changed.

Why: The badges and labels are the series' shared signs; they work only if every tool uses the same few, with the same meaning.
Check: 🤖 `check_project`.

## Pinning

### DREPO-030 Specification copy

`.agent/` MUST hold the five specifications of one release, each in English and Chinese, exactly as released. `.agent/SPEC_VERSION` MUST hold that release's version number as its only line. `.agent/SPEC_SHA256` MAY hold the SHA-256 of that release's bundle; the project's scripts then accept only a bundle with that digest.

Why: Agents working on the tool read, offline, exactly the rules the tool was built against.
Check: 🤖 `check_project` compares hashes (DREPO-034).

### DREPO-031 Contract copy

`contract/driver-contract.json` MUST be the contract of the same release, unchanged. `contract/commands.json` MUST be generated from the tool's command registration (DCLI-029), MUST validate against `contract/schemas/commands.schema.json` and MUST NOT be edited by hand; it is regenerated whenever a command changes.

Why: Tests and scripts read the rules and the commands as data; a hand-edited copy lets the data drift from the code.
Check: 🤖 `check_project` compares the contract's hash, regenerates `commands.json`, compares it and validates it against `commands.schema.json`.

### DREPO-032 Runtime copy

The driver runtime MUST sit in `<pkg>/_driver/`, unchanged. `<pkg>/_driver/MANIFEST.json` MUST record the specification release the runtime came from and the SHA-256 hash of every runtime file. A tool MUST NOT change runtime files or reimplement anything the runtime provides, that is, anything listed for the runtime in the Runtime and Tool Responsibilities table of a specification.

Why: Every tool then runs the same tested code for envelopes, writes and update, and a change to it shows up as a hash mismatch.
Check: 🤖 `check_project` checks every file against `MANIFEST.json`.

### DREPO-033 Licenses

`licenses/` MUST hold the license texts of the specification and of the runtime, as shipped with the release. The licenses of the application and of third-party components belong in `NOTICE.md` (DREPO-002).

Why: The copied specification and runtime carry their own license terms, and those terms must travel with them.
Check: 🤖 `check_project` compares hashes (DREPO-034).

### DREPO-034 Sync, never edit

- Pinned copies (DREPO-030 to DREPO-033 and DREPO-035) MUST NOT be edited by hand.
- To upgrade, the author runs the sync script `sync_spec`. It replaces all pinned copies together, from one specification release.
- A tool MUST NOT pin the specification, the contract or the runtime in any other way, such as partial copies, submodules or package dependencies.

Why: One way of pinning, checked by hash, means every tool states exactly which rules and which runtime it runs, and nobody's local fix hides in a copy.
Check: 🤖 `check_project` checks the hash of every pinned file against the release named in `.agent/SPEC_VERSION`.

### DREPO-035 Script copies

`scripts/spec.py`, `scripts/build.py`, `scripts/pack.py`, `scripts/platforms.py` and `scripts/release_gate.py` MUST be the template's scripts of the same release, unchanged. `scripts/smoke.py` and any other script in `scripts/` are the author's.

Why: These scripts are the same in every tool; a fix to them reaches a tool only when `sync_spec` replaces them with the rest of the release.
Check: 🤖 `check_project` compares hashes (DREPO-034).

### DREPO-036 Shared text

The template marks the text that is the same in every tool between `<!-- driver:spec-<name>:start -->` and `<!-- driver:spec-<name>:end -->`: whole sections of the entry Skill and of `CONTRIBUTING.md`, and parts of the README, `SECURITY.md` and `docs/E2E.md`, in both languages. A project MUST keep each marked region of its pinned release, with its markers, in its section. `python scripts/spec.py generate` writes the text of every region from that release, also when `python scripts/spec.py sync` moves the pin, so the author MUST NOT edit it. The tool's own text goes outside the markers.

Why: Text every tool shares changes with the specification. Written by the script, it follows each release in every tool; edited by hand, it falls behind while the checks still pass.
Check: 🤖 `check_project`: every region of the template is in the project once, with the text of the pinned release.

## Versions, CI and Release

### DREPO-040 Versions and tags

- Tool versions MUST follow SemVer.
- The `version` field of `package.json` is the only source of the version. The output of `<tool> --version`, the Skills' `metadata.version` (DSKL-002), the platform packages and the CHANGELOG release heading MUST all equal it.
- Each release MUST have an annotated tag `vX.Y.Z` on the released commit.
- Release notes MUST be generated from the version's section in `CHANGELOG.md`.

Why: One version, written in one place, means the tool, its Skills, its packages and its notes never disagree about what is installed.
Check: 🤖 `check_project` compares the versions; the release workflow compares the tag with `package.json`.

### DREPO-041 Merge gate

Every change MUST pass these checks before it merges. The release workflow runs the same checks before it publishes (DREPO-043).

| Check | What it covers |
|---|---|
| Lint | The tool's code style rules |
| Unit tests | The tool's own tests |
| Conformance suites | Both suites (DVER-001), on every shipped platform |
| Project check | `check_project`: layout, documents, labels, pinned copies and capability tracing |
| Output size check | Every envelope stays within the size limits (DCLI-008); general conformance test R04 performs it |
| npm package content check | Each package holds only what DREPO-051 allows, with no install scripts (DSEC-072) |

The merge gate runs in `.github/workflows/ci.yml`. A failed check blocks the merge and the release.

Why: When merging and releasing pass the same gate, nothing reaches users that a merge would have refused.
Check: 👀 Review of `.github/workflows/ci.yml` and `.github/workflows/release.yml`.

### DREPO-042 Platform delivery

Before a release is published, CI MUST do the following for every platform package listed in `package.json`, on that package's operating system and CPU architecture:

1. Start from a clean environment, with no earlier install of the tool and no development dependencies.
2. Install the actual packages that will be published.
3. Start the tool.
4. Run one representative key operation. When the clean environment cannot have the application, the operation runs against the fake host (DVER-020) and the record says so.
5. Record the package digest and the result.

The release workflow publishes only when platform delivery passed for every platform package listed in `package.json` (DREPO-043). A platform that was not delivered is never described as supported (DREPO-023).

Why: Tests on a development machine do not prove that the published package installs and starts; only a clean install of the real package does.
Check: 🤖 The release workflow stops before publishing when delivery fails for any platform package; 👀 review of `.github/workflows/release.yml`.

### DREPO-043 Release only from CI

A release MUST run only in the release workflow `.github/workflows/release.yml`, started by pushing an annotated tag `vX.Y.Z` that equals the version in `package.json`. The workflow runs the merge gate (DREPO-041) and platform delivery (DREPO-042) for every platform package listed in `package.json`. It MUST publish nothing unless both pass for every one of them; only then does it publish every package to npm with provenance (DSEC-071). A package MUST NOT be published any other way, such as from a personal machine.

Why: A release built by CI from a tag can be traced to its source; a release from a laptop cannot.
Check: 👀 Review of the release workflow; `update` reports `provenance_status` after every install (DCLI-074).

## Distribution

### DREPO-050 npm only

Tools MUST be distributed only through npm. A tool MUST NOT offer another install channel, such as another package manager, an installer or a program attached to a release.

Why: One channel gives every tool the same install, update and provenance path, and `update` (DCLI-070) relies on it.
Check: 👀 Review of the README, the release workflow and the release page.

### DREPO-051 Native programs and platform packages

- CI MUST build the tool as a standalone native program for each shipped platform and CPU architecture. Tools written in Python are built the same way. The native program runs without a separately installed language runtime.
- Each native program ships in its own platform package, named `<package>-<os>-<cpu>`: `<os>` is `win32`, `darwin` or `linux` and `<cpu>` is `x64` or `arm64`, the values npm uses to pick the package. npm installs only the platform package that matches the machine.
- The main package MUST hold only the launcher, `skills/` (DSKL-030) and the list of platform packages, besides the files npm always includes (`package.json`, README, LICENSE) and `NOTICE.md`. Each platform package also carries `NOTICE.md` and `licenses/` (DREPO-033).
- Everything the commands read at run time, such as the builtin knowledge and the bundled CHANGELOG (DCLI-034), ships in the platform package with the native program.

Why: A native program starts fast and needs no Python or other runtime on the user's machine, and the main package stays small.
Check: 🤖 The npm package content check of the merge gate (DREPO-041).

### DREPO-052 Platforms follow the application

- A tool SHOULD ship every platform among Windows, macOS and Linux on which everything it needs runs: the application, or in device mode the device driver or SDK. It MUST NOT ship a platform on which they do not run, and MUST NOT claim a platform it did not deliver (DREPO-023).
- A tool that needs neither the application nor a platform-specific driver, such as a file-mode tool that reads the files itself, can run everywhere and SHOULD therefore ship all three.
- On every shipped platform the tool MUST ship an x64 platform package. It SHOULD also ship an arm64 platform package where the application, or in device mode the device driver or SDK, ships for arm64 on that system.
- The tool and the application always run on the same machine. A tool MUST NOT ship a platform only to drive the application on another machine.
- The Application section of `docs/APPLICATION.md` records the operating systems and CPU architectures on which the application, or in device mode the device driver or SDK, runs. The platform packages in `package.json` MUST follow from it by the rules above.

Why: Users get the tool wherever they can run the application, and no package promises to drive an application that cannot run beside it or claims a platform nobody delivered.
Check: 🤖 `check_project` compares the platform packages with the capability survey.

### DREPO-053 package.json is the source

The package name, the version, the command name and the platform packages MUST come only from `package.json`. Every other place that needs them, such as `update` (DCLI-070), the badges, the install block, the build scripts or `pyproject.toml`, MUST read them from `package.json` or be checked equal to it. The environment variable prefix `<PREFIX>` is the command name in upper case with `-` replaced by `_`: for the command `kicad-cli` it is `KICAD_CLI`. The source package directory `<pkg>` is the command name with `-` replaced by `_`: `kicad_cli`.

Why: A value written in two places drifts; one source keeps the install command, the update target and the badges in agreement.
Check: 🤖 `check_project`.

### DREPO-054 Install block

The Install section of the README MUST give these steps, in this order:

1. `npm install -g <package>` installs the tool.
2. `<tool> update`, run once, points the command entry at the native program (DCLI-075). The version is already current, so `update` only finishes the command entry (DCLI-077).
3. Install the Skills from the installed package (DSKL-031). Skills never come from the network (DSEC-073).
4. Only for a tool with a host component: `<tool> plugin install`, a two-step write that needs a scope (DSEC-050).

The template supplies the text of the block; `<package>` and `<tool>` come from `package.json`.

Why: Every call through the npm launcher first starts Node.js; running `update` once removes that cost, and the same block works for every tool.
Check: 🤖 `check_project` compares the Install section with the template's block.

## Capability Survey and Onboarding

### DREPO-060 Capability survey

Before commands are designed, the author MUST fill in `docs/APPLICATION.md` with the sections of DREPO-015. In the Capabilities section, each capability gets one row. Each row MUST have an ID `CAP-nnn` (three digits, unique in the survey) and one disposition:

<!-- contract:capability_dispositions -->
| Disposition | Meaning |
|---|---|
| `provided` | The tool offers it through one or more domain commands, or through standard commands such as `doc open`, `doc save` and `doc close` |
| `deferred` | Worth offering, but left for a later version |
| `excluded` | The tool will not offer it, for example because it is unsafe or cannot be automated reliably |
| `unknown` | Not yet known whether the application allows it |

Each row SHOULD also name the automation interface that reaches the capability and, for a disposition other than `provided`, the reason in one line.

Why: Commands designed from a survey cover what users need and leave out, on purpose and in writing, what the application cannot do safely.
Check: 🤖 `check_project` checks the IDs and the dispositions.

### DREPO-061 Commands trace to capabilities

Removed: its rule is part of DREPO-060.

### DREPO-062 Onboarding steps

A new tool is usually built by an AI Agent. It SHOULD follow these eight steps, in this order:

| Step | What to do |
|---|---|
| 1 Survey | Fill in `docs/APPLICATION.md`; give every capability an ID and a disposition (DREPO-060) |
| 2 Choose modes | Choose file, session or device mode from the survey; modes MAY be combined (DCLI-201) |
| 3 Generate the project | Generate the project from the template with `instantiate`; `check_project` then lists only what the next steps fill in: the placeholders (DREPO-019) and the domain commands still to register |
| 4 Register commands | Register domain commands by the naming rules (DCLI-020 to DCLI-024) and the registration fields (DCLI-029), each with effects, units and target type, and mark the capabilities they offer `provided` (DREPO-060) |
| 5 Implement the adapter | Implement only the adapter capabilities the chosen modes need (DCLI-260); put each in the adapter layer or the host component (DCLI-262) |
| 6 Test | Run both conformance suites on the fake host (DVER-001); write a behavior matrix for each domain command (DVER-030) and run it on the real application, on authorized targets (DVER-031, DVER-050) |
| 7 Write Skills and knowledge | Decide whether to split the Skills (DSKL-020, DSKL-021); add playbooks to the template's fixed sections (DSKL-009); turn the survey's operating notes, limits and troubleshooting into knowledge entries (DSKL-126); run the evaluation before marking a command stable (DSKL-043) |
| 8 Publish | Build platform packages for the platforms and CPU architectures DREPO-052 calls for, run platform delivery (DREPO-042) and publish to npm with provenance (DREPO-043) |

The development Skill `build-driver-cli`, which comes with this specification, guides an Agent through these steps. For each step it names the clauses to read, the template to fill and the check to run. At the end it reviews the tool against this specification and lists what does not conform.

Why: A fixed path with a check after every step lets an Agent build a conforming tool quickly, without rediscovering the order of the work.
Check: 👀 Review; `build-driver-cli` lists every clause the tool does not meet.

## Checklist

- [ ] DREPO-001 Project layout
- [ ] DREPO-002 Root files
- [ ] DREPO-003 Nothing else at the root
- [ ] DREPO-004 Required paths
- [ ] DREPO-010 Bilingual documents
- [ ] DREPO-011 README sections
- [ ] DREPO-012 AGENTS sections
- [ ] DREPO-013 SECURITY sections
- [ ] DREPO-014 CONTRIBUTING sections
- [ ] DREPO-015 Capability survey sections
- [ ] DREPO-016 Compatibility sections
- [ ] DREPO-017 Acceptance sections
- [ ] DREPO-018 CHANGELOG
- [ ] DREPO-019 No template leftovers
- [ ] DREPO-020 Badge row
- [ ] DREPO-021 Info card
- [ ] DREPO-022 Compatibility labels
- [ ] DREPO-023 No overstatement
- [ ] DREPO-024 No invented labels
- [ ] DREPO-030 Specification copy
- [ ] DREPO-031 Contract copy
- [ ] DREPO-032 Runtime copy
- [ ] DREPO-033 Licenses
- [ ] DREPO-034 Sync, never edit
- [ ] DREPO-035 Script copies
- [ ] DREPO-036 Shared text
- [ ] DREPO-040 Versions and tags
- [ ] DREPO-041 Merge gate
- [ ] DREPO-042 Platform delivery
- [ ] DREPO-043 Release only from CI
- [ ] DREPO-050 npm only
- [ ] DREPO-051 Native programs and platform packages
- [ ] DREPO-052 Platforms follow the application
- [ ] DREPO-053 package.json is the source
- [ ] DREPO-054 Install block
- [ ] DREPO-060 Capability survey
- [ ] DREPO-061 Commands trace to capabilities
- [ ] DREPO-062 Onboarding steps
