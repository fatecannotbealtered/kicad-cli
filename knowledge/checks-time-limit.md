---
id: checks-time-limit
title: A check or export on a large design can run out of time
kind: troubleshooting
applies_to: [schematic check, board check, board export, board render, schematic export, schematic render, footprint move, track create, via create, zone create, board update]
tags: [drc, erc, timeout]
---

Every call returns within 60 seconds. kicad-cli gives KiCad's program 25 seconds for a check or
a pre-write DRC, and 40 seconds for an export. KiCad's DRC on a small or medium board takes a few
seconds; a large, dense board with many zones can take longer.

- When a check or a pre-write DRC runs out of time, the command returns `E_TIMEOUT`; when an export does,
  `E_NATIVE_FAILURE`. Either way nothing changed, and trying again gives the same result.
- Ask the person to run the check or the export in KiCad's editor for that design.
- kicad-cli does not run checks as long jobs.
