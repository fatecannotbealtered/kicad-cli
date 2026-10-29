"""Explicit regression-only exclusion of a retained native failure signature."""

import hashlib
import json
import re
from pathlib import Path

PATH_KEY = "KICAD_BENCH_NATIVE_QUARANTINE"
HASH_KEY = "KICAD_BENCH_NATIVE_QUARANTINE_SHA256"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def blocked_attempt(command, environment):
    path, expected = environment.get(PATH_KEY), environment.get(HASH_KEY)
    if path is None and expected is None:
        return None
    if (
        not path
        or not Path(path).is_absolute()
        or not re.fullmatch(r"[0-9a-f]{64}", expected or "")
    ):
        raise ValueError("Native quarantine requires an absolute registry and its frozen SHA256")
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Native quarantine registry drift")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"schema_version", "blocked"}:
        raise ValueError("Invalid native quarantine registry")
    if (
        value["schema_version"] != 1
        or not isinstance(value["blocked"], list)
        or not value["blocked"]
    ):
        raise ValueError("Invalid native quarantine entries")
    for row in value["blocked"]:
        if not isinstance(row, dict) or set(row) != {"id", "image_sha256", "input_sha256"}:
            raise ValueError("Invalid native quarantine entry")
        if (
            not isinstance(row["id"], str)
            or not row["id"]
            or not all(
                isinstance(row[key], str) and re.fullmatch(r"[0-9a-f]{64}", row[key])
                for key in ("image_sha256", "input_sha256")
            )
        ):
            raise ValueError("Invalid native quarantine identity")
    args = list(map(str, command))
    if args[1:4] != ["sch", "export", "netlist"]:
        return None
    # Current native helpers pass one final schematic argument. Refuse ambiguous
    # metadata instead of guessing. Output location and format cannot evade the
    # quarantine; a diagnostic retry needs a separately reviewed test plan.
    if (
        not Path(args[0]).is_absolute()
        or not Path(args[-1]).is_absolute()
        or Path(args[-1]).suffix != ".kicad_sch"
    ):
        raise ValueError("Ambiguous native netlist invocation under quarantine")
    image, schematic = digest(args[0]), digest(args[-1])
    for row in value["blocked"]:
        if (image, schematic) == (row["image_sha256"], row["input_sha256"]):
            return dict(row, registry_sha256=expected, process_launches=0)
    return None
