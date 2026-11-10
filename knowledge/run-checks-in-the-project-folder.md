---
id: run-checks-in-the-project-folder
title: Checks need the whole project folder
kind: troubleshooting
applies_to: [schematic check, board check, footprint move, track create]
tags: [drc, erc, library]
---

KiCad's ERC and DRC read more than the one file: the project file (`.kicad_pro`) holds the rule
settings and net classes, `.kicad_dru` holds custom rules, and the library tables say where the
symbols and footprints come from. kicad-cli runs KiCad's program in the folder of the file.

- A board or schematic copied out of its folder alone loses its rules and libraries; the DRC
  then reports library problems on every footprint. Run checks on the file in its project folder.
- When the report shows `lib_footprint_issues` or missing-library warnings for parts that exist,
  check with `library list` that the project's library tables name those libraries.
