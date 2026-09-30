# Development status and remaining release work

This checkout is an **unpublishable development candidate**, not a new release.
PR #4 is a scoped command-boundary improvement. Merging it does not assert that
all board operations are safe, that FCC is verified, or that live E2E passed.
An empty issue/PR queue is an organizational state, not a quality certificate.

## Goal (agreed 2026-09-28)

An agent using only this tool takes a circuit description to a schematic and
a board that an engineer would sign off for fabrication -- both are
deliverables -- and can check and modify existing designs, schematic and
board alike, with evidence for every conclusion.

The engineer states the requirement, reads the previews and signs off; the
agent does the work in between; this tool is the deterministic, verifiable
instrument the agent holds. Not goals: replacing the engineer, inferring
electrical requirements from names, relaxing design rules, declaring a board
correct because DRC passes, authoring symbols or footprints, choosing parts,
SPICE.

What "an engineer would sign off" means, measured on `bench/`:

- **Schematic.** Its netlist matches the specification. ERC is clean: unused
  pins marked no-connect, power inputs driven. It reads: grouped by function,
  no symbol or text drawn over another, power and ground as power symbols.
  Every part is annotated, valued and has a footprint.
- **Board.** Matches the schematic, fully connected, no DRC errors. Every net
  at its netclass width. A complete, self-consistent fabrication package.
  Every reference designator identifies its own part. An outline from the
  mechanical requirement or fitted to the placement; decoupling at the pins it
  serves; connectors at the edge. A reference plane under signal tracks.
- **Reproducible.** The same input gives the same schematic and board.

For existing designs: the checks above; editing a schematic -- parts,
connections, values; carrying a schematic change onto a board that is already
placed and routed without disturbing the layout, which is what KiCad's
"Update PCB from Schematic" does and has no headless entry point for; and
board edits with preview, verification and rollback. The first two do not
exist yet.

Where it stands: the chain runs end to end and produces neither deliverable
to that standard. On the benchmark (below), the board misses the width,
silkscreen, layout, plane and reproducibility criteria in every run. The
schematic is further off: five runs gave four different drawings and, once,
no drawing at all; the one inspected closely draws the crystal, its load
capacitors and the LED circuit on top of the microcontroller's pins and of
each other; and every drawing has 23 ERC errors -- 19 unused pins without a
no-connect marker, 4 power or input pins undriven.

## Direction: the engine moves into this tool (decided 2026-09-28)

KiCad 11 removes the SWIG `pcbnew` bindings -- the plan of record is 11.0,
expected around February 2027 -- and most commands here run on them. The
decision is to drop SWIG entirely, and not to replace it with calls into
KiCad's official binary either: the work moves into this tool. That includes
the three things the binary has done for it until now: DRC, ERC and netlist
export.

This reverses a position held since 0.1.0: that DRC is deliberately not
reimplemented, because a verdict is only worth something if an unmodified
KiCad reproduces it. The argument has not gone away; it changes what is owed.
A verdict from this tool's own engine has to say which checks it does not run
(`not_checked`), and its agreement with KiCad's DRC has to be measured and
published per check class rather than assumed.

Rules for the work:

- KiCad is GPL-3.0 and this tool is MIT. Nothing is translated from KiCad's
  source. Behaviour comes from published formats -- KiCad's file-format
  documentation, Gerber X2, Excellon -- and from KiCad itself treated as a
  black box: the official binary appears only in tests, as the reference that
  this tool's output is compared against.
- Silkscreen text uses the upstream Newstroke release, which is CC0. KiCad's
  own copy carries a GPL header and its CJK glyphs are under the OFL.
- Every step shows it made nothing worse: field-by-field comparison against
  KiCad on its bundled demo projects, and the chain benchmark in `bench/`.
- Same input, same output. The SWIG-era router is not deterministic -- see
  below -- and a benchmark that moves between runs cannot tell a regression
  from noise. The new engine is byte-for-byte reproducible by construction.

Order, by deadline:

