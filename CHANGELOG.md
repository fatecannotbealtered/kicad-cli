# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `board route --engine freerouting` — a second routing engine, reached over
  Specctra DSN/SES. The grid router does not push and shove: nets are routed
  one at a time against copper it treats as immovable, so a connection needing
  an existing track to move aside is never found. Freerouting does. Measured
  on the 15-part ATmega328P, same placement and the same ground pour, both
  fully connected and fabricable:

      grid          178 mm copper   113 tracks   23 vias
      freerouting   172 mm copper   112 tracks   11 vias

  Half the vias: half the drilled holes, half the punctures in the plane.

  Freerouting is **not redistributed**. It is GPL-3.0, as KiCad is, and
  `NOTICE.md` has always said this project runs what the user installed rather
  than shipping it. `KICAD_CLI_FREEROUTING` and `KICAD_CLI_JAVA` point at them,
  `doctor` reports `freerouting_engine`, and a missing install is `warn` rather
  than `fail` because `--engine grid` remains the default and needs nothing.

  Three things the first real run forced: `-mt 1`, because Freerouting's own
  log warns its multi-threaded optimiser is broken and produces clearance
  violations; `-l en`, because otherwise its output follows the system locale
  and comes back as mojibake on a Chinese Windows; and widening its fanout
  stubs back to the board's own minimum track width, because it necks to
  0.15 mm to escape tight pads and KiCad DRC called that 22 `track_width`
  errors. The widened count is reported, not absorbed.

  Freerouting writes its log to `<cwd>/<language>/freerouting.log`, so even a
  bare `--help` grows an `en/` folder wherever it ran. `doctor` calls that on
  every invocation; it did so in this repository's root and the folder was
  very nearly committed. The probe now runs in a temporary directory and
  without `-l`, and the routing run is given the scratch directory as its cwd.

  `freerouting_violations` is its clearance model and `verify.errors_final` is
  KiCad's. They disagree -- it reported 0 violations on the board KiCad found
  22 errors on -- so both are reported and KiCad's is what the rollback is
  judged against.
- `board pour` — fill a copper layer with a zone for one net, usually ground.
  `board stitch` joins the islands of a pour and `board plane` audits what
  sits under each track; both assumed a pour existed and neither could make
  one. A board out of this chain had zero zones, so `board plane` returned
  FAIL with `backed_fraction: 0.0` -- all 170 track segments with no copper
  beneath them, on a board DRC was perfectly happy with.
- `board route --mode full --use-planes` — let a copper pour carry its own
  net. `mode_full` read its plane nets from a `log` key nothing ever wrote, so
  the set was always empty and the entire plane-awareness path was dead code:
  `escape_pins` skipped no pin, `fanout_planes` iterated an empty set,
  `plane_served` always reported `[]`. Ground was routed pad to pad with a
  ground plane sitting right there. `fanout_planes` also special-cased the net
  *named* GND and called it served without doing anything -- a fact about the
  board it was written for, not about ground: where the pour is only on B.Cu,
  the top-layer SMD ground pads reach no copper and were skipped anyway.

  Plane nets now come from the board's zones and a pad is judged by whether
  copper of its own net covers it on its own layer. Measured on a 15-part
  board: copper 255.1 mm -> 177.5 mm, all twelve top-layer ground pads fanned
  out to the plane, both boards fully connected.

  Off by default: turning it on takes KiCad's own `interf_u` demo from 3 DRC
  errors to 5 and the rollback guard then fails a board that used to route.
  Nothing that routes today changes; opting in still gets the guard.
- `board netclass` — create a netclass and assign nets to it. The chain had
  no way to say "this net carries current". `board from-netlist` makes a board
  whose only class is Default at 0.20 mm, roughly 0.74 A on 1 oz copper at a
  10 C rise; `board rewidth` and `board widen` both read their target *from* a
  netclass and nothing could make one. So `board audit` reported an error
  against a board this tool had just produced, and no command in the tool
  could clear it. Netclasses live in the project file, so this is a JSON edit
  behind the same write gate and transaction as everything else.
- `board rewidth` no longer defaults to three netclass names from one board.
  `--classes` defaulted to `PWR_MAIN,BTL_OUT,SWITCH` -- the classes of the
  amplifier this tool was first written for. On any other board none of them
  exist, so rewidth did nothing and returned `ok: true`. It now defaults to
  every non-Default class in the project, and naming a class that is not there
  is `E_NOT_FOUND` rather than a quiet no-op.

