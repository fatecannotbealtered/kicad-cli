# DRIVER-VERIFY-SPEC

English | [中文](DRIVER-VERIFY-SPEC_zh.md)

> Version 1.2.0 · Clauses DVER-001 to DVER-051

## How to Read

This specification says how a driver tool proves that it works. Each rule is a clause with a fixed ID, such as `DVER-010`. A clause ends with two lines. `Why:` gives the reason. `Check:` says how the rule is checked: 🤖 means a test or a script checks it, 👀 means a person checks it in review.

The normative words mean:

- MUST and MUST NOT: required. A tool that breaks the rule does not conform.
- SHOULD and SHOULD NOT: the expected choice. Depart from it only for a reason you can state.
- MAY: allowed, not required.

The machine contract `contract/driver-contract.json` holds the test IDs, the behavior matrix rows, when each test and each row applies, the maturity levels and the evidence kinds as data. The schemas in `contract/schemas/` define the files that hold evidence. They are the schemas of the release a tool pins: a tool project does not copy them, and `check_project` reads them from that release. Tables that repeat contract values carry a contract marker. When such a table and the contract disagree, the contract wins.

## Scope

This specification applies to every driver tool and to the driver runtime. It covers:

- the two conformance suites every tool runs: the general suite (G and R tests) and the driver suite (D tests)
- the fake host, which stands in for the application in tests
- the behavior matrix of each domain command, and of each standard command that calls an adapter capability
- the maturity level and the evidence that every command declares
- live acceptance on the real application

It does not repeat the rules being tested. Each test names the clauses it checks.

Two kinds of runs test a tool, and each proves something different. The conformance suites, run on the fake host, prove that the runtime handles every situation correctly for the tool. A live run exercises the tool's real adapter with the real application. Only live runs, and the evaluation with Agents on the real application, count as evidence for a command's maturity (DVER-021, DVER-040).

One idea runs through this specification: only what is tested counts. Not run is not passed. Simulated is not real. Not verified is not success.

## Terms

| Term | Meaning |
|---|---|
| driver runtime | The shared code every tool copies unchanged. It implements the built-in and standard commands, the write flow and receipts |
| domain command | A command the tool author registers, as opposed to the built-in and standard commands of the runtime |
| write command | A command whose declared effect is anything other than `observe` (DCLI-051) |
| halt command | A command that only stops work in progress: a `stop` command or `job cancel`, registered with `halt: true`. It runs at once, without confirmation (DSEC-018) |
| conformance test | One test of the two suites in this specification: general (G00 to G10, R01 to R05) and driver (D01 to D26) |
| write sample | The arguments of one valid call to a write command, kept in the `normal` row of its behavior matrix and used by the general suite (DVER-002) |
| fake host | A small program from the tool-driver-cli-spec repository that stands in for the application in tests (DVER-020) |
| behavior matrix | A JSON file per command, with one row per situation: arguments, expected and actual result, and evidence (DVER-030) |
| maturity | How far a command is proven: `draft`, `verified` or `stable` (DVER-040) |
| evidence | A file that shows a maturity claim is true. An evidence reference points to it (DVER-041) |
| simulated result | A result obtained with the fake host or a simulated device. It never counts as evidence (DVER-021) |
| live run | Running the tool, with its real adapter, against the real application on authorized targets |
| live record | The JSON record of one live run, kept in `docs/evidence/` (DVER-042) |
| eval report | The JSON file `docs/evidence/eval-*.json` that records a run of the tool's evaluation questions with Agents (DSKL-043) |
| live acceptance | Checking the tool on the real application, as described in `docs/E2E.md` |
| authorized target | A file, folder or device that a person allows live runs to use, listed in `docs/E2E.md` (DVER-050) |

## Runtime and Tool Responsibilities

| Area | The runtime and the tool-driver-cli-spec repository provide | The tool author does |
|---|---|---|
| Conformance suites | Both suites, which run any tool from the command line | Runs both suites in CI on every merge and release, on every shipped platform |
| Write samples | The G tests that use the samples; samples for built-in and standard commands | The `normal` row of each domain write command's behavior matrix |
| Fake host | The fake host, reached through `<PREFIX>_FAKE_HOST` (DVER-023) and the host protocol | Nothing to write |
| Behavior matrix | The rows and when each applies, the matrix schema and the matrix runner | One matrix file per domain command and per standard command that calls an adapter capability |
| Maturity and evidence | Maturity and evidence shown by `reference`; `check_project` compares levels with evidence | Declares the level and the evidence of each command and keeps the evidence current |
| Live acceptance | The schemas of live records and eval reports | Runs on authorized targets; writes `docs/E2E.md` and the records in `docs/evidence/` |

## General Conformance

### DVER-001 Conformance suites

The driver runtime comes with two conformance suites: the general suite (DVER-003) and the driver suite (DVER-010). Every tool MUST run both suites on every merge and every release, on every platform it ships (DREPO-041). A tool MUST NOT skip, weaken or edit any test, and MUST NOT mark a test as an expected failure. A test that applies (DVER-010) but did not run counts as failed. The suites MUST NOT need the real application, a license or a device: the fake host stands in for them (DVER-020).

Why: The suites check the rules every Agent relies on. A skipped test hides a broken rule behind a green build.
Check: 👀 Review of the CI workflow: both suites run on every shipped platform, and no test is skipped or changed.

### DVER-002 Write samples

