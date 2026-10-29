"""Freeze only a fully checked and explicitly reviewed current RC snapshot."""

import json
import os
import sys
from pathlib import Path

import review_binding as audit

base = Path(__file__).resolve().parent
work = Path(os.environ.get("RC4_WORK_ROOT", str(base))).resolve()
packet = base.parent / "agent-packet"
try:
    version = sys.argv[1]
    if version not in {"baseline", "eco"} or not work.is_relative_to(base):
        raise ValueError("One stage and an owned work directory are required")
    pointer = audit.read(work / "plans" / (version + "-review.json"))
    review = (work / pointer["directory"]).resolve()
    if not review.is_relative_to(work / "artifacts"):
        raise ValueError("Review escaped the owned run")
    result = audit.freeze(work, base, packet, review, work / "snapshots" / version)
    print(
        json.dumps(
            {
                "ok": True,
                "snapshot": str(work / "snapshots" / version),
                "review_id": result["review_id"],
            }
        )
    )
except (audit.ReviewError, OSError, ValueError, KeyError, IndexError) as exc:
    print(
        json.dumps(
            {
                "ok": False,
                "error": {
                    "code": getattr(exc, "code", "RC_FREEZE_REFUSED"),
                    "message": str(exc),
                    "details": getattr(exc, "details", {}),
                },
            }
        )
    )
    raise SystemExit(2) from exc
