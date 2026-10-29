"""Consume native fill geometry without silently upgrading the source board format."""

import json
import subprocess
from pathlib import Path


def fill(path):
    from . import kicad_env
    from .fileformat.sexpr import Document, List, copy, symbol
    from .move_transaction import digest

    python = kicad_env.find_python()
    if python is None:
        raise ValueError("External KiCad interpreter required for native ground fill")
    child = subprocess.run(
        [python, "-B", str(Path(__file__).parent / "payload/engineering_fill.py"), str(path)],
        capture_output=True,
        timeout=300,
    )
    if child.returncode:
        raise ValueError(
            "External native fill failed: " + child.stderr.decode("utf-8", "replace")[-500:]
        )
    result = json.loads(child.stdout)
    if digest(Path(result["output"])) != result["output_sha256"]:
        raise ValueError("Native filled output changed before consumption")
    native = Document.load(result["output"])
    original = Document.load(path)
    by_id = {n.find("uuid").value(1): n for n in native.root.find_all("zone")}
    for zone in original.root.find_all("zone"):
        if zone.find("keepout") is not None:
            continue
        source = by_id[zone.find("uuid").value(1)]
        for item in list(zone.items):
            if isinstance(item, List) and item.head in {"filled_polygon", "fill_segments"}:
                zone.remove(item)
        for poly in source.find_all("filled_polygon"):
            zone.append(copy(poly))
        status = zone.find("fill")
        if status is not None and (len(status.items) < 2 or isinstance(status.items[1], List)):
            status.insert(1, symbol("yes"))
    path.write_bytes(original.dumps().encode())
    return result