### Fixed
- `board rewidth` reported `ok` while leaving the board with more DRC errors
  than it started with. Its verify was a per-net revert loop, which only ever
  looks at the nets it touched -- and "no single net introduced an error" is
  not "the board has no more errors than before". Measured: a board at 0
  errors came out of rewidth with 3 clearance violations and `ok: true`. It
  now takes a whole-board baseline like `board route` and `board place` do,
  and rolls the entire board back with `E_INTEGRITY` when the count rises.
- A subprocess that segfaults no longer wedges the board. `board rewidth`
  re-routes one net per subprocess; SWIG crashes in the revert path
  (`rc 0xC0000005`), and a process that dies that way never reaches its
  commit, so its journal, backup and lock stayed on disk and the board was
  unwritable until the lock went stale. The whole-board copy that guards
  against this was only taken on the `--nets` path, so the netclass path --
  which is what `board rewidth` actually uses -- had no protection at all.
  It is now taken on both, and after restoring from it the dead worker's
  transaction is discarded, which is the one case where that is a fact rather
  than a guess.
- `board rewidth` could not finish a class with more than one net. Each net is
  re-routed in its own subprocess, and each one left a journal, a backup and a
  lock behind: the worker exits through `os._exit`, which skips the commit.
  The next worker read that as an unfinished write and refused. The parent
  did the same thing on a larger scale -- it saved the board before delegating
  and held that transaction open across every child. The leftover lock also
  wedged the board against any later write until it went stale.
- A subprocess that reported a failure correctly became a `KeyError` in the
  parent. `_child` returned any JSON line it found, including a well-formed
  `{"ok": false, ...}`, and the caller then read `r["txt"]`. The child's own
  error code and message are now propagated instead of discarded.
- `sch create` survives a drawing that will not route, and usually now draws
  it anyway. The netlist and the `.kicad_sch` are separate outputs of the same
  generator and only the netlist is load-bearing, but a failure in the wire
  router failed the whole command and discarded a netlist that had already
  been written and was correct. On the first realistic board tried -- a
  15-part ATmega328P with a TQFP-32 -- that blocked the entire chain on the
  router's inability to lay out a 14-pin ground net.

  It now retries with SKiDL's `auto_stub`, which draws high-fanout nets as
  global labels and power symbols, the way an engineer would have drawn ground
  anyway. That succeeded on the ATmega board. If it still fails, the netlist
  is delivered with `drawing.status: "failed"` and `written.schematic: null`,
  said plainly in the note rather than left to be noticed.
- `board route --mode repair` says what to do when it has stopped helping.
  "Repeat until it stops improving" was true and one sentence short: `repair`
  only finds paths through the gaps in existing copper, and that copper is
  what blocks it, so a stalled `repair` stays stalled. Measured on the same
  ATmega board: `repair` sat at 1 unconnected across three passes; `--mode
  full` routed all 35 with none failed. The note now names `--mode full` when
  `unconnected_after` is above zero and no better than before. Advice, not an
  automatic switch -- `full` deletes hand-drawn tracks too.
- `board drc` — run KiCad's design rule check and read the result. Every write
  command here already ran DRC: it is the referee for `board route` and
  `board place` and the thing they roll back against. There was no way to
  simply ask, so "is this board manufacturable?" -- the question at the end of
  the chain -- could only be answered by performing a write. The host-side
  entry point existed in `kicad_env.run_drc` and nothing called it.

  Exit is 0 whatever DRC finds: the command succeeded at checking, and a
  violation is a fact about the board rather than a failure of the command.
  Read `ok_to_fabricate`. `--severity` and `--limit` trim the list and never
  the counts -- a count that shrank to fit would report a board with warnings
  as clean.
