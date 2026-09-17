# Failure modes that look like success

Every entry here is something that produced a confident, wrong answer at least
once. They are grouped by how they fool you, not by which command they touch.

- [A. Numbers that are real but mean something else](#a-numbers-that-are-real-but-mean-something-else)
- [B. Clean results that checked nothing](#b-clean-results-that-checked-nothing)
- [C. Counts far larger than the number of problems](#c-counts-far-larger-than-the-number-of-problems)
- [D. Writes that cannot be undone by retrying](#d-writes-that-cannot-be-undone-by-retrying)

## A. Numbers that are real but mean something else

**A netclass width is not the width on the board.** A trace that is 1.2 mm for
most of its length and 0.2 mm for one segment carries 0.2 mm of current. Read
`width_compliance` and `width_by_net` — the achieved percentage — never the
target. `board rewidth` deliberately reports what it achieved, not what it was
asked for.

**Ampacity depends on assumptions you supplied.** `board audit --oz/--dt`
default to 1.0 oz and 10 K. On 2 oz copper every current figure is wrong. The
output echoes `copper_oz` and `delta_t_c`; check them before quoting.

**Plane-served nets look under-width and are not.** A net fed by a copper pour
through many vias shows a low track-width compliance because most of its current
never travels on a track. `board audit` removes them from the per-class summary
(`note: plane-served nets excluded`) but they still appear per net. The real
defect for a plane net is a pour with no via into it.

**Zero added and zero deleted is not "nothing happens."** `sch sync-preview`
reporting `CLEAN` means no footprint is created or destroyed. "Update fields" is
ticked by default in KiCad, so a field present on the symbol and absent from the
footprint is written into every affected footprint. Read `counts.update_fields`.

**A clean ERC is not a clean schematic.** A stock KiCad project ships with four
rules disabled, `single_global_label` among them — and a global label used
exactly once is what a mistyped label looks like. `sch audit` re-runs the
silenced rules in a throwaway copy; `with_rules_enabled` is a measurement, not a
prediction. It re-runs everything at `warning` severity, so a rule whose default
is `error` appears there as a warning.

**`pcb drc --schematic-parity` is not an oracle for the update dialog.** KiCad's
own parity check matches by reference designator; the updater matches by uuid
path. A board whose links are all broken but whose references are all correct
reads as perfectly in sync. Worse, empty paths are a state KiCad itself creates —
once someone has run "re-link by reference," parity will say the board is fine
forever. Use `sch link` and `sch sync-preview`.

## B. Clean results that checked nothing

**Check how much was examined before reading what was found.** `board plane`
reports `samples`; if it is 0, then `segments_without_reference: 0` means
nothing was looked at, not that nothing is wrong. An early version classified
layers into planes and signals by pour coverage and analysed only the signals —
on a four-layer board where every layer has a pour, that analysed nothing and
returned success.

**`not_checked` is part of the answer.** Most reads carry it. A `PASS` beside a
long `not_checked` is a narrow result, not a clean bill of health. Quote it.

**Some things only KiCad's GUI can confirm.** After `sch relink`, the tool has
verified that what it wrote is what KiCad's netlist declares and that KiCad reads
it back unchanged. It has *not* verified what the update dialog will say —
there is no IPC command to read that. Say so rather than implying closure.

## C. Counts far larger than the number of problems

One cause commonly produces many findings. Report the cause.

**`sch audit`**: on one real board, enabling the silenced rules produced 97
violations. 89 were a single decision — footprints republished into one project
library as `OriginalLib__OriginalName`, which every symbol's footprint filter
then misses. Two were genuine. Read `footprint_filters.genuinely_unmatched` and
the `patterns` array, not the raw count.

**`board plane`**: one hole in a plane produces a finding for every trace
crossing it. 462 segments grouped into 20 locations. Read `hotspots`.

**`board audit` width findings**: severity separates them. `error` means the
width is set by current; `warn` usually means a signal net is below a
stylistic netclass target. On one board, 33 findings contained 5 that mattered.

**Crossing a plane split is a fact, not automatically a problem.** `board plane`
reports the crossing and the distance to the nearest capacitor that could carry
the return current (`nearest_stitch`). A few millimetres is different from
fifteen, and a static strap line is different from a clock. Nets that the pours
themselves own are excluded — a GND trace passing over a power-plane boundary is
not a return-path break.

## D. Writes that cannot be undone by retrying

**KiCad holding the project makes writing pointless.** The editor keeps the
whole board in memory and writes all of it on save, so anything written
underneath is overwritten silently — not merged. Every write command refuses
when it finds `~*.lck` and returns `E_CONFLICT` with `lock_files`. Retrying will
not help; the user has to close KiCad.

**`board route --mode full` clears every existing track first.** That is what
`full` means. `repair` keeps what is there and only attempts what is
unconnected, and can be run repeatedly until it stops improving.

**`--no-verify` / `--no-restore` remove the safety net.** These commands run
DRC after writing and revert whatever introduced a new error. That is the only
reason they are safe unattended.

**`E_INTEGRITY` means a write was reverted for changing more than it should.**
`sch relink` once used pcbnew to save, which upgraded a KiCad 9 file to the
KiCad 10 format as a side effect — the user asked for a link to be restored,
not for their file to be migrated. The integrity check caught it. Do not retry
past this error; report it.

**Do not trust "the handler exists."** In KiCad 10.0.6,
`ParseAndCreateItemsFromString` and `UpdateBoardStackup` are registered and
unimplemented: they return success with nothing done. Verify by reading back.

## Working against a running KiCad

`board live` is the only command that acts on the board in the editor rather
than on the file. Changes appear on screen and go through KiCad's undo stack, so
the user can revert them with Ctrl+Z — which makes it the right mode when
someone is watching. It needs KiCad open with the API enabled
(Preferences → KiCad API); otherwise it returns `E_CONFIG` with the fix.

There is no IPC command to create or open a document, to read DRC results, or to
place a footprint from a library. Do not plan around those.
