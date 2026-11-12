# DRIVER-CLI-SPEC

English | [中文](DRIVER-CLI-SPEC_zh.md)

> Version 1.3.0 · Clauses DCLI-001 to DCLI-281

## How to Read

This specification sets how a driver tool behaves on the command line: what it prints, how it fails, how its commands are named, how it writes, how it updates itself and how it reaches the application it drives.

The rule words mean:

- MUST and MUST NOT: required. A tool that breaks the rule does not conform.
- SHOULD and SHOULD NOT: the expected behavior. Depart from it only for a reason you can state.
- MAY: allowed, not required.

Each clause has a stable ID such as DCLI-202, a title, the rule, a `Why:` line and a `Check:` line. 🤖 means a test or a script checks the clause; 👀 means a person checks it in review. G00 to G10 and R01 to R05 are general conformance tests; D01 to D26 are driver conformance tests. Other rules are referred to by clause ID only, such as DSEC-015.

A table marked `<!-- contract:NAME -->` repeats values from the machine contract, `contract/driver-contract.json`. When such a table and the contract disagree, the contract wins.

## Scope

This specification applies to driver tools: command-line tools that let AI Agents operate professional desktop applications, such as CAD, EDA and simulation software, and devices, such as machine tools and instruments. The Agent calls the tool. A person decides what the Agent may change, through the scope file.

It covers:

- the output envelope, errors and exit codes;
- command names, flags, target keys and command registration;
- built-in commands and driver standard commands;
- the write flow: confirmation tiers, previews, confirm tokens, read-back, receipts, operation records, batches and halt commands;
- self-update;
- units and coordinates;
- the driver model: modes, sessions, documents, long jobs, devices and host components;
- the adapter interface, the driver runtime and the host protocol.

It does not define what domain commands do, such as how to route a track; it only defines their names and shape. It does not cover tools that call cloud service APIs, multi-step workflow commands, or driving an application on another machine.

## Terms

| Term | Meaning |
|---|---|
| Agent | The AI that calls the tool on a person's behalf |
| driver tool | A command-line tool that lets an Agent operate a desktop application or a device |
| application | The desktop software the tool drives |
| instance, session | An instance is a running copy of the application. A session is the tool's handle on one instance and has a session ID |
| document | A file open in an instance, or, in file mode, a file the tool reads and writes directly |
| driver runtime | The shared code that implements the envelope, the built-in and standard commands, the write flow and self-update; every tool copies it unchanged |
| adapter (layer) | The tool author's code that implements the adapter capabilities (DCLI-260) for one application |
| host component | The part of a tool that is installed into the application and runs inside it, such as a plugin, an add-in or a script service |
| host protocol | The local protocol between the CLI and a host component (DCLI-270 to DCLI-281) |
| fake host | A small program that simulates applications, instances and a device for conformance tests |
| envelope | The one JSON object a command writes to stdout |
| command registration | The declaration of a command that the runtime reads: path, effects, flags, units and more (DCLI-029) |
| effect | What kind of change a command makes, such as `observe`, `change` or `physical` (DCLI-051) |
| confirmation tier | How a write is confirmed: direct, two-step or dangerous (DCLI-052) |
| halt command | A command that stops work in progress: a command with the verb `stop`, or `job cancel` (DSEC-018) |
| risk tier | T0, T1 or T2: how much harm a command can do (DSEC-001) |
| authorization scope, scope file | The effects and targets a person allows, written in the scope file (DSEC-010 to DSEC-018) |
| target key | The one way to write the identity of an object (DCLI-028) |
| dry run, preview | A run with `--dry-run` changes nothing and returns a preview of the change; a two-step write run without `--confirm` returns the same preview (DCLI-053, DCLI-054) |
| confirm token | A random, single-use value, returned with a preview and stored in the operation record, that lets the same write run once (DCLI-055) |
| confirm-time review | The checks the runtime repeats just before a write runs (DCLI-056) |
| dispatch | Sending a change to the application or the device |
| read-back | Reading the changed fields again after dispatch, to verify them |
| receipt | The report of a write, split into five outcome dimensions (DCLI-061) |
| operation record | The stored history of one write; its ID starts with `op_` (DCLI-059) |
| outcome unknown | A write that may or may not have taken effect (DCLI-063) |
| long job | Work that takes longer than one call may; the tool returns a job to poll (DCLI-065) |
| state directory | The per-user directory where the tool keeps operation records, locks, caches and adapter state (DSEC-030) |
| untrusted content | Text from documents, the application or other sources outside the tool; never treated as instructions (DSEC-020) |
| built-in command, standard command, domain command | Commands the runtime implements for every tool (DCLI-030); commands the runtime implements from adapter capabilities (DCLI-200); commands the tool author writes |
| conformance test | A test from the runtime's suites: general tests G00 to G10 and R01 to R05, driver tests D01 to D26 |
| maturity, evidence | How well a command is proven, and the records that prove it (DVER-040) |
| platform package, launcher, command entry | The npm package that holds the native program for one platform; the starter script in the main npm package; the file npm puts on PATH to start the tool |
| provenance | A signed statement that an npm package was built by the tool's release workflow |

## Runtime and Tool Responsibilities

| Area | The driver runtime provides | The tool author does |
|---|---|---|
| Output and errors | Envelope, error object, exit codes, list paging, size limits, mapping states to error codes | Domain result data; passes the application's own error text as `native_message`; declares which result fields hold untrusted content (DSEC-021) |
| Commands | Argument parsing, global, shared and target flags, target key normalization, checking every registration | Registers every domain command with the fields of DCLI-029 |
| Built-in commands | `reference`, `context`, `doctor`, `changelog`, `operation`, `update`, `--version`, `--help` | Supplies the npm package name and the CHANGELOG |
| Standard commands | `session`, `doc`, `job`, `device`, `knowledge` and `plugin` commands | Implements the adapter capabilities they call (DCLI-260) |
| Writes | Scope check, confirmation tiers, preview and token, confirm-time review, records and locks, comparing read-back with the preview, receipt and `ok` derivation, batches, long jobs, halt commands | Implements `preview`, `apply` and `read_back`, which report facts (DCLI-260); supplies the write sample of each domain write command, the `normal` row of its behavior matrix (DVER-002) |
| Units | Parsing units in parameters, refusing bare numbers and converting values to standard units | Declares the unit quantity of every physical parameter and output field; converts to the application's units inside the adapter |
| Self-update | `update`, update notices, command entries, Skill sync | Nothing beyond the npm package name |
| Adapter state | The adapter state store under `adapter/` in the state directory (DCLI-260) | Keeps there whatever the adapter must remember between calls |
| Host component | The CLI side of the host protocol; host-side libraries where provided | Writes the host component in a language the application supports |
| Tests | General and driver conformance suites, fake host | The behavior matrix of each command and live acceptance on the real application (DVER-030, DVER-031) |

## Output

### DCLI-001 One envelope on stdout

With the default `--format json`, a command MUST write exactly one JSON envelope to stdout, followed by one newline, and nothing else. Progress, warnings and diagnostics MUST go to stderr. When stderr is not a terminal, the tool MUST NOT write progress bars, animations or banners. `--quiet` MUST silence all stderr output except errors.

Why: The Agent parses stdout as one JSON document; one stray line breaks the parse and wastes the call.
Check: 🤖 General conformance test R04 validates every envelope the suites see.

### DCLI-002 Output formats

`--format` takes one of these values:

- `json`: the default, and the only format for Agents.
- `text`: for people. It MAY change between versions; Agents and scripts MUST NOT parse it.
- `raw`: only for commands that declare it, such as exporting a log or file content. The bytes MUST be written as they are.

`--json` MUST mean the same as `--format json`. JSON MUST be compact by default; `--pretty` indents it for people. A format the command does not support MUST return `E_USAGE`.

Why: One machine format lets an Agent read every tool the same way; a text form that may change is safe only if nobody parses it.
Check: 🤖 Runtime unit tests.

### DCLI-003 Envelope keys

A success envelope MUST have the keys `ok` and `data`, and an error envelope the keys `ok` and `error`; both carry `meta` only when it holds something (DCLI-004), and no other key. A failed command MUST NOT return `data`.

A success:

```json
{"ok":true,"data":{"id":"doc_1","path":"D:/proj/board.kicad_pcb","session":"kicad-1234","revision":"r7","dirty":true,"readonly":false,"disk":"in_sync"}}
```

An error:

```json
{"ok":false,"error":{"code":"E_BUSY","message":"KiCad is running a design rule check; nothing was sent.","retryable":true,"details":{"dispatch_state":"not_started","next_action":"Wait 5 s, then run the same command again.","retry_after_ms":5000}}}
```

Why: Fixed keys let the Agent check `ok` first and then read one known place for the result or the error.
Check: 🤖 General conformance test R04.

### DCLI-004 Meta

`meta` MUST hold only these keys, and is left out when both are:

- `notices`: a list of `{type, severity, message}`, where `severity` is `info` or `warning`. The update notice (DCLI-079) is one example. Omitted when empty.
- `untrusted`: a list of JSON paths in the envelope that hold untrusted content (DSEC-021). Omitted when empty.

`untrusted` MUST also list every text the runtime writes that embeds an untrusted value, such as a `message`, the `action` or a `label` of a preview, or a generated command (DCLI-011) that contains a name taken from a document. A notice the adapter adds (DCLI-260) has the type `application`, and its `message` is listed in `untrusted`.

Why: The Agent finds notices and untrusted markers in the same place in every tool, and a document's text cannot slip in through a sentence the tool wrote.
Check: 🤖 General conformance test R04.

### DCLI-005 Names and values

- Every key MUST be snake_case, including the keys inside `data` and `details`. Only two kinds of key keep their own spelling: the keys of a receipt's `observed`, which are target keys or object names (DCLI-061), and the keywords of a JSON Schema carried as data, such as `output_schema` in `reference` (DCLI-031).
- IDs MUST be strings.
- Times MUST be UTC in ISO 8601 with a `Z` suffix, under keys that end in `_at`. Dates without a time of day MUST be under keys that end in `_on`.
- Enum values MUST be lower snake_case.
- Keys of physical quantities carry a unit suffix, such as `x_mm` or `angle_deg`, as DCLI-081 requires.

Why: With one naming rule the Agent never guesses a key's spelling, a time zone or a unit.
Check: 🤖 General conformance test R04.

### DCLI-006 Lists

A command that returns a list MUST return `items`, `count` (the number of items in this page) and `has_more`. It MUST add `total` when the total is known, and `next_offset` when `has_more` is `true`. When nothing matches, it MUST return an empty `items` with `ok: true`, never `E_NOT_FOUND`. `--limit` and `--offset` page the `items` (DCLI-026), so the runtime MUST refuse an adapter result of a `list` command that has no `items` list, with `E_PROTOCOL`.

Why: The Agent pages every list the same way and can tell "nothing matches" from "wrong target".
Check: 🤖 General conformance test R04 checks the list keys; the "empty" row of the behavior matrix (DVER-030) covers the empty case.

### DCLI-007 Default fields

Each list command MUST declare its default fields in its registration (DCLI-029), chosen for the questions Agents most often ask about that object. `--fields a,b` MUST return only the named fields, but MUST NOT leave out the `job` handle of a read that runs as a long job (DCLI-065). `--fields all` MUST return every field.

Why: Short default answers save the Agent's context; `--fields` fetches more only when needed.
Check: 👀 Review of the default fields in `reference`.

### DCLI-008 Response size

Called with default arguments, a command MUST keep a success envelope within 8000 characters and an error envelope within 2000 characters, counting the whole envelope. An `E_CONFIRMATION_REQUIRED` envelope carries a preview and MUST keep within the success limit, 8000 characters (DCLI-054). Lists stay within the limits by paging (DCLI-006). A command whose result can be too large to fit, such as file content or a long log, MUST offer `--output <path>`: it writes the full result to that file, and the response holds only a summary and the file path. A dry run's preview is shortened first, as a confirmation's is (DCLI-054); when it still does not fit, the next action asks for fewer objects in one call. Any other success envelope that is still too long fails with `E_USAGE`, whose next action names only what the command offers: `--fields`, `--limit` for a list, `--output` for a read that has it, `job get --output`, which writes a job, result included, to a file, and `operation get --output`, which writes a long job's whole operation record, result included, to a file. After a write that ran, it says so and names the operation, so that the Agent does not send the write again.

Why: Every character the tool returns costs the Agent context; a fixed limit keeps each call cheap.
Check: 🤖 General conformance test R04 checks the size limits.

## Errors

### DCLI-010 Error object

`error` MUST have exactly the keys `code`, `message`, `retryable` and `details`.

- `message` is one or two sentences written for the model: what happened and what changed.
- `retryable: true` MUST mean that nothing changed and the same call can be repeated later. A write that is retried MUST start again at its confirmation tier (DCLI-052).

Why: The Agent picks its next step from `code` and `retryable`; the message only adds context.
Check: 🤖 General conformance test R04.

### DCLI-011 Dispatch state and next action

`details` MUST always hold these two fields:

- `dispatch_state`: whether the change request reached the application or the device. `not_started` means it was not sent, `dispatched` means it was sent, `unknown` means the tool cannot tell. A read command MUST always report `not_started`.
- `next_action`: one sentence that tells the Agent what to do next. When an exact command exists, it MUST be in backticks.

Every command the runtime writes for the Agent to run, in `next_action`, `resume`, `lookup` or `poll`, MUST be complete: it names the session, the document and every ID it needs (`--session`, `--doc`, `--id`, `--job`), so that it runs unchanged later. Every argument that contains a space or starts with `-` MUST be quoted. A generated command that embeds an untrusted value is listed in `meta.untrusted` (DCLI-004). When a document is not found at a path such as `D:projectsboard.pcb`, a Windows path whose backslashes a POSIX shell removed, `next_action` MUST say so and ask for forward slashes.

Why: After a failure the Agent's first question is whether anything was sent, and the second is what to run next; a command that relies on a default instance can run against another one later.
Check: 🤖 General conformance tests G01, R03 and R04.

### DCLI-012 Fixed details fields

These codes and cases MUST carry these fields in `details`:

| Code or case | Field | Content |
|---|---|---|
| `E_USAGE` | `problem`, `parameter` | What is wrong, and with which parameter; for a number without a unit, `problem` lists the accepted units (DCLI-082) |
| `E_AMBIGUOUS` | `candidates` | The objects or instances that match; an instance carries the session summary of DCLI-202. The Agent picks one only when the user named it |
| `E_CONFLICT` | `conflict` | Which precondition failed, one of the contract's `conflict_values` (DCLI-056, DCLI-201, DCLI-206, DCLI-211, DCLI-212). A conflict inside the application, such as another model of that name already open or a model another one uses, is `application_state`, and `message` says what stands in the way; it is for a read or a capability that has no preview of its own, such as `open_doc` or `close_doc`, or a dispatch the state changed under. A domain command's preview reports such a conflict as `can_apply: false` with `blocked_by` (DCLI-054) |
| `E_FORBIDDEN` | `scope` | `{missing, snippet}`: `missing` is `{effects, targets, dangerous}`, what the scope lacks; `snippet` is a scope fragment the person can paste into the scope file (DSEC-015) |
| `E_OUTCOME_UNKNOWN` | `lookup` | The exact command that settles the outcome (DCLI-063) |
| `E_BUSY` | `retry_after_ms` | How long to wait before trying again |
| `E_HUMAN_REQUIRED` | `action`, `resume` | What a person must do, and the command to run afterwards (DCLI-066) |
| `E_CONFIRMATION_REQUIRED` | `preview`, `confirm_token`, `expires_at` | The preview and a token that confirms it (DCLI-053) |
| `E_PARTIAL` | `receipt`, `batch` or `update` | The receipt of a single write (DCLI-062), the result of a batch (DCLI-064), or the result of `update` when the Skill sync failed (DCLI-078) |
| `E_VERIFY_FAILED`, `E_VERIFY_INCOMPLETE` | `receipt` or `batch` | The receipt of a single write, or the result of a batch |
| Any other write whose receipt gives `ok: false` | `receipt` | The receipt of the write (DCLI-062) |
| Any other batch that does not fully succeed | `batch` | The result of the batch, `{items, summary}` (DCLI-064) |
| The application reported an error | `native_message` | The application's own error text, listed in `meta.untrusted` |
| A knowledge entry explains the fix | `knowledge` | The IDs of the related knowledge entries (DSKL-124) |