The tool MUST supply one valid sample for each domain write command, as the arguments of the `normal` row of its behavior matrix (DVER-030). A sample is the arguments of one call that the command accepts and that, run with `--dry-run`, returns a valid preview (G02). The general suite runs the samples in its write tests against the fake host (DVER-020). Samples for built-in and standard commands come with the suites. A command at maturity `draft` still needs its sample (DVER-040).

The same arguments serve the fake host and the real application. A sample MUST therefore name its session, document, device, output directory, job and input files only through these placeholders, which the suites fill from the fake host and the matrix runner fills from the authorized targets (DVER-050). Every row of a behavior matrix MAY use them.

<!-- contract:sample_placeholders -->
| Placeholder | With the fake host | On the real application |
|---|---|---|
| `{session}` | An instance of the fake host | An instance started for the run |
| `{doc}` | A document of the fake host: open in that instance, or a file in file mode | The authorized document or file |
| `{device}` | The fake host's device | The authorized device |
| `{dir}` | A new empty temporary directory, for output files | A new empty directory inside an authorized folder |
| `{job}` | A job the suite started with the sample of a long command | A job started in the same run: `--set job=<id>`, or the job the `normal` row of a long command starts first (`run_matrix --job-from`) |
| `{inputs}` | The project's `tests/inputs/` folder: files a command only reads, such as a design description | The same folder |

Why: The suite cannot guess valid arguments for a domain command. Without a sample, the confirmation tests cannot run on it, and keeping it in the matrix leaves one place to update.
Check: 🤖 General conformance test G00.

### DVER-003 General conformance tests

The general suite MUST contain the tests below, and every tool MUST pass all of them. In the pass conditions, "nothing written" means the documents, files and devices the test uses are unchanged afterwards. The last column names the clauses each test checks.

<!-- contract:conformance_general -->
| ID | What the test does | Pass condition | Checks |
|---|---|---|---|
| `G00` | Looks up the sample of every write command: the `normal` row of its behavior matrix, or the suites' own sample for built-in and standard commands | Every write command has a sample | DVER-002 |
| `G01` | Runs the sample of each two-step command without flags; also without its target flags, and an artifact sample whose output file already exists | Exit code 5 with `E_CONFIRMATION_REQUIRED`; `details` hold `preview`, `confirm_token` and `expires_at`; the generated command names the session and document the runtime chose; an artifact over an existing file is confirmed first; nothing written | DCLI-011, DCLI-014, DCLI-050, DCLI-051, DCLI-053, DSEC-003 |
| `G02` | Runs each sample with `--dry-run` | Exit code 0; the preview is valid, with the tier the rules give and its values from the application listed in `meta.untrusted`; nothing written | DCLI-054, DSEC-021 |
| `G03` | Gets a token for a sample, then confirms with each argument changed, each optional argument removed, one argument added, and the sample pointed at another document or device | `E_CONFLICT` with `token_mismatch`, naming the argument; nothing written | DCLI-055, DCLI-056 |
| `G04` | Confirms with the same arguments, then confirms again with the same token | The first call succeeds; the second gets `E_CONFLICT` with `token_used`; the change is written once | DCLI-055, DCLI-056 |
| `G05` | Confirms with tampered tokens | Every attempt is refused with `E_CONFLICT` `token_invalid`; nothing written; a token holds at least 128 random bits | DCLI-055 |
| `G06` | Runs the sample of a two-step command with `--format text` and no token | Exit code 5; nothing written | DCLI-014, DCLI-053 |
| `G07` | Dry-runs and confirms with no scope file; then runs each sample under a scope that lacks its effects, its targets or, for a dangerous sample, the dangerous permission, under an expired or invalid scope, and under a prefix that ends inside a name | `E_FORBIDDEN` with `details.scope` naming what is missing; `context` reports the scope status; nothing written | DCLI-050, DSEC-014, DSEC-015, DSEC-016 |
| `G08` | Makes `operations/` in the state directory unwritable, then runs the write samples and a read | Every write gets `E_IO` and nothing is written; the read still works | DCLI-059 |
| `G09` | Confirms the token of each dangerous command without `--dangerous`, then with it | Exit code 5 and nothing written; then the same token runs the write | DCLI-014, DCLI-051, DCLI-052, DCLI-053, DSEC-003 |
| `G10` | Changes the scope file's ID, or its content under the same ID, between the dry run and the confirm; separately, the application's preview can no longer apply, or shows other changes | `E_CONFLICT` with `scope_changed`, `blocked` or `preview_changed`; nothing written | DCLI-056, DSEC-015 |
| `R01` | Validates `reference` (the index), `reference --command` and `reference --all --output` | Valid structure; the index is at most 8000 characters and one command at most 6000; every command shows its tier per mode; every example parses | DCLI-030, DCLI-031, DCLI-051, DCLI-201 |
| `R02` | Walks the command tree | Follows DCLI-020 to DCLI-024; every command has a summary, an output schema and examples | DCLI-020, DCLI-021, DCLI-022, DCLI-024, DCLI-031 |
| `R03` | Sends an unknown command, an unknown flag and an extra argument | `E_USAGE` with `problem` and the suggested correct form | DCLI-011, DCLI-012, DCLI-013 |
| `R04` | Validates every envelope both suites see, after all other tests, including reads of fields that hold untrusted text, an error with the application's own message, and a long list paged through | Matches the contract, and its data matches its schema, such as the receipt's; keys are snake_case; `*_at` values are UTC; within the size limits; untrusted values are marked; every receipt made with the fake host says `simulated: true`; pages follow `next_offset` without gaps | DCLI-001, DCLI-003, DCLI-004, DCLI-005, DCLI-006, DCLI-008, DCLI-010, DCLI-011, DCLI-012, DCLI-013, DCLI-014, DCLI-061, DSEC-021, DVER-023 |
| `R05` | Runs `context`, `doctor` and `operation list` on an empty state directory | Valid output; `doctor` lists every fixed check; the state directory is protected; `context` sends no network request | DCLI-030, DCLI-032, DCLI-033, DCLI-035, DSEC-016, DSEC-032, DSKL-125 |