1. **Benchmark.** The chain on a reconstructed 15-part ATmega328P board,
   recorded against the current implementation (`bench/`).
2. **File model.** One lossless document layer for `.kicad_pcb`,
   `.kicad_sch` and `.kicad_pro` -- editing an existing schematic needs the
   same guarantees as editing a board, so both are built on it -- with board
   geometry and connectivity compared against pcbnew on the demo projects
   while pcbnew still exists to compare against.
3. **Replace SWIG, command by command.** The DRC referee stays the official
   binary for this step, so only one thing is unknown at a time: new code is
   judged by the referee it has always been judged by.
4. **Own DRC**, check class by check class, measured against KiCad's. It
   becomes the referee when the measurement says it can.
5. **Schematic side:** connectivity, netlist and ERC; a drawing a person can
   read; editing an existing schematic; carrying its changes onto a routed
   board.
6. **Remove the last call into KiCad's binaries**, then verify on KiCad 10 and
   11 before anything is released.

`board live` is outside this: it talks to a running KiCad over the IPC API,
and there is no other way to do what it does.

Step 5 has begun ahead of order, because it does not wait on the zone
filler that most of step 3's remaining writes need:
`kicad_cli/fileformat/schematic.py` reads a schematic into symbols, pins,
wires, labels and sheets, and `kicad_cli/fileformat/circuit.py` works out the
design's nets. On KiCad's 35 demo projects its nets are KiCad's, pin for pin.
On 34 the names and their order are too; one net of 95 is named differently
on RoyalBlue54L-Feather (`tests/test_fileformat_circuit.py`).
`kicad_cli/fileformat/netlist.py` writes the netlist from that, and it is
KiCad's own on 34 of the 35, part for part and net for net
(`tests/test_fileformat_netlist.py`). `board parity`, `sch sync-preview`
and `sch relink` read the schematic through it; `sch relink` reads the board
back with this tool's reader instead of pcbnew.

`sch create` draws its schematic in this process now
(`kicad_cli/native/sch_create.py`); SKiDL is gone. On the benchmark's
ATmega328P the drawing KiCad's own ERC finds nothing in -- SKiDL's drawings
had 23 errors each -- no symbol or text over another, power and ground as
power symbols, decoupling beneath the chip it serves, and the same file from
the same description every time.

`kicad_cli/fileformat/erc.py` is ERC of this tool's own: 35 of the 44 rules
KiCad 10 has -- pins, drivers and conflicts, no-connect flags, labels,
wires, names, the grid, references, parts of several units, the hierarchy,
buses, text variables and net classes -- measured on two designs drawn to
ask KiCad's ERC one question per case (`tests/fixtures/erc/`, 209 of them)
and held to it on KiCad's 35 demo projects: 2047 of the 2049 findings KiCad
makes there, and nothing else. Of the other nine, four are about libraries,
one about simulation, and four KiCad's own ERC was never seen to report
(`erc.NOT_CHECKED`). No command uses it yet. Next on that side: `sch audit`
on it -- the last `sch` command on KiCad's binary -- the library rules, then
editing an existing schematic, and carrying a schematic change onto a
routed board.

Progress. Step 1 is in (`bench/`). Step 2 is in for boards:
`kicad_cli/fileformat/sexpr.py` reads and writes KiCad's S-expressions
losslessly, held to KiCad's own files by `tests/test_fileformat_conformance.py`;
`kicad_cli/fileformat/board.py` reads a board into footprints, pads, tracks,
vias, zones and its outline, held to pcbnew item by item on every board KiCad
ships by `tests/test_fileformat_board.py`; `kicad_cli/fileformat/connectivity.py`
works out which copper touches which and what is left unconnected, held to
pcbnew net by net by `tests/test_fileformat_connectivity.py`. Not yet built:
the schematic side of the file model.

