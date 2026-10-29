"""A harmless caller that requests one response, then reads only its stdin pipe.

This program is deliberately unaware of the service command, evidence directory,
service output, and canaries. It cannot infer service success from an empty pipe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys


def emit(value):
    sys.stdout.buffer.write(
        (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    )
    sys.stdout.buffer.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--request-sha256", required=True)
    args = parser.parse_args()
    if not args.operation_id or len(args.operation_id) > 256:
        raise ValueError("A bounded opaque operation ID is required")
    if len(args.request_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.request_sha256):
        raise ValueError("Lowercase SHA-256 request identity is required")
    emit({
        "kind": "request_ready",
        "operation_id": args.operation_id,
        "request_sha256": args.request_sha256,
        "transport": "stdin_pipe",
    })
    # Actual blocking transport read. There is no success flag or fake response.
    digest = hashlib.sha256()
    received = 0
    while True:
        chunk = sys.stdin.buffer.read(65536)
        if not chunk:
            break
        received += len(chunk)
        digest.update(chunk)
    emit({
        "kind": "receiver_complete",
        "operation_id": args.operation_id,
        "request_sha256": args.request_sha256,
        "received_bytes": received,
        "received_sha256": digest.hexdigest(),
        "eof": True,
        "service_outcome_known": False,
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
