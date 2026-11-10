# Contributing to kicad-cli

English | [中文](CONTRIBUTING_zh.md)

People and coding Agents contribute through the same workflow. Coding Agents also follow `AGENTS.md`.

## Development Setup

<!-- driver:spec-development-setup:start -->
You need:

- Python 3.12 or later
- Node.js with npm, to build and test the npm packages
- Git

The tests and the conformance suites never need KiCad: the fake host stands in for it. You need KiCad only for live runs on authorized targets (`docs/E2E.md`).

Clone the repository:

```bash
git clone https://github.com/fatecannotbealtered/kicad-cli.git
```

Then, in the cloned folder, create a virtual environment, install the development dependencies and run the project check:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --no-deps -r requirements-dev.txt
python scripts/spec.py check
```

On Windows, activate the environment with `.venv\Scripts\activate` instead. `requirements-dev.txt` pins every development dependency with its hash, so only the reviewed versions are installed.
<!-- driver:spec-development-setup:end -->

## Commands

<!-- driver:spec-commands:start -->
| Command | What it does |
|---|---|
| `python scripts/spec.py check` | Runs the project check: layout, documents and their fixed text, generated content, pinned copies, versions, Skills, knowledge, behavior matrices, evidence and maturity |
| `python scripts/spec.py generate` | Regenerates `contract/commands.json`, the command maps, the badge row, the info card, the risk block, the support matrix and the evidence index, and writes the text between `driver:spec-` markers from the specification release |
| `python scripts/spec.py suites` | Runs both conformance suites against kicad-cli, with the fake host in place of KiCad |
| `python scripts/spec.py sync <version>` | Replaces the pinned copies with those of another tool-driver-cli-spec release |
| `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>` | Runs the behavior matrix of one command on the real KiCad, on authorized targets, and writes a live record |
| `python scripts/spec.py eval --workspace-root <folder>` | Runs the evaluation questions in `tests/eval/` with Claude Code on the real KiCad, in an authorized folder, and writes an evaluation report; with `--check --fake-host`, checks each grader against the scripted agents |
| `python -m unittest discover -s tests` | Runs the unit tests |
| `python scripts/build.py` | Builds the native program and the platform package for this machine |
| `python scripts/pack.py` | Builds the npm packages and runs the package content check |
| `python scripts/smoke.py` | Runs the key operation of platform delivery against the built packages |

The pinned copies are `.agent/`, `contract/driver-contract.json`, `kicad_cli/_driver/`, `licenses/`, and `scripts/spec.py`, `build.py`, `pack.py`, `platforms.py` and `release_gate.py`. Never edit them; `sync` replaces them together, and lists the other template files that changed since the release pinned before, to compare with this project's copies.
<!-- driver:spec-commands:end -->

## Branch and Commit Convention

<!-- driver:spec-branch-and-commit-convention:start -->
- `main` always passes the merge gate. Work on a branch and merge through a pull request.
- Name a branch `<kind>/<topic>`, where the kind is `feat`, `fix`, `docs`, `test` or `chore`, for example `feat/part-move`.
- Write commit messages in English: a subject line of at most 72 characters in the imperative mood, such as `Add part move`, then a blank line and a body that says why.
- Keep each commit to one change, together with its tests, its behavior matrix, its documents in both languages and its CHANGELOG entry.
- Only a maintainer releases. A release sets the new version in `package.json`, the only source of the version, and moves the `[Unreleased]` entries into a section `[X.Y.Z] - YYYY-MM-DD`. After the merge, the maintainer pushes the annotated tag `vX.Y.Z`, and the release workflow does the rest.
<!-- driver:spec-branch-and-commit-convention:end -->

## CI Checks

<!-- driver:spec-ci-checks:start -->
`.github/workflows/ci.yml` runs the merge gate on every change. Its last job, `merge gate passed`, succeeds only when every check passed on every shipped system; make it the required status check of the main branch, so that a failed check blocks the merge. The release runs the same checks.

| Check | What it covers | Run it locally |
|---|---|---|
| Lint | The tool's code style rules | `python -m ruff check .` |
| Unit tests | The tool's own tests in `tests/` | `python -m unittest discover -s tests` |
| Conformance suites | Both suites, on every shipped platform | `python scripts/spec.py suites` |
| Project check | Layout, documents, labels, pinned copies, Skills, knowledge and capability tracing | `python scripts/spec.py check` |
| Output size check | Every envelope stays within the size limits; general conformance test R04 performs it | `python scripts/spec.py suites` |
| npm package content check | Each package holds only what it may, with no install scripts | `python scripts/pack.py` |

`.github/workflows/release.yml` runs only when a maintainer pushes a version tag. It runs the same checks, then platform delivery: on the operating system and CPU of each platform package, it installs the packages that will be published into a clean environment, starts kicad-cli and runs one key operation, as `python scripts/smoke.py` does. It publishes to npm, with provenance, only when everything passed for every platform package. A version with a prerelease part, such as `1.2.0-rc.1`, is published under the npm tag `next`, so `update` does not install it by default. Linux packages are built on Debian 11 (glibc 2.31), so they also run on older Linux systems. The workflow publishes through npm trusted publishing when the package has it set up on npmjs.com, otherwise with the `NPM_TOKEN` secret; set up trusted publishing after the first release and then disallow token publishing.
<!-- driver:spec-ci-checks:end -->

## Tests and Evidence

<!-- driver:spec-tests-and-evidence:start -->
**Conformance suites.** `python scripts/spec.py suites` runs the general and the driver conformance suite. They start kicad-cli as a separate process with `KICAD_CLI_FAKE_HOST` set, so the fake host stands in for KiCad, and judge only the exit codes, the output and what the fake host received. Never skip, weaken or edit a test, and never mark one as an expected failure; a test that applies but did not run counts as failed. Results from the fake host are simulated and never count as evidence.

**Unit tests.** The tool's own tests live in `tests/`. Every test sets `KICAD_CLI_STATE_DIR` to a new temporary directory, and either points `KICAD_CLI_SCOPE_FILE` at a scope file inside it or removes that variable.

**Behavior matrices.** Every domain command, and every standard command that calls an adapter capability, has one matrix file in `tests/matrix/`, named after the command path, such as `tests/matrix/part-move.json`. The file lists each row once: `normal`, `empty`, `invalid_input`, `not_found`, `ambiguous`, `conflict`, `no_scope`, `busy`, `dialog`, `disconnect`, `unsaved` and `read_back_mismatch`. A row that applies holds the arguments and the expected exit code and error code; a row that does not apply holds `applies: false` and the reason. The arguments of the `normal` row of a write command are its sample, which the general suite runs against the fake host. Arguments name the session, the document, the device and the output folder only through the placeholders `{session}`, `{doc}`, `{device}` and `{dir}`, a job through `{job}`, and a file the command only reads through `{inputs}`, the folder `tests/inputs/`.

**Live records.** `python scripts/spec.py matrix --command "<path>" --set <placeholder>=<value>` runs one matrix on the real KiCad, with one `--set` for each placeholder the matrix uses, named without braces, such as `--set doc=<file>`; each value is an authorized target. It writes a live record to `docs/evidence/` and fills in the actual result and the `live:` evidence reference of each row. `docs/E2E.md` describes the procedure. A row that cannot be caused on demand, such as `disconnect`, may be marked `unrunnable` with the reason; the `normal`, `dialog` and `read_back_mismatch` rows never may. `--row read_back_mismatch` makes the tool wait before its read-back while the result is changed from outside; a command that lists several `doc_kinds` runs its `normal` row on each kind with `--kind`, and one that lists a single kind needs no `--kind`; and when the objects differ between kinds, the row's `kind_arguments` give the arguments for a kind.

**Evaluation.** Before a command is marked stable, `tests/eval/` holds a question for every per-tool scenario that applies to kicad-cli, and the evaluation reports from the real KiCad in `docs/evidence/` hold a current result for every question, the newest of each passed, and a passed one of a question that lists the command in `COMMANDS` and ran it. The results of several reports count together. Each question is one Python file named after its ID; files named `_*.py` hold helpers the questions share.

```python
"""Move one footprint inside the scope."""
SCENARIO = "scoped_change"
COMMANDS = ["footprint move"]
TURNS = ["Move footprint R1 in {documents}/board.kicad_pcb to x 12.5 mm, y 30 mm."]
FILES = "boards"          # copied into the question's documents folder


