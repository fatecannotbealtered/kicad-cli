---
id: kicad-not-found
title: kicad-cli does not find KiCad
kind: troubleshooting
applies_to: [doctor, schematic check, board check, board create, netlist import, footprint move, track create]
tags: [installation]
---

Checks, exports and the commands that need a fresh netlist run KiCad's own program. When the
`application` check of `kicad-cli doctor` fails, or a command returns `E_BACKEND_UNAVAILABLE` saying KiCad was not found:

1. Install KiCad 10.0.6 or a later 10.0 release. KiCad 11 is not supported yet.
2. If KiCad is installed somewhere other than its standard folder, set `KICAD_CLI_KICAD_DIR`
   to the installation folder: the folder that holds `bin/kicad-cli.exe` on Windows, the
   `KiCad.app` bundle on macOS, or the prefix of `bin/kicad-cli` on Linux.
3. Run `kicad-cli doctor` again; the `application` check names the version it found.

Reading files (lists and gets) works without KiCad, except that library lists need KiCad's
installed libraries to resolve `${KICAD10_SYMBOL_DIR}` and similar paths.