Why: Fixed fields let the Agent act on an error without parsing the message.
Check: 🤖 General conformance test R03 checks `problem` on usage errors; R04 checks every envelope against the contract.

### DCLI-013 Error codes

A tool MUST use only the codes in this table and MUST NOT invent codes. A new code is first added to the contract, then to this table. Each code has a fixed exit code and a fixed `retryable` value.

<!-- contract:error_codes -->
| Code | Exit | Retryable | Meaning |
|---|---|---|---|
| `E_USAGE` | 2 | no | The command line is wrong: unknown command or flag, missing argument, or invalid value |
| `E_VALIDATION` | 2 | no | Well-formed, but the application or the tool rejects the content, such as a coordinate outside the board outline |
| `E_AMBIGUOUS` | 2 | no | A name matches several objects or instances; pick one from `candidates` |
| `E_UNSUPPORTED` | 2 | no | The request is valid, but this application version, mode or adapter cannot do it |
| `E_NOT_FOUND` | 3 | no | The object, document, session, job or operation does not exist |
| `E_AUTH` | 4 | no | Credentials that a networked device or service requires are missing, invalid or expired |
| `E_FORBIDDEN` | 4 | no | The authorization scope does not grant this operation; `scope` says what is missing |
| `E_CONFIG` | 4 | no | Local configuration is missing or invalid |
| `E_BACKEND_UNAVAILABLE` | 4 | no | The application is not installed, not running, not licensed or not reachable; nothing was dispatched |
| `E_CONFIRMATION_REQUIRED` | 5 | no | Nothing changed; check the preview, then run the same command with `--confirm <token>`, adding `--dangerous` when the preview's tier is `dangerous` (DCLI-053) |
| `E_CONFLICT` | 6 | no | A precondition failed; `conflict` says which (DCLI-056) |
| `E_NETWORK` | 7 | yes | The network is unreachable (self-update, networked devices); nothing was sent |
| `E_BUSY` | 7 | yes | The application's main thread, the instance or the device is busy or does not respond; nothing was dispatched; retry after `retry_after_ms` |
| `E_TIMEOUT` | 7 | yes | A read timed out; nothing changed |
| `E_OUTCOME_UNKNOWN` | 8 | no | The change may have taken effect; run `lookup` before anything else and never resend the write (DCLI-063) |
| `E_HUMAN_REQUIRED` | 9 | no | A person must act first (`action`), then the Agent runs `resume` (DCLI-066) |
| `E_PARTIAL` | 1 | no | The change took effect in part; read `receipt`, `batch` or `update`, and resend only what failed |
| `E_VERIFY_FAILED` | 1 | no | The change took effect, but the read-back does not match the preview |
| `E_VERIFY_INCOMPLETE` | 1 | no | The change took effect, but it could not be read back to verify it |
| `E_NATIVE_FAILURE` | 1 | no | The application refused or rolled back the operation, or the recipient rejected the delivery; no lasting change remains |
| `E_PROTOCOL` | 1 | no | The CLI and the host component speak different protocol versions, or a reply cannot be parsed |
| `E_INTEGRITY` | 1 | no | The integrity or provenance check of an update package failed; do not retry |
| `E_IO` | 1 | no | A local file or the state directory failed; nothing was dispatched |
| `E_INTERRUPTED` | 130 | yes | Interrupted before any lasting effect |
| `E_UNKNOWN` | 1 | no | Internal error; read `message` and `details` |

Why: A fixed set of codes lets one Agent handle the errors of every tool the same way.
Check: 🤖 General conformance test R03 checks `E_USAGE`; R04 checks every code against the contract.

### DCLI-014 Exit codes

The exit code MUST follow from the error code as DCLI-013 sets it. A success MUST exit with 0.

<!-- contract:exit_codes -->
| Exit | Meaning | Agent's next step |
|---|---|---|
| `0` | Success | Continue |
| `1` | Partial effect, verification failed or incomplete, application error, protocol, integrity, local I/O, internal error | Read `error.details` |
| `2` | Usage, validation, ambiguity, unsupported | Fix the request; do not retry it unchanged |
| `3` | Not found | Find the right target |
| `4` | Credentials, authorization scope, configuration, application unavailable | Fix the environment or the scope, or ask the user |
| `5` | Confirmation required | Check the preview, then run the same command with `--confirm <token>`, and `--dangerous` when the preview's tier is `dangerous` |
| `6` | Conflict | Read the state again, then decide |
| `7` | Network down, application or device busy or not responding, timeout | Wait, then retry |
| `8` | Outcome unknown | Run `lookup` first; never resend the write |
| `9` | Human required | Pass `action` on to the user; after they act, run `resume` |
| `130` | Interrupted | Check the state, then retry |

Why: A script or Agent that sees only the exit code still knows which kind of next step applies, and exit 8 alone means "look it up, never resend".
Check: 🤖 General conformance tests G01, G06 and G09 check exit code 5; driver conformance test D06 checks exit code 8; R04 checks the other codes against the contract.

## Command Shape

### DCLI-020 Noun then verb

A domain command MUST have the form `noun verb`, such as `footprint move`. The command tree SHOULD be flat: `footprint move`, not `board footprint move`.

Why: With one fixed shape the Agent can guess a command from the object and the action.
Check: 🤖 General conformance test R02.

### DCLI-021 Paths and aliases

A command path MUST be either a group or a command, never both. An alias MUST be unique among its siblings; DCLI-024 lists the names an alias may not take.

Why: If one path can mean two things, the same words run different commands.
Check: 🤖 General conformance test R02.

### DCLI-022 Verbs

A domain command MUST take its verb from this table. It MUST NOT use synonyms such as `show`, `inspect`, `info` or `status`. The names and effects of built-in and standard commands follow DCLI-030 and DCLI-200. A command with the verb `stop` MUST be registered as a halt command (DSEC-018).

<!-- contract:verbs -->
| Verb | Effect | Meaning |
|---|---|---|
| `list` | `observe` | Returns one page of objects; an empty list when nothing matches, never `E_NOT_FOUND` |
| `get` | `observe` | Returns one object; `E_NOT_FOUND` when it does not exist, `E_AMBIGUOUS` when a name matches several |
| `search` | `observe` | Finds objects that match `--query` and returns one page of results |
| `check` | `observe` | Checks rules or consistency, such as a design rule check, and changes nothing |
| `create` | `change` | Creates an object; the result carries the new object's `id` |
| `update` | `change` | Changes only the given properties and leaves the rest; `E_USAGE` when no property is given |
| `delete` | `change` | Deletes; a missing target gives `E_NOT_FOUND` already at the preview |
| `move` | `change` | Moves or repositions |
| `import` | `change` | Imports an external file into the current document |
| `render` | `artifact` | Produces a picture, a PDF or another preview |
| `export` | `artifact` | Writes the content to a file in another format |
| `run` | — | Performs an action, short or long, with the effect the command declares, such as an analysis, or a computation that only reads and returns what it worked out, such as a routing plan, declared `observe`; work longer than 60 seconds becomes a long job (DCLI-065) |
| `stop` | `lifecycle` | Halts work in progress, such as a machine cycle or a running analysis (DSEC-018) |

Why: With a short fixed verb list the Agent never wonders whether to call `show`, `info` or `get`.
Check: 🤖 General conformance test R02.

### DCLI-023 Nouns

A noun MUST be a full English word, or several full words joined by hyphens, such as `forward-annotation` or `cross-section`. The one exception is an abbreviation that is common in the industry or in the application, such as `pcb` or `bom`, which MAY be used. Invented abbreviations such as `sch`, `fab` or `kb` MUST NOT be used. The noun SHOULD be the application's own term, as used in its user interface and manual, because users and Agents use those words.

Why: The Agent maps the user's words to commands; an invented abbreviation breaks that mapping.
Check: 👀 Review of the command tree.

### DCLI-024 Reserved names

These names belong to the built-in and standard commands: `reference`, `context`, `doctor`, `changelog`, `update`, `operation`, `help`, `version`, `session`, `doc`, `job`, `device`, `plugin`, `knowledge`. A domain command MUST NOT use one of them as its noun, and an alias MUST NOT equal one of them.

Why: The built-in and standard commands then sit at the same names in every tool.
Check: 🤖 General conformance test R02.

### DCLI-025 Global flags

These are the only global flags, with the same meaning in every tool. A tool MUST NOT add global flags. A command flag MUST NOT reuse the name of a global flag.

<!-- contract:global_flags -->
| Flag | Value | Meaning |
|---|---|---|
| `--format` | `json\|text\|raw` | Output format (DCLI-002) |
| `--json` | — | Same as `--format json` |
| `--pretty` | — | Indent the JSON |
| `--fields` | `<a,b\|all>` | Choose the fields to return (DCLI-007) |
| `--quiet` | — | Silence non-error output on stderr |
| `--dry-run` | — | Preview only; change nothing (DCLI-054) |
| `--confirm` | `<token>` | Run the write with the token from `E_CONFIRMATION_REQUIRED` or a dry run (DCLI-053, DCLI-055) |
| `--dangerous` | — | Required on the confirming call of a dangerous write (DCLI-053) |

Why: The Agent learns these flags once and uses them in every tool.
Check: 🤖 Runtime unit tests (argument parsing).

### DCLI-026 Shared flags

When a command offers one of these functions, it MUST use this flag name and meaning.

<!-- contract:shared_flags -->
| Flag | Value | Meaning |
|---|---|---|
| `--limit` | `<n>` | Items per page: by default 20, at most 100, for every list, domain lists included; the runtime adds `--limit` and `--offset` to every `list` command and keeps both values. Above the maximum the command returns `E_USAGE`; it never lowers the value silently |
| `--offset` | `<n>` | Start position of the page |
| `--query` | `<text>` | What `search` looks for |
| `--sort` | `<field>` | Field that orders a list |
| `--order` | `asc\|desc` | Direction of the order |
| `--state` | `<state>` | Filter by state; `all` means no filter |
| `--ids` | `<a,b,c>` | Act on several objects at once, with the same values (DCLI-064) |
| `--continue-on-error` | `true\|false` | In a batch, whether to go on after an object fails (DCLI-064) |
| `--wait` | `<duration>` | Wait until the state changes or the time runs out, then return (DCLI-220) |
| `--output` | `<path>` | Write the result, or the full preview, to a local file and return only a summary (DCLI-008, DCLI-054) |
| `--input` | `<path\|->` | Read all parameters as one JSON object from a file or from stdin; `{"items": [...]}` gives each object of a batch its own values (DCLI-064) |

A duration is a `time` quantity: it MUST be written as `<n>ms`, `<n>s`, `<n>min` or `<n>h`, and a bare number is refused as DCLI-082 sets. Other physical quantities follow DCLI-082.

Why: The same function under the same flag in every tool means the Agent does not have to look it up again.
Check: 🤖 Driver conformance test D13 for durations; 👀 review of each command's flags in `reference`.

### DCLI-027 Target flags

A command on a session, document, device or job MUST select the object with these command-level flags, under the same names in every tool; for example `job get --job 7`.

<!-- contract:target_flags -->
| Flag | Object | Meaning |
|---|---|---|
| `--session` `<id>` | session | Selects the instance (DCLI-202) |
| `--doc` `<path-or-id>` | document | Selects the document; in file mode it is the file path (DCLI-201) |
| `--device` `<id>` | device | Selects the device |
| `--job` `<id>` | job | Selects the long job |

Any other object a command acts on MUST be selected with `--id <id>` for one object or `--ids <a,b,c>` for several (DCLI-064), and the output MUST name it under the key `id`. The application's own name for the object, such as the reference designator `R1`, is the ID value: `footprint move --id R1`. Operation records and knowledge entries are selected with `--id` too.

Why: The Agent addresses the same kind of object the same way in every tool.
Check: 👀 Review of `reference`.

### DCLI-028 Target keys

A target key is the one way to write the identity of an object. Scope files, previews, receipts and operation records MUST use it. A target key is built from what a person can write down in advance, such as a file path or a device ID, because the scope file is written before any session ID exists.

<!-- contract:target_keys -->
| Kind | Format | Example |
|---|---|---|
| `file`: a whole file | `file:///<absolute-path>` | `file:///D:/proj/board.kicad_pcb` |
| `file_object`: an object in a file | `file:///<absolute-path>#<domain-path>` | `file:///D:/proj/board.kicad_pcb#/footprints/R1` |
| `untitled_document`: a new document not yet saved | `untitled:<session-id>/<n>` | `untitled:kicad-1234/1` |
| `untitled_object`: an object in a new document | `untitled:<session-id>/<n>#<domain-path>` | `untitled:kicad-1234/1#/footprints/R1` |
| `device_part`: a part of a device | `device:<device-id>/<part>` | `device:cnc-01/axis-x` |

Matching MUST be by prefix: `file:///D:/proj/` in a scope file covers every object in every file under that folder, and `untitled:` covers every new document. Before comparing, the runtime MUST normalize paths: absolute paths, one separator style, and case-insensitive comparison on Windows. A prefix MUST match only at a boundary, that is, when one of these holds: the key equals the prefix, the prefix ends with `/`, `#` or `:`, or the key continues with `/` or `#` right after the prefix. So `file:///D:/proj` covers `file:///D:/proj/board.kicad_pcb` but not `file:///D:/proj2/board.kicad_pcb`. Target locks use the same boundary rule (DCLI-060).

Why: A scope written before the work starts must still match the objects the Agent touches later.
Check: 🤖 Runtime unit tests (normalization and prefix matching).

### DCLI-029 Command registration

Every command MUST be registered before it can run. The runtime registers the built-in and standard commands; the tool author registers each domain command, lists in `long_commands` the standard writes that take the application more than a minute (DCLI-065), and lists in `doc_kinds` the kinds of document the application has, when it has several (DCLI-210). A registration holds these fields:

<!-- contract:registration_fields -->
| Field | Content |
|---|---|
| `path` | The command path, such as `footprint move` (DCLI-020) |
| `summary` | One sentence that says what the command does |
| `effects` | Its effects (DCLI-051) |
| `modes` | The modes it supports (DCLI-201) |
| `target_kind` | The kind of object it acts on, which becomes the target key's kind (DCLI-028) |
| `flags` | Every flag, each with its type (`string`, `integer`, `number`, `boolean`, `quantity`, `path` or `json`, and `list`, `input` or `duration` for the shared flags of DCLI-026), the `schema` of a `json` flag, the unit quantity of a `quantity` flag (DCLI-080), `secret: true` when it carries a secret (DSEC-040), and `output: true`, with the type `path`, when it names a file or folder the command creates (DCLI-051) |
| `untrusted_fields` | The output fields that hold untrusted text (DSEC-021) |
| `irreversible` | `true` when the application cannot take the change back; the command is then dangerous (DCLI-051) |
| `halt` | `true` for a halt command (DSEC-018) |
| `tolerances` | Per field, how far a read-back value may differ from the preview and still match (DCLI-057) |
| `default_fields` | The fields a list returns by default (DCLI-007) |
| `related_knowledge` | The IDs of related knowledge entries (DSKL-124) |