Step 3 has begun: `board audit`, `board plane`, `board parity`, `board move`,
`board netclass` and `board from-netlist` run in this process
(`kicad_cli/native/`), output identical to the pcbnew versions -- on every
demo board, and for `board from-netlist` on a netlist of every kind of part a
footprint library holds. `board from-netlist` reads KiCad's footprint
libraries, as files; the rest need no KiCad installed -- `board parity` reads
the schematic through this tool's own netlist. The
three writes go through the same transaction the payloads used -- backup,
lock, journal, rollback on any failure, `write_state` in every refusal -- now
in this process (`kicad_cli/native/write.py`). Each port keeps the old
version's behaviour exactly, mistakes included, so that the port is provable
by comparison; the mistakes are fixed afterwards, one change each. The one
exception is a mistake that destroys the owner's work: pcbnew's Save reset an
existing `.kicad_pro` to KiCad's defaults, and `board from-netlist` leaves it
alone.

### Carried over from the payloads, to fix after the port

Found while porting `board audit`, kept so the port stays identical:

- Width compliance (A4) counts straight segments only; an arc track is left
  out of every net's and class's length -- while `copper_mm` counts arcs, and
  `copper_by_layer_mm` again does not, so the three numbers disagree.
- A zone's fill is judged (A1, A2) on the first of its layers only; a pour on
  several layers is reported as if it had one.
- Layers are reported under the board's own names ("top_copper"), and "an
  inner ground plane exists" (A2) is decided by the name starting with `In`,
  so an inner layer someone renamed is not seen as inner.
- Net classes are matched with `fnmatch.fnmatch`, which ignores case on
  Windows and not elsewhere: one board can be classed differently by
  platform. What KiCad itself does is to be measured against pcbnew first.
- The power-node check (A3) compares a reference's first character with
  "FB", which can never match; only inductors are caught.
- Two fix texts name `pcb_route.py`, a script this tool does not have; the
  command is `board route`.
- Titles, details and fixes are Chinese in an otherwise English contract.

Found while porting `board parity`:

- The sample of mismatched nets was the first ten of a Python set, which is
  ordered differently in every process. The port sorts it, and the test
  holds it to the payload only where there are ten or fewer to sample.
- The `unrouted` count is connectivity's, now this tool's own.
- A board with no schematic beside it is refused with a message that
  mentions `--netlist`, a flag the payload had and the command never
  passed through.

Fixed since: the schematic side was KiCad's XML netlist, which keeps the
parts marked "exclude from board", so a part kept off the board on purpose
was reported missing from it -- five on the CM5_MINIMA_3 demo, the compute
module among them. It is this tool's own netlist now, which leaves them out
as the netlist a board is updated from does.

Found while porting `board move` and `board netclass`:

- `board move` converts millimetres to nanometres by truncating, as
  pcbnew's `FromMM` does: asked for 1.005 mm, a part lands on 1.004999.
- Its note tells the caller to re-route with `pcb_route.py`; the command is
  `board route`.
- `board netclass` splits `--nets` on commas after joining them, so a net
  whose name has a comma in it is taken for two nets.
- It writes the project file in text mode, so on Windows `.kicad_pro` comes
  back with CRLF line ends.

Found while porting `board from-netlist`:

- A placed footprint is named by its file alone -- "C_0603_1608Metric", not
  "Capacitor_SMD:C_0603_1608Metric" -- so it no longer names its library,
  and `board parity` reports every part's footprint as differing from the
  schematic's.
- Of several pads with one number -- a crystal's case, a QFN's exposed pad
  and its vias, a connector's shield -- only the last gets the net.
  KiCad's own update puts it on all of them.
- Footprints get no path to their symbols, though the netlist carries the
  symbols' uuids, so every footprint of a board made this way is unlinked
  (`sch link`).

## What the benchmark showed

The numbers this project has quoted for a realistic board came from a 15-part
ATmega328P whose specification was never checked in. `bench/specs/atmega328p.json`
reconstructs it from the description -- TQFP-32, regulator, crystal, ICSP
header, decoupling -- and `bench/chain.py` runs the Skill's own recipe on it.
The two boards are not the same board, so the old figures are not comparable;
what matters is that these can be made again.

