---
id: net-names-and-labels
title: How nets get their names in a schematic
kind: concept
applies_to: [net list, net update, pin update, symbol get]
tags: [schematic, net, label]
---

- Pins joined by wires form a net. A net is named by a label on it: a local label names it on its
  own sheet, a global label everywhere, a hierarchical label through a sheet pin to the parent
  sheet. A power symbol such as GND names a global net.
- A net no label names gets a generated name from one of its pins, such as `Net-(R1-Pad1)`.
- Local label names are local to their sheet: two sheets can each have a net called `SIG`, and
  they are different nets. Nets that cross sheets need global or hierarchical labels.
- `pin update --net` joins a pin to a net by a short wire and a label of the same kind the net
  already uses; `net update` renames a net by changing the labels that name it.