Why: These tests cover the write safety and the self-description that all tools share, so an Agent can trust every tool in the same way.
Check: 🤖 General conformance tests G00 to G10 and R01 to R05.

## Driver Conformance

### DVER-010 Driver conformance tests

The driver suite MUST contain the tests below, and every tool MUST pass every test that applies to it. The contract lists, for each test, when it applies under `conformance.driver[].applies_when`, with values from `conformance.applies_when_values`. A test applies when any of its values holds:

- `always`: for every tool;
- `writes`: the tool has write commands;
- `direct_writes`: the tool has a write command that runs in the direct tier, in at least one mode (DCLI-052);
- `documents`: the tool acts on documents, in file or session mode;
- `dangerous`: the tool has a command that runs in the dangerous tier, at least in some cases (DCLI-051);
- `jobs`: the tool has a command registered with `jobs: true`, whose long jobs the application runs;
- `session` or `device`: the tool supports that mode;
- `host_component`: the tool has a host component;
- `physical`: the tool has commands with the `physical` effect;
- `halt`: the tool has a halt command (DSEC-018) in session or device mode;
- `long_jobs`: the tool has a command registered `long: true`, whose job the runtime runs (DCLI-065).

The runtime, not the tool author, decides from the command registration (DCLI-029) which tests apply. It reports a test that does not apply as not applicable, with its condition. The suite runs the CLI with the fake host in place of the application, so its results are simulated results (DVER-021). The last column names the clauses each test checks.

