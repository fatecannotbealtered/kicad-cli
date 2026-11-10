---
id: kicad-11-not-supported
title: kicad-cli 1.0 supports KiCad 10.0.6 to below 11
kind: concept
applies_to: [doctor]
tags: [version, compatibility]
---

- kicad-cli writes KiCad's files itself and was tested with KiCad 10.0.6. Within 10.0 releases
  the file formats do not change.
- KiCad 11 is planned for about February 2027. It brings new file format versions and a larger
  IPC API, so kicad-cli refuses KiCad 11 until a release declares support for it.
- Files saved by an earlier KiCad can be read and changed: kicad-cli edits a file in its own text
  and keeps its format version. KiCad 10 converts the file to its own format when the person saves.