A registration MAY also hold these fields:

| Field | Content |
|---|---|
| `tier_when` | Conditions of this command that make a call stricter, each `{when, tier, description}`; the adapter's preview names the ones that hold for a call (DCLI-052) |
| `doc_kinds` | The kinds of document the command works on, when the application has several, such as `part`, `assembly` and `drawing` (DCLI-210) |
| `verify_after_save` | `true` when, in session mode, the application shows the change correctly only after the document is saved; `doc save` then verifies it (DCLI-057) |
| `long` | `true` when the work can take longer than one call may; the runtime then runs it as a long job (DCLI-065) |
| `jobs` | `true` for a write the application may run as a long job: `apply` reports the job and the runtime follows it with `job_status` (DCLI-065). A command is `jobs` or `long`, never both |

The adapter capabilities a domain command calls follow from its registration: `read` for a read; `preview`, `apply` and `read_back` for a write, and also `job_status` and `cancel` with `jobs` (DCLI-260).

A `json` flag takes a structured value, such as a pad table: on the command line, inline JSON or the path of a file that holds it; in an `--input` item, the value itself. Its `schema` is a JSON Schema of type `object` or `array`, and `reference` shows it. A physical number inside the value MUST carry the unit in its key, with the standard unit's suffix, such as `width_mm` (DCLI-081). The runtime checks the top-level type and the adapter checks the rest, answering `E_VALIDATION` when it is wrong.

The runtime MUST reject a registration that breaks these rules, such as a physical parameter without a declared unit quantity (DCLI-080), and MUST derive the confirmation tier from the registration, never take it from the author (DCLI-052). `reference` is built from the registrations (DCLI-031). `contract/commands.json` in the tool's project MUST be generated from the registrations, and `contract/schemas/commands.schema.json` validates it.

Why: One declaration per command feeds `reference`, the tier, the scope check, the masking of secrets and the read-back, so none of them can disagree.
Check: 🤖 `check_project` validates `contract/commands.json` against `commands.schema.json`; runtime unit tests check that the runtime rejects a registration that breaks a rule.

## Built-in Commands

### DCLI-030 Built-in commands

The driver runtime implements these commands for every tool. A tool author MUST NOT write them. They MUST all be declared with the effect `observe`: the tool's own state (operation records, locks, update cache) and `update` do not count as effects. `operation resolve` still needs a scope (DCLI-035).

<!-- contract:builtin_commands -->
| Command | Flags | Returns |
|---|---|---|
| `reference` | `--command <path>`, `--all --output <path>` | The index, one command, or every command written to a file (DCLI-031) |
| `context` | — | Local state, without going online (DCLI-032) |
| `doctor` | — | Health checks (DCLI-033) |
| `changelog` | `--since <version>` | Release notes from the bundled CHANGELOG (DCLI-034) |
| `operation get` | `--id`, `--output` | One operation record (DCLI-035) |
| `operation list` | `--state`, `--batch`, `--limit`, `--offset` | Operation records, newest first (DCLI-035) |
| `operation resolve` | `--id`, `--outcome`, `--evidence` | Settles an outcome-unknown write by reading the target back, or records the outcome the Agent found (DCLI-035) |
| `update` | `--check`, `--target-version <v>`, `--allow-downgrade` | Self-update (DCLI-070) |

Why: Every tool answers the same questions with the same commands, and no author spends time rebuilding them.
Check: 🤖 General conformance tests R01 and R05.

### DCLI-031 reference

`reference` MUST describe every command from its registration (DCLI-029): path, summary, flags, output schema, examples, effects, confirmation tier, maturity, modes and related knowledge entries (DSKL-124). It MUST also return a `driver` block with the supported modes, the application and its supported version range. The adapter capabilities a command needs and the evidence behind its maturity appear only in the file of `reference --all`, which the project scripts check (DVER-041).

- `tier` MUST give the confirmation tier for each mode the command supports. A tier that depends on the call MUST be described in `tier_when`, such as `doc close --discard`, closing an instance the tool did not start, a save-as or export over an existing file, or a condition the command registers itself, with its `description` (DCLI-029).
- A command that registers `doc_kinds`, `long` or `verify_after_save` MUST show them, and a command registered `jobs` shows `long` too, because it also returns a job, so the Agent knows before the call which documents it takes, that it returns a long job, and that its verification waits for the save.
- Every parameter and output field that holds a coordinate MUST name its `coordinate_system` (DCLI-084).
- A required parameter that `--input` can give, or for `--id` also `--ids` (DCLI-064), MUST name those flags in `required_without` in place of `required`, so the Agent never adds it beside them.
- `reference` without flags MUST return only the index, within 8000 characters: each command with its path and its one-sentence summary. When the summaries do not fit, it MUST return the paths alone and say in `summaries` how to get one command's description. Limits and conditions belong in the command's own description.
- `reference --command <path>` MUST return one command, within 6000 characters.
- `reference --all --output <path>` MUST write the description of every command to that file and return only a summary (DCLI-008).

Why: The Agent learns what the tool offers from one short call, and the full use of a command, including its tier in each mode, from one more; it never guesses.
Check: 🤖 General conformance tests R01 and R02.

### DCLI-032 context

`context` MUST work offline and read only local state. It returns these keys:

<!-- contract:context_keys -->
| Key | Content |
|---|---|
| `tool` | Tool name |
| `version` | Tool version |
| `spec_version` | The version of this specification the tool follows |
| `state_dir` | The state directory: its path, whether it is usable, and its actual access protection (DSEC-032) |
| `authorization` | The scope status (DSEC-016), scope ID, scope file path, allowed effects, whether dangerous writes are allowed, and the expiry time |
| `application` | The application's install location, version and license status |
| `sessions` | An overview of running instances (DCLI-202) |
| `components` | The CLI version, the host component version on disk, and the host component version each instance has loaded |
| `knowledge` | The number of builtin knowledge entries, and the team knowledge registrations: `id`, `title`, `kind`, `url` (DSKL-125) |

Why: One offline call shows the Agent where it stands before it touches anything.
Check: 🤖 General conformance test R05.

### DCLI-033 doctor

`doctor` MUST return `checks`, a list of `{check, status, message, fix}`, where `status` is `pass`, `warn` or `fail`. It MUST run these checks:

<!-- contract:doctor_checks -->
| Check | What it checks |
|---|---|
| `state_store` | The state directory is usable and protected (DSEC-032) |
| `authorization` | The status of the scope file (DSEC-016) |
| `version` | The CLI and the installed Skills have the same version (DSKL-031) |
| `update` | Whether a newer version exists (DCLI-079) |
| `application` | The application's install location and version |
| `license` | The application's license status |
| `plugin` | The host component version on disk equals the version each instance has loaded (DSEC-053) |
| `sessions` | The running instances and their states |
| `knowledge` | The entry format and the registration file, and lark-cli on PATH when team knowledge is registered (DSKL-125) |

Why: One command finds the common setup problems and says how to fix each one.
Check: 🤖 General conformance test R05 checks that every fixed check is present.

### DCLI-034 changelog

`changelog` MUST read the CHANGELOG bundled with the tool and return `current_version` and `entries`. Each entry is `{version, date, changes}`, and `changes` MUST be grouped as `added`, `changed`, `deprecated`, `removed`, `fixed` and `security`. `changelog --since <version>` MUST return only entries newer than that version.

Why: After an update the Agent can see what changed without going online.
Check: 🤖 Runtime unit tests.

### DCLI-035 Operation commands

- `operation get --id <id> [--output <path>]` MUST return one operation record. With `--output` it writes the whole record, with every file the write produced, to that file (DCLI-061).
- `operation list [--state <state>] [--batch <id>] [--limit <n>] [--offset <n>]` MUST list operation records, newest first. `--batch` takes the operation ID of a batch and lists only its items (DCLI-064).
- `operation resolve --id <id>` settles an outcome-unknown write (DCLI-063). Without `--outcome`, the runtime MUST read the target back through the adapter, compare it with the preview, record the outcome it finds with `resolved_by: read_back` and the observed values, and return them. A standard write that changes no field is read back by what it changes: `session open` and `doc create` in an instance by comparing the instances and documents with those there before it was sent, `session close`, `doc open` and `doc close` by the instances and documents now, `doc save` by whether the document is clean and its revision, `doc save-as` and `doc create` in file mode by the files written under the name or one that starts with it (DCLI-057), `job cancel` by the job's state, and `plugin install` and `plugin remove` by the version on disk. An instance that is starting, busy or blocked says so first, with this command as the one to run again (DCLI-204). When what it changes cannot be read or does not tell the outcome, such as no new instance listed yet, or unsaved changes in a document that changed after the save was sent, the runtime MUST NOT guess the outcome; it says so, and `--outcome` is allowed.
- `operation resolve --id <id> --outcome applied|not_applied|partial --evidence <text>` records an outcome the Agent found by other means, with `resolved_by: agent`. It is allowed only when the target cannot be read back; otherwise it MUST be refused with `E_VALIDATION`.
- Resolving MUST be refused with `E_FORBIDDEN` unless the scope in force covers the targets and effects of that operation; `details.scope` says what is missing (DSEC-017).

Why: The Agent can always look back at what it did, and settles a write whose outcome was unknown from what the application shows rather than from a guess.
Check: 🤖 General conformance test R05 runs `operation list` on an empty state; driver conformance test D06 resolves an outcome-unknown write.

### DCLI-036 Version and help

Every tool MUST support `--version`, which reports the tool version, and `--help`, which shows usage.

Why: `update` relies on `<tool> --version` to confirm what is installed (DCLI-074).
Check: 🤖 Runtime unit tests.

## Writes

### DCLI-050 Write flow

Every write MUST go through these steps in this order, with an operation record kept throughout (DCLI-059):

1. Check the authorization scope (DSEC-014, DSEC-015).
2. Build the preview (DCLI-054). A two-step or dangerous write called without `--confirm` stops here and returns `E_CONFIRMATION_REQUIRED` (DCLI-053).
3. Review the state just before dispatch (DCLI-056).
4. Lock the targets and dispatch the change (DCLI-057, DCLI-060).
5. Read back and verify (DCLI-057).
6. Return the receipt (DCLI-061).

Every tier MUST check the scope, build a preview, keep an operation record, read back and return a receipt. The direct tier skips only the confirmation; it skips no safety check. Halt commands follow the same steps with the exceptions of DSEC-018.

The Agent's write recipe is therefore the same for every write. The Agent runs the command without `--dry-run` or `--confirm`. A direct-tier write runs. A two-step or dangerous write returns `E_CONFIRMATION_REQUIRED`; the Agent checks the preview against the STOP checkpoints (DSKL-008) and runs the same command again with `--confirm <token>`, adding `--dangerous` when `preview.tier` is `dangerous`. `--dry-run` is only for looking at a change without making it (DCLI-054).

Why: One fixed flow makes every write in every tool equally careful and equally well reported, and one recipe lets the Agent write without first looking up how.
Check: 🤖 General conformance tests G01 and G07; driver conformance test D16.

### DCLI-051 Effects

Every command MUST declare its effects. The effect sets the default confirmation tier and the minimum risk tier:

<!-- contract:effects -->
| Effect | Meaning | Confirmation tier | Risk tier |
|---|---|---|---|
| `observe` | Read only | none | `T0` |
| `lifecycle` | Starts, opens, creates, closes or halts something without changing content, such as starting an instance, creating or opening a document, or stopping a job | direct | `T1` |
| `change` | Changes objects in the application or in a file | direct in session mode; two-step in file and device mode | `T1` |
| `artifact` | Creates new local files, such as an export or a save-as | direct; two-step, as for `persist`, when a target file already exists | `T1` |
| `persist` | Overwrites an existing local file, such as a save | two-step | `T1` |
| `configure` | Changes settings, permissions or host components | two-step | `T2` |
| `deliver` | Hands content to people or systems outside, such as submitting to a PLM system or sending mail | dangerous | `T2` |
| `physical` | A physical action, such as moving a machine axis or switching power on | dangerous | `T2` |

- A command that declares several effects MUST take the strictest confirmation tier among them.
- A command that creates a new file as well as changing a document, such as one that adds a board to a project, declares `artifact` with its other effects. So does a command that creates a file only in some calls, such as one that defines a symbol in a new library only when the library does not exist yet.
- An `output` flag MAY name a folder that the command writes its files into, such as a set of fabrication files. The folder exists, for the tier of `artifact`, when it holds a file or folder; the apply reports each file in `files` (DCLI-260).
- An `artifact` command whose file the application names itself has no `output` flag; its preview MUST name as a target the file or the document it goes into, so the scope covers it, and its apply reports the file in `files` (DCLI-260).
- The runtime finds an existing file of an `output` flag itself. When an `artifact` command would overwrite a file that no `output` flag names, such as a new document named by `--doc`, its preview MUST name `target_exists` in `tier_raises` (DCLI-052); the runtime registers that condition for every `artifact` command.
- A command whose change the application writes to the file at once, in session mode too, such as rules written straight into the project database, declares `persist` with `change` (DCLI-058).
- Whatever the effect, these operations MUST be dangerous: an irreversible change, discarding unsaved changes, and closing an instance the tool did not start. A change is irreversible when no command of the application or of the tool can set the earlier state back: a delete without undo is irreversible, in file mode too, while a move or a field change, which another call can set back, is not. Such a command MUST declare `irreversible: true` in its registration (DCLI-029); the runtime then makes it dangerous and never guesses.
- Halt commands declare `lifecycle` and are always direct (DSEC-018).

Why: The confirmation effort follows the risk: changes held in memory run at once, while writes to disk and to the outside world are confirmed.
Check: 🤖 General conformance test R01 checks that every command declares its effects; G01 and G09 check the tiers that follow from them.

### DCLI-052 Confirmation tiers

| Tier | What falls in it | How to call |
|---|---|---|
| direct | Changes held only in the application's memory, which can be discarded: document changes in session mode; starting instances, creating and opening documents; creating a new file that overwrites nothing; halt commands | One call runs it. The Agent MAY add `--dry-run` to see the preview first |
| two-step | Writes to disk and changes to the environment: saving and overwriting files; changes in file and device mode; changing settings; installing host components | The first call returns `E_CONFIRMATION_REQUIRED` with the preview and a token; the same command with `--confirm <token>` runs it (DCLI-053) |
| dangerous | Physical actions; handing content to the outside; irreversible changes; discarding unsaved changes; closing an instance the tool did not start | As two-step, and the confirming call also carries `--dangerous`; the scope MUST allow dangerous operations |

- The tier follows from the rules of DCLI-051 and is decided by the runtime. A tool MUST NOT lower it (DSEC-003).
- A direct-tier write whose preview touches a target key beyond those named in its inputs, such as a move that also drags connected tracks, MUST be treated as two-step.
- A command MAY register its own conditions in `tier_when` (DCLI-029), such as a delete that takes a whole sheet. When one holds for a call, the adapter's preview names it in `tier_raises`, and the runtime MUST make the call as strict as that condition's tier. A condition the command did not register MUST be refused with `E_PROTOCOL`.
- `reference` MUST show the tier of every write command in every mode (DCLI-031), so the Agent never guesses.
- When the scope file sets `confirm: always`, every write except a halt command MUST be two-step, direct-tier writes included (DSEC-013, DSEC-018).