<!-- contract:conformance_driver -->
| ID | Applies when | Scenario | Expected result | Checks |
|---|---|---|---|---|
| `D01` | `session` | Several instances; the command has no `--session` | `E_AMBIGUOUS` listing the candidates, each with its session summary | DCLI-202 |
| `D02` | `session` | The instance reconnects or restarts after the dry run; separately, it loads another host component version | The confirm is refused with `E_CONFLICT` `session_changed`, or `plugin_changed` | DCLI-056, DCLI-203 |
| `D03` | `documents` | The document, open in an instance or a file in file mode, changes after the dry run; separately, the file changes on disk, and a file-mode write names a file an instance has open | `E_CONFLICT` with `document_changed`, `file_changed_on_disk` or `file_open_in_session`; nothing sent | DCLI-056, DCLI-201, DCLI-210, DCLI-211 |
| `D04` | `session`, `device` | The application or the device is busy, before the call or between the dry run and the confirm; or the instance is `unreachable` | `E_BUSY`, retryable, with `retry_after_ms`, or `E_BACKEND_UNAVAILABLE` for an unreachable instance; `dispatch_state` is `not_started`; once it is ready, the same token runs the write | DCLI-055, DCLI-057, DCLI-204, DCLI-276 |
| `D05` | `session` | A dialog blocks the application before dispatch; separately, the change itself opens a dialog after dispatch | Before dispatch: `E_HUMAN_REQUIRED` with `action` and a `resume` command that runs the call again. After dispatch: the operation is outcome unknown, the target stays locked and `resume` is the lookup, never the write | DCLI-066, DCLI-204 |
| `D06` | `session`, `device` | The connection drops after dispatch, for a single write and for one object of a batch | `E_OUTCOME_UNKNOWN` whose `lookup` names `operation resolve`; the target stays locked until the outcome is resolved. For the batch, `lookup` is `operation list --state unknown --batch <id>` and that object stays locked | DCLI-014, DCLI-035, DCLI-060, DCLI-063, DCLI-064, DCLI-275 |
| `D07` | `writes` | A change is applied but cannot be read back | `E_VERIFY_INCOMPLETE`, with the receipt | DCLI-057, DCLI-062 |
| `D08` | `writes` | The read-back differs from the preview's `after` value by more than the declared tolerance, for each write sample and for one object of a batch; separately, a number within the tolerance, then a `doc save` of that document when the write waits for a save | `E_VERIFY_FAILED`, with the mismatched fields; in the batch, that object's item has `E_VERIFY_FAILED`; within the tolerance, the write and the save succeed | DCLI-057, DCLI-062, DCLI-064 |
| `D09` | `session` | A change in session mode | Persistence is `unchanged` and `dirty` is `true`; after `doc save`, persistence is `saved` | DCLI-058, DCLI-061 |
| `D10` | `session` | Closing a document with unsaved changes | Refused with `E_CONFLICT` `unsaved_changes`; with `--discard` it is dangerous and refused without `--dangerous` | DCLI-212 |
| `D11` | `jobs` | Cancelling a long job, also while the instance is busy; separately, a job that ends with its outputs read back, not read back, or in part | `cancel_requested` is reported first; while the application still reports the job running, `job get` does not report it cancelled; the job's `state` becomes `cancelled` only after the application confirms it stopped; `job get` gives the `ok` and `code` the receipt derives, and a cancelled job a `message` and `details` with `next_action` | DCLI-065, DCLI-220, DCLI-221, DSEC-018 |
| `D12` | `host_component` | The host component on disk differs from the loaded version, before the call or between the dry run and the confirm | The `plugin` check of `doctor` fails; write commands are refused with `E_HUMAN_REQUIRED`, action `restart_application` | DCLI-240, DSEC-053 |
| `D13` | `always` | A bare number for a physical parameter of a domain command; separately, for `job get --wait` | `E_USAGE` whose `problem` lists the accepted units | DCLI-026, DCLI-082 |
| `D14` | `physical` | A physical effect without `--dangerous`, or with a scope that does not allow `physical` | Refused; nothing is dispatched | DSEC-014 |
| `D15` | `device` | A simulated device | The device object and the result of every command on it carry `simulated: true` | DCLI-230, DSEC-062 |
| `D16` | `session` | A direct change in session mode | Done in one call, with an operation record, read-back and a full receipt; refused like any write when there is no scope | DCLI-050, DCLI-052, DSEC-015 |
| `D17` | `direct_writes` | The scope sets `confirm` to `always` | A direct-tier command run without flags returns `E_CONFIRMATION_REQUIRED` with a preview and a token, and does not run | DCLI-052, DSEC-013 |
| `D18` | `session` | A person edits the document in the application before save | The `doc save` preview lists both the tool's changes (`origin: tool`) and the external ones (`origin: external`) | DCLI-058, DCLI-277 |
| `D19` | `always` | Registers a link that is not a Feishu link; then, with no scope file, registers a Feishu link and reads it | The first returns `E_VALIDATION` and registers nothing. The second needs two-step confirmation but no scope; `knowledge get` returns the link and the reader; the link and the description are listed in `meta.untrusted`. No network request at any point | DSEC-021, DSEC-041, DSKL-102, DSKL-104, DSKL-105, DSKL-107, DSKL-110, DSKL-122, DSKL-123 |
| `D20` | `host_component` | The host component's protocol major version is newer or older than the CLI's | The handshake fails with `E_PROTOCOL`; no change is sent | DCLI-273, DCLI-280 |
| `D21` | `host_component` | The host closes the connection before it replies | To a write: the write is recorded as outcome unknown and its target stays locked. To a read: `E_BUSY`, and the session is reported `unresponsive` | DCLI-063, DCLI-204, DCLI-275, DCLI-278 |
| `D22` | `session` | One of two instances does not respond | `session list` returns both, waiting at most 2 s for the silent one, whose `state` is `unresponsive` | DCLI-204, DCLI-205 |
| `D23` | `device` | The device is in `emergency_stop` or `fault` when a command other than a halt command acts on it: before the call, between the dry run and the confirm, or during the confirm | `E_HUMAN_REQUIRED` with action `confirm_on_device`; `dispatch_state` is `not_started`; nothing is dispatched; once the device is ready, the `resume` command runs the write | DCLI-055, DCLI-230, DSEC-061 |
| `D24` | `writes` | A batch in which one object fails; a dangerous batch with a failure; a batch stopped by a busy instance; a batch in which every object fails | `E_PARTIAL` with `details.batch`: the failed item carries its code and the others succeeded; a dangerous batch skips the rest; a busy instance stops the batch and `resume` covers only the unfinished objects; when every object fails, the code is the first object's | DCLI-064 |
| `D25` | `halt` | A halt command acts on a device that is `busy`, in `fault` or in `emergency_stop`, or on an instance that is `busy` or `blocked`; the scope sets `confirm: always` and covers the target but not the `lifecycle` effect | The command runs at once, with no `E_BUSY` and no confirmation, and returns a receipt | DCLI-230, DSEC-018 |
| `D26` | `long_jobs` | Each command registered `long: true` runs its sample; a long write also runs with a read-back that does not match, and once refused by the application with `E_CONFLICT`; a long read runs with no scope | The call returns a job at once; `job get` reports the write's receipt with verification `passed`, applied once, or `E_VERIFY_FAILED` for the mismatch; the refused job ends `failed` with the application's message and `conflict`, and its `next_action` leads to the write's first call; the read job returns its result, with `operation_id` `null` | DCLI-065, DCLI-220 |

Why: These are the failures specific to driving an application: two open projects, an unsaved change taken as done, a dialog that hangs the call, a lost connection after a write, a stop that waits behind the work it should stop.
Check: 🤖 Driver conformance tests D01 to D26.

### DVER-011 Black-box testing

The suites MUST treat the CLI as a black box. They start it from the command line as a separate process, and judge only its exit code, the envelope on stdout and what the fake host received. They MUST NOT import the tool's code or call the runtime's functions directly. Each run MUST use a temporary state directory (DSEC-034). This keeps the suites independent of the implementation language: a runtime written in another language MUST pass the same suites unchanged.

Why: An Agent sees only the command line. A test that looks inside would pass tools that fail Agents, and would tie the suites to one language.
Check: 👀 Review of the suite code: every test starts the CLI as a process.

## Fake Host

### DVER-020 Fake host

The tool-driver-cli-spec repository provides the fake host: a small program that stands in for the application in tests. It MUST stand in for both ways a tool reaches an application: the adapter inside the CLI (DCLI-260), on the fake host's own address (DVER-023), and a host component, over the host protocol (DCLI-270 to DCLI-280); both use the payloads of `contract/schemas/adapter.schema.json`. It is the reference implementation of that protocol (DCLI-281). It MUST be able to simulate:

