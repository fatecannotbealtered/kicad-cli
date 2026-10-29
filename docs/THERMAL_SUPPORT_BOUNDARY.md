# Retained J1 thermal support boundary

The original PCB SHA-256 is
`1444094ee139d81be904a31c2c9f73f6357aa853ce0e58329dd811f8c80e83a2`;
the project SHA-256 is
`fbcfc51e0eac63c42d1012477dd5a49c81058e2d82dfeb35c8d223eb75fd405c`.
Original files/configuration/reports remain local and untouched.

## What the saved geometry establishes

On KiCad 10.0.6 the original has one native `starved_thermal` error at J1.6.
Custom counts four middle-gap intersections, i.e. two spokes for a minimum of
two, and emits no thermal error. Independent native polygon reading finds two
separate F.Cu contours each touching only J1.6; a third contour touches neither
the pad nor a same-layer track. The long B.Cu track endpoint lies inside the
third contour in XY only. That projection is not physical F.Cu contact.

The [10.0.6 native implementation](https://github.com/KiCad/kicad-source-mirror/blob/10.0.6/pcbnew/drc/drc_test_provider_zone_connections.cpp)
excludes single-connection islands before adding manual track contributions,
and has an all-isolated fallback when the resulting count is zero. Its track
endpoint branch in this revision does not filter track layer there.
These implementation branches explain this saved case and the controls below;
they are not a generic specification for all KiCad versions.

| Case | Only change from parent | Native thermal errors | Independent geometry |
|---|---|---:|---|
| retained | Isolated paths, same saved bytes | 1 | Two pad-only islands; remote copper, B.Cu endpoint projection |
| remove-short | Remove the short B.Cu stub | 1 | Same thermal geometry |
| simple-back | Replace the three contours with three rectangles | 1 | Same contact topology |
| simple-front | Long track B.Cu → F.Cu | 1 | Remote fill now directly contacts the track; spoke islands still separate |
| joined-front | Join the two islands to remote copper using 0.50 mm F.Cu bridges | 0 | One fill contour directly contacts pad and track |
| no-track-control | From remove-short, remove the long track | 0 | Isolated copper remains; **not** the positive connection case |

The negative/positive pair is `simple-front` / `joined-front`: one pad, one
track, one thermal zone, unchanged minimum 2, gap/spoke 0.50 mm, clearance
0.30 mm and minimum fill width 0.25 mm. Project bytes and ignored rules are
identical. Custom counts two spokes in both. The positive retains the native
missing-library warning from the original isolated configuration; no ignore
was added to remove it. These small saved-fill fixtures are intentionally
constructed, **not** the result of zone refilling or manufacturing approval.
The separate ECO workflow exercises real refilling.

## Reproduce with explicit dependencies

Use a fresh checkout and an existing installation; all outputs must be new
owned directories. No historical path is a fallback.

    python bench/prepare_thermal_cases.py --original D:/owned/retained/case.kicad_pcb --output D:/owned/thermal-inputs

The preparation script freezes each input hash, parent, single changed factor
and expected native result before checks. It does not call the product writer.
For the original, or a case in the prepared plan:

    python bench/run_thermal_counterexample.py --board D:/owned/thermal-inputs/simple-front/case.kicad_pcb --expected-board-sha256 <plan SHA> --expect-native-thermal 1 --expect-topology separated --hypothesis "Two spokes reach pad-only islands" --factor "Long track on F.Cu" --binary D:/candidate/kicad-cli.exe --kicad-root D:/software/kicad/home --config-seed D:/owned/retained/configuration/10.0 --output D:/owned/thermal-negative

For joined-front use its own frozen SHA, `--expect-native-thermal 0`,
`--expect-topology joined`, and its declared bridge factor. Explicit
`--native-python` / `--official-cli` override the installation's executables.

The runner reuses durable invocation records, independently reloads native
geometry, checks frozen thermal settings and contact topology, runs public
`board check` and official DRC separately, and preserves all rule violations
and ignores. `custom-trace.json` records ring coordinates, polygon identities
and exact counted intersections. That trace explains the product implementation;
it is not the independent oracle. Originals and saved test inputs must remain
unchanged. Native failures remain failures with raw evidence.

## Required boundary

This local disagreement is explained; general native-equivalent thermal
checking is **not** implemented. Replacing the crossing count with a blanket
"island means failure" rule would contradict the no-track control and broaden
an unverified promise. `board check` continues to report `starved_thermal`
under `native_drc_required_rules`; custom zero and `limited_checks_passed`
do not clear that requirement. Native DRC, intended connectivity and actual
layer contact are separate gates. The original board is rejected for its
native finding. Joined-front is a different test input, not a repair of the
original board or of the custom algorithm. No solid mode, relaxed rule,
exclusion or supplier signoff is involved.
