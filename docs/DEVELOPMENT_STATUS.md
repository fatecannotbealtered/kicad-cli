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
| CONFIRM | Pending | Authenticated, expiring, single-use tokens; complete target/state binding; replay and changed-target rejection tests. |
| DRC | Pending | One trusted runner, isolated reports and per-operation temporary files; fresh-report and upstream-failure tests; no acceptance of a previous invocation's report. |
| TRANSACTION | Pending | Explicit per-mode verification, safe backup/rollback, cancellation and concurrent-write semantics; fault-injection tests plus real KiCad validation. |
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
