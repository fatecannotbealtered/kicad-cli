---
id: checks-are-not-sign-off
title: Clean checks are not a manufacturing approval
kind: guide
applies_to: [schematic check, board check, board export]
tags: [drc, erc, fabrication]
---

- ERC and DRC check what their rules describe: connections, pin types, clearances, widths, holes
  and the rules in the project. They do not check that the circuit works, that parts fit the
  enclosure, that currents and impedances are right, or that a fabricator can build the board.
- A run with no errors means the check ran and found nothing it was told to look for. Exit code 0
  of KiCad's program only means the check ran.
- When reporting, say which checks ran and what they found. Never call a design "ready to
  manufacture" because the checks are clean; that decision is the person's.
