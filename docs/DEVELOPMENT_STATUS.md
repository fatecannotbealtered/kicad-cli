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
| CONTRACT | Pending | Review remaining runtime/schema, untrusted-data and permission-boundary gaps against the pinned spec; complete command/flag/error coverage, not dispatch counts alone. |
| EVIDENCE | Pending | Fresh full live KiCad suite and frozen-artifact smoke for the actual candidate; record platform, backend version and source identity. Historical 1.0.0 evidence is not reused. |

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

## Subsequent capability work

After the write/verification foundation: design snapshots, object/region queries,
an independent DRC entry, change inspection and recovery, then measured
end-to-end latency, output size, progress and memory improvements. Do not add
roadmap items to `reference` until their implementations and tests exist.

## Repository queue convention

Closed legacy dependency PRs #1–#3 remain closed; cleanup does not silently
reverse an earlier closure. PR #4 contains the foundation increment and its
review corrections. Later PRs should name the blocker or capability they address
and include current validation evidence. Record actual closure/merge state on
GitHub rather than maintaining a second, drifting PR-state table here.