Example: moving 20 footprints and then saving takes 3 calls with a batch command (one direct batch move, then the save: one call that returns the preview and the token, one call that confirms), and 22 calls when the footprints are moved one by one.

Why: In session mode a change stays in memory until it is saved, so confirming every small change only costs calls; the save and the outside-world effects are where confirmation protects.
Check: 🤖 General conformance test G09; driver conformance tests D16 and D17.

### DCLI-053 Confirmation required

When a two-step or dangerous command runs without `--confirm`, it MUST change nothing and return `E_CONFIRMATION_REQUIRED` with `details` = `{preview, confirm_token, expires_at}`. No separate dry run is needed: the Agent checks the preview and runs the same command again with `--confirm <token>`.

- When `preview.tier` is `dangerous`, the confirming call MUST also carry `--dangerous`. Without it, the runtime MUST change nothing and return `E_CONFIRMATION_REQUIRED` again; the token is not spent (DCLI-055). A dangerous command never runs without `--dangerous`.
- When the scope sets `confirm: always`, the Agent MUST show the preview to the person and confirm only after the person approves (DSKL-008).
- The envelope keeps within the success size limit (DCLI-008).
- `--format text` MUST NOT change any of this.

Why: A forgotten flag costs no extra call and never causes a write.
Check: 🤖 General conformance tests G01, G06 and G09.

### DCLI-054 Dry run and preview

A dry run (`--dry-run`) MUST change nothing. For a two-step or dangerous write it returns `data` = `{preview, confirm_token, expires_at}`, the same keys as the details of `E_CONFIRMATION_REQUIRED`. For a direct-tier write it returns only `{preview}`; under `confirm: always` that write is two-step and the dry run returns the token too (DSEC-013). The ID of the planned operation is `preview.operation_id`.

Every write builds a preview, in the direct tier too, where the runtime keeps it for the review before dispatch (DCLI-056). The preview holds:

<!-- contract:preview_fields -->
| Field | Content |
|---|---|
| `operation_id` | The ID of the planned operation |
| `command` | The command path |
| `action` | One sentence that says what will be done |
| `target` | `{key, kind, label}`; `key` is the target key (DCLI-028). Absent for a command with no target object, such as `session open` without `--doc`, `session close`, `doc create` without `--path`, `knowledge create`, `knowledge delete`, `plugin install`, `plugin remove` or `operation resolve` |
| `effects` | The command's effects |
| `tier` | `direct`, `two_step` or `dangerous` |
| `summary` | The number of changes, counted by `origin` and by target kind |
| `changes` | At most 50 items, one per change: `{target, field, before, after, origin}`; `origin` is `tool` or `external` (DCLI-058) |
| `changes_truncated` | `true` when there are more changes than `changes` holds |
| `session` | `{id, generation}` |
| `document` | `{id, revision, dirty}` |
| `can_apply` | Whether the operation can run |
| `blocked_by` | When it cannot run, the reasons |

- Each change names the target key of the object it changes. Changes the tool did not make are grouped, one item per object.
- `after` MUST be the value the application will actually store, such as a position snapped to the grid, not the value the Agent asked for; read-back compares with it (DCLI-057).
- The changes MUST include the direct effects the adapter can foresee, not only the fields the inputs set: a track that joins two nets lists the net it changes, so the person sees it before confirming (DCLI-068).
- With `--output <path>`, the full preview, with every change, is written to that file, and the response holds the rest of the preview (DCLI-008).
- The `action` and every `label` are listed in `meta.untrusted` when they contain untrusted text (DCLI-004).

Why: The Agent and the person see exactly what will change, and on which object, before anything happens, even when the change is large.
Check: 🤖 General conformance test G02.

### DCLI-055 Confirm token

The confirm token MUST bind these values. When any of them changes, the token MUST NOT be accepted.

<!-- contract:token_binds -->
| Bound value | Meaning |
|---|---|
| `command` | The command path |
| `inputs` | All inputs, after normalization |
| `target` | The target key |
| `changes` | The changes the preview showed, each with its value before and after |
| `session_id` | The session ID |
| `session_generation` | The session generation (DCLI-203) |
| `document_revision` | The document revision (DCLI-210) |
| `document_dirty` | Whether the document has unsaved changes |
| `plugin_version` | The host component version loaded in the instance |
| `scope_id` | The ID of the scope file |
| `scope_hash` | The hash of the scope file's content |

- The runtime MUST create the token as a random value of at least 128 bits from a cryptographically secure random source and store it in the planned operation record (DCLI-059), together with the bound values and the expiry time. A token is checked against that record; nothing is signed, and the runtime keeps no signing key. At the confirm, the runtime MUST accept a token only when it finds it in a planned operation record, before its expiry and not yet spent (DCLI-056, step 1).
- The token MUST expire after 10 minutes.
- The token MUST be usable only once, and it is spent when dispatch starts. A confirming call that is refused before dispatch, for example with `E_BUSY`, `E_HUMAN_REQUIRED`, `E_FORBIDDEN` or the conflict `target_locked`, leaves the token usable until it expires.
- Global flags (DCLI-025), such as `--dangerous`, `--format` and `--quiet`, are not inputs and are not bound.

Why: A token that binds everything it previewed cannot be spent on a different change, and a refusal that changed nothing does not force the Agent to start over.
Check: 🤖 General conformance tests G03, G04 and G05; driver conformance tests D04 and D23.

### DCLI-056 Confirm-time review

When a command runs with `--confirm`, the runtime MUST look the token up in the planned operation records (DCLI-055, DCLI-059), run these checks in order and stop at the first failure. A failed check MUST stop the write and return `E_CONFLICT` with `details.conflict` naming the check. When step 3 finds that the scope no longer grants the operation, it MUST return `E_FORBIDDEN` instead.

<!-- contract:confirm_review -->
| Step | Check | Conflict on failure |
|---|---|---|
| `1` | The token is valid, not expired and not used | `token_invalid`, `token_expired`, `token_used` |
| `2` | The command and all inputs match the token; the error names the parameter that differs | `token_mismatch` |
| `3` | The scope file is unchanged and still grants the operation | `scope_changed` |
| `4` | The target is not locked (DCLI-060) | `target_locked` |
| `5` | The session still exists, with the same generation | `session_changed` |
| `6` | The document revision and unsaved state are unchanged | `document_changed` |
| `7` | The host component version is unchanged | `plugin_changed` |
| `8` | The preview, computed again, shows the same changes and can still be applied | `preview_changed`, `blocked` |

A direct-tier command has no token. It MUST run steps 3 to 8 before dispatch, comparing with the state the runtime captured when it built the preview. For a halt command, step 3 asks only that the scope covers its target (DSEC-018), and step 4 does not apply (DCLI-060).

Why: The world can change between the preview and the confirmation; checking again just before dispatch stops a write that no longer matches what was approved.
Check: 🤖 General conformance tests G03, G04 and G10; driver conformance tests D02 and D03.

### DCLI-057 Execution and read-back

- Before dispatch, the runtime MUST check the state of the session (DCLI-204), the document (DCLI-211) and the device (DCLI-230), and the loaded host component version (DSEC-053). When one of them blocks the write, it MUST return the error those clauses give. When it is certain that nothing was sent, `dispatch_state` MUST be `not_started`.
- The runtime then dispatches the change through the adapter's `apply`. The adapter reports facts only: the dispatch state, the change, the delivery, the persistence and the observed values (DCLI-260). It MUST NOT decide whether the write succeeded.
- After dispatch, the runtime MUST read back what the change touched, from the running instance, the file or the device, also when the adapter's reply reports no lasting effect but the preview or the files the call names expect one, put the values in the receipt's `observed`, and compare them with the preview's `after` (DCLI-054). A value matches when it lies within the tolerance that the command's registration declares for that field (DCLI-029), or is equal when none is declared. A field that an earlier write changed, such as one `doc save` reads back or `doc undo` takes back, MUST be compared with the tolerance of the command whose write changed it. Verification MUST be `passed` only when every value matches. Fields that do not match MUST be listed in `mismatched`.

What the read-back checks depends on the write:

| Write | The read-back checks |
|---|---|
| Changing fields, such as `update` or `move` | Every changed field has the preview's `after` value |
| `create` | The object exists with the given fields |
| `delete` | The object is absent |
| `import` | The imported objects are present, by count |
| An `artifact` write, such as `export`, `render` or `doc save-as` | Every produced file exists, and its path, size and SHA-256 are recorded; several files are allowed. A folder an `output` flag names holds a file or folder. When the application writes a file under another name than the call gave, such as the numbered version `part.prt.1` for `part.prt`, the adapter lists it in `files` with the given path as `requested_path`, and that file counts for the given path. The receipt makes no claim about bytes the application wrote itself |
| `doc save` in session mode | The saved file is read again and the changed fields are found in it, and the document's field `saved` is `true`. A target whose last change deletes it is checked only for being absent; the fields earlier writes gave it, and the targets under it, are not read. When the application can read the file only after the document is closed and opened again, the adapter reads the fields from the instance instead and reports `saved: true` only when it saw the file on disk change. When the adapter cannot tell, verification is `incomplete` |
| A long job | The job's outputs, when it ends, as for a call of the same command (DCLI-065) |

- A write that creates files and also changes a document MUST be verified both ways: the files exist, and the changed fields read back. Verification is `passed` only when both pass. A call with no file in `files`, in its preview or in an `output` flag is verified by its other changes alone.
- A command registered with `verify_after_save` (DCLI-029) reports verification `deferred` in session mode, with a note that `doc save` verifies it, because the application shows the change correctly only after a save. The next `doc save` of that document MUST read back the deferred changes from the saved document as well, report `E_VERIFY_FAILED` when one does not match, and settle each deferred operation record with its result. The read-back after the save MUST NOT change the document or discard anything. Deferred changes are matched by session and document. When the document or its session is closed without a save, their records MUST be settled with verification `not_run`.

When anything was applied, saved or delivered, `verification` MUST NOT be `not_applicable`.

Why: A change counts as done only when the tool has seen it in place, compared with what the application was expected to store.
Check: 🤖 Driver conformance tests D04, D07 and D08.

### DCLI-058 Save is the commit point

In session mode a change is not a save. After a change in session mode, the receipt's `persistence` MUST be `unchanged` and the document's `dirty` MUST be `true`, except for a command that declares `persist`. Only `doc save`, writes in file mode (DCLI-201), writes that create files and changes of a command that declares `persist` report `saved`.

A command declares `persist` with `change` when the application writes its change to the file at once (DCLI-051). Its receipt MUST report `persistence: saved`, and the change leaves `dirty` as the document's other changes make it. The host component, or the adapter when there is none, MUST NOT list it among the changes since the last save, so the next `doc save` preview does not show it. It is verified as DCLI-057 sets.

A write with the `artifact` effect, such as `doc save-as`, `export` or `render`, creates files. Its receipt MUST report `change: not_applicable` and `persistence: saved`, and it is verified as DCLI-057 sets.

The preview of `doc save` MUST list every change since the last save: the tool's own changes with `origin: tool`, and changes the tool did not make, such as a person's edits in the application, with `origin: external`. The host component, or the adapter when there is none, keeps the list of changes since the last save (DCLI-277). When the application cannot say what changed, the preview MUST still show at least one external change, without field details, whenever the document's revision changed without a write of the tool. The runtime compares the revision with the receipt of the tool's last completed write on the document in that session (DCLI-061); when the document could not be read again after that write, it skips the comparison. A job the application runs is read when the tool first sees it ended, so a person's edit made before that counts as the tool's.

A write on the document whose outcome is unknown keeps its target locked (DCLI-060), and that lock blocks the save of the whole file. The save preview MUST then list each such operation in `blocked_by`, with the `operation resolve` command that settles it (DCLI-063).

Why: "Changed but not saved" must never look like "done", and a save must show exactly what goes to disk, including changes whose outcome nobody knows yet.
Check: 🤖 Driver conformance tests D09 and D18.

### DCLI-059 Operation records

- Every write MUST get an operation ID that starts with `op_`.
- Its record MUST be kept in the state directory. It holds the command, the target when the command has one (DCLI-054), the effects, the scope ID, the preview, the confirm token with its bound values and expiry time (DCLI-055), the receipt, the time of each stage, the state (`planned`, `dispatched`, `completed`, `failed` or `unknown`) and any outcome recorded by `operation resolve`, with `resolved_by` and the observed values or the Agent's evidence (DCLI-035).
- A batch has its own operation ID, and each of its objects is a child operation with its own record that names the batch (DCLI-064). The operation of a long job is completed when the job ends (DCLI-065).
- Records MUST be kept for 30 days.
- When operation records cannot be written, every write MUST be refused with `E_IO`, and nothing is dispatched; without records, confirm tokens can be neither stored nor checked. Reads MUST still work.

Why: Without a record, nobody can later tell what the Agent did or settle a write whose outcome is unknown.
Check: 🤖 General conformance test G08.

### DCLI-060 Target locks

- Before dispatch, the runtime MUST lock the target. Two target keys conflict when one is a prefix of the other at a boundary (DCLI-028): a lock on `file:///D:/proj/board.kicad_pcb#/footprints/R1` also blocks `doc save` of `file:///D:/proj/board.kicad_pcb`. Only one write runs at a time on conflicting targets. Another write MUST get `E_CONFLICT` with `target_locked`, and MAY be retried later.
- A batch MUST lock all its targets before it dispatches the first object, taking the locks in the sorted order of their target keys.
- A lock is released when the write ends with a known outcome, whether it succeeded or failed. The lock of a long job is released when the job ends (DCLI-065). A target whose write is outcome unknown MUST stay locked until `operation resolve` records the outcome (DCLI-063).
- Locks MUST live in the state directory and be shared by all processes of the same user, so writes from several Agents to the same target run one at a time.
- A halt command (DSEC-018) MUST NOT be blocked by the lock of the work it stops.
- The tool does not detect a person working in the application. A person's edit changes the document revision: this gives `document_changed` at confirm time (DCLI-056) and shows up as external changes in the save preview (DCLI-058).

Why: Two writers on one object, or one writer on an object and another on the whole file, can overwrite each other; a lock turns that into a clear conflict, and sorted locking keeps two batches from waiting on each other forever.
Check: 🤖 Driver conformance test D06; runtime unit tests for two processes writing to one target.

### DCLI-061 Receipt

A write that succeeds MUST return `data` = `{operation_id, receipt, result}`, where `result` is the command's own result. When the receipt gives `ok: false`, the receipt goes in `error.details.receipt` instead (DCLI-062). The runtime builds the receipt from the facts the adapter reports and from its own read-back comparison (DCLI-057). The receipt holds:

- `operation_id`, `command`, `target` (absent for a command with no target object, DCLI-054), `effects` and `tier`;
- `outcome`, with the five dimensions below;
- `observed`: the values read back, one entry per key: each target key the adapter's `read_back` reports (DCLI-260); each produced file under `file:///<path>`, with `path`, `bytes` and `sha256`; or, for a command with no target object and for `session open`, the object's name, such as `session`, `document` or `plugin`;
- `mismatched`: each field that does not match, written `<field>@<key>`, where `<key>` is its key in `observed`;
- `session` `{id, generation}` and `document` `{id, revision, dirty}`, the document as read again after the write, or for a long job when the job ends; when it cannot be read again, the revision from before the write with `dirty: null`;
- `notes`: extra remarks;
- `simulated: true` when the result comes from a simulated device (DSEC-062);
- `more_files` `{count, command}` when the write produced more files than the contract's `receipt.listed_files` (10): `observed` keeps that many, missing files first, `count` is the number left out, and `command` is the `operation get --output` call that writes the whole record to a file (DCLI-008).

