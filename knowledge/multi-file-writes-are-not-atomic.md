---
id: multi-file-writes-are-not-atomic
title: A change that writes several files is not all-or-nothing
kind: guide
applies_to: [symbol create, footprint create, schematic create, symbol update, pin update, net update]
tags: [file-mode, recovery]
---

Each file is written whole: kicad-cli writes a temporary file beside it and renames it over the
old one, so a single file is never left half written. Some changes write more than one file:

- defining the first symbol or footprint of a new project library writes the library and the
  project's `sym-lib-table` or `fp-lib-table`;
- a change to a symbol on a sheet used by a hierarchy can write several sheet files;
- `schematic create` writes the schematic and, when there is none, the project file.

The files are written one after another. If a write fails midway, the receipt reports
`E_PARTIAL` and lists what was written. Read the files back with `symbol get`, `library get` or
`project get` before trying again, so the change is not applied twice.