- `board place` — automatic placement by connectivity, the last hole in the
  middle of the chain. `board from-netlist` lays out a grid ordered by
  reference designator, which is a position for every part and a layout for
  none of them: the netlist says which parts belong together and nothing read
  it. This does, force-directed, holding courtyards apart and staying inside
  the board's own `m_CopperEdgeClearance`.

  It is judged on half-perimeter wirelength, the standard placement metric,
  reported before and after; it leaves the board untouched when it finds
  nothing shorter, and rolls back if DRC errors increase. The test that earns
  it builds two boards from one netlist, places one, routes both with the same
  router and compares the copper. Measured by hand on two boards: 115.5 mm ->
  53.2 mm and HPWL 111.3 -> 48.0 on one, HPWL 50.0 -> 15.4 on the other.

  Two defects it found on the way, both fixed here: the collision report folded
  `--clearance` into the overlap test, so every pair the legaliser had
  separated *correctly* -- to exactly the margin asked for -- came back as a
  courtyard clash; and clamping courtyards to Edge.Cuts put pads 0.25 mm from
  the board edge where the rule said 0.5 mm, which the rollback guard caught as
  two DRC errors before the board was written.
- `board audit` reports `copper_mm`, `track_count` and `via_count`. Total
  routed length is the headline cost of a layout and the tool could not read
  it, which meant `board place` could not prove its own improvement without an
  external script.
- The Skill and both READMEs document the chain. `sch create` and
  `board from-netlist` existed for an hour without the Skill mentioning them,
  which for an agent is the same as not existing: it reads the routing table,
  not the registry. The table now answers "make a board from a requirement",
  and a worked section names the three things the envelope will not repeat --
  placement is a grid and not a layout, the schematic is for machines, and a
  part with no footprint fails at the board step rather than the schematic one.
- `board from-netlist` — the step between a schematic and a board. Loads each
  component's footprint, places it, joins the pads into nets and draws an edge
  cut. `docs/COMPATIBILITY.md` has always said "Update PCB from Schematic has
  no headless entry point", which is true of the dialog and not of the work:
  pcbnew exposes every part of it. With `sch create` this closes the chain —
  a JSON specification now runs all the way to Gerbers without KiCad's GUI,
  and a test asserts exactly that rather than the pieces separately.
  Placement is a grid ordered by reference, not a layout.
- The write transaction understands creation. A target that does not exist is
  a write whose undo is removal, not a missing backup; `board from-netlist`
  met the old behaviour as "cannot take a backup before writing", a true
  statement about a file that was never there.
- `sch create` — the first command that starts from a description instead of a
  design. It takes a JSON circuit specification (parts named by KiCad library
  symbol, nets named by the pins they join) and writes a `.kicad_sch` and a
  netlist. Every symbol and every pin is resolved against the installed
  libraries during the dry run, so a wrong pin fails as `E_VALIDATION` naming
  it and listing the pins that exist, rather than as a schematic quietly
  missing a connection. The generator is an implementation detail and not part
  of the contract; symbol placement is not laid out for reading, and the
  netlist is what downstream commands consume.
- `docs/evidence/live-smoke-1.0.0+029cac55bc56.md`: a live run recorded for this
  candidate rather than inherited from the 1.0.0 artifact — full suite and frozen
  binary against KiCad 10.0.6 on Windows 11, with the source commit named and the
  measured dispatch and error-code coverage from the same run. `live_smoke_status`
  moves from `missing` to `verified` on the strength of it; `level` stays
  `unpublishable` and `fcc_status` stays `unknown`, and the release gate still
  refuses, which is the point of having a gate that reads the declaration.
- Typed parameter metadata in the command registry: defaults, enums, units,
  numeric bounds, global options and per-mode constraints. `reference --command`
  returns one command and only its output schema to reduce Agent response size.
- Canonical `reference.risk_tier`, `reference.error_codes` and `context.version`.
- KiCad-free regression tests for parsing, no-dispatch write refusal, discovery
  and interpreter-probe reuse.
- Error-code coverage, measured and enforced. `reference` publishes a table an
  agent branches on, and nothing checked that a command could actually produce
  each row. `envelope.fail` now records the code it emitted to its own trace,
  and the guard fails when an applicable code is never reached: 9/9 now, with
  seven declared not-applicable and a reason each. Finding this required fixing
  the measurement first -- the guard sorted before the tests it counted and
  reported four codes unreachable that the suite already produced.
- `tests/test_error_paths.py` stages the one code nothing else reached: a
  substituted DRC oracle that reports the board getting worse, with the routing
  itself running against a real KiCad. `board route --mode full` returns
  `E_INTEGRITY`, the whole write rolls back, and the board is byte-identical.
