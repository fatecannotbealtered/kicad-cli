---
id: flatpak-kicad
title: KiCad installed as a Flatpak on Linux
kind: troubleshooting
applies_to: [doctor]
tags: [linux, installation]
---

A Flatpak KiCad keeps its programs inside its sandbox, where kicad-cli cannot run them directly.
kicad-cli does not find a Flatpak KiCad.

- Install KiCad from the distribution's packages or KiCad's PPA instead, so that
  `/usr/bin/kicad-cli` exists, or point `KICAD_CLI_KICAD_DIR` at another installation.
- Reading files and lists still works without KiCad's program; checks, exports and the commands
  that need a fresh netlist do not.