Five runs of one specification did not produce one board. The grid router
breaks ties between equal-length paths in an order that follows object
identity, so the geometry differs from run to run, and on this board that is
the difference between `board rewidth` succeeding and rolling back. Fixing
`PYTHONHASHSEED` makes routing repeat and leaves the silkscreen step varying.

Across the five recorded runs (`bench/baseline/atmega328p-swig.json`): no DRC
errors in any; 0 or 1 connection left open, so two of five end with
`ok_to_fabricate: true`; the Power class at 0 to 52% of its 0.5 mm target --
in two runs `board rewidth` rolled back with `E_INTEGRITY` and left power at
0.2 mm; `backed_fraction` 0.31 to 0.41, which `board plane` calls FAIL.

`ok_to_fabricate` is DRC's view, and the board fails in ways DRC does not
see. In every run, 9 or 10 of the 15 reference designators end nearer another
part's courtyard than their own -- `C7` printed inside `U1`'s courtyard, for
one -- so the silkscreen is legible and points at the wrong parts. And every
part sits in one corner of an outline sized for the grid layout that `board
place` replaced; nothing shrinks it. Looking at the plotted copper found both;
no metric here would have.

## Landed scope

Typed parameter validation, mode constraints, discoverable defaults and enums,
scoped reference output, canonical discovery keys and process-local probe reuse.
No routing algorithm or geometry behavior was changed by this increment.
The `board live` command is read-only status, not a live editing interface.

## Pending release blockers

| ID | Status | Acceptance evidence required |
|---|---|---|
| CONFIRM | Pending (live validation) | Random, expiring, single-use tokens bound to operation, preview and target contents, with replay/expiry/changed-target/forged-token tests, are implemented. Remaining: validation against real KiCad write flows on more than one platform, and the store's behaviour under a shared or hostile state directory. |
| DRC | Pending | One trusted runner, isolated reports and per-operation temporary files; fresh-report and upstream-failure tests; no acceptance of a previous invocation's report. |
| TRANSACTION | Pending (live breadth) | Backup, whole-write rollback, a cross-process write lock, an interrupted-write journal and a reported `write_state` are implemented and fault-injection tested. All three `board route` modes now verify against DRC and report `verify`; repair/full compare the post-write error count to a baseline taken before any change and roll the whole write back if it rose, and refuse before writing when the oracle is unavailable. Remaining: cancellation semantics beyond signal handling, concurrent-write behaviour under KiCad's own lock, and validation on more than one platform and KiCad version. |
| CONTRACT | Pending (global options) | Flag-combination coverage exists: 31 combinations across all 22 commands run under strict mode, which found and fixed three `board route` shapes and six undeclared `board rewidth` fields. `untrusted_fields` is measured by injection rather than asserted, and holds for all six reporting commands. Error-code coverage is measured the same way and enforced: 9/9 applicable codes are produced by a real test, with 7 declared not-applicable and a reason each. Remaining: global-option coverage (`--fields`, `--quiet`, `--format text/raw`, `--json`) and permission-boundary review against the pinned spec. |
| EVIDENCE | Pending (breadth and enforcement) | Recorded: `docs/evidence/live-smoke-1.0.0+029cac55bc56.md` — full suite and frozen binary against KiCad 10.0.6 on Windows 11, with the source commit in the filename and the header, alongside the dispatch and error-code coverage from the same run. The historical 1.0.0 record is untouched. Remaining: a second platform or KiCad version, and a release-time check that the recorded evidence describes the tree being tagged — `check_release.py` validates the declaration, not the evidence behind it. |

These are requirements, not claims of implementation. Keep release readiness
unpublishable until the applicable blockers are closed with reviewable evidence.
A CI run with skipped live tests cannot close EVIDENCE or prove FCC = 100%.
No tag or package publication is authorized by this cleanup.

## DRC boundary increment

A shared runner and fault-injection tests now cover DRC report/config isolation,
upstream failure and input fingerprint checks. See [DRC_BOUNDARY.md](DRC_BOUNDARY.md).
DRC remains pending until live validation; transaction backup/snapshot files and
non-DRC upstream calls still require separate isolation work.

