#!/usr/bin/env python3
"""Change what kicad-cli just wrote, for the read_back_mismatch row of a live run (DVER-030).

    python tests/live/disturb.py delete           delete the files the paused write made
    python tests/live/disturb.py restore <copy>   put the bytes of <copy> back over the written file of that name

The matrix runner starts it with `--tamper` once kicad-cli pauses before its read-back, and sets
TOOL_DRIVER_PAUSE_FILE to the pause file, which names the write's targets. Changing the result
in that time is what another program writing the same files would do, so kicad-cli's read-back
must report the mismatch. Use it only on targets listed in docs/E2E.md, for example:

    python scripts/spec.py matrix --command "symbol update" --set doc=<practice copy> --row read_back_mismatch \
        --tamper "{python} tests/live/disturb.py restore <pristine copy of the same file>"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def target_files(pause: Path, whole: bool) -> list[Path]:
    """The files the paused write named: with ``whole``, only files it made as a whole (keys
    without a fragment); otherwise also the files that hold the objects it changed."""
    info = json.loads(pause.read_text(encoding="utf-8"))
    files: list[Path] = []
    for key in info.get("targets", []):
        if not key.startswith("file:///") or (whole and "#" in key):
            continue
        path = unquote(urlsplit(key.partition("#")[0]).path)
        if len(path) > 2 and path[0] == "/" and path[2] == ":":  # file:///D:/... on Windows
            path = path[1:]
        if Path(path) not in files:
            files.append(Path(path))
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["delete", "restore"])
    parser.add_argument("copy", nargs="?", type=Path, help="restore: the original bytes, such as a pristine copy")
    args = parser.parse_args()
    pause = os.environ.get("TOOL_DRIVER_PAUSE_FILE")
    if not pause:
        print("TOOL_DRIVER_PAUSE_FILE is not set; run this through `spec.py matrix --tamper`", file=sys.stderr)
        return 2
    files = target_files(Path(pause), whole=args.mode == "delete")
    changed = 0
    if args.mode == "delete":
        for path in files:
            if path.is_file():
                path.unlink()
                print(f"deleted {path}")
                changed += 1
    else:
        if args.copy is None or not args.copy.is_file():
            print("restore needs the file whose bytes to put back", file=sys.stderr)
            return 2
        original = args.copy.read_bytes()
        for path in files:
            if path.name == args.copy.name and path.is_file() and path.read_bytes() != original:
                path.write_bytes(original)
                print(f"restored {path}")
                changed += 1
    if not changed:
        print(f"nothing to change among {[str(f) for f in files]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
