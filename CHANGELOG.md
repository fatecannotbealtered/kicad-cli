# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Typed parameter metadata in the command registry: defaults, enums, units,
  numeric bounds, global options and per-mode constraints. `reference --command`
  returns one command and only its output schema to reduce Agent response size.
- Canonical `reference.risk_tier`, `reference.error_codes` and `context.version`.
- KiCad-free regression tests for parsing, no-dispatch write refusal, discovery
  and interpreter-probe reuse.

### Fixed
- Share one DRC runner between the shell and layout payloads. Isolate each
  report and temporary KiCad configuration; validate exit status, JSON shape,
  units, source and input fingerprints instead of accepting stale reports.
- Honor the configured official executable in all DRC paths, reject automatic
  shim/self-recursion and recognize macOS application bundles.
- Fail explicitly when DRC is unavailable; widening no longer silently changes
  to approximate verification. Payload failures report uncertain write state.
- Add no-KiCad fault-injection and real-stub-process regression tests. This is
  not whole-write rollback or fresh live validation; see docs/DRC_BOUNDARY.md.
- Boolean strings such as `false` and `0` no longer enable safety overrides.
- Reject conflicting dry-run/confirm controls, repeated scalar options, extra
  positional arguments, empty values and non-finite/non-positive sampling inputs.
- Accumulate repeated layer selections rather than silently using the last one.
- Refuse route options in modes that do not implement them: notably `--nets`
  in repair/full, instead of accepting a selector and routing a broader scope.
- Reuse interpreter discovery's version probe within a process; a frozen CLI
  no longer tries to run itself as the KiCad Python interpreter.
- `board live` no longer advertises an editing capability it does not have. Its
  `capabilities` field described drawing into the open editor and the undo
  entries that would leave; the command only ever read status. The READMEs and
  the Skill were corrected without it, so the claim an Agent actually reads
  outlived the claim a person reads.
- Reject `--` instead of silently pushing the options after it into the command
  path and reporting an unknown command. The tool takes no positional values;
  the error now names `--option=--value` as the way to pass one starting with
  `--`.
- Write the envelope as UTF-8 regardless of the console encoding. On a non-UTF-8
  console — zh-CN Windows being the common case — every response carrying a
  non-ASCII character went out in the process locale, so an Agent decoding the
  document as UTF-8 got a decode error rather than a result. Payload stdout and
  stderr progress share the fix. Regression tests run the CLI under hostile
  encodings and assert the harness is really applying them; every pre-existing
  test sets `PYTHONIOENCODING=utf-8`, which is what kept this invisible.

### Added
- A write transaction around board saves: a backup before the first write, a
  cross-process `.kicad-cli.lock` beside the board, whole-write rollback on any
  failure, unhandled exception or Ctrl+C, and a journal that survives a kill so
  the next invocation refuses to write over an unfinished write instead of
  silently retrying on top of it. The envelope now reports `write_state`
  (`committed` / `rolled_back` / `not_started` / `unknown`) rather than only
  being able to say `unknown` after a failure.

### Changed
- Separate FCC measurement from FCC enforcement. The guard returned early unless
  `fcc_status` already said `verified`, so while the status was `unknown` nothing
  was counted -- and the status cannot honestly become `verified` without the
  count. Coverage was not failing; it was unmeasured, which looks identical in a
  green run. Every capable full run now counts leaf-command dispatch, writes
  `.fcc-coverage.json` and reports the number as a warning; enforcement still
  fires only against a `verified` claim. First measurement: 22/22 (100%) on
  Windows with KiCad 10.0.6. That is dispatch coverage, not flag/error coverage,
  so `fcc_status` stays `unknown`.
- Confirmation tokens are random, single-use and expire after 15 minutes, and
  bind to the operation, the preview and the target file's contents. They were
  `sha256(operation + preview)`: a pure function of public inputs, so the same
  token came back from every dry run, stayed valid forever and could be
  replayed without limit. A gate like that costs one extra round trip, not a
  decision. Tokens issued by an earlier build are refused. Pending records are
  kept under `KICAD_CLI_STATE`; see SECURITY.md for exactly what is stored.
- This development candidate is explicitly unpublishable. The historical 1.0.0
  live evidence is not reused for changed code, and remaining confirm-token,
  DRC-isolation and verification/rollback gaps stay release blockers.

### Security
- Block the stable publishing workflow before building when runtime readiness,
  evidence statuses or tag/version identity are invalid; recheck each frozen
  candidate before packaging. Beta publication is intentionally not enabled.
- Give build/preflight jobs read-only repository permissions; reserve signing
  and publication permissions for publication jobs.
- Derive Python distribution metadata from the runtime version rather than the
  stale 0.1.0 literal. This does not create a tag or publish a package.

## [1.0.0] - 2026-09-17

### Added
- `sch link` / `sch relink` — check and restore the uuid that ties a footprint to
  its schematic symbol. Without it, "Update PCB from Schematic" deletes and
  recreates every part. The path is taken from KiCad's own netlist export rather
  than reconstructed, and a multi-unit part exports one uuid per unit which the
  updater tries in turn — verified across all 16 demo projects, 3835 footprints.