- Contract coverage by flag combination, not by command. `test_contract.py` ran
  each command once, which cannot catch a command whose output matches for the
  flags a test happens to pass and not for the others -- it did not catch
  `board route`. `tests/test_contract_flag_coverage.py` runs 31 combinations
  under strict mode; the other 21 commands came back clean, which is now a
  recorded fact rather than an assumption.
- `tests/test_untrusted_fields.py` measures the `untrusted_fields` declarations
  instead of trusting them: the design is poisoned with a marker and anything
  that comes back carrying it must be declared. All six reporting commands hold.
  SECURITY.md tells an agent it may read undeclared fields as the tool's own
  words, and nothing checked that claim.
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

### Fixed
- The Skill warns that routing is not finished when it returns `ok`. It had a
  checkpoint for `--mode full` deleting every track and none for what the board
  is like afterwards, which is the trap an agent actually falls into: `ok: true`
  with width compliance at 0%. Names `verify.width_regressed` and the two
  commands that fix it.
- `board route` reports the width DRC cannot see. On KiCad's `ecc83` demo,
  `--mode full` left DRC errors, unconnected count and `ok` all unchanged while
  taking the board from 100% netclass width compliance to 0% and its minimum
  ampacity from 2.03 A to 0.74 A. That is by design -- `full` routes at the
  router's neck width and expects `board widen` after -- but the only thing
  that said so was a sentence of prose telling the caller to run another
  command. `verify.width_before`, `verify.width_after` and
  `verify.width_regressed` put it in the machine contract, where a caller that
  branches on fields can see it.
- `doctor` probes the IPC API instead of reading the preference file. It
  reported `ipc_api_server: pass` with no fix at the same moment `board live`
  returned `E_CONFIG: connection refused`, because the file answers "is the API
  switched on", not "is it reachable" -- and those differ whenever KiCad is not
  running, which is the normal state. A preflight check that says a command
  will work when it will not is worse than no check, because it is believed.
  The probe is now the same call `board live` makes, so the two cannot disagree.
- Retry the rollback's file replacement instead of giving up on the first
  refusal. On Windows `os.replace` fails while anything still holds the
  destination open -- a scanner, the indexer, a child process whose handles are
  not yet reaped -- and those clear in milliseconds. The rollback is the safety
  mechanism, and it was losing to the most ordinary condition on the platform
  this tool is mostly used on. When it still cannot finish, the envelope now
  carries `rollback_error`: `write_state: unknown` with no cause attached is
  the hardest state to act on and the hardest to diagnose afterwards.
- Record evidence against a commit, not just a version. The filename was
  `live-smoke-<version>.md` and the version does not move between candidates
  here, so a second run would have overwritten the recorded 1.0.0 evidence with
  a different tree's result under its name. The record now carries the source
  commit, and generating it from a dirty tree is refused.
- Declare the six fields `board rewidth` produces and never advertised:
  `rewidth_reverted`, `rewidth_drc_cause`, `stripped_segments`, `skipped_nets`,
  `verify` and `note`. A caller reading `reference` could not know a reverted
  net would be reported.
- Stop the relay's output shaping from swallowing an undeclared key. Filling a
  field a mode does not set is shaping; dropping one the schema never declared
  made the strict check agree with itself instead of with the payload, so
  removing a field from a schema silently changed the output to match it. The
  keys `board rewidth` deliberately does not surface are now named in one place.
- `board route` verifies every mode against DRC and reports what it verified.
  `repair` and `full` ran no DRC at all -- `full`, whose definition is "clear
  every track and route again", judged itself on connectivity counts alone and
  reported success with the DRC oracle deliberately unavailable. Both now take
  an error-count baseline before touching the board, roll the whole write back
  if the count rises, and refuse before writing when no oracle is available.
- Declare one shape for `board route`. Its three modes emitted three different
  key sets behind a single declaration: `full` returned four undeclared fields
  and omitted two it promised, `rewidth` returned three and omitted twelve. No
  test had ever run either mode live, so an agent reading `reference` was wrong
  about two thirds of the command. Both are now covered by live write tests,
  and the payload refuses to emit a field it has not declared.
- Run CI on every pull request, not only those targeting `main`. A stacked PR
  got no CI at all.
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
