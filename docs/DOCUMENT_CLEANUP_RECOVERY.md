# Saved document cleanup and recovery

The seven document-set engineering tasks retain the pinned v1.0.2 operation
protocol. This change concerns local transaction evidence, not a new resolve API.
Old intact v1 journals remain attributable; missing old evidence is not inferred.
New signed transactions cannot be downgraded to v1, including at cleanup entry.
Restoration checks journal attribution before touching any document.

After a verified save, cleanup first records an authenticated archive preparation
in `document-archive-preparations`. It binds the original journal, saved revisions,
complete baseline digests and each exclusive temporary reservation. Baselines
are written to hidden operation-specific `.archive-tmp` files, flushed, verified
and then atomically published without replacing an existing formal archive.
Windows uses no-overwrite rename; the POSIX implementation attributes only its
exact two-link publication pair. Other-platform execution is not yet verified.

An interrupted reserved temporary must be a regular single file and an exact
prefix of its already verified backup. It is preserved as signed retained partial
evidence, then copied again into a fresh exclusive reservation. It never becomes
the formal archive without full verification. A changed/foreign temporary, wrong
formal archive, forged preparation or extra hardlink is preserved and refused.
Already mismatched formal archives from older software are not silently repaired.
No cleanup failure invokes engineering-document rollback after the verified save.

Once all complete baselines are published, a signed cleanup checkpoint binds the authenticated plan,
document revisions, original journal, archive digests and exact sidecar paths.
Before each unlink it durably records the one pending deletion. Afterwards it
records completed progress. The final checkpoint survives removal of the journal
and a lost reply. A process interruption can therefore resume at any backup,
lock or journal deletion, including during resolve itself.

Only an already completed deletion or the single pending deletion may be absent.
Future missing evidence, changed archives, forged records, recreated sidecars
and foreign document revisions are refused. No global permission, ownership or
lock bypass is used. Project files are never rewritten by cleanup/resolve.
Process-crash evidence does not certify power-loss or filesystem durability on
every platform. The historical WinError5 restoration incident is a separate,
unresolved cause; this cleanup repair does not close it.

Use `operation show --id` after a lost reply and read its actual saved-file
observation and cleanup attribution. Resolve with that fresh signed evidence and
the observed outcome. A legitimate interrupted cleanup can finish even after
its sidecars disappeared in recorded steps. The SDK rejects a repeated resolve
as already registered (`E_VALIDATION`); files and archived baselines remain
unchanged. An unresolved operation blocks later writes to all members of the
document set, including a secondary member whose sidecar lock was already removed.

The public CLI fault matrix is `tests/test_document_cleanup_runtime.py`. It
covers every deletion in a two-document edit, deletion refusal, interrupted
resolve, partial archive writes, preparation/publication/checkpoint boundaries,
evidence tampering and a valid foreign saved revision. The independent
hash comparisons establish applied/partial/unknown state; an error is never
automatically described as rollback. Normal commits retain baseline archives
and signed checkpoints for audit; automatic retention deletion is not provided.
