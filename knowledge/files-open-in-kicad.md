---
id: files-open-in-kicad
title: Close a file in KiCad before kicad-cli changes it
kind: guide
applies_to: [symbol create, symbol update, symbol move, symbol delete, pin update, net update, netlist import, footprint move, track create, track delete, footprint create]
tags: [file-mode, conflict, save]
---

kicad-cli works in file mode: it writes KiCad's files on disk. KiCad keeps an open file in
memory and writes it back when the person saves, which would throw away a change made on disk.

- While KiCad has a file open it keeps a lock file `~<file name>.lck` beside it. kicad-cli
  refuses to change such a file and returns `E_CONFLICT` with `file_open_in_session`: a person has
  to close the file first. Exports, checks and reads use the file on disk and go ahead, with a
  warning in `meta.notices` that KiCad may hold changes that are not saved yet; to include them,
  the person saves in KiCad first.
- Ask the person to save and close the schematic or board in KiCad, run the change, then let
  them open the file again. Reading a file that KiCad has open is fine.
- A lock file can stay behind after KiCad crashes. If KiCad is closed and the lock file is still
  there, the person may delete it.