- `sch sync-preview` — what the update dialog would report, without opening it.
  Uses the updater's uuid-path matching, not `--schematic-parity`, which matches
  by reference designator and is therefore blind to a broken link.
- `sch audit` — re-runs ERC in a throwaway copy with the silenced rules enabled,
  so "what is being hidden" is measured rather than guessed. Distinguishes rules
  KiCad ships disabled from rules this project disabled.
- `fab drill` — Excellon files, map and report, reconciled against KiCad's own
  hole count before the result is reported as successful.
- `board plane` — checks the copper under every track: gaps where the adjacent
  layer has none, and points where a track crosses a split in the plane. Both
  force the return current to detour and DRC reports neither. Gaps are grouped
  by location, so one hole in a plane reads as one finding rather than as every
  trace that crosses it.
- `board route` / `stitch` / `rewidth` / `widen` / `move` — migrated from the
  layout skill, each behind the write gate with DRC self-verification and revert.
- Write commands refuse to run while the project is open in KiCad.
- `board live` — draws into a KiCad that is already open, through its IPC API.
  Changes appear on screen and enter KiCad's undo stack, so the operator can
  revert them with Ctrl+Z. It is the only command that does not act on the file.
- `fab gerber` / `pdf` / `svg` / `dxf` — plot output for the layers you name.
- A bundled Agent Skill that describes this tool rather than a template. It
  routes between the commands that overlap, marks the switches that retrying
  cannot undo, and separates `E_CONFLICT`'s two opposite causes — a stale token
  wants a fresh dry run, a held lock wants the user to close KiCad. Install it
  with `npx skills add fatecannotbealtered/kicad-cli -y -g`. There is no
  self-update command; upgrading is the two install lines plus `changelog`.
- `docs/COMPATIBILITY.md` — which KiCad versions were actually exercised, which
  of the four routes each command depends on, and the upstream gaps that look
  like bugs in this tool and are not.
- `KICAD_CLI_ROOT` — one variable naming the KiCad installation, for installs
  that are not where KiCad usually puts itself. Previously an unusual location
  had no supported answer: a developer's own path was simply listed in the
  search hints, which is not something to ship in product code.
- `docs/E2E.md` — what `release_readiness` claims and what backs it, plus
  `docs/evidence/live-smoke-1.0.0.md`, the verbatim output of the suite against
  KiCad 10.0.6 rather than a summary of it.
- Contract tests against a substituted KiCad. Both boundaries — the official
  binary and the bundled interpreter — are resolved by absolute path from an
  environment variable, so they can be replaced without a seam in the product
  code. This reaches what a real KiCad will not do on demand: refuse to launch,
  exit zero having written nothing, hang, emit rubbish, or fail twice and then
  succeed. Those paths decide whether a healthy board is reported as broken and
  had been reasoned about and shipped untested. It also needs no KiCad, so a
  meaningful part of the suite now runs where every live test skips.

### Fixed
- The confirmation gate returned a token without the preview it was computed
  from, asking callers to authorise something they could not see.
- `board audit` advertised three fields it never emitted and omitted four it
  always did. A strict mode now compares emitted data against the declared
  schema, and the test suite runs every command with it enabled.
- Netlist export no longer writes to the user's KiCad configuration directory.
- Every hyphenated option was unreachable. The parser normalised hyphens to
  underscores, so `--ignore-lock` landed under a name the lock guard never read
  and the override had never once taken effect.
- Undeclared options were silently dropped. `board rewidth --nets VSYS` reads
  like it names a target; rewidth has no `--nets`, so the option vanished, a
  confirm token was issued, and the preview described re-routing the default
  netclasses instead. Undeclared options now fail `E_USAGE` and return the
  command's real parameter list.
- The coverage guard had never run: it enumerates only while `fcc_status` says
  `verified`, and the status said `present`, so the gate that keeps that claim
  honest was disarmed by the claim itself. Four commands had no test at all.
  Coverage is now measured from a dispatch trace rather than by searching the
  test sources, which counted a command named in a docstring and missed one
  called through a parametrised fixture.
- `sch audit` could report a healthy schematic as `E_IO`. Launching a process
  on Windows fails occasionally with an empty stderr and no output; the netlist
  export already retried, ERC did not, and `sch audit` runs ERC twice per call.
- `board parity` resolved KiCad's binary by name through `PATH` — and `PATH` is
  exactly where it must not look, since this tool shares that name deliberately.
  Harmless on Windows by accident, wrong on POSIX.
- `live_smoke_status` reported `present`, which is not one of the four values
  the spec allows for it, so the field was unreadable to anything consuming it
  mechanically.
- `errors.py` restated the sixteen-row error table by hand and claimed it was
  checked against the contract. It was not; the two agreed because someone kept
  them agreeing. It now derives from the generated contract module, which CI
  regenerates and compares.
