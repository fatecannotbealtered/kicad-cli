---
id: design-flow
title: From a new part to fabrication files
kind: guide
applies_to: [symbol create, footprint create, schematic create, pin update, schematic check, board create, netlist import, footprint move, track create, board check, board export]
tags: [workflow]
---

The usual order of a design with kicad-cli, each step on the files with the editors closed:

1. Parts: look in `library get` first. For a new part, define its footprint with
   `footprint create` and its symbol with `symbol create` on a project `.kicad_sym`, naming the
   footprint in the symbol's Footprint field.
2. Schematic: `schematic create`, then `symbol create` for each part and `pin update` to join pins
   to nets. Check with `schematic check` (KiCad's ERC) and fix what it reports.
3. Board: `board create` builds the board from a fresh netlist. After later schematic changes,
   `netlist import` brings them over. Position parts with `footprint move`, add copper with
   `track create`, and check with `board check` (KiCad's DRC with schematic parity).
4. Outputs: `board export` for Gerber and drill files, `board render` and `schematic render` for
   PDF or SVG drawings.

Clean checks are not a manufacturing approval: see checks-are-not-sign-off.
