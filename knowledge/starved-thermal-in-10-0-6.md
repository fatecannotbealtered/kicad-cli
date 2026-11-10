---
id: starved-thermal-in-10-0-6
title: starved_thermal on a pad whose spokes look connected
kind: troubleshooting
applies_to: [board check]
tags: [drc, zone, thermal]
---

KiCad 10.0.6 can report `starved_thermal` for a pad whose thermal spokes are visible. Its check
drops fill islands that touch only that pad before it counts the spokes, so spokes that end in
such an island do not count, even when a track on another layer ends inside the island.

- This is KiCad's rule, not a reading error of kicad-cli. The report is what KiCad's editor shows.
- The fix is in the design: join the islands to the zone's main fill, for example with a short
  copper bridge on the same layer, or remove the islands. Then refill and check again.
- Do not exclude the finding without the person's agreement.