- `.agent/SPEC_VERSION` did not exist, so CI's spec-drift guard had never
  passed. Pinned at `v1.6.2`, with all eight spec files verified identical.
- **The released binary did not run at all.** `build.py` handed
  `kicad_cli/main.py` to PyInstaller as a top-level script, so every relative
  import in it failed: each command exited 1 with a Python traceback on stdout
  instead of an envelope. It built cleanly and archived cleanly. Nothing caught
  it because the suite runs `python -m kicad_cli.main`, which has the package
  context the frozen binary does not — the tests were exercising something
  nobody installs. There is now a dedicated entry point, and
  `scripts/smoke_binary.py` runs the artifact itself: it must start, emit one
  parseable envelope, carry its payloads and changelog, report the version in
  `package.json`, and fail an unknown command as an envelope rather than a
  traceback. The release workflow runs it before anything is archived.
- `board live` was dead in any built binary. The vendored IPC client was
  carried as data, which copies the files without analysing them, so importing
  it failed on a missing standard-library module. It is now on PyInstaller's
  import path and frozen in with its dependencies.
- `board live` swallowed the reason an import failed, which made a packaging
  defect indistinguishable from a missing directory. It now reports what could
  not be imported.
- **The PATH fallback accepted this tool as KiCad's binary.** We share that name
  deliberately, so the fallback guarded against it — by comparing the hit with
  `sys.argv[0]`, which is almost never the same path. A global npm install puts
  a `kicad-cli.CMD` shim on PATH; that shim passed the guard. Every DRC and ERC
  call would then re-enter this tool, get `E_USAGE` for a command it does not
  have, and surface as `E_IO` on a perfectly healthy board. It stayed hidden
  because a local search hint matched first on the machine it was written on.
  KiCad is now identified structurally — the rest of its suite sits beside the
  binary, or its `share/kicad` tree one level up — and both directions have
  regression tests. Third occurrence of this class; the first two were
  `pcb_widen.run_drc` and `payload/parity.py`.
- **`board live` misreported its most ordinary failure.** With KiCad simply not
  running it returned `E_UNKNOWN` and exit 1 — an internal error, with no fix
  attached — because `kipy.KiCad()` is lazy: it constructs happily against
  nothing and the socket error surfaces on the first real call, long outside the
  handler meant to catch it. The client is now probed where it is created, so
  this is `E_CONFIG` with exit 4 and an actionable fix. The test that was
  supposed to cover this had passed only because KiCad happened to be open on
  the machine it ran on; CI, with no KiCad and no vendored client, found the
  second half of the same hole.
- `board live` gave the same failure two different shapes depending on whether
  the IPC client was missing or KiCad was unreachable, and only one of them
  carried the note that every other command works on the file. A fresh clone
  meets the one that did not.
- Seven test modules hardcoded an absolute path to one developer's KiCad
  install and skipped when it was absent. On any other machine — including a CI
  runner with KiCad installed somewhere perfectly ordinary — every live test
  skipped silently and the run still reported green. The demo projects are now
  located from the KiCad the tool itself resolved, and the skip reason says
  what was looked for.

## [0.1.0] - 2026-09-14

Skeleton release. The spine conforms to the spec; board-writing commands are
being migrated from a working prototype and are not exposed yet.

### Added

- Unified output envelope, canonical error codes and exit-code mapping, mirrored
  from the vendored `contract/contract.json`.
- Write gate helpers: a confirm token bound to the previewed state, so a plan
  computed against a board that has since changed is refused with `E_CONFLICT`
  instead of applied.
- Self-describing commands: `reference`, `context`, `doctor`, `changelog`.
  `reference` carries real output schemas and runnable examples for every leaf
  command; `doctor` reports the same release level that `reference` claims.
- `board audit` - design-quality audit including stackup, copper pours, and
  netclass trace-width sizing checked against IPC-2221 ampacity.
- `board parity` - verifies a `.kicad_pcb` still matches its `.kicad_sch` by
  component, net and pad membership.
- Payload bridge: board work runs inside KiCad's bundled interpreter as a
  subprocess, because `pcbnew` is a compiled extension that ships with KiCad and
  cannot be pip-installed or frozen. The envelope is parsed from the first JSON
  line, since KiCad's SWIG layer writes diagnostics to the C-level stdout after
  ours is flushed.
- No passthrough surface: this CLI exposes only its own commands. It is not a
  wrapper around KiCad's binary. The single remaining call into it is DRC,
  which write commands use as their verification oracle -- `pcbnew` exports
  `WriteDRCReport` but it segfaults outside the running application, because
  the rules engine needs project context the app sets up. `doctor` reports that
  dependency as the `drc_oracle` check rather than hiding it.
- DRC behaviour is deliberately not reimplemented. A verdict is only worth
  something because a fab and a reviewing engineer can reproduce it in an
  unmodified KiCad; a verdict from our own rules engine would prove nothing
  about their install.
- `doctor` reports whether KiCad's IPC API server is enabled, which is off by
  default and is the only supported route to the interactive router.
