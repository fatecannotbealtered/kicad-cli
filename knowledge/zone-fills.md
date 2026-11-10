---
id: zone-fills
title: Copper zone fills in checks and exports
kind: concept
applies_to: [board check, board export, board render, footprint move, track create, track delete]
tags: [zone, drc, export]
---

A board file stores each copper zone's outline and the fill KiCad computed when the board was
last filled. After footprints or tracks move, the stored fill can be out of date.

- `board check` asks KiCad to refill every zone in memory before its DRC, and `board export`
  and `board render` ask KiCad to refill zones as needed before plotting. Nothing is written
  back to the board file.
- The fill stored in the file changes only when a person opens the board in KiCad, refills the
  zones (B) and saves.
- So the checks and outputs reflect current fills, while a viewer that shows the stored fill can
  still show the old one.
