# Saved drawing origin and page clipping

The retained original ATmega board has SHA-256
813c33fec3fe09a915c1b83dfb122e08554b99ba49f28679b31ed7d8fd18aaeb.
Its saved profile enables auxiliary origin (10,20) mm, scale 1, no autoscale,
no mirror, no frame and no SVG page-fit. The outline is (0,0)–(55,55) mm.

On installed KiCad 10.0.6, origin subtraction moves outline endpoints to
(-10,-20)–(45,35) mm, outside the fixed positive A4 viewport. Public exports and
a separate native PLOT_CONTROLLER control agree on SVG endpoints; PDF native
drawing bounds are compared separately. Actual page rendering corroborates
the missing upper/left artwork.

A one-factor control disables only UseAuxOrigin in an owned copy. Endpoints
translate by exactly (10,20) mm into the page. The failed input remains unchanged.
The earlier alternative also requested SVG page-fit, but native save/reload
reports it false; that setting did not explain the improvement. This native
setter's persistence behavior is observed, not diagnosed.

This establishes a saved-profile boundary, not a tool origin-conversion defect.
Automatically shifting the origin or fitting the page would change the promised
saved semantics. The original profile remains clipped. An explicitly different
drawing profile is suitable only for its different coordinate/page intent.
Gerber/manufacturing origin needs separate assessment. Syntax, counts or positive
endpoints do not establish complete visible geometry or readable drawings.

    python bench/run_plot_origin_case.py --binary D:/work/change-build/dist/kicad-cli.exe --baseline D:/work/inputs/atmega-snapshot --kicad-root D:/software/kicad/home --config-seed D:/work/inputs/configuration/10.0 --output D:/work/plot-origin-controls

This experiment preserves the original profile, creates two explicitly labelled
owned native configuration controls, invokes public SVG/PDF exports and compares
independent native output. Expanded-view images are inspection artifacts, not
corrected deliverables. Nonidentity SVG transforms refuse this bounded consumer.