<!-- contract:receipt_dimensions -->
| Dimension | Values |
|---|---|
| `execution` | `not_started`, `running`, `completed`, `failed`, `interrupted`, `unknown` |
| `change` | `not_applicable`, `not_applied`, `applied`, `partial`, `rolled_back`, `unknown` |
| `delivery` | `not_applicable`, `not_attempted`, `delivered`, `partial`, `rejected`, `unknown` |
| `persistence` | `not_applicable`, `unchanged`, `saved`, `partial`, `unknown` |
| `verification` | `not_applicable`, `not_run`, `passed`, `failed`, `incomplete`, `deferred` |

The five dimensions MUST be reported separately and MUST NOT be merged into one success flag.

```json
{"ok":true,"data":{"operation_id":"op_7mq2xk4v","receipt":{"operation_id":"op_7mq2xk4v","command":"footprint move","target":{"key":"file:///D:/proj/board.kicad_pcb#/footprints/R1","kind":"footprint"},"effects":["change"],"tier":"direct","outcome":{"execution":"completed","change":"applied","delivery":"not_applicable","persistence":"unchanged","verification":"passed"},"observed":{"file:///D:/proj/board.kicad_pcb#/footprints/R1":{"x_mm":12.5,"y_mm":30.0}},"mismatched":[],"session":{"id":"kicad-1234","generation":3},"document":{"id":"doc_1","revision":"r8","dirty":true}},"result":{"id":"R1","x_mm":12.5,"y_mm":30.0}}}
```

Why: Ran, changed, delivered, saved and verified are different facts, and the Agent must report each one truthfully.
Check: 🤖 General conformance test R04; driver conformance test D09.

### DCLI-062 Deriving ok and the error code

A tool MUST NOT choose `ok` or the error code of a write itself. The runtime MUST derive both from the receipt with this table, using the first rule that matches. When `ok` is `false`, the receipt MUST go in `error.details.receipt`.

<!-- contract:derivation -->
| Rule | The receipt has | ok | Error code |
|---|---|---|---|
| `1` | `unknown` in execution, change, delivery or persistence, or execution `running` | false | `E_OUTCOME_UNKNOWN` |
| `2` | `partial` in change, delivery or persistence | false | `E_PARTIAL` |
| `3` | Execution `failed`, `interrupted` or `not_started`, but a lasting effect exists (change `applied`, delivery `delivered` or persistence `saved`) | false | `E_PARTIAL` |
| `4` | Execution `failed`, `interrupted` or `not_started`, and no lasting effect | false | The code of the cause; by default `E_NATIVE_FAILURE` |
| `5` | Change `rolled_back` | false | The code of the cause; by default `E_NATIVE_FAILURE` |
| `6` | Delivery `rejected` | false | The code of the cause; by default `E_NATIVE_FAILURE` |
| `7` | Verification `failed` | false | `E_VERIFY_FAILED` |
| `8` | Verification `incomplete`; or verification `not_run` while change is `applied`, persistence is `saved` or delivery is `delivered` | false | `E_VERIFY_INCOMPLETE` |
| `9` | Anything else | true | none |

Rule 8 keeps "changed but not verified" from counting as a success. For the same reason, `verification` MUST NOT be `not_applicable` when anything was applied, saved or delivered (DCLI-057).

Why: When the runtime derives `ok` from facts, no tool can report success that it has not seen.
Check: 🤖 Driver conformance tests D07 and D08; runtime unit tests cover every rule.

### DCLI-063 Outcome unknown

- When the connection closes before the reply, the application crashes or no reply arrives within the deadline after dispatch (DCLI-278), and the tool cannot tell whether the change took effect, the command MUST return `E_OUTCOME_UNKNOWN` with `dispatch_state: unknown`. A dialog that the change itself opened is handled the same way (DCLI-066).
- `details.lookup` MUST be the command that settles the outcome: `operation resolve --id <id>` for a single write, and `operation list --state unknown --batch <id>` for a batch (DCLI-064).
- The Agent MUST run that lookup first and MUST NOT resend the write.
- `operation resolve` reads the target back and records the outcome (DCLI-035). The target stays locked until then (DCLI-060); recording the outcome unlocks it. The original receipt MUST stay unchanged.

Why: Resending a write that already took effect can apply it twice; reading the target back settles the question from what is really there.
Check: 🤖 Driver conformance tests D06 and D21.

### DCLI-064 Batch operations

- A command that acts on several objects MUST take them in one of two forms. Both give one preview and one token for the whole batch.
  - `--ids a,b,c` applies the same values to every object. The runtime MUST remove duplicates and keep the input order.
  - `--input <file|->` with `{"items": [{...}, ...]}` gives each object its own values, such as 20 parts moved to 20 positions. Each item names its object with `id`; two items for the same object MUST return `E_USAGE`. An item of a `create` command MAY omit `id`: it makes a new object, and the adapter's preview gives it a target key.
- An empty list MUST return `E_USAGE`. When the preview shows that any object cannot run (`can_apply: false`), the whole batch MUST NOT run. The batch locks all its targets first (DCLI-060).
- The batch has its own operation ID, and each object is a child operation with its own record and receipt (DCLI-059).
- A batch that succeeds MUST return `data` = `{operation_id, items, summary}`. Each item is compact, `{target, operation_id, ok, code}`, and a skipped object is `{target, ok: false, skipped: true}`; the full receipt of an item comes from `operation get --id <operation_id>`. An item that succeeded MUST also carry its `result`, what a call for that object alone returns (DCLI-068), as far as the response has room; the record of every item keeps its result, and when some do not fit, `more_results` names the `operation get` command that gives them. `summary` is `{total, succeeded, failed, unknown, skipped}`. When the batch does not fully succeed, `error.details.batch` holds the same object.
- The batch succeeds only when every object succeeds. Otherwise the code MUST be `E_OUTCOME_UNKNOWN` when any object is outcome unknown, with `lookup` set to `operation list --state unknown --batch <operation_id>`; else `E_PARTIAL` when any object took effect; else the code of the first failed object.
- `--continue-on-error` MUST default to `true`, and to `false` for dangerous commands. When the batch stops, the remaining objects MUST be reported as skipped and counted in `summary.skipped`.
- When the session becomes `busy`, `unresponsive`, `blocked` or `lost` (DCLI-204), the batch MUST stop whatever `--continue-on-error` says, and the remaining objects are skipped. Unless an object's outcome is unknown, the code is `E_PARTIAL` when an object took effect, and otherwise the one that state gives; `details.resume` gives the command that covers only the unfinished objects.
- A tool MUST NOT claim an all-or-nothing result that the application does not provide.
- When the same operation is often needed on several objects, such as moving parts, changing properties or deleting, the tool SHOULD offer a batch form.

Why: One call for many objects saves calls, a result per object keeps the report honest when some of them fail, and an application that stops answering stops the batch at once instead of failing it object by object.
Check: 🤖 Driver conformance tests D06, D08 and D24; runtime unit tests.

### DCLI-065 Long jobs

A call that does work MUST return within 60 seconds. Work that can take longer MUST become a long job. Waiting is not work: `job get --wait` may wait longer (DCLI-220), and so may the wait before the read-back that a live behavior matrix asks for (DVER-030).

A long job runs in one of two places:

- The application runs it: the command is registered with `jobs: true` (DCLI-029), the adapter's `apply` starts the work in the application and reports the job, and `job_status` reports its state (DCLI-260).
- The runtime runs it: the command is registered with `long: true` (DCLI-029). The call does every check, records the job and starts a worker, then returns at once. The worker is the tool's own program, started again with `job get --job <id>` and the variable `<PREFIX>_JOB_WORKER=<id>`, detached from the call and with no window. It makes the adapter call without the 60-second limit, up to `jobs.worker_max_seconds` in the contract, and records the outcome in the state directory. When the instance or the device answers `E_BUSY` before the dispatch, nothing was dispatched, so the worker tries again after `retry_after_ms` for up to 10 seconds (`jobs.busy_retry_seconds` in the contract); only then does the job fail with `E_BUSY`. The tool author writes no job code.

A standard write that can take more than a minute, such as `doc open` of a large project or `session close` of an application that takes long to quit, runs the same way when the tool lists it in `long_commands` (DCLI-029); the contract's `jobs.standard_long` names the standard writes that may be listed. The command's own checks run again in the worker right before dispatch, its own read-back runs when the work ends, and its result, such as the new session of `session open`, comes with `job get`.

Writes and reads:

- Submitting a write job is a normal write (DCLI-050), usually a command with the verb `run` (DCLI-022). Its receipt reports `execution: completed` and `change: not_applicable`, because what it did was start the job. `data.result.job` MUST be `{id, state, poll}`, where `poll` is the exact command that checks the job, such as `job get --job 7 --wait 60s`. A long write takes one object per call, and a long command takes no secret flag, because the worker never sees a secret.
- A long read, such as a design rule check of a large board, changes nothing: it needs no scope and no confirmation and has no operation record. Its call returns `data.job` = `{id, state, poll}`; `job get` returns the read's result when the job ends.
- The target of a write job stays locked until the job ends (DCLI-060).
- When the worker ends without recording an outcome, a write job ends `unknown` and a read job `failed`.
- When the job reaches a final state, the runtime MUST complete the job's operation record with a full receipt: the outputs are read back, and every file the job produced is recorded with its size and SHA-256 (DCLI-057). `job get` then returns the job as DCLI-220 sets.
- A job that ends in `unknown` is settled like any outcome-unknown write, with `operation resolve` (DCLI-063).

Why: A call that hangs for minutes looks to the Agent like a dead tool, and the real outcome of a job must be reported as carefully as that of any other write.
Check: 🤖 Driver conformance tests D11 and D26; 👀 review of command durations in the behavior matrix (DVER-030).

### DCLI-066 Human required

When a dialog, a license, a sign-in, a confirmation on the device or anything else needs a person, the command MUST NOT wait or guess. It MUST return `E_HUMAN_REQUIRED` at once, with these fields in `details`:

- `action`: one of the values below;
- `resume`: the exact command to run after the person has acted (DCLI-011);
- `dispatch_state`: whether anything was dispatched.

<!-- contract:human_required_actions -->
| Action | The person must |
|---|---|
| `dismiss_dialog` | Deal with a dialog in the application |
| `activate_license` | Deal with the license |
| `sign_in` | Sign in |
| `install_component` | Install a component |
| `confirm_on_device` | Confirm on the device |
| `restart_application` | Restart or reload the application instance, for example to load a new host component (DSEC-053) |
| `other` | Do something else |

What `resume` is depends on whether the change was dispatched:

- Before dispatch, `resume` is the command to run again: the confirming command while its token is still valid (DCLI-055), otherwise the command without flags.
- After dispatch, such as when the change itself opened a dialog, the operation's state becomes `unknown` and its target stays locked (DCLI-060). `resume` MUST then be `operation resolve --id <id>` (DCLI-063), never the write again.

The dialog's title and text MUST go in `details` and MUST be listed in `meta.untrusted`.

Why: A modal dialog can block a call forever; returning at once lets the Agent ask the person, and a dialog opened by the change itself must not lead to the change being sent twice.
Check: 🤖 Driver conformance test D05; runtime unit tests for a dialog after dispatch.

### DCLI-067 Undo and guarantees

A tool MAY offer `doc undo`, which undoes the tool's most recent change. It MUST NOT offer it when the application has no undo. A tool MUST NOT claim transactions, undo, cancellation or idempotency that the application does not provide.

Why: A promise the application cannot keep is worse than no promise.
Check: 👀 Review against the capability survey (DREPO-060).

### DCLI-068 The result shows what the change made

The `result` of a write MUST describe each object the write created or changed as the application now holds it, and the direct effects of the change, so that the Agent knows from this one return whether the change is right. It MUST NOT be only a success flag.

- A track or wire: its end points and width as stored, after snapping; its layer; its net; the pins or other tracks each end connects to; whether an end is left unconnected.
- A modelling feature: whether it regenerated; its actual dimensions; the references it uses.
- The result MUST hold only the objects the change touched and their direct effects, not the whole design: for a track joined to `GND`, the net's name, not every member of `GND`.
- The result holds what the application reports without lengthy computation. A check that takes long, such as a design rule check, MUST be its own `check` command, which the Agent calls when it needs it.
- An effect the adapter can foresee is a change in the preview (DCLI-054); an effect known only after the change is in the `result`.
- When the application cannot report an effect, such as the connections of an object, the capability survey MUST say so (DREPO-060), and the result MUST NOT pretend to know it.

Read commands give the same structured view of the design: positions, sizes and connections as data. The Agent checks a change with the result, the receipt and the read commands, never by recognizing a screenshot; renders and screenshots are for people and for purely visual questions, such as whether a drawing looks crowded.

Why: An Agent that must take a screenshot after every line works slowly, pays for every image and still misreads it. A structured result is fast, exact and the same every time.
Check: 👀 Review of each write command's output schema against the capability survey; the `normal` rows of the behavior matrices show the results (DVER-030).

## Self-update

### DCLI-070 update is required

Every tool MUST provide `update`, the one that comes with the runtime (DREPO-032), and MUST NOT write its own update logic.

Why: Without one shared implementation, tools either skip self-update or each build a different one.
Check: 🤖 `check_project` checks the runtime copy by hash (DREPO-034).

### DCLI-071 Choosing the version

- `update` MUST query the npm registry for versions.
- It MUST validate every version string as semver before using it.
- It MUST refuse a downgrade unless both `--target-version` and `--allow-downgrade` are given.
- `update --check` MUST only query and install nothing.

Why: A malformed version or an unintended downgrade must never reach npm.
Check: 🤖 Runtime unit tests.

### DCLI-072 Moving the running program aside

On Windows a running program cannot be overwritten, but it can be renamed. So on Windows, before running npm, `update` MUST move the native program out of the platform package directory to a place on the same disk, by default `trash/` in the state directory. The next run of `update` MUST delete what it moved there.

Why: Otherwise npm cannot replace the program that is running the update.
Check: 🤖 Runtime unit tests.

### DCLI-073 Running npm safely

`update` MUST build the npm arguments only from the validated package name and version. It MUST NOT run npm through a shell. It MUST run the npm found by absolute path on PATH, never a file of that name in the current directory.

Why: A crafted version string or an npm planted in the working directory must not run code.
Check: 🤖 Runtime unit tests.

### DCLI-074 Checking the result

After installing, `update` MUST run `<tool> --version` and trust the version it reports. The update succeeds only when that version equals the target version. `update` MUST then check the provenance of the installed package. `provenance_status` MUST be `verified` only when that check ran and passed, and `not_checked` when it could not run. A check that ran and failed gives `E_INTEGRITY` (DCLI-078).

Why: npm can report success while the old program is still the one that runs.
Check: 🤖 Runtime unit tests.

### DCLI-075 Direct command entry

Starting the tool through the npm launcher first starts Node.js, which costs a few hundred milliseconds or more on every call, most on Windows. `update` MUST therefore point npm's command entries at the native program in the platform package. On Windows these are the three entry files used by cmd, PowerShell and Git Bash; on other systems it is one symbolic link. The launcher stays as a fallback. The result's `entry` MUST be `direct` when the entry runs the native program and `launcher` when it still goes through the launcher.

Why: An Agent makes many calls; a delay on each one adds up over a task.
Check: 🤖 Runtime unit tests.

