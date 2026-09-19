# Development status and remaining release work

This checkout is an **unpublishable development candidate**, not a new release.
PR #4 is a scoped command-boundary improvement. Merging it does not assert that
all board operations are safe, that FCC is verified, or that live E2E passed.
An empty issue/PR queue is an organizational state, not a quality certificate.

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

The router's real ceiling is still that it does not push and shove, and
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
