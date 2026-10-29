"""Synthetic stdout/stderr only. No product, native consumers, or design files."""

from __future__ import annotations

import argparse
import sys
import time


CASES = {
    "chunked-ascii": {
        "stdout": [b"SERVICE-STDOUT-CANARY-7fb6|", b"chunk-two|", b"complete\n"],
        "stderr": [b"SERVICE-STDERR-CANARY-671a|", b"diagnostic\n"], "exit": 0,
    },
    "unicode": {
        "stdout": ["SERVICE-STDOUT-CANARY-7fb6|中文Ω😀\n".encode("utf-8")],
        "stderr": ["SERVICE-STDERR-CANARY-671a|诊断λ\n".encode("utf-8")], "exit": 0,
    },
    "nonzero": {
        "stdout": [b"SERVICE-STDOUT-CANARY-7fb6|actual-service-failure\n"],
        "stderr": [b"SERVICE-STDERR-CANARY-671a|exit-seven\n"], "exit": 7,
    },
    "empty-stdout": {
        "stdout": [], "stderr": [b"SERVICE-STDERR-CANARY-671a|no-service-stdout\n"], "exit": 0,
    },
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=tuple(CASES), required=True)
    args = parser.parse_args()
    case = CASES[args.case]
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name).buffer
        for chunk in case[stream_name]:
            stream.write(chunk)
            stream.flush()
            time.sleep(0.025)
    return case["exit"]


if __name__ == "__main__":
    raise SystemExit(main())