- several instances at once
- documents in memory, with unsaved changes
- dialogs, a busy main thread, an instance that does not reply in time, and a connection that closes before the reply
- a person editing a document in the application
- long jobs
- one simulated device, which can be put in each device state (DCLI-230)

The fake host implements the standard adapter capabilities itself. For a domain command it answers in place of the tool's adapter, from the command's registration (DCLI-029) and the call's inputs. The targets are the objects the call selects with `--id`, `--ids` or the `id` of each `--input` item (DCLI-064), in the session, document or device the call names; when the call selects no object, the target is the document it names, or else its device, and a call that names neither has no target. Every other flag the command registers is a changed field. The fake host treats any object ID as existing unless a test says otherwise. It previews one change per target, applies the change to its memory and reads back the previewed values. For an `artifact` command, the files its `output` flags name start absent, and the apply writes them (DCLI-029); an `artifact` command without an `output` flag makes one file that the fake host names itself, as an application does that names the file. A document is of the kind it was created or its instance opened with (`--kind`), else of the kind an evaluation question gives its file extension, else of the kind a test sets, so a command registered with `doc_kinds` runs on a document it works on (DCLI-210). A file that is not a fake host document opens as an empty document. The tool author writes nothing for it. Tests MUST be able to put the fake host into each of these situations; make any adapter call fail, hang or read back a different value, for the whole call or for one item of a batch; and read the fake host's state, so they can see whether a change arrived, how often, and which requests broke the protocol or the payloads.

The conformance suites run the tool's real CLI and its real command registration with the fake host in place of the application. They prove that the runtime handles each situation correctly for this tool. They never run the tool's own adapter (DVER-021).

Why: The failures that matter most, such as a dialog or a lost connection, are rare and hard to cause on demand in a real application.
Check: 🤖 Driver conformance tests D01 to D26, which need each of these situations.

### DVER-021 Simulated results are not evidence

Each kind of run proves something different:

| Run | What runs | What it proves |
|---|---|---|
| Conformance suites (DVER-001) | The tool's CLI and command registration, with the fake host in place of the application and of the tool's adapter | The runtime handles every situation correctly for this tool |
| Live run (DVER-042) | The tool's real adapter, with the real application | The command works with the application; needed for `verified` |

Results obtained with the fake host or with a simulated device (DSEC-062) are simulated results. They MUST NOT be presented as results on the real application, and MUST NOT support a maturity level (DVER-040) or a ✅ verified label (DREPO-022, DREPO-023).

Why: The fake host behaves the way its authors wrote it, not the way the application behaves. Counting it as evidence would claim support that was never shown.
Check: 🤖 `check_project`: no command and no support matrix row is marked `verified` without a current `live:` record.

### DVER-022 Fake host in the specification repository's CI

The tool-driver-cli-spec repository MUST run the driver runtime, the project template and both conformance suites with the fake host in CI on Windows, macOS and Linux, on every merge and every release. It MUST also run the evaluation suite of DSKL-041 there with its scripted agents. A tool's own evaluation covers only the scenarios whose result depends on text its author wrote (DSKL-042). The runtime's unit tests MUST run in the same CI, on the same three systems. Like the two conformance suites, they are a required check: a failure blocks the merge and the release. They cover the built-in commands and every rule whose check line cites runtime unit tests.

Why: Every tool copies the runtime unchanged. A fault found here is fixed once instead of in every tool.
Check: 👀 Review of the tool-driver-cli-spec repository's CI workflow.

### DVER-023 Running a tool on the fake host

When the environment variable `<PREFIX>_FAKE_HOST` holds the address of a fake host, the runtime MUST use the fake host in place of the tool's adapter and of the application:

- It MUST send every adapter capability call (DCLI-260), CLI-side and instance-side, to that address as a request of the same name, with the framing and the handshake of the host protocol (DCLI-272, DCLI-273) and the payloads of `contract/schemas/adapter.schema.json`.
- For a tool with a host component (DCLI-240), it MUST send the instance-side calls to the instances the fake host lists under `hosts/` in the state directory (DCLI-271), so the suites also test the runtime's own host protocol client.
- Every receipt MUST carry `simulated: true`, and `context` and `doctor` MUST name the fake host as the application (DVER-021).

The runtime MUST NOT take this address from any file, flag or setting other than the variable.

Why: Black-box suites need one switch that works for a runtime in any language. Marking every receipt keeps a variable left set by mistake from passing simulated results off as real.
Check: 🤖 Every conformance test (DVER-001) reaches the tool through this variable; general conformance test R04 and the runtime unit tests check the marking.

## Behavior Matrix

### DVER-030 Behavior matrix

Every domain command, and every standard command whose adapter capabilities the tool implements (DCLI-200, DCLI-260), MUST have a behavior matrix: one JSON file per command in `tests/matrix/`, named after the command path, that validates against `contract/schemas/behavior-matrix.schema.json`. Built-in commands, standard commands that call no adapter capability, such as the `knowledge` commands, and standard commands whose capability the tool does not implement, which return `E_UNSUPPORTED`, need none; the runtime's unit tests cover them (DVER-022).

