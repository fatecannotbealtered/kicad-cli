---
id: plot-origin
title: Which origin Gerber and drill files use
kind: guide
applies_to: [board export, board render]
tags: [export, gerber, origin]
---

- `board export --file-type gerber` uses the plot settings saved in the board, as KiCad's Plot
  dialog would, including whether coordinates are relative to the page origin or to the
  drill/place (auxiliary) origin.
- Gerber and drill files of one board must use the same origin, or the fabricator's tools put the
  holes beside the pads. Export both with kicad-cli, which keeps them on the board's saved choice.
- When a board's saved settings use the auxiliary origin, artwork left of or above that origin has
  negative coordinates. A PDF or SVG plot of such a board can cut off the part outside the page.
- Changing plot settings is done in KiCad's Plot dialog; kicad-cli does not change them.