### DCLI-076 Skill sync

After installing, `update` MUST sync installed Skills from the new package. It looks for them in the Agent Skill folders the contract lists under `update.skill_dirs`, replaces each installed Skill directory with the new copy, MUST leave symbolic links alone, and MUST NOT create a Skill that was not installed.

Why: The CLI and its Skills must stay at the same version (DSKL-030).
Check: 🤖 Runtime unit tests.

### DCLI-077 Running update again

When the tool is already at the target version, `update` MUST return `status: up_to_date` and finish any command entry change and Skill sync that was left undone, so running it again is always safe. After an install, `status` is `updated`. `update --check` installs nothing and returns `available` when a newer version exists, `up_to_date` otherwise. The result's `data` holds:

| Key | Content |
|---|---|
| `status` | `up_to_date`, `updated` or `available` |
| `current_version` | The version installed now |
| `target_version` | The version `update` aims at |
| `previous_version` | The version before this update |
| `update_available` | Whether a newer version than the installed one exists |
| `provenance_status` | `verified` or `not_checked` (DCLI-074) |
| `skill_sync_status` | `synced`, `not_needed`, `failed` or `skipped` |
| `entry` | `direct` or `launcher` (DCLI-075) |

Why: An interrupted update is finished by running the same command again.
Check: 🤖 Runtime unit tests.

### DCLI-078 Update failures

`update` MUST report each failure with this code and leave the tool in this state:

<!-- contract:update_failures -->
| Case | Situation | Code | State afterwards |
|---|---|---|---|
| `registry_unreachable` | The network is down or rate-limited while querying versions | `E_NETWORK` (retryable) | Old version, unchanged |
| `invalid_version_or_unflagged_downgrade` | An invalid version, or a downgrade without both flags | `E_USAGE` | Unchanged |
| `npm_not_found` | No npm on PATH | `E_CONFIG` | Unchanged |
| `running_program_not_movable` | On Windows, the running program cannot be moved aside | `E_IO` | Unchanged |
| `install_failed_or_wrong_version` | npm install fails, or the installed program reports the wrong version; `details` gives the manual install command | `E_IO` | Old version |
| `provenance_check_failed` | The provenance check fails | `E_INTEGRITY` (do not retry) | The installed version, reported as it is |
| `skill_sync_failed` | The tool was updated but the Skill sync failed; `details` gives the command that fixes it | `E_PARTIAL` | New tool, old Skills; run `update` again to finish |

Why: The Agent must know after every failure which version it is running and what to do next.
Check: 🤖 Runtime unit tests.

### DCLI-079 Update notices

- Only `update` and `doctor` contact the npm registry; other commands MUST NOT. `doctor` MUST wait at most 3 seconds for it and ignore a failure.
- The answer MUST be cached in the state directory. Other commands read only the cache and add an `update_available` notice to `meta.notices` at most once every 24 hours.
- The notice's `severity` MUST be `warning` when a new major version exists or the installed version is deprecated on the registry, and `info` otherwise.
- `update` updates only the CLI and the Skills, never a host component (DSEC-050). When a host component needs installing again, the result of `update` and the `doctor` output MUST give the `plugin install` command.

Why: The Agent learns about updates without any command going online, and a host component is never replaced behind the user's back.
Check: 🤖 Runtime unit tests.

## Units

### DCLI-080 Standard units

Every physical quantity MUST use one standard unit, the same in all tools:

<!-- contract:units -->
| Quantity | Unit | Key suffix | Accepted in parameters |
|---|---|---|---|
| `length` | mm | `_mm` | nm, um, mm, cm, m, mil, in |
| `angle` | deg | `_deg` | deg, rad |
| `time` | s | `_s` | ms, s, min, h |
| `temperature` | degC | `_degc` | degC, K, degF |
| `mass` | kg | `_kg` | mg, g, kg |
| `force` | N | `_n` | N, kN |
| `pressure` | Pa | `_pa` | Pa, kPa, MPa, bar |
| `speed` | mm/s | `_mm_per_s` | mm/s, mm/min, m/s |
| `angular_speed` | deg/s | `_deg_per_s` | deg/s, rad/s, rpm |
| `frequency` | Hz | `_hz` | Hz, kHz, MHz, GHz |
| `voltage` | V | `_v` | uV, mV, V, kV |
| `current` | A | `_a` | nA, uA, mA, A |
| `resistance` | ohm | `_ohm` | milliohm, ohm, kohm, megohm |
| `capacitance` | F | `_farad` | pF, nF, uF, mF, F |
| `inductance` | H | `_henry` | nH, uH, mH, H |
| `power` | W | `_w` | mW, W, kW |
| `acceleration` | mm/s2 | `_mm_per_s2` | mm/s2, m/s2 |
| `torque` | `N*m` | `_n_m` | `N*mm`, `N*m` |
| `energy` | J | `_j` | mJ, J, kJ |
| `area` | mm2 | `_mm2` | mm2, cm2, m2 |
| `volume` | mm3 | `_mm3` | mm3, cm3, l, m3 |
| `ratio` | % | `_pct` | % |

A quantity missing from this table MUST first be added to the contract. A tool MUST NOT invent a unit, a suffix or a quantity of its own.

Why: When every tool uses the same unit, an Agent can pass values between tools without converting.
Check: 🤖 Runtime unit tests (unit conversion).

### DCLI-081 Unit suffixes in output

An output key for a physical quantity MUST carry the unit suffix from DCLI-080, and its value MUST be a number in the standard unit, such as `x_mm: 12.5`. The timing fields of the error details and the host protocol (`retry_after_ms`, `deadline_ms`) are in milliseconds and are not domain quantities.

Why: The unit is visible in the key, so the Agent cannot misread a value.
Check: 👀 Review of the output schemas in `reference`.

### DCLI-082 Units in parameters

A parameter for a physical quantity MUST be given with a unit, such as `--x 100mil` or `--x 2.54mm`; the runtime converts it to the standard unit. A bare number MUST be refused with `E_USAGE`, and `problem` MUST list the units the parameter accepts. Only the units listed for the quantity in DCLI-080 are accepted; any other unit is an invalid value (`E_USAGE`).

Why: Users and manuals mix units, and a bare `100` can mean millimetres or mils; asking for the unit and converting in one place prevents unit mistakes in every tool.
Check: 🤖 Driver conformance test D13; runtime unit tests.

### DCLI-083 Display units

A tool MUST NOT rely on the display units currently set in the application. The adapter MUST convert values whenever it exchanges data with the application.

Why: A user can switch the display from mm to mil at any time; a tool that trusts the display writes wrong values.
Check: 👀 Review of the adapter; the read-back rows of the behavior matrix on the real application (DVER-031).

### DCLI-084 Coordinate systems

For every parameter and output field that holds a coordinate, `reference` MUST state the coordinate system in `coordinate_system`: the origin, the axis directions, and the object the coordinate is relative to.

Why: The same numbers mean different points when the origin or an axis direction differs.
Check: 👀 Review of `reference`.

### DCLI-085 Undeclared units

Removed: its rule is part of DCLI-029.

## Modes and Sessions

### DCLI-200 Driver standard commands

The runtime implements these commands from the adapter capabilities (DCLI-260). A tool MUST NOT offer the same function under another name, such as `session inspect`, `model open` or `service install`.

<!-- contract:standard_commands -->
| Command | Effect | Modes | Tier | Description |
|---|---|---|---|---|
| `session list` | `observe` | session | — | Every running instance: session ID, application version, state, whether the tool started it, its documents and, when blocked, what blocks it (DCLI-202) |
| `session get` | `observe` | session | — | The full state of one instance, including whether it is busy, blocked or not responding |
| `session open` | `lifecycle` | session | direct | Starts a new instance and returns its session ID; needs `open_session` |
| `session close` | `lifecycle` | session | direct; dangerous for an instance the tool did not start (DCLI-051) | Closes an instance; refused when it has unsaved changes; needs `close_session` |
| `doc list` | `observe` | file, session | — | Documents in every instance: path, revision, unsaved changes, read-only, disk state |
| `doc get` | `observe` | file, session | — | The state of one document |
| `doc create` | `lifecycle` | file, session | direct; two-step when the file at `--path` exists | Optional: creates a document, untitled or at `--path`, from `--template` when given (DCLI-212) |
| `doc open` | `lifecycle` | session | direct | Optional: opens a file in an instance |
| `doc save` | `persist` | session | two-step | Saves to the original path; the preview lists every change since the last save |
| `doc save-as` | `artifact` | session | direct; two-step when the target file exists | Optional: saves as a new file |
| `doc close` | `lifecycle` | session | direct; dangerous with `--discard` (DCLI-051) | Optional: closes; refused with unsaved changes unless `--discard` discards them |
| `doc undo` | `change` | session | direct | Optional, only when the application supports undo: undoes the tool's most recent change |
| `job list` | `observe` | all | — | Long jobs in every session |
| `job get` | `observe` | all | — | The state of one long job and `poll_after_ms`; `--wait <duration>` waits until it ends or the time runs out; `--output <path>` writes the job, its result included, to a new file (DCLI-220) |
| `job cancel` | `lifecycle` | all | direct; a halt command (DSEC-018) | Asks to cancel a long job the tool started; the result tells "cancel requested" from "confirmed stopped" |
| `device list` | `observe` | device | — | Devices: model, firmware, state, whether simulated |
| `device get` | `observe` | device | — | The state of one device |
| `knowledge list` | `observe` | all | — | Knowledge entries (DSKL-123) |
| `knowledge get` | `observe` | all | — | The body of a builtin entry; the link and reader of a team entry |
| `knowledge search` | `observe` | all | — | Finds knowledge by keyword |
| `knowledge create` | `configure` | all | two-step | Registers the link of a team knowledge document |
| `knowledge delete` | `configure` | all | two-step | Removes a team knowledge registration; the linked document is untouched |
| `plugin get` | `observe` | with a host component | — | The version on disk and the version each instance has loaded |
| `plugin install` | `configure` | with a host component | two-step | Installs or upgrades the host component (DSEC-050) |
| `plugin remove` | `configure` | with a host component | two-step | Removes the host component |

- A command whose adapter capability is missing MUST return `E_UNSUPPORTED` (DCLI-260); the commands marked optional, and `session open` and `session close`, depend on optional capabilities.
- DSKL-123 sets the confirmation and scope of `knowledge create` and `knowledge delete`.

Why: The same functions under the same names in every tool mean the Agent never relearns sessions, documents or jobs.
Check: 👀 Review of the command tree against this table.

### DCLI-201 Modes

A tool works in one or more of these modes. It MUST declare in each command's registration which modes the command supports (DCLI-029), and it MUST provide the required commands of every mode it supports.

<!-- contract:modes -->
| Mode | When | Required commands | Read-back from | Saving |
|---|---|---|---|---|
| `file` | Reads and writes files directly, without starting the application | `doc get`; `doc list` is optional | The file, read again | Writing the file is saving (`persistence: saved`) |
| `session` | Drives a running instance of the application | `session list`, `session get`, `doc list`, `doc get`, `doc save`; the other `session` and `doc` commands where the adapter has their capabilities | The running instance | Only `doc save` saves |
| `device` | Drives hardware or its controller | `device list`, `device get` | The device or its sensors | Does not apply |

In file mode, `--doc` MUST be the file path, and `revision` is a hash of the file content (DCLI-210).

When a command supports both file and session mode, the runtime MUST choose the mode for each call:

- session mode when `--session` is given, or when exactly one running instance has the document open; that instance is then selected (DCLI-202);
- file mode when no running instance has the file open.

A write in file mode that changes or saves a file that a running instance has open MUST be refused with `E_CONFLICT` and `conflict` `file_open_in_session`. A tool that runs only in file mode, and so lists no instances, lets the adapter report this conflict (DCLI-279). A write that only makes other files from the document, such as an export, MAY go ahead; when the instance may have unsaved changes, the response MUST carry a notice that it used the file on disk. A tool that runs only in file mode lets its adapter add this notice (DCLI-260). `reference` shows the tier of each mode (DCLI-031).

Why: The mode decides where the truth lives and what saving means, so every write knows where to read back; two writers on one file, one in memory and one on disk, would undo each other.
Check: 🤖 General conformance test R01 checks the mode declarations in `reference`; driver conformance test D03 checks `file_open_in_session`.

### DCLI-202 Selecting a session

When several instances are running and a command that acts on one instance has no `--session`, the command MUST return `E_AMBIGUOUS` with the candidates. It MUST NOT pick an instance by window focus or by recent use. With exactly one instance it MAY pick it and MUST name it in the output. A `--doc` that exactly one instance has open selects that instance (DCLI-201).

The list reads `session list`, `doc list` and `job list` cover every session and MUST NOT return `E_AMBIGUOUS`. Each session in `session list`, and each instance among the candidates of `E_AMBIGUOUS`, MUST carry a summary: `documents` = `[{id, path, dirty}]` and, when the session is blocked, `blocker` = `{action, title}`, where `title` is untrusted (DSEC-021), with `knowledge` when the adapter knows an entry for the dialog (DSKL-124). When the adapter cannot see every instance, `session list` MUST return `complete: false`.

Why: With two projects open, picking by focus changes the wrong project; with each instance's documents in view, the Agent can name the right one.
Check: 🤖 Driver conformance test D01.

### DCLI-203 Session object

A session MUST have at least these fields; the full definition is in the contract's `objects.schema.json`:

- `id`: the session ID. The runtime MUST derive it from the process ID and the process start time, so it stays the same across calls and is never reused by a later process;
- `application`: `{name, version}`;
- `pid`: the process ID;
- `owned`: whether this tool started the instance; the adapter remembers this in the adapter state store (DCLI-260);
- `generation`: the connection generation. It increases by one when the runtime detects a reconnect after a lost connection, or a reload of the host component (DCLI-278). Separate CLI calls to the same running instance see the same generation;
- `state`: see DCLI-204;
- `documents`: the open documents, each `{id, path, dirty}`;
- `plugin_loaded`: the version of the host component loaded in this instance, or `null` when none is loaded;
- `extra`: application-specific fields.

A session, document or device object MUST put every field that this specification does not define inside `extra`.

Why: `owned` decides whether closing is dangerous, a stable ID and `generation` let a token notice a reconnect, and `extra` keeps application fields apart from the standard ones.
Check: 🤖 Driver conformance test D02.

### DCLI-204 Session states

Writes, and reads that need the application's main thread, MUST react to each session state as this table says:

<!-- contract:session_states -->
| State | Meaning | Command reaction |
|---|---|---|
| `starting` | The instance is starting | `E_BUSY`, retryable |
| `ready` | Ready for commands | Runs normally |
| `busy` | The application reports that its main thread is busy | `E_BUSY`, retryable, with `retry_after_ms`; nothing was dispatched |
| `unresponsive` | Connected, but the instance gave no reply within 2 seconds; `since_at` says since when | `E_BUSY`, retryable; nothing was dispatched |
| `blocked` | A dialog, license window or similar needs a person | `E_HUMAN_REQUIRED` with the dialog title (untrusted) and the resume command |
| `lost` | A discovery file and a live process exist, but the connection fails (DCLI-278) | `E_BACKEND_UNAVAILABLE`; writes already dispatched are recorded as outcome unknown |
| `unreachable` | The process runs, but the tool cannot drive it, such as a second instance when the application lets a tool reach only one | `E_BACKEND_UNAVAILABLE`; nothing was dispatched |
| `closed` | The process has ended | The session ID is no longer valid; `E_NOT_FOUND` |

