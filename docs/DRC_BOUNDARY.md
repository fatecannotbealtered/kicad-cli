# Shared DRC execution boundary

This is a development change, not a release or proof that every PCB write is
transactional. `release_readiness` remains **unpublishable**.

## What changed

The shell, common payload library, routing payload and widening payload now
use `kicad_cli/payload/drc_runner.py`. It uses only the standard library and
can be imported both from the package and from KiCad's standalone interpreter.
There is no new public command and no change to routing geometry in this slice.

Each DRC invocation gets a new temporary workspace containing its report and
an isolated copy of the user's KiCad configuration. The original project stays
in place so local `.kicad_pro` and `.kicad_dru` resolution is preserved. Config
copy errors fail instead of silently changing which settings are used. The
workspace is cleaned on success, missing output, malformed output and timeout.

The runner requests JSON, millimetres and all severities. It does not request
`--exit-code-violations`, so only process exit 0 is accepted. **That means the
check ran, not that the board passed.** Findings, including exclusions, remain
in the original report. No `--save-board`, `--refill-zones`, or schematic-parity
option is passed. Stored zone fills are checked without recalculation.

Reports require the expected source, units, group arrays and structured finding
items with finite coordinates. Duplicate JSON keys and non-finite values are
rejected. Unknown additional properties are retained. A report exceeding 64 MiB
is refused explicitly rather than silently truncated. KiCad emits a basename
in `source`; a matching basename alone is not proof of freshness. Freshness
comes from an initially empty, invocation-specific directory and a successful
process, not from trusting timestamps supplied by the report.

The board and the two local sidecars are hashed before and after the call.
A changed/deleted input invalidates the report with `E_CONFLICT`. This detects
observable before/after changes; it is **not** a write lock and does not detect
all transient edits or changes to external library/config dependencies.

Automatic discovery rejects standalone npm/script shims and recognizes the
macOS application-bundle location. Explicit `KICAD_CLI_OFFICIAL` configuration
is the owner's trust decision, not an authenticity check. An invalid override
never silently selects another installation. Known self-invocation is rejected.

## Failure and recovery

Widening no longer silently falls back from a broken DRC process to geometric
approximation. The approximate path remains available only with the existing
explicit no-verify option. No permission or safety switch is enabled by this
change.

Payload verification failures carry `write_state: unknown` and a recovery hint:
inspect the board and backup before retrying the enclosing write. Some callers
may already have saved changes before DRC failed. The runner does **not** roll
those changes back and does not make interrupted writes safe to replay.
Routing backup/snapshot files, netlist/ERC isolation, whole-write transactions,
process-tree cancellation and the confirmation-token lifecycle are separate
pending work. Do not infer that all temporary files are now isolated.

## Evidence and upstream references

`tests/test_drc_runner.py` covers report validation, stale files, nonzero exits,
timeouts, source mismatch, state drift, config isolation, same-name concurrent
boards, resolver behavior and the host/payload adapters. It also launches real
Python stub processes in both import modes. These are **mock upstream tests**,
not real-KiCad live tests or a complete functional-contract-coverage claim.

Upstream behavior was checked against the KiCad 10.0 CLI manual and the native
DRC report/schema sources, not reimplemented from assumptions:

- https://docs.kicad.org/10.0/en/cli/cli.html#pcb_drc
- https://gitlab.com/kicad/code/kicad/-/blob/10.0/pcbnew/drc/drc_report.cpp
- https://gitlab.com/kicad/code/kicad/-/blob/10.0/resources/schemas/drc.v1.json

Fresh real-KiCad and frozen-binary validation are still required before release.
