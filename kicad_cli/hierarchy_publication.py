"""No-clobber new hierarchy sources with retained Windows directory handles.

The source set is saved sequentially, not atomically. Failed or partial source
sets and owned pending files are preserved for original-operation observation.
"""

from __future__ import annotations

import hashlib
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .candidate_output import Directory
from .fileformat.schematic import Schematic
from .sch_hierarchy_inputs import validate_directories


def save_order(paths):
    """Save changed children before their referring parents; preserve sibling order."""
    selected = {Path(path).resolve(): Path(path) for path in paths}
    visiting, saved, result = set(), set(), []

    def visit(path):
        key = path.resolve()
        if key in saved:
            return
        if key in visiting:
            raise ValueError("A candidate hierarchy contains a cycle")
        visiting.add(key)
        if path.suffix == ".kicad_sch":
            for child in Schematic.load(path).sheets:
                child_key = (path.parent / child.file).resolve()
                if child_key in selected:
                    visit(selected[child_key])
        visiting.remove(key)
        saved.add(key)
        result.append(path)

    for path in paths:
        visit(Path(path))
    return result


@contextmanager
def reserve(plan):
    """Acquire every relevant ancestor before any formal transaction sidecar."""
    extensions = plan["extensions"]
    validate_directories(extensions.get("output_directories", []))
    rows = extensions["files"]
    with ExitStack() as stack:
        parents = {}
        if extensions.get("hierarchy_incremental") and any(
            row["baseline"] == "absent" for row in rows
        ):
            for name in sorted({str(Path(row["path"]).parent) for row in rows}):
                parents[name] = stack.enter_context(Directory(Path(name)))
            validate_directories(extensions.get("output_directories", []))
        yield parents


def publish_new(row, operation, parents):
    path = Path(row["path"])
    parent = parents[str(path.parent)]
    pending = path.name + "." + operation + ".pending"
    raw = Path(row["candidate"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != row["intended"]:
        raise OSError("Prepared new source changed before publication")
    if parent.exists(path.name):
        raise FileExistsError("New hierarchy source appeared after preview: " + str(path))
    with parent.create(pending) as created:
        # Preserve a partial owned pending file too. Never delete a newly
        # published formal source to represent the whole batch as rolled back.
        created.retained = True
        created.write(raw)
        if created.read() != raw:
            raise OSError("Pending hierarchy source readback differs")
        created.publish(path.name)
        if created.read() != raw:
            raise OSError("Published hierarchy source readback differs")
