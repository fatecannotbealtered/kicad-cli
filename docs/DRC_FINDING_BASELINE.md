# Grid DRC finding baseline — unreleased

The grid engine in board route (full, repair and rewidth) and board rewidth
compares non-excluded error findings, including multiplicity. Counts remain
diagnostic. One old error disappearing while a new one appears must fail even
when both totals are one. New findings report E_INTEGRITY and use the existing
write transaction to roll back.

Identity version rule-item-position-v1 uses rule, severity and an order-independent
list of item kind, UUID and integer nanometre position. Display messages and
descriptions remain in the report but do not define identity. A changed position
under the same UUID is conservatively a new finding; this can reject replacement
copper that retains the same total. Additional duplicates cannot collapse into
one. Errors without usable object identity/position yield E_VERIFY_INCOMPLETE.
When a custom-rule file declares rules and errors exist, this checker currently
lacks a structured per-finding custom-rule origin. The gate conservatively reports
incomplete; a shared rule code cannot prove that one named rule did not replace
another. Structured rule origins are follow-up adapter work.
Changes to checker identity, project/custom-rule bytes, effective rules or
declared coverage also invalidate the comparison. Input drift during the check
or before its commit check cannot become success.

Verification keeps both saved-file observations and intermediate rewidth checks.
They record custom checker origin, package version, source-code fingerprint,
completion/time, board SHA-256, present/absent project and custom-rule inputs,
effective minima, severities, net classes, exclusions, custom-rule status, checked,
ignored, partial and unimplemented rules. A failed read retains the known source
and input with completion unknown; it never supplies a zero-error substitute.
The final observation describes the attempted saved state before rollback or
commit. write_state separately reports recovery and can be unknown if recovery
fails. This slice does not add an independent restoration reader to all writers.

The source is kicad_cli.fileformat.drc, a custom checker sharing parser/geometry
with the writer. It is not KiCad's official DRC and does not invoke pcbnew or the
official CLI. Unconnected findings, warnings, ignored rules and omissions remain
outside this legacy error gate. Coverage is bounded even when the criterion
passes. Library root is reported; library table/content dependencies are not
fingerprinted by this slice. Existing findings can worsen in magnitude without
creating a new identity; no_new_findings does not certify electrical correctness.

The existing explicit rewidth no-verify option records status not_run and ran
false; its diagnostic DRC reports are still observations, not a passed gate.
Safety defaults and the confirmation workflow are unchanged. Freerouting,
stitching and widening keep their existing guards and need separate identity
adoption. Native DRV operation/scope/HMAC migration is separate work.

Fixed before/replacement boards in tests/fixtures/drc_baseline isolate track_width.
Only the violating track UUID changes; the custom checker actually reports one
error for each saved file. tests/test_drc_baseline.py tests the identity rejection,
decreasing counts, duplicate multiplicity, incomplete reports, rule/input drift
and actual CLI rollback for all grid modes and the public rewidth leaf. Router
save substitution is fault injection, not evidence that a natural router run
produced this board. No native DRC acceptance is claimed by those tests.

Production uses no newly installed dependencies. The code fingerprint currently
requires on-disk Python sources; a frozen build needs a bound build manifest
before it can produce equivalent provenance. This candidate remains unpublishable.
