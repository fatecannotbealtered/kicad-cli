"""Private external native refill; output is a disposable candidate only."""

import hashlib
import json
import sys
from pathlib import Path

import _pcbnew
import pcbnew


def main():
    path = Path(sys.argv[1]).resolve(strict=True)
    if "engineering-trials" not in path.parts or "candidate" not in path.parts:
        raise ValueError("Native refill requires an owned candidate")
    manager = pcbnew.SETTINGS_MANAGER()
    project = str(path.with_suffix(".kicad_pro"))
    manager.LoadProject(project, False)
    board = pcbnew.LoadBoard(str(path))
    board.SetProject(manager.GetProject(project))
    board.BuildConnectivity()
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    output = path.with_name("native-filled.kicad_pcb")
    pcbnew.SaveBoard(str(output), board)
    print(
        json.dumps(
            {
                "version": pcbnew.GetBuildVersion(),
                "module": _pcbnew.__file__,
                "module_sha256": hashlib.sha256(Path(_pcbnew.__file__).read_bytes()).hexdigest(),
                "output": str(output),
                "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "method": "external pcbnew ZONE_FILLER and SaveBoard on captured trial",
            }
        )
    )


if __name__ == "__main__":
    main()