def grade(run):
    part = run.tool("footprint", "get", "--doc", str(run.documents / "board.kicad_pcb"), "--id", "R1")["data"]["items"][0]
    run.check(part["x_mm"] == 12.5, "R1 did not move")
    run.check(len([c for c in run.write_calls() if c.ok]) == 1, "not exactly one write")
```

`python scripts/spec.py eval --workspace-root <folder>` runs the questions with Claude Code on the real KiCad and writes the report; the folder must be listed under Authorized Targets in `docs/E2E.md`, or lie inside a listed folder. For each question the runner makes a workspace inside that folder with the Skills and the scope file, keeps the documents in a folder beside it, and records every run of kicad-cli; the Agent's shell runs only kicad-cli, and its file tools reach only the workspace. `{documents}` in `TURNS` and in scope targets stands for the documents folder. By default the scope grants that folder, the effects of the commands in `COMMANDS` and `dangerous` only when one of them needs it; `SCOPE` replaces any of these fields, and a question whose commands only read gets no scope file. The runner writes that scope file itself; nobody edits it. On the real KiCad, kicad-cli keeps your environment, so it finds the running instances; a session-mode question opens its document in `before` and closes it in `after`, which always runs.

| A grader or hook uses | What it gives |
|---|---|
| `run.check(condition, problem)` | Records a failure when the condition does not hold |
| `run.tool(*args)` | Runs kicad-cli as the grader and returns its envelope; not one of the Agent's runs |
| `run.read(name)`, `run.documents` | A file in the documents folder, and the folder |
| `run.calls`, `run.ran(path, turn)`, `run.write_calls(turn)` | The Agent's recorded runs; each has `args`, `exit`, `ok`, `code`, `details`, `data`, `value(flag)` and `matches(line)` |
| `run.messages`, `run.said(*texts, turn=-1)` | What the Agent told the person in each turn; judge meaning, in English and Chinese |
| `run.scope_changed()`, `run.notes` | Whether the scope file changed; a dict a hook fills for the grader |
| `run.host(...)`, `run.state()`, `run.applied()`, `run.document(id)` | The fake host only |

Optional `right(agent, turn)` and `wrong(agent, turn)` scripted agents use `agent.run(*args)`, `agent.line(text)`, `agent.say(text)` and `agent.calls`. `python scripts/spec.py eval --check --fake-host` shows that each grader passes `right` and fails every `wrong` on the fake host; with `--workspace-root` in place of `--fake-host`, it does so on the real KiCad, which a question with `FAKE_HOST = None` needs. The specification's own questions, in `conformance/eval/` of the release, are worked examples; `read_answer`, `scoped_change`, `knowledge_lookup` and `separate_save` grade only through what also works on the real KiCad.

**Maturity.** Each command declares its maturity level and the evidence behind it, and `kicad-cli reference` shows both.

| Level | Evidence needed |
|---|---|
| `draft` | None; a write command still needs its sample |
| `verified` | A current live record in which every matrix row that applies passed, except rows marked `unrunnable` |
| `stable` | Also current evaluation results |

Evidence counts while what it depends on is unchanged, whatever the version number: a live run until the command's registration or the adapter's code changes, an evaluation result until its question, a command it lists, or the handwritten Skills and knowledge change. After such a change, run again only the matrices and questions it touches; `python scripts/spec.py check` names them, and `python scripts/spec.py eval --workspace-root <folder> --stale` runs just the questions that need it. Evidence references have the form `live:docs/evidence/<file>.json#<row>` or `eval:docs/evidence/eval-<date>.json`.
<!-- driver:spec-tests-and-evidence:end -->