## Measured command coverage

Command dispatch coverage is now counted on every full run that can count it —
a complete selection, on a machine with KiCad, with no earlier failure — and
written to `.fcc-coverage.json` with a warning carrying the number. It is not
gated on `fcc_status`, because gating measurement on the claim it exists to
justify is how the number stayed unknown.

Most recent measurement: **22/22 leaf commands (100%)**, Windows 11 with KiCad
10.0.6. That is dispatch coverage. `fcc_status` stays `unknown` because CLI-SPEC
asks for every documented behavior — flags, modes, error codes — to have a
command-level test, and that larger set is what the CONTRACT blocker tracks.
Dispatch coverage is a floor for FCC, not FCC.

## Write transaction increment

Board writes take a backup and hold a `.kicad-cli.lock` beside the board for the
duration. Success commits and drops both; any failure, unhandled exception or
Ctrl+C restores the original bytes; a kill that runs nothing leaves a
`.kicad-cli.journal`, and the next invocation refuses to write over that board
and says where the backup is. The envelope reports `write_state` —
`committed`, `rolled_back`, `not_started` or `unknown` — instead of only being
able to say `unknown` after a failure.

This is the recovery half of TRANSACTION, not the verification half. A rollback
is only triggered by a failure something actually detects, and `board route
--mode full` detects nothing beyond connectivity. Until per-mode verification
exists, a mode that cannot tell it made the board worse will commit.

## Measured contract coverage

Two things that were claimed and unchecked are now measured on every capable run.

`tests/test_contract_flag_coverage.py` exercises 31 flag combinations under
`KICAD_CLI_STRICT`, because running each command once cannot catch a command
whose output matches for the flags a test happens to pass. That is exactly how
`board route` kept three shapes behind one declaration. The other 21 commands
came back clean; recording that is the point, so nobody has to re-derive it.

`tests/test_untrusted_fields.py` injects a marker into net names and reference
designators and requires every field that carries it to be declared untrusted.
All six reporting commands hold. The declaration is a security claim -- an
agent is told it may read undeclared fields as the tool's own words -- and it
had never been checked against a design that fought back.

`tests/test_error_coverage.py` counts which declared `E_*` a run actually
produced, from a trace `envelope.fail` writes, and fails if an applicable code
is never reached. Nine of sixteen apply; seven do not and say why, because a
tool with no service cannot return `E_RATE_LIMITED` and pretending otherwise
would make the other nine mean less. The trace is deliberately separate from
the dispatch trace: a stub may prove an error code reachable -- that is what
stubs are for -- and may never prove a command covered.

What an `E_*` envelope's `details` carries is still undeclared, and the
`_untrusted` key the fleet contract defines is used in one place. No unmarked
design-derived text was found in the error paths probed, but probed is not
covered. Global options (`--fields`, `--quiet`, `--format text/raw`, `--json`)
are exercised incidentally rather than enumerated. Both are why `fcc_status`
stays `unknown`: three measured dimensions are a floor for FCC, not FCC.

## The chain, and where it stops

A JSON circuit specification now runs to Gerbers without KiCad's GUI:
`sch create` -> `board from-netlist` -> `board place` -> `board route` ->
`fab *`, asserted end to end by
`test_the_whole_chain_runs_from_a_specification_to_gerbers`.

Placement is no longer only a grid. `board from-netlist` still lays one out --
it has to put parts somewhere before anything knows better -- but `board place`
then reads the netlist as a placement instruction and rearranges by
connectivity, force-directed, with courtyard separation and the board's own
`m_CopperEdgeClearance` as constraints. It reports half-perimeter wirelength
before and after, declines to write when it found nothing shorter, and rolls the
board back if DRC errors increase.
`test_an_auto_placed_board_routes_with_less_copper_than_the_grid` is the claim
that matters: same netlist, same router, and the placed board routes with less
copper. On the two boards measured by hand it was roughly half (115.5 mm ->
53.2 mm on one, HPWL 111.3 -> 48.0).