The adapter MUST look for a dialog each time it reports a session, also when the instance answers: some applications keep answering their automation interface while a modal dialog is open. A dialog that the tool's own running work answers itself, such as the options dialog of an export, needs no person; the session is `busy` while it is open. The adapter MUST report a dialog as `busy` only when it knows the dialog comes from the tool's own running work, such as by a title that work expects; any other dialog makes the session `blocked`.

The standard observe commands `session list`, `session get`, `doc list`, `doc get`, `device list`, `device get`, `job list` and `job get` MUST return the state as data and MUST NOT fail because of it. Halt commands run in every state but `lost`, `unreachable` and `closed` (DSEC-018).

Why: Each state has one fixed reaction, so a busy or blocked application never leaves a call hanging, and the Agent can always see the state. A dialog taken wrongly for the tool's own would leave the Agent waiting for an answer that never comes.
Check: 🤖 Driver conformance tests D04, D05, D21 and D22, and the live `dialog` row of the behavior matrix (DVER-030); 👀 review of how the adapter recognizes the dialogs of its own work.

### DCLI-205 Session read deadline

`session list`, `session get`, `doc list` and `doc get` MUST return within 5 seconds. When an instance does not answer within 2 seconds, the command MUST stop waiting for it, report its `state` as `unresponsive`, and return the other instances normally.

Of the 5 seconds, the adapter's `sessions` has 2.5 seconds (`session_read_deadline.adapter_sessions_ms` in the contract); meanwhile the runtime connects to each host component and reads its hello, which take at most 2 seconds together. The rest is for the instances' `documents`, which `doc list` asks of all instances at once. An adapter in the CLI's process MUST keep these limits itself, asking the instances at the same time and giving each its own limit of 2 seconds, because the runtime cannot stop a call on its own thread: a call that passes its deadline ends the process.

Why: One hung instance must not hide every other instance from the Agent.
Check: 🤖 Driver conformance test D22; 👀 review of the `sessions` and `documents` of an adapter in the CLI's process.

### DCLI-206 Opening and closing sessions

`session open` MUST start a new instance and return its session ID. When the application runs a separate program for some kinds of document, `--kind` names the kind and the adapter starts that program. `session close` MUST refuse to close an instance that has unsaved changes, with `E_CONFLICT` and `conflict` `unsaved_changes`. Closing an instance the tool did not start (`owned: false`) is dangerous (DCLI-051). When the adapter lacks `open_session` or `close_session`, the matching command returns `E_UNSUPPORTED` (DCLI-260).

Why: Closing loses unsaved work, and an instance the tool did not start may hold a person's work.
Check: 👀 Review of the behavior matrix (DVER-030).

## Documents

### DCLI-210 Document object

A document MUST have at least these fields; the full definition is in the contract's `objects.schema.json`:

- `id`;
- `path`: the file path, or `null` for a new document that has never been saved;
- `key`: only for a document without a path: its target key `untitled:<session-id>/<n>` (DCLI-028). The adapter MUST give it, because only the adapter knows `<n>`. A document with a path has no `key`; its target key is `file:///<path>`;
- `session`: the session that holds it, or `null` in file mode;
- `kind`: the kind of document, when the application has several, such as `part`, `assembly` or `drawing`. The tool lists them in its `doc_kinds`, and `--kind` of `session open` and `doc create` MUST then take only those, refusing another with `E_USAGE` before anything starts. A command that registers `doc_kinds` lists only kinds among them and refuses a document of another kind with `E_VALIDATION` (DCLI-029);
- `revision`: an opaque string, supplied by the adapter, that changes with every change and every save; in file mode it is a hash of the file content;
- `dirty`: whether it has unsaved changes, or `null` when the application cannot tell; `null` counts as possibly unsaved, so closing it needs `--discard` (DCLI-212);
- `readonly`: whether it may be written;
- `disk`: how the file on disk relates to the document (DCLI-211);
- `extra`: application-specific fields (DCLI-203).

Why: `revision` and `dirty` let a token and the save preview notice any change, including a person's, and `disk` shows when someone else changed the file. A wrong kind refused at once never looks like an instance that started.
Check: 🤖 Driver conformance test D03; runtime unit tests check that `--kind` takes only the tool's kinds.

### DCLI-211 Document states

Writes MUST react to a document's `disk` value as this table says:

<!-- contract:disk_states -->
| State | Meaning | Command reaction |
|---|---|---|
| `in_sync` | The file on disk is the one the document was opened from or last saved to | Runs normally |
| `changed` | Someone else changed the file on disk after it was opened or saved | Writes and saving are refused with `E_CONFLICT`, conflict `file_changed_on_disk` |
| `missing` | The file no longer exists on disk | Saving to the original path is refused with `E_CONFLICT`, conflict `file_missing` |

A write to a document with `readonly: true` MUST be refused with `E_VALIDATION`. Unsaved changes (`dirty: true`) refuse nothing by themselves; closing or discarding them follows DCLI-212. `doc list` and `doc get` report these values as data (DCLI-204).

Why: A save must never silently overwrite someone else's edits or recreate a file that was moved or deleted.
Check: 🤖 Driver conformance test D03; runtime unit tests.

### DCLI-212 Document commands

The effects and tiers of the document commands are in DCLI-200. In addition:

- `doc create` creates a document, of the kind `--kind` names, from the template `--template` names when given. Without `--path` it creates an untitled document in an instance; the command itself has no target (DCLI-054), and later writes to the new document use the target key `untitled:<session-id>/<n>` (DCLI-028), which a scope covers with the prefix `untitled:`.
- With `--path`, the target is `file:///<path>`, and the call creates that file before it returns: in file mode the tool writes it, and in session mode the application does. An application that names a new document but writes it only when saved takes no `--path`: it creates an untitled document, which `doc save-as` later writes. It is direct, and two-step when the file already exists (DCLI-051). The receipt reports `persistence: saved` once the file is written, and read-back checks that it exists (DCLI-057). In file mode `--path` is required. When a command supports both modes, `--session` selects session mode.
- `doc create` makes a document that stands on its own, with the companion files the application makes along with it, such as a project's schematic and board; the receipt lists every file. A domain command MUST NOT do the same under another name, such as `model create` (DCLI-200). Adding a part to a container the application manages, such as a board to a project, is a domain command that also declares `artifact` (DCLI-051).
- `doc create`, `doc open`, `doc save-as` and `doc close` are optional. Without the adapter capability a command needs, the runtime MUST return `E_UNSUPPORTED` (DCLI-260).
- `doc close` MUST refuse to close a document with unsaved changes, or with `dirty` `null`, with `E_CONFLICT` and `conflict` `unsaved_changes`. `doc close --discard` discards them and is dangerous (DCLI-051).
- `doc save` writes to the original path, and its preview lists every change since the last save (DCLI-058).
- `doc undo` is optional (DCLI-067).

Why: Unsaved work is lost only when someone has asked for it explicitly and confirmed it as dangerous.
Check: 🤖 Driver conformance test D10.

### DCLI-213 Documents in file mode

Removed: its rules are part of DCLI-201 and DCLI-210.

## Long Jobs

### DCLI-220 Job object and commands

A job MUST have at least these fields: `id`, `operation_id` (`null` for a read job, DCLI-065), `state`, `cancel_requested`, `started_at`, and `finished_at` once the job has ended. When the application reports progress, the job MUST also have `progress` `{done, total, unit}`; `total` is `null` when unknown.

`state` is one of `planned`, `running`, `blocked`, `completed`, `failed`, `partial`, `unknown` and `cancelled`. A `blocked` job also carries `action`: what a person must do before it can go on (DCLI-066). A job the runtime runs (DCLI-065) MUST show as `blocked` while a dialog holds its session, with `action`, and `knowledge` when the adapter names entries, taken from the session's `blocker` (DCLI-202); once the person has dealt with the dialog it shows as `running` again. When the job is in a final state (`completed`, `failed`, `partial`, `unknown` or `cancelled`), `job get` MUST also return its `receipt`, its `result`, and `ok`, derived from the receipt as DCLI-062 sets (DCLI-065). When `ok` is `false`, the job MUST also carry the error the call would have returned: its `code`, its `message`, and its `details`, such as `conflict`, `native_message`, `knowledge` and `next_action`, without a receipt, because the job carries its own. A command in the details that sends the work again, in `resume` or in `next_action`, MUST be the job's own first call, never the worker's; a write job that changed nothing has spent its token, so it starts again from that first call. A write job that a dialog, or anything else that needs a person, stops after dispatch ends `unknown`, as the call would (DCLI-066): its `code` is `E_HUMAN_REQUIRED`, and its details carry `action`, the dialog's `title` when known, `knowledge` when the adapter names entries, and `resume`, the `operation resolve` command. A read job has no receipt: it returns `ok`, and the read's `result` when `ok` is `true`. These are fields of `data`: `job get` itself succeeds, and the `error.details.receipt` of DCLI-062 belongs to the write, not to `job get`.

`job list` lists the long jobs of every session, a blocked one as `blocked`. `job get --job <id> --wait <duration>` MUST return when the job ends, becomes blocked, or the time runs out, whichever comes first. `--wait` MAY be up to 600 seconds, because waiting is not work (DCLI-065); a longer value returns `E_USAGE`. `job get` MUST return `poll_after_ms`, the time after which asking again is useful.

Why: The Agent waits for long work in bounded calls instead of one call that hangs, and reads the job's real outcome in the same shape as that of any write. A job stuck behind a dialog says so, so the Agent asks the person instead of waiting. A failed job says why and what to do next, as the call would have, instead of only that it failed.
Check: 🤖 Driver conformance tests D11 and D26; runtime unit tests for a blocked job, for a job a dialog stops after dispatch, and for the error of a failed job.

### DCLI-221 Cancelling a job

`job cancel` acts only on long jobs the tool started. It is a halt command (DSEC-018), and its target is the target of the job's operation. It MUST first report `cancel_requested: true`. The state MUST become `cancelled` only after the application confirms that the job stopped. A job cancelled on request ends with the execution `interrupted`, and its cause is `E_INTERRUPTED` (DCLI-062). When the application cannot cancel, `job cancel` MUST return `E_UNSUPPORTED`. A read job changes nothing: `job cancel` ends its worker, needs no scope, and returns the job, `cancelled` once the worker has stopped; the application may still be busy finishing the read, which a later call sees as busy. A write job the runtime runs cannot be stopped safely midway: `job cancel` MUST return `E_UNSUPPORTED`, and the job runs to its end.

Why: Reporting "cancelled" before the application stops would let the Agent start new work while the old job still runs.
Check: 🤖 Driver conformance test D11.

## Devices

### DCLI-230 Device object and states

A device MUST have at least these fields: `id`, `model`, `firmware`, `state`, `simulated` (whether it is a simulated device) and `extra` (DCLI-203). Before acting, the runtime reads the device state (DSEC-061), and every command that acts on the device, except a halt command, MUST react as this table says:

<!-- contract:device_states -->
| State | Meaning | Command reaction |
|---|---|---|
| `ready` | Ready to act | Runs normally |
| `busy` | The device is busy | `E_BUSY`, retryable |
| `fault` | The device reports a fault | `E_HUMAN_REQUIRED` with action `confirm_on_device` |
| `emergency_stop` | The emergency stop is engaged | `E_HUMAN_REQUIRED` with action `confirm_on_device` |
| `offline` | The device cannot be reached | `E_BACKEND_UNAVAILABLE` |

A halt command runs in every state except `offline` (DSEC-018). `device list` and `device get` report the state as data (DCLI-204). Results from a simulated device are marked as DSEC-062 sets.

Why: A machine in fault or emergency stop needs a person on site before anything moves, while stopping it must stay possible in every state in which it can still be reached.
Check: 🤖 Driver conformance tests D15, D23 and D25; runtime unit tests with the fake host's simulated device in each state.

## Host Components

### DCLI-240 Host component commands

When a tool has a host component, it MUST offer `plugin get`, `plugin install` and `plugin remove` (DCLI-200). `plugin get` returns the host component object: `name`, `version_on_disk`, `protocol_version`, `install_path` and `loaded` (the version each instance has loaded). Installing and upgrading MUST follow DSEC-050 to DSEC-053.

The host component's source and its manifest live in `plugin/`. The manifest `plugin/manifest.json` MUST hold `version`, the tool version; `protocol_version` (DCLI-280); `files`, the SHA-256 of every file `plugin install` copies, by path inside `plugin/`; and `methods`, the instance-side capabilities the host component implements (DCLI-260). `check_project` writes the first three from the files, and the author writes `methods`. The tool passes the manifest to the runtime, and the native program carries `plugin/` with it.

`plugin install` copies every file in `plugin/` except the manifest, `README.md` and the folder `plugin/source/`, which holds code that is built into the installed files and is never installed. A file that only the vendor's licensed tools can build or unlock, such as a compiled toolkit library, MAY be committed in `plugin/`; `plugin/README.md` MUST then name the tool and version that built it and say how to build it again. The manifest records its SHA-256 like any other file.

Why: One install path, with its checks, keeps code that runs inside the application under control. A manifest written from the files cannot disagree with them.
Check: 🤖 Driver conformance test D12; `check_project` regenerates the manifest and compares it.

## Adapter and Runtime

### DCLI-260 Adapter capabilities

Besides the domain commands, the tool author writes only the adapter capabilities in this table; the runtime does everything else (DREPO-032). When a capability is missing, the runtime MUST return `E_UNSUPPORTED` for the commands that need it, with no tool code.

<!-- contract:adapter_capabilities -->
| Capability | Side | File | Session | Device | Description |
|---|---|---|---|---|---|
| `probe` | CLI | required | required | required | Finds the application's install location, version and license |
| `sessions` | CLI | — | required | — | Lists instances and their states |
| `open_session` | CLI | — | optional | — | Starts an instance |
| `close_session` | instance | — | optional | — | Closes an instance |
| `documents` | instance | required | required | — | Lists documents and their states |
| `create_doc` | instance | optional | optional | — | Creates a document: untitled, or at a path, from a template when given |
| `open_doc` | instance | — | optional | — | Opens a document |
| `save_doc` | instance | — | required | — | Saves a document to its path |
| `save_doc_as` | instance | — | optional | — | Saves a document as a new file |
| `close_doc` | instance | — | optional | — | Closes a document |
| `undo` | instance | — | optional | — | Undoes the tool's most recent change, when the application supports undo |
| `read` | instance | required | required | required | Runs a read-only command |
| `preview` | instance | with writes | with writes | with writes | Builds the preview, with the values the application will store |
| `apply` | instance | with writes | with writes | with writes | Applies the change and reports what happened |
| `read_back` | instance | with writes | with writes | with writes | Reads back the changed fields, per target key |
| `job_status` | instance | with `jobs` commands | with `jobs` commands | with `jobs` commands | Reports the state of a long job |
| `cancel` | instance | with `jobs` commands | with `jobs` commands | with `jobs` commands | Cancels a long job |
| `devices` | CLI | — | — | required | Lists devices and their states |
| `plugin_status` | CLI | with a host component | with a host component | with a host component | Reports the host component versions |
| `plugin_install` | CLI | with a host component | with a host component | with a host component | Installs the host component |
| `plugin_remove` | CLI | with a host component | with a host component | with a host component | Removes the host component |