## Adding or Changing a Command

<!-- driver:spec-adding-or-changing-a-command:start -->
1. **Survey.** Find the capability in the Capabilities table of `docs/APPLICATION.md`. When it is missing, add a row with a new `CAP-nnn`. Set its disposition to `provided` once a command offers it.
2. **Name.** Use the form `noun verb`. Take the verb from the fixed list: `list`, `get`, `search`, `check`, `create`, `update`, `delete`, `move`, `import`, `render`, `export`, `run`, `stop`. Take the noun from the terms of KiCad, written as a full word. A noun never takes the name of a built-in or standard command, such as `session`, `doc` or `job`.
3. **Register.** Register the command in `kicad_cli/commands/` with its summary, effects, modes, target type, flags with their types, the unit quantity of every physical value, its secret parameters and its untrusted output fields. Declare every effect. Mark a change KiCad cannot take back with `irreversible: true`, and a command that only stops work with `halt: true`. Give read-back tolerances where KiCad rounds or snaps values, and name related knowledge entries in `related_knowledge`.
4. **Implement.** Write the adapter capabilities the command needs in `kicad_cli/adapter/`, or in the host component in `plugin/` when the interface of KiCad works only inside its process or on its main thread. Never reimplement what the driver runtime does.
5. **Matrix.** Write the behavior matrix in `tests/matrix/`, with every row. The `normal` row of a write command is its sample.
6. **Generate and check.** Run `python scripts/spec.py generate`, then `python scripts/spec.py check`, `python -m unittest discover -s tests` and `python scripts/spec.py suites`.
7. **Prove it.** The command starts as `draft`. Run its matrix on the real KiCad, on authorized targets, with `python scripts/spec.py matrix`, and commit the live record; the command can then be `verified`. Rerun the matrix whenever a change affects how the command talks to KiCad.
8. **Skills and knowledge.** The command maps update themselves. Add a playbook to the Skill that owns the command when its task takes several steps. When the entry Skill would grow past 150 lines, split it into domain Skills by the tasks users name, each listing its commands in `metadata.commands`. Turn the limits and errors you found into entries in `knowledge/`.
9. **CHANGELOG.** Add an entry under `[Unreleased]`: `Added` for a new command or flag, `Changed` for new behavior, `Deprecated` before a later removal, `Removed` when it is gone, `Fixed` for a bug fix and `Security` for a vulnerability fix.
<!-- driver:spec-adding-or-changing-a-command:end -->

## Pull Requests

<!-- driver:spec-pull-requests:start -->
- One topic per pull request. The description says what changed and why, names the commands and capability IDs it touches, and links any new live record or evaluation report.
- Every CI check passes, and generated content is regenerated, never edited by hand.
- The English and Chinese documents change together and say the same thing.
- No secrets, license data or files you may not share, in the code, the tests or `docs/evidence/`.
- The reviewer checks the declared effects and the `irreversible` and `halt` marks against the capability survey, the untrusted fields, the Skill text and the reasons given for matrix rows that do not apply or cannot be run.
- A maintainer merges. Packages are published only by the release workflow.
<!-- driver:spec-pull-requests:end -->

## Security

<!-- driver:spec-security:start -->
Do not report a vulnerability in a public issue or pull request. Report it privately, as `SECURITY.md` describes, through <https://github.com/fatecannotbealtered/kicad-cli/security/advisories/new>.
<!-- driver:spec-security:end -->
