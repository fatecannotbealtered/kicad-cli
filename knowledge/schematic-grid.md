---
id: schematic-grid
title: Schematic positions sit on the 1.27 mm grid
kind: guide
applies_to: [symbol create, symbol move, pin update]
tags: [schematic, grid, units]
---

- Library symbol pins are drawn on a 1.27 mm (50 mil) grid. A wire end or pin that is off this
  grid does not connect, even when it looks connected.
- kicad-cli takes schematic positions in millimetres, such as `--x 25.4mm`, and refuses a
  position that is not a multiple of 1.27 mm. The error names the nearest grid point.
- The origin is the top-left corner of the page; x grows to the right and y grows downwards.
  Rotations are 0, 90, 180 or 270 degrees, counter-clockwise on screen.
- Schematic files store millimetres with 4 decimals (100 nm); read-back compares on that resolution.
