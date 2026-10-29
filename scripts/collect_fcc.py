"""Bind reviewed public behavior cases to actual JUnit and source/artifact bytes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.fcc_evidence import collect  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, action="append", required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    value = collect(ROOT, args.junit, args.binary)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "counts": value["counts"],
                "fcc_status": value["fcc_status"],
                "out": str(args.out.resolve()),
            }
        )
    )


if __name__ == "__main__":
    main()
