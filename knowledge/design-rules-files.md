---
id: design-rules-files
title: Where a project's design rules live
kind: guide
applies_to: [project get, board check, footprint move, track create]
tags: [drc, rules, netclass]
---

- The board setup rules (minimum clearance, track width, via size) and the net classes are kept
  in the project file `.kicad_pro`, not in the board file. A board opened without its project file
  falls back to defaults. `project get` shows them as `rules` and `net_classes`.
- Net classes are assigned to nets by the patterns under `netclass_patterns` in `.kicad_pro`.
  A class name stored inside the board file can be stale; the patterns decide.
- Custom rules are in `<project>.kicad_dru` beside the project file. Each `(condition "...")`
  must stay on one line, and comments start with `#`.
- KiCad's DRC reads all of them, which is why kicad-cli runs it in the project folder.
