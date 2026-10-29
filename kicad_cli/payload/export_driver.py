"""Run the existing SDK exporter on a captured copy, reporting loaded identity.

This subprocess never receives the original board or final output directory.
The driver constructs receipts from independently consumed output files.
"""

import json
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_identity import describe  # noqa: E402

identity = describe()
if sys.argv[1:] == ["--describe"]:
    print(json.dumps(identity))
else:
    import kicad_lib as K

    original_ok = K.ok

    def report(data):
        original_ok(dict(data, loaded_identity=identity))

    K.ok = report
    exporter = sys.argv.pop(1)
    if exporter not in {"plot", "drill"}:
        raise ValueError("unknown exporter")
    runpy.run_path(str(Path(__file__).with_name(exporter + ".py")), run_name="__main__")