The file lists each row of the table below once; each row is one situation. A row that applies to the command holds the arguments and the expected result: the exit code and, for a failure, the error code. Once the row has run, it also holds the actual result and an evidence reference (DVER-041). A row that does not apply holds `applies: false` and the reason. The contract lists when each row applies under `behavior_matrix_rows[].applies_when`; for a row, the values are read for the command: `writes` holds when it is a write command, `session` and `device` when it supports that mode, `documents` when it acts on a document, `host_component` when it reaches the application through the host component, and `always` always. Only the rows whose condition holds are run.

<!-- contract:behavior_matrix_rows -->
| Row | Group | Applies when | Situation |
|---|---|---|---|
| `normal` Normal | General | `always` | A valid call that succeeds; for a write command, its arguments are the sample (DVER-002) |
| `empty` Empty | General | `always` | A valid call with nothing to return |
| `invalid_input` Invalid input | General | `always` | Arguments that are malformed or out of range, or a physical value without a unit |
| `not_found` Not found | General | `always` | The named object, document, session or job does not exist |
| `ambiguous` Ambiguous | General | `always` | A name matches several objects or instances |
| `conflict` Conflict | General | `writes` | A precondition fails, such as a locked target or a changed document |
| `no_scope` No scope | General | `writes` | No scope file, or one that does not cover the call |
| `busy` Busy | Driver | `session`, `device` | The application's main thread or the device is busy, or the instance does not reply in time |
| `dialog` Dialog | Driver | `session` | A dialog blocks the application |
| `disconnect` Disconnect | Driver | `session`, `device`, `host_component` | The connection drops during the call |
| `unsaved` Unsaved | Driver | `documents` | The document has unsaved changes |
| `read_back_mismatch` Read-back mismatch | Driver | `writes` | The value read back differs from the expected value by more than the declared tolerance |

A row whose condition does not hold MUST be marked as not applying. A row whose condition holds but that cannot happen for the command MUST also stay in the file, marked as not applying, with the reason.

The `read_back_mismatch` row checks that the read-back reads the application, not the tool's own expectation. A correct preview predicts what the application stores, so the row is caused from outside: `run_matrix --row read_back_mismatch` makes the tool wait before its read-back (the variable `<PREFIX>_TEST_PAUSE_BEFORE_READ_BACK`, up to `matrix.max_pause_seconds`; the tool writes `pause.json` in its state directory while it waits), and during the wait a person, or a command given with `--tamper`, changes what was just written in the application, or deletes the file a write made. In the `--tamper` command, `{python}` stands for the interpreter `run_matrix` runs on; the command runs without the variable, so it may call the tool. The wait ends early only on a real mismatch: a busy or blocked application keeps the tool waiting. `run_matrix` confirms a two-step or dangerous write with its token, as it does for every row, so the pause falls on the call that dispatches. For a long command it falls in the worker, and the row is decided by how the job ends; for a command registered with `verify_after_save` it falls at the `doc save` that follows the write. The row passes when the tool reports `E_VERIFY_FAILED`; the live record marks the run `tampered`. The row applies to every write whose result can be changed from outside in that wait, artifact writes included. It is not applicable only when it cannot be, such as a delivery to an outside system or a physical action, and the reason MUST say so.

A command that registers `doc_kinds` (DCLI-029) MUST pass its `normal` row on a document of every kind it lists; `run_matrix --kind <kind>` records the kind of each run and keeps the `normal` row's evidence of each kind in `kind_evidence`, so the newest run on each kind decides whether the row passed on it. When the objects the arguments name differ between kinds, the `normal` row MAY give `kind_arguments`, the arguments for a kind keyed by that kind; `arguments` serve the other kinds. The other rows need one kind. The `normal` row of a command registered with `verify_after_save` runs `doc save` on the same document after the write, and passes only when that save verified the change (DCLI-057).

The rows run on the real application, on authorized targets (DVER-031, DVER-050). The matrix runner `run_matrix`, which comes with this specification, fills in the placeholders (DVER-002), runs the rows that apply, writes a live record that names each row (DVER-042), and fills in each row's actual result and its `live:` evidence. It writes the scope file of each run itself, granting every effect, `dangerous` included, on the targets listed under Authorized Targets and, when `--set session=` names the instance, on its new documents, `untitled:<session-id>/`; the `no_scope` row runs with no scope file, and a scope file named in the environment is not used. A run that fails without an error code of the contract, such as a crash, gives no actual result: the live record marks it failed, and the row loses its actual result and evidence, so it counts as not run. A row whose write asks for confirmation is confirmed with its token, unless the row expects `E_CONFIRMATION_REQUIRED`; the row of a long command waits for the job to end and takes the job's outcome. A row that applies but has no actual result and evidence has not been run. A row whose actual result differs from its expected result, or whose live run has `result: fail`, has failed. A row that has not been run or has failed MUST NOT count as passed.

A row that applies but cannot be caused on demand on the real application, such as `disconnect`, MAY be marked `unrunnable`, with the reason. It then needs no live run, and the driver suite covers how the runtime handles it (DVER-010). The `normal`, `dialog` and `read_back_mismatch` rows MUST NOT be marked `unrunnable` (DVER-031).

Why: Agents meet these situations every day. A command whose behavior in them is unknown fails at the worst moment. A data file with every row lets a script see at once which row is missing or was never run.
Check: 🤖 `check_project` validates every matrix file, compares each row's `applies` with the contract's conditions and each actual result with the expected one; 👀 review of the reasons given for rows that cannot happen or cannot be caused on demand.

### DVER-031 Adapter behavior on real software

