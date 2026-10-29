"""One-factor owned plot experiment using only the installed native SDK.

Fixture profile changes are explicit experimental setup, not product features.
"""

import hashlib
import json
import sys
from pathlib import Path

import pcbnew
import wx

app = wx.App(False)
path, out, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
board = pcbnew.LoadBoard(str(path))
opts = board.GetPlotOptions()


def profile(parameters):
    return {
        name: getattr(parameters, "Get" + name)()
        for name in (
            "UseAuxOrigin",
            "Scale",
            "AutoScale",
            "Mirror",
            "SvgFitPagetoBoard",
            "PlotFrameRef",
            "Negative",
        )
    }


before = profile(opts)
if mode == "no-aux":
    opts.SetUseAuxOrigin(False)
elif mode == "svg-fit":
    opts.SetSvgFitPageToBoard(True)
elif mode != "original":
    raise ValueError(mode)
requested = profile(opts)
if mode != "original":
    pcbnew.SaveBoard(str(path), board)
    board = pcbnew.LoadBoard(str(path))
    opts = board.GetPlotOptions()
saved = profile(opts)
out.mkdir(parents=True, exist_ok=False)
controller = pcbnew.PLOT_CONTROLLER(board)
target = controller.GetPlotOptions()
# Independent explicit native setup for this exact profile, not product copy_saved.
target.SetUseAuxOrigin(opts.GetUseAuxOrigin())
target.SetScale(opts.GetScale())
target.SetAutoScale(opts.GetAutoScale())
target.SetMirror(opts.GetMirror())
target.SetNegative(opts.GetNegative())
target.SetPlotFrameRef(opts.GetPlotFrameRef())
target.SetSvgFitPageToBoard(opts.GetSvgFitPagetoBoard())
target.SetOutputDirectory(str(out))
files = []
for layer in ("F.Cu", "B.Cu", "Edge.Cuts"):
    controller.SetLayer(board.GetLayerID(layer))
    for fmt in (pcbnew.PLOT_FORMAT_SVG, pcbnew.PLOT_FORMAT_PDF):
        controller.OpenPlotfile(layer.replace(".", "_"), fmt, layer)
        assert controller.PlotLayer()
        file = Path(controller.GetPlotFileName())
        controller.ClosePlot()
        files.append({"path": str(file), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
origin = board.GetDesignSettings().GetAuxOrigin()
print(
    json.dumps(
        {
            "before": before,
            "requested": requested,
            "saved": saved,
            "aux_origin_nm": [origin.x, origin.y],
            "version": pcbnew.GetBuildVersion(),
            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "files": files,
            "scope": (
                "Bounded direct SDK control: saved origin/scale/mirror/page options; "
                "not all plot settings"
            ),
        }
    )
)
