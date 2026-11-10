---
id: kicad-cli-name-clash
title: Two programs are called kicad-cli
kind: troubleshooting
applies_to: [doctor, schematic check, board check, schematic export, board export]
tags: [installation, path]
---

KiCad installs its own command-line program named `kicad-cli`, the same name as this tool.
In a terminal, whichever comes first in PATH runs.

- This tool never runs KiCad's program through PATH alone. It looks in `KICAD_CLI_KICAD_DIR`
  when that is set, then in KiCad's standard installation folders, then in PATH, and accepts
  only a program that sits inside a KiCad installation and is not this tool.
- If `kicad-cli --version` prints a bare KiCad version such as `10.0.6` instead of this tool's
  JSON, KiCad's program comes first in PATH. Call this tool by its full path, or move the npm
  global folder before KiCad's `bin` folder in PATH.
- `kicad-cli doctor` shows which KiCad installation the tool uses under the `application` check.