The fake host cannot show how the real adapter behaves with the real application: whether it recognizes the application's real dialogs, and whether its read-back returns the values the application really holds. Only a live run exercises the real adapter. These MUST be checked by running the behavior matrix on the real application, on authorized targets (DVER-050). A claim about them MUST cite live evidence, never a simulated result (DVER-021).

Why: A fake dialog is whatever the test says it is. Only the real application shows whether the adapter sees its dialogs and reads its values correctly.
Check: 🤖 `check_project`: in the matrix of every `verified` command, the `normal` row and, where they apply, the `dialog` and `read_back_mismatch` rows passed with `live:` evidence, and none of them is marked `unrunnable`.

## Maturity and Evidence

### DVER-040 Maturity levels

Every command MUST declare a maturity level and the evidence references behind it. `reference` shows both (DCLI-031). A level MUST NOT be higher than its evidence supports. Built-in commands, and standard commands that call no adapter capability, take the level the runtime declares, backed by the runtime unit tests (DVER-022). Each level needs the evidence of the level below it plus its own:

<!-- contract:maturity_levels -->
| Level | Meaning | Evidence needed |
|---|---|---|
| `draft` | Declared; not yet shown on the real application, and the interface may still change | None; a write command still needs its sample (G00) |
| `verified` | Run on the real application, with the real adapter and a record | Current `live:` evidence that every row of the behavior matrix that applies passed, except rows marked `unrunnable`, and that the `normal` row passed on every kind of document the command lists (DVER-030, DVER-043) |
| `stable` | Also passed evaluation, with no open severe defect | Plus current evaluation results, named by an `eval:` reference (DSKL-043) |

Only a run that passed counts as evidence (DVER-030). When evidence stops counting, the level MUST drop to the highest level the remaining evidence supports. A severe defect is one that makes a command report success for a wrong result, change something outside its target or scope, or lose the user's data.

Why: Maturity tells an Agent and a person how far to trust a command. A level above its evidence is a false promise.
Check: 🤖 `check_project`: the level of every command in `reference` is backed by the evidence that level needs.

### DVER-041 Evidence references

An evidence reference has the form `<kind>:<path>[#<anchor>]`. The `path` is relative to the root of the tool project and MUST name a file in the repository. The `anchor` is optional and names one part of that file, such as one behavior matrix row. The `kind` MUST be one of these:

<!-- contract:evidence_kinds -->
| Kind | What it shows | Where the file is |
|---|---|---|
| `live` | The command ran on the real application, with the real adapter | A live record in `docs/evidence/` (DVER-042) |
| `eval` | Agents completed the tool's evaluation questions with its Skills, on the real application | An eval report `docs/evidence/eval-*.json` for the questions in `tests/eval/` (DSKL-043) |

Each file MUST validate against its schema in `contract/schemas/`: a behavior matrix against `behavior-matrix.schema.json`, a live record against `evidence.schema.json` and an eval report against `eval-report.schema.json`.

Examples: `live:docs/evidence/2026-11-01-footprint-move.json#normal` and `eval:docs/evidence/eval-2026-11-01.json`.

Why: A reference that names a real file lets a script and a person check each claim instead of trusting it.
Check: 🤖 `check_project`: every reference parses, uses a listed kind and names an existing file, and every matrix, record and report validates against its schema.

### DVER-042 Live records

Each live run MUST leave one JSON record in `docs/evidence/` that validates against `contract/schemas/evidence.schema.json`. The record holds:

- `kind`: always `live`
- `tool`, `tool_version` and `commit`: the tool and the exact build that ran
- `application`: the name and version of the application
- `os`: the operating system as it reports itself, and `platform`: `windows`, `macos` or `linux`
- `recorded_at`: the time of the run, in UTC
- `targets`: the authorized targets the run used (DVER-050)
- `runs`: one entry per command, with the command path, its arguments as run, with the placeholders filled in (DVER-002), the exit code, `result` and the `mode` it ran in; `doc_kind` when the command lists document kinds, `fingerprint`, what the run depends on (DVER-043), and `tampered: true` for a run whose result was changed from outside during the pause (DVER-030)

`result` is `pass` when the command behaved as expected, and `fail` otherwise. When an entry names a behavior matrix row in `matrix_row`, the expected result is the one in that row. An entry MAY add the printed envelope (`envelope`) and notes. The envelope is kept as the tool wrote it and is not checked against the current envelope schema, so a record stays valid while its runs count (DVER-043). Arguments that carry secrets MUST appear as `***` (DSEC-040). The support matrix of `docs/COMPATIBILITY.md` is generated from these records (DREPO-016).

```json
{
  "kind": "live",
  "tool": "kicad-cli",
  "tool_version": "1.4.2",
  "commit": "3f9c2ab",
  "application": {"name": "KiCad", "version": "10.0.1"},
  "os": "Windows 11 24H2 x64",
  "platform": "windows",
  "recorded_at": "2026-11-01T09:30:00Z",
  "targets": ["file:///D:/e2e/demo-board/"],
  "runs": [
    {
      "command": "footprint move",
      "arguments": ["--session", "kicad-1234", "--doc", "D:/e2e/demo-board/board.kicad_pcb", "--id", "R1", "--x", "12.5mm", "--y", "30mm"],
      "exit": 0,
      "result": "pass",
      "mode": "session",
      "matrix_row": "normal",
      "fingerprint": "4b1f0c9e2d7a6b5c8e3f1a0d9c8b7a6e5f4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b"
    },
    {
      "command": "footprint move",
      "arguments": ["--session", "kicad-1234", "--doc", "D:/e2e/demo-board/board.kicad_pcb", "--id", "R1", "--x", "12.5mm", "--y", "30mm"],
      "exit": 7,
      "result": "pass",
      "mode": "session",
      "matrix_row": "busy",
      "fingerprint": "4b1f0c9e2d7a6b5c8e3f1a0d9c8b7a6e5f4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b",
      "notes": "Started during a design rule check; E_BUSY with dispatch_state not_started."
    }
  ]
}
```