What that still does not mean. The placer does not rotate, mirror, or group by
function, and it treats every part as a box -- a human layout engineer does none
of those things that way. Packing parts closer collides silkscreen text, which
shows up as `verify.warnings_added` rather than being solved. The router does
not push and shove, so a connection needing existing copper to move aside will
not be found. Routing leaves the board at neck width and
`verify.width_regressed` says so, but nothing prevents handing back an
under-rated board. Each of those is a capability gap rather than a contract gap,
and they are what stands between "the chain runs" and "the result is what an
engineer would have drawn".

## What a realistic board showed

The chain had only ever been run on boards of two to seven parts. A 15-part
ATmega328P -- TQFP-32, regulator, crystal, ICSP header, five decoupling caps,
46 pads across 11 nets -- found three things that the small boards could not.

`sch create` failed outright. `generate_netlist` had already written a correct
netlist and then the wire router could not lay out the 14-pin ground net, so
the command failed and discarded it. The netlist and the drawing are separate
outputs and only one of them is load-bearing; the drawing is now best-effort,
retried with auto-stubbing, which drew this board successfully.

`board place` was worth more than on the small boards and in a way the metric
did not predict: HPWL 404.5 -> 176.3 mm, and DRC errors 4 -> 0. The grid had
put parts where the board edge clearance was violated; connectivity-driven
placement pulled them inward.

`board route --mode repair` stalled at 1 unconnected and stayed there across
three passes, on a connection whose two pads were 2.28 mm apart -- so the
geometry was never the problem. `--mode full` routed all 35 with none failed.
The ceiling here was not push-and-shove but the incremental mode's inability
to escape a corner it had routed itself into, and nothing said so. The
envelope now names `--mode full` when `repair` stops making progress.

### The router takes a pour into account only when asked

`mode_full` read its set of plane nets from a `log` key that nothing ever
wrote, so the set was always empty and the whole plane-awareness path was dead:
`escape_pins` skipped no pin, `fanout_planes` iterated nothing, `plane_served`
always reported `[]`. Ground was routed pad to pad with a ground plane sitting
right there. `fanout_planes` also special-cased the net *named* GND and called
it served without doing anything, which was a fact about the board it was
written for rather than about ground -- where the pour is only on B.Cu, the
top-layer SMD ground pads reach no copper and were skipped anyway.

Both are fixed, behind `board route --mode full --use-planes`. With it, plane
nets come from the board's zones and a pad is judged by whether copper of its
own net covers it on its own layer. Measured on the 15-part board: copper
255.1 mm -> 177.5 mm, all twelve top-layer ground pads fanned out to the
plane, both boards fully connected.

It is a flag rather than the default because turning it on takes KiCad's own
`interf_u` demo from 3 DRC errors to 5 (`starved_thermal`: that pour's spoke
settings do not support carrying those connections), and the rollback guard
then fails a board that used to route. Off by default, nothing that routes
today changes; on, the guard still restores the board if the result is worse,
and says why.

Making it the default needs the pattern `sch create` uses for its drawing --
try the better way, fall back to the old way when the result is worse -- which
is a change to `mode_full`'s control flow and its write transaction, not a
flag.

The router's other ceiling is still that it does not push and shove, and
Specctra DSN export / SES import are available for handing the board to an
external router. That remains the largest open routing gap.

## Subsequent capability work

After the write/verification foundation: design snapshots, object/region queries,
change inspection and recovery, then measured
end-to-end latency, output size, progress and memory improvements. Do not add
roadmap items to `reference` until their implementations and tests exist.

## Repository queue convention

Closed legacy dependency PRs #1–#3 remain closed; cleanup does not silently
reverse an earlier closure. PR #4 contains the foundation increment and its
review corrections. Later PRs should name the blocker or capability they address
and include current validation evidence. Record actual closure/merge state on
GitHub rather than maintaining a second, drifting PR-state table here.