- A capability marked required for a mode MUST be implemented by every tool that supports that mode. "With writes" means required when the tool has write commands; "with `jobs` commands" and "with a host component" work the same way. An optional capability MAY be left out. A dash means the mode does not use the capability.
- CLI-side capabilities run in the CLI process. Instance-side capabilities run in the adapter or, as host methods, in a host component (DCLI-262, DCLI-274).
- `contract/schemas/adapter.schema.json` defines the request and the result of every capability. The in-process adapter interface and the host methods MUST use the same definitions.
- A capability reports facts: the dispatch state, the change, the delivery, the persistence and the observed values. The runtime compares the read-back with the preview and derives the receipt (DCLI-057, DCLI-062).
- A read result and the facts of a write MAY carry `notices`, each `{severity, message}`, for what the Agent should know, such as the notice of DCLI-201. The runtime puts them in `meta.notices` (DCLI-004).
- Each CLI call is a separate process. What an adapter must remember between calls, such as the instances the tool started or, without a host component, the changes since the last save (DCLI-277), MUST be kept in the adapter state store that the runtime provides under `adapter/` in the state directory (DSEC-031).

Why: The author writes only what is specific to the application; everything shared, including the judgment of success, comes from the runtime.
Check: 🤖 Runtime unit tests.

### DCLI-261 Runtime responsibilities

Removed: its rule is part of DREPO-032.

### DCLI-262 Where adapter code runs

The code for each adapter capability MUST run where the application's interface can be called, as this table says. CLI-side capabilities (DCLI-260) always run in the CLI process.

| The application's interface | Where the code runs | Language |
|---|---|---|
| Callable from the CLI: a Python API, COM, the application's own command line, file formats | In the adapter, inside the CLI process | The runtime's language |
| Callable only inside the application's process: a plugin SDK (.NET, Java, C++) or the application's own script language; or calls that must run on the application's main thread | In a host component inside the application's process, talking to the CLI over the host protocol | A language the application supports |

- A tool MAY mix both, capability by capability: a capability the adapter in the CLI process implements runs there, and every other instance-side capability goes to the host component. For example, reading files runs in the adapter and changing the model goes through the host component.
- This specification provides the driver runtime in Python. Python reaches COM, most applications' Python interfaces and common file formats; every other interface connects through the host protocol, so one runtime covers every application.

Why: The interface the application offers decides where code can run; one rule saves each author from deciding again.
Check: 👀 Review against the capability survey (DREPO-015).

### DCLI-263 Black-box conformance

Removed: its rule is part of DVER-011.

## Host Protocol

### DCLI-270 Local transport

The CLI and the host component MUST connect only on the local machine: through a named pipe on Windows and a Unix socket on macOS and Linux. The host component MUST NOT open network ports and MUST accept connections only from the current user.

Why: The host component can change the user's designs, so nobody else on the network or on the machine may reach it.
Check: 👀 Review of the host component.

### DCLI-271 Discovery files

When the host component starts, it MUST write a discovery file under `hosts/` in the state directory with `address`, `pid`, `application`, `protocol_version`, `plugin_version` and `started_at`. It MUST delete the file on exit. `session list` MUST read these files and check that each process is still alive. So that a host started by a person from the desktop writes to the same directory, `plugin install` MUST write the path of the state directory into the host component's configuration.

Why: A crashed host leaves its file behind; checking the process keeps dead instances out of the list, and one shared directory lets the CLI find instances it did not start.
Check: 🤖 Runtime unit tests.

### DCLI-272 Framing

Messages MUST be JSON-RPC 2.0. Each message MUST be the header line `Content-Length: <bytes>`, then an empty line, then the JSON body encoded in UTF-8. The header line and the empty line each end with CRLF (`\r\n`). `<bytes>` is the length of the body in bytes.

Why: A length header lets each side read whole messages from a stream without guessing where one ends.
Check: 🤖 Runtime unit tests.

### DCLI-273 Handshake

After connecting, the CLI MUST first send `hello` with `protocol_version` and `cli_version`. The host MUST reply with `protocol_version`, `plugin_version`, `application` (name and version), `session` `{id, generation}` and `methods`, the methods it supports. When the major protocol versions differ, the CLI MUST return `E_PROTOCOL` and MUST NOT send any change. Every connection MUST start with this handshake, so a different major version ends it before any change is sent. Host components SHOULD use the host libraries, which do the transport of DCLI-270 and this handshake (DCLI-281).

Why: A CLI and a host that disagree on the protocol must stop before they misunderstand a write.
Check: 🤖 Driver conformance test D20.

### DCLI-274 Methods and deadlines

The host methods MUST be the ones in this table: `hello` and the instance-side adapter capabilities (DCLI-260), each with the same name and meaning as the capability. A method's request and result are the ones `contract/schemas/adapter.schema.json` defines for that capability. CLI-side capabilities, such as `probe`, `sessions` and `plugin_install`, have no host method; they run in the CLI process. Every request MUST carry `deadline_ms`: how long, in milliseconds, the CLI waits for the reply (DCLI-278).

<!-- contract:host_methods -->
| Method | Write | Meaning |
|---|---|---|
| `hello` | no | Handshake (DCLI-273) |
| `close_session` | yes | Closes the instance |
| `documents` | no | Lists documents and their states |
| `create_doc` | yes | Creates a document |
| `open_doc` | yes | Opens a document |
| `save_doc` | yes | Saves a document |
| `save_doc_as` | yes | Saves a document as a new file |
| `close_doc` | yes | Closes a document |
| `undo` | yes | Undoes the tool's most recent change |
| `read` | no | Runs a read |
| `preview` | no | Builds the preview; for `doc save` it includes the external changes since the last save (DCLI-277) |
| `apply` | yes | Applies the change |
| `read_back` | no | Reads back the changed fields |
| `job_status` | no | Reports the state of a long job |
| `cancel` | yes | Cancels a long job |

Why: One method set and one payload definition for every tool let the runtime and the fake host speak to any host component.
Check: 🤖 Runtime unit tests.

### DCLI-275 Dispatch state in replies

A reply to a write method (marked in DCLI-274) MUST carry `dispatch_state`. When the host fails before the change starts, it MUST reply `not_started`. When the CLI gets no reply, it MUST record the write as outcome unknown (DCLI-063, DCLI-278).

Why: Only the host knows whether the change began; the CLI must not guess on its behalf.
Check: 🤖 Driver conformance tests D06 and D21.

### DCLI-276 Main thread

The host component MUST call the application's interface on the application's main thread, running one instance-side request at a time. `hello` does not wait for the main thread, and neither do `cancel` and the `apply` of a halt command (DSEC-018): the host passes them on in the way the application allows while its main thread works, such as through a stop flag the running work checks. A request that finds the main thread running an earlier request of the tool MUST wait for it, up to 1.5 seconds (`host_protocol.busy_wait_ms` in the contract) and never past the request's `deadline_ms`, and only then reply `E_BUSY`: the tool's own requests often overlap, such as a long job's worker and a read the Agent runs meanwhile. When the application itself holds the main thread, such as while a person runs a command, the host SHOULD reply `E_BUSY` at once, with `retry_after_ms` (DCLI-279), instead of holding the request. A host that cannot answer while its main thread is busy is reported `unresponsive` once the per-instance deadline passes (DCLI-205).

Why: A held request may run much later, after the Agent has already moved on; an immediate busy reply lets the Agent wait knowingly. The short wait keeps the tool's own requests from refusing each other.
Check: 🤖 Driver conformance test D04; 👀 review of how the host component takes requests.

### DCLI-277 External changes

The host never pushes messages to the CLI: each CLI call is short-lived and asks for what it needs. The host component MUST therefore keep the list of document changes made by anyone other than the tool since the last save, and MUST return it through `preview` when the CLI previews `doc save` (DCLI-058). Without a host component, the adapter keeps this list in the adapter state store (DCLI-260).

Why: The save preview can show a person's edits only if something records them between the CLI's calls.
Check: 🤖 Driver conformance test D18.

### DCLI-278 Lost connections

Each CLI call opens its own connection and closes it when the call ends; no connection stays open between calls.

- When the connection closes before the reply, or no reply arrives within `deadline_ms`, the CLI MUST record a write as outcome unknown (DCLI-063), and MUST report the instance of a read as `unresponsive` (DCLI-204).
- When `apply` has replied and only a read that follows it, such as the read-back, gets no reply in time, the outcome is known: the runtime MUST record the reported change with verification `incomplete`, never as outcome unknown, also when the call that passed its deadline ends the CLI's process or a long job's worker.
- When a discovery file names a live process but the connection to its address fails, the session MUST be reported `lost` (DCLI-204).
- When the runtime connects again to an instance after a lost connection, or detects that the host component was reloaded, the session's `generation` MUST increase by one (DCLI-203).

Why: A host that stopped answering must not look alive, and its unfinished writes must not look done.
Check: 🤖 Driver conformance test D21.

### DCLI-279 Host errors

The host MUST return only these error codes:

<!-- contract:host_error_codes -->
| Code | When |
|---|---|
| `E_BUSY` | The main thread is busy (DCLI-276) |
| `E_NATIVE_FAILURE` | The application refused the operation and left no lasting change |
| `E_HUMAN_REQUIRED` | A dialog or similar needs a person |
| `E_NOT_FOUND` | The object or document does not exist |
| `E_AMBIGUOUS` | A name matches several objects, such as two parts with the same reference; with `candidates` |
| `E_VALIDATION` | The application rejected the content |
| `E_CONFLICT` | A precondition does not hold, such as a file another program has open; with `conflict`, a value of DCLI-012, `application_state` for a conflict inside the application |
| `E_UNSUPPORTED` | The application cannot do this |
| `E_BACKEND_UNAVAILABLE` | The application is not installed, has no license or cannot be reached; nothing was sent |
| `E_TIMEOUT` | A read took too long; nothing was changed |

An error MUST be sent as a JSON-RPC error with code `-32000`. Its `data` MUST hold `code` and `dispatch_state`, and `native_message` when the application gave an error text. An `E_BUSY` error MUST hold `retry_after_ms`; an `E_HUMAN_REQUIRED` error SHOULD hold `action`. An `E_AMBIGUOUS` error MUST hold at least two `candidates`, and an `E_CONFLICT` error MUST hold `conflict`; both, like `E_BACKEND_UNAVAILABLE` and `E_TIMEOUT`, come with `dispatch_state` `not_started`. The runtime MUST answer any other code, or a missing field, with `E_PROTOCOL`. When a builtin knowledge entry explains the error, such as a known dialog, `data` SHOULD hold `knowledge`, the IDs of those entries; the CLI then names the `knowledge get` command in `next_action` (DSKL-124). An adapter in the CLI's process reports the same fields. The CLI adds the other `details` fields of DCLI-012, such as `next_action` and `resume`. `native_message` is the application's own text and MUST be treated as untrusted content (DSEC-020).

Why: A small, fixed set of host errors maps cleanly onto the error codes the Agent already knows.
Check: 🤖 Runtime unit tests.

### DCLI-280 Protocol version

The protocol version MUST follow SemVer and MUST be recorded both in the contract and in the host component's manifest. This specification defines protocol version 1.3.0.

Why: Both sides can compare versions before they exchange a single write.
Check: 🤖 Driver conformance test D20.

### DCLI-281 Host libraries

This specification provides the protocol schema in the contract, a reference implementation in the fake host, and small host-side libraries for some host languages. A host component SHOULD use the library for its language, when one exists, instead of writing its own connection, framing, handshake and deadline handling.

Why: Connection, framing, handshake and deadlines are easy to get subtly wrong and are the same for every host.
Check: 👀 Review of the host component.

## Checklist

- [ ] DCLI-001 One envelope on stdout
- [ ] DCLI-002 Output formats
- [ ] DCLI-003 Envelope keys
- [ ] DCLI-004 Meta
- [ ] DCLI-005 Names and values
- [ ] DCLI-006 Lists
- [ ] DCLI-007 Default fields
- [ ] DCLI-008 Response size
- [ ] DCLI-010 Error object
- [ ] DCLI-011 Dispatch state and next action
- [ ] DCLI-012 Fixed details fields
- [ ] DCLI-013 Error codes
- [ ] DCLI-014 Exit codes
- [ ] DCLI-020 Noun then verb
- [ ] DCLI-021 Paths and aliases
- [ ] DCLI-022 Verbs
- [ ] DCLI-023 Nouns
- [ ] DCLI-024 Reserved names
- [ ] DCLI-025 Global flags
- [ ] DCLI-026 Shared flags
- [ ] DCLI-027 Target flags
- [ ] DCLI-028 Target keys
- [ ] DCLI-029 Command registration
- [ ] DCLI-030 Built-in commands
- [ ] DCLI-031 reference
- [ ] DCLI-032 context
- [ ] DCLI-033 doctor
- [ ] DCLI-034 changelog
- [ ] DCLI-035 Operation commands
- [ ] DCLI-036 Version and help
- [ ] DCLI-050 Write flow
- [ ] DCLI-051 Effects
- [ ] DCLI-052 Confirmation tiers
- [ ] DCLI-053 Confirmation required
- [ ] DCLI-054 Dry run and preview
- [ ] DCLI-055 Confirm token
- [ ] DCLI-056 Confirm-time review
- [ ] DCLI-057 Execution and read-back
- [ ] DCLI-058 Save is the commit point
- [ ] DCLI-059 Operation records
- [ ] DCLI-060 Target locks
- [ ] DCLI-061 Receipt
- [ ] DCLI-062 Deriving ok and the error code
- [ ] DCLI-063 Outcome unknown
- [ ] DCLI-064 Batch operations
- [ ] DCLI-065 Long jobs
- [ ] DCLI-066 Human required
- [ ] DCLI-067 Undo and guarantees
- [ ] DCLI-068 The result shows what the change made
- [ ] DCLI-070 update is required
- [ ] DCLI-071 Choosing the version
- [ ] DCLI-072 Moving the running program aside
- [ ] DCLI-073 Running npm safely
- [ ] DCLI-074 Checking the result
- [ ] DCLI-075 Direct command entry
- [ ] DCLI-076 Skill sync
- [ ] DCLI-077 Running update again
- [ ] DCLI-078 Update failures
- [ ] DCLI-079 Update notices
- [ ] DCLI-080 Standard units
- [ ] DCLI-081 Unit suffixes in output
- [ ] DCLI-082 Units in parameters
- [ ] DCLI-083 Display units
- [ ] DCLI-084 Coordinate systems
- [ ] DCLI-085 Undeclared units
- [ ] DCLI-200 Driver standard commands
- [ ] DCLI-201 Modes
- [ ] DCLI-202 Selecting a session
- [ ] DCLI-203 Session object
- [ ] DCLI-204 Session states
- [ ] DCLI-205 Session read deadline
- [ ] DCLI-206 Opening and closing sessions
- [ ] DCLI-210 Document object
- [ ] DCLI-211 Document states
- [ ] DCLI-212 Document commands
- [ ] DCLI-213 Documents in file mode
- [ ] DCLI-220 Job object and commands
- [ ] DCLI-221 Cancelling a job
- [ ] DCLI-230 Device object and states
- [ ] DCLI-240 Host component commands
- [ ] DCLI-260 Adapter capabilities
- [ ] DCLI-261 Runtime responsibilities
- [ ] DCLI-262 Where adapter code runs
- [ ] DCLI-263 Black-box conformance
- [ ] DCLI-270 Local transport
- [ ] DCLI-271 Discovery files
- [ ] DCLI-272 Framing
- [ ] DCLI-273 Handshake
- [ ] DCLI-274 Methods and deadlines
- [ ] DCLI-275 Dispatch state in replies
- [ ] DCLI-276 Main thread
- [ ] DCLI-277 External changes
- [ ] DCLI-278 Lost connections
- [ ] DCLI-279 Host errors
- [ ] DCLI-280 Protocol version
- [ ] DCLI-281 Host libraries
