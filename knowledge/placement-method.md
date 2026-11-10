---
id: placement-method
title: Placing the parts of a board
kind: guide
applies_to: [board get, board update, footprint list, footprint move, footprint update, board check]
tags: [layout, placement, workflow]
---

A layout with kicad-cli is a loop of reading, moving and checking, with the files closed in KiCad.

1. Read the board: `board get` gives the outline, the box around all courtyards (`parts_extent`),
   the margin from it to each side of the outline and the footprints that stick out.
   `footprint list --fields id,x_mm,y_mm,rotation_deg,courtyard,pads` gives each part's size and
   its pads with their nets.
2. Group the parts by the circuit: a part's decoupling capacitor beside its supply pin, the parts
   of one function together, connectors on the board's edge facing outwards. Parts joined by many
   nets go close together; the courtyards may touch but not overlap.
3. A part's `x_mm`, `y_mm` is its origin, often pad 1 rather than its middle: place it by its
   courtyard box, which moves with the origin. `--rotation` turns it about the origin,
   counterclockwise with y down; turn it so that its pads face the parts they connect to.
4. Move the parts with `footprint move`, several in one `--input` file: one DRC check covers the
   whole call, so parts can trade places in one step. A move that would add a DRC error, such as
   overlapping courtyards, is refused, and the preview lists the errors: change the positions
   and send the call again.
5. Fit the outline with `board update` once the parts sit: `parts_extent` plus a margin of 1 to
   3 mm on each side. A connector meant to reach the edge sits with its courtyard just inside.
6. Check with `board check`: no courtyard overlaps and no copper too near the edge. Unconnected
   items are expected before routing.
7. Reference texts: `footprint update` puts each reference beside its own part, outside the other
   parts' courtyards and off the pads. Each item's result names the courtyard nearest the text's
   anchor, which must be its own part's. Then `board check --severity warning` names a text on a
   pad (`silk_over_copper`) or on another text (`silk_overlap`).

Tracks do not follow a moved footprint: place before routing.
