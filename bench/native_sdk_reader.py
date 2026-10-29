"""Fresh KiCad SDK consumer; no product modules or expected values imported."""

import ctypes
import hashlib
import importlib
import json
import os
import pathlib
import platform
import struct
import sys

import wx

wx.Log.SetActiveTarget(wx.LogStderr())
pcbnew = importlib.import_module("pcbnew")
_pcbnew = importlib.import_module("_pcbnew")


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def pe_machine(p):
    data = pathlib.Path(p).read_bytes()
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[offset : offset + 4] == b"PE\x00\x00"
    number = struct.unpack_from("<H", data, offset + 4)[0]
    return {
        "machine_code": number,
        "image_arch": {0x8664: "amd64", 0xAA64: "arm64", 0x14C: "x86"}.get(number, "unknown"),
    }


if sys.platform != "win32":
    print(
        json.dumps(
            {
                "completed": False,
                "error": "Windows loaded-module verification only; other platforms unvalidated",
            }
        )
    )
    raise SystemExit(2)
component = pathlib.Path(_pcbnew.__file__).resolve()
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
kernel.GetModuleHandleW.restype = ctypes.c_void_p
kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
handle = kernel.GetModuleHandleW(component.name)
buf = ctypes.create_unicode_buffer(32768)
loaded_len = kernel.GetModuleFileNameW(handle, buf, len(buf)) if handle else 0
loaded_path = pathlib.Path(buf.value).resolve() if loaded_len else None
assert loaded_path == component, (loaded_path, component)
record = {
    "completed": True,
    "source": "native_checker",
    "method": "fresh pcbnew import / LoadBoard",
    "process_id": os.getpid(),
    "executable": str(pathlib.Path(sys.executable).resolve()),
    "executable_sha256": sha(sys.executable),
    "python_version": sys.version,
    "application_version": pcbnew.GetBuildVersion(),
    "sys_platform": sys.platform,
    "platform_machine": platform.machine(),
    "pointer_bits": struct.calcsize("P") * 8,
    "reader_sha256": sha(__file__),
    "module": {
        "python_path": pcbnew.__file__,
        "python_sha256": sha(pcbnew.__file__),
        "disk_path": str(component),
        "loaded_path": str(loaded_path),
        "disk_sha256": sha(component),
        "loaded_path_checked": True,
        **pe_machine(component),
    },
    "independence": (
        "Separate native process; no product writer/parser import. Disk hash identifies "
        "the loaded module path, not a memory-code hash."
    ),
}
if len(sys.argv) > 1:
    p = pathlib.Path(sys.argv[1]).resolve()
    before = sha(p)
    board = pcbnew.LoadBoard(str(p))
    if board is None:
        record.update(
            completed=False,
            input_path=str(p),
            input_sha256=before,
            input_unchanged=before == sha(p),
            load_error="pcbnew.LoadBoard returned None",
        )
        print(json.dumps(record))
        raise SystemExit(2)
    footprints = []
    for fp in board.GetFootprints():
        footprints.append(
            {
                "ref": fp.GetReference(),
                "uuid": fp.m_Uuid.AsString(),
                "value": fp.GetValue(),
                "footprint": str(fp.GetFPID().GetLibNickname())
                + ":"
                + str(fp.GetFPID().GetLibItemName()),
                "position_nm": [int(fp.GetPosition().x), int(fp.GetPosition().y)],
                "rotation_deg": float(fp.GetOrientationDegrees()),
                "layer": pcbnew.BOARD.GetStandardLayerName(fp.GetLayer()),
                "dnp": bool(fp.IsDNP()),
                "pads": [
                    {
                        "number": pad.GetNumber(),
                        "net": pad.GetNetname(),
                        "position_nm": [int(pad.GetPosition().x), int(pad.GetPosition().y)],
                        "drill_nm": [int(pad.GetDrillSize().x), int(pad.GetDrillSize().y)],
                    }
                    for pad in fp.Pads()
                ],
            }
        )
    zones = []
    for zone in board.Zones():
        layers = list(zone.GetLayerSet().Seq())
        polygons = {}
        for layer in layers:
            polygons[pcbnew.BOARD.GetStandardLayerName(layer)] = zone.GetFilledPolysList(
                layer
            ).OutlineCount()
        zones.append(
            {
                "uuid": zone.m_Uuid.AsString(),
                "net": zone.GetNetname(),
                "rule_area": bool(zone.GetIsRuleArea()),
                "filled": bool(zone.IsFilled()),
                "filled_polygon_counts": polygons,
            }
        )
    record.update(
        input_path=str(p),
        input_sha256=before,
        input_unchanged=before == sha(p),
        footprints=sorted(footprints, key=lambda f: f["ref"]),
        zones=zones,
        tracks=len(board.GetTracks()),
        origin_nm=[
            int(board.GetDesignSettings().GetAuxOrigin().x),
            int(board.GetDesignSettings().GetAuxOrigin().y),
        ],
        coverage={
            "footprint_identity_pose_pads": "checked",
            "saved_zone_fill_objects": "checked",
            "DRC": "not_checked",
            "connectivity_algorithm": "not_checked",
            "manufacturing": "not_checked",
            "visual": "not_checked",
        },
    )
print(json.dumps(record))