Why: A record with versions, arguments and results lets anyone see what was run and run it again. A summary written from memory proves nothing.
Check: 🤖 `check_project` validates every record in `docs/evidence/` against the evidence schema.

### DVER-043 Current evidence

A run in a live record counts as evidence while nothing it depends on has changed: its `fingerprint` (DVER-042) MUST equal the fingerprint of the command as it is now. The fingerprint is the SHA-256 of two parts. The first is the command as the tool registers it and as `reference` reports it, without what cannot change what it does: `summary`, `examples`, `related_knowledge`, `output_schema`, the summaries of its flags and the descriptions in `tier_when`, and `maturity` and `evidence`; a field at its empty default is left out too. The second is the adapter's code: the files of the tool's package that git tracks or would track, except the pinned runtime `<pkg>/_driver/` and the command registrations `<pkg>/commands/`; the same files of the host component in `plugin/`, except its README and its generated manifest, of which only the methods count; and the dependencies in `pyproject.toml`. The runtime is left out because it is the same for every tool and the conformance suites check it at every sync. A new version number alone MUST NOT make evidence stale. An evaluation result counts by DSKL-043. A run or result without a fingerprint counts only while the `tool_version` of its record or report has the same major and minor version as the tool. Evidence that does not count MUST NOT support a maturity level (DVER-040).

Why: Evidence shows how the code that ran behaves. It goes stale when that code changes, not when the version number moves; running everything again for a version that changed nothing costs hours and shows nothing new.
Check: 🤖 `check_project` computes the fingerprint of each command and compares it with the runs behind its level, and the version of a run without one.

## Live Acceptance

### DVER-050 Authorized targets only

Live runs MUST use only the files, folders and devices that a person has authorized and listed under Authorized Targets in `docs/E2E.md` (DREPO-017), and instances started for the run. A file that a command only reads from the project's `tests/inputs/` folder (`{inputs}`, DVER-002) is not a target and needs no authorization; everything a command writes MUST still be an authorized target. That folder is committed, so it holds only files the author made or may publish, reviewed like code. That section is the only list of authorized targets; other documents, such as `AGENTS.md`, point to it. A target is a folder or file made for testing, or a device set up for it; a target that covers a whole drive or the home folder is refused. A target is added only by a person, or by a coding Agent when the person's own message grants it; the Agent then tells the person the line it added. The Agent keeps the descriptions of listed targets up to date itself. A listed folder is a work folder: inside it, the coding Agent makes practice copies and output folders without asking again. An application instance has no lasting ID, so a live run uses only instances started for it, by the person or by the coding Agent, with documents from the listed files and folders; the coding Agent closes only the instances it started. The tool's own locations are not targets and are not listed: its state directory, and the copy of the host component that belongs to that state directory (DSEC-050). Live runs MUST NOT touch any other project, instance or device. Every scope file used for live runs MUST grant only these targets and the new documents of the instances started for the run (DSEC-012), so the runtime refuses any write outside them.

Why: A live run makes real changes. On an unauthorized project or a production machine, a test can do real damage. One grant for a work folder lets the Agent prepare and run every test inside it without asking again, and touch nothing outside it. A single list cannot disagree with a second one.
Check: 🤖 `check_project`: every entry in the `targets` of every record in `docs/evidence/` is listed under Authorized Targets; `check_project` and `run_matrix` refuse a target that covers a whole drive or the home folder; `run_matrix` writes the scope of each run from the list; 👀 review of the other scope files used for live runs.

### DVER-051 Acceptance document

Every tool MUST describe its live acceptance in `docs/E2E.md` and `docs/E2E_zh.md`, with the sections DREPO-017 sets, in that order. A reviewer finds there which targets a person authorized (DVER-050), how the behavior matrices run on the real application (DVER-031) and which records the runs left (DVER-042).

Why: A reviewer, or the next maintainer, needs one place that says what may be run, how and where its records are.
Check: 🤖 `check_project` checks that both files exist and that their sections are complete and in order.

## Checklist

- [ ] DVER-001 Conformance suites
- [ ] DVER-002 Write samples
- [ ] DVER-003 General conformance tests
- [ ] DVER-010 Driver conformance tests
- [ ] DVER-011 Black-box testing
- [ ] DVER-020 Fake host
- [ ] DVER-021 Simulated results are not evidence
- [ ] DVER-022 Fake host in the specification repository's CI
- [ ] DVER-023 Running a tool on the fake host
- [ ] DVER-030 Behavior matrix
- [ ] DVER-031 Adapter behavior on real software
- [ ] DVER-040 Maturity levels
- [ ] DVER-041 Evidence references
- [ ] DVER-042 Live records
- [ ] DVER-043 Current evidence
- [ ] DVER-050 Authorized targets only
- [ ] DVER-051 Acceptance document
