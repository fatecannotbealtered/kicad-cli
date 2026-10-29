"""Rasterize an existing native SVG read-only; write only review PNG artifacts."""

import hashlib
import json
import sys
from pathlib import Path

import fitz

source, target = map(Path, sys.argv[1:3])
if target.exists():
    raise SystemExit("Render target already exists")
document = fitz.open(stream=source.read_bytes(), filetype="svg")
page = document[0]
scale = float(sys.argv[3]) if len(sys.argv) > 3 else 3
pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
pixmap.save(str(target))
print(
    json.dumps(
        {
            "source": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "target": str(target),
            "target_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "page_points": list(page.rect),
            "pixel_size": [pixmap.width, pixmap.height],
            "coverage": "full SVG viewport; no cropping",
        }
    )
)
