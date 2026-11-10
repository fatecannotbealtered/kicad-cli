---
id: library-changes-do-not-refresh-instances
title: Changing a library does not change placed symbols and footprints
kind: guide
applies_to: [symbol create, footprint create, library get, netlist import]
tags: [library, symbol, footprint]
---

A schematic keeps its own copy of every library symbol it uses (`lib_symbols`), and a board
keeps a copy of every footprint. Defining or redefining a symbol or footprint in a library does
not touch the copies already placed.

- A new symbol or footprint is used from the moment it is placed, or, for footprints, when
  `netlist import` adds a footprint for a symbol that names it.
- To make placed copies follow a changed library item, the person uses KiCad's "Update Symbols
  from Library" or "Update Footprints from Library". kicad-cli does not do this.
