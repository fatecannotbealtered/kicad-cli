"""One-shot native saved-board readback with explicit site-free startup."""

import sys

if __name__ == "__main__" and (not sys.flags.no_site or not sys.flags.dont_write_bytecode):
    raise SystemExit("saved_scene_native_read requires Python -B -S")

import os

_NATIVE_BIN = os.path.dirname(os.path.abspath(sys.executable))
_NATIVE_SITE = os.path.join(_NATIVE_BIN, "Lib", "site-packages")
_EXPECTED_PATH = [
    os.path.join(_NATIVE_BIN, "Lib"),
    os.path.join(_NATIVE_BIN, "DLLs"),
    os.path.join(_NATIVE_BIN, f"python{sys.version_info.major}{sys.version_info.minor}.zip"),
    _NATIVE_SITE,
]
_DLL_HANDLE = None


def initialize_search_path():
    global _DLL_HANDLE
    sys.path[:] = _EXPECTED_PATH
    if os.name == "nt":
        _DLL_HANDLE = os.add_dll_directory(_NATIVE_BIN)


if __name__ == "__main__":
    initialize_search_path()

# These imports follow the explicit standard-library path initialization.
import hashlib  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def xy(point):
    return [int(point.x), int(point.y)]


def module_sources(native_bin=None, modules=None):
    root = Path(native_bin or _NATIVE_BIN).resolve()
    this = Path(__file__).resolve()
    modules = sys.modules if modules is None else modules
    result = []
    for name, module in sorted(modules.items()):
        filename = getattr(module, "__file__", None)
        if not filename:
            origin = getattr(getattr(module, "__spec__", None), "origin", None)
            if name == "swig_runtime_data4":
                provider = Path(modules["_pcbnew"].__file__).resolve()
                if provider != root / "Lib/site-packages/_pcbnew.pyd":
                    raise ValueError("Unbound native SWIG registration provider")
                result.append(
                    {
                        "module": name,
                        "origin": "SWIG registration from bound _pcbnew.pyd",
                        "keys": sorted(vars(module)),
                        "provider": str(provider),
                        "provider_sha256": sha(provider),
                    }
                )
                continue
            if name in {"typing.io", "typing.re"}:
                typing = modules["typing"]
                provider = Path(typing.__file__).resolve()
                if (
                    vars(typing)[name.removeprefix("typing.")] is not module
                    or provider != root / "Lib/typing.py"
                ):
                    raise ValueError("Unbound native typing alias provider")
                result.append(
                    {
                        "module": name,
                        "origin": "exact alias in bound standard-library typing",
                        "provider": str(provider),
                        "provider_sha256": sha(provider),
                    }
                )
                continue
            if origin not in {"built-in", "frozen"}:
                raise ValueError("Native reader has unbound source-free module: " + name)
            result.append({"module": name, "origin": origin})
            continue
        path = Path(filename).resolve()
        if path != this and root not in path.parents:
            raise ValueError("Native reader imported undeclared external code: " + name)
        if not path.is_file():
            archive = root / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
            if archive in path.parents and archive.is_file():
                result.append({"module": name, "path": str(path), "archive_sha256": sha(archive)})
                continue
            raise ValueError("Native module source is unavailable: " + name)
        result.append({"module": name, "path": str(path), "sha256": sha(path)})
    return result


def startup_identity():
    if (
        list(sys.path) != _EXPECTED_PATH
        or "sitecustomize" in sys.modules
        or "usercustomize" in sys.modules
    ):
        raise ValueError("Native Python startup exceeded explicit search paths")
    return {
        "no_site": bool(sys.flags.no_site),
        "dont_write_bytecode": bool(sys.flags.dont_write_bytecode),
        "actual_sys_path": list(sys.path),
        "explicit_site_packages": _NATIVE_SITE,
        "sitecustomize_or_usercustomize_loaded": False,
        "Documents_KiCad_thirdparty_consulted": False,
        "normal_site_addsitedir_executed": False,
        "native_bin_dll_directory": _NATIVE_BIN if _DLL_HANDLE is not None else None,
        "actual_module_sources": module_sources(),
    }


def main():
    if not sys.flags.no_site or not sys.flags.dont_write_bytecode:
        raise SystemExit("saved_scene_native_read requires Python -B -S")
    import pcbnew

    path = Path(sys.argv[1]).resolve()
    before = sha(path)
    board = pcbnew.LoadBoard(str(path))
    if board is None:
        raise RuntimeError("Native LoadBoard returned no board")
    footprints = []
    for fp in board.GetFootprints():
        footprints.append(
            {
                "ref": fp.GetReference(),
                "uuid": fp.m_Uuid.AsString(),
                "path": fp.GetPath().AsString(),
                "value": fp.GetValue(),
                "fields": {field.GetName(): field.GetText() for field in fp.GetFields()},
                "dnp": bool(fp.IsDNP()),
                "fpid": fp.GetFPID().GetUniStringLibId(),
                "position_nm": xy(fp.GetPosition()),
                "rotation_deg": fp.GetOrientationDegrees(),
                "layer": board.GetLayerName(fp.GetLayer()),
                "locked": bool(fp.IsLocked()),
                "pads": sorted(
                    [
                        {
                            "uuid": pad.m_Uuid.AsString(),
                            "number": pad.GetNumber(),
                            "net": pad.GetNetname(),
                            "position_nm": xy(pad.GetPosition()),
                            "size_nm": xy(pad.GetSize()),
                            "drill_nm": xy(pad.GetDrillSize()),
                            "shape": int(pad.GetShape()),
                            "rotation_deg": pad.GetOrientationDegrees(),
                            "layers": [
                                board.GetLayerName(layer) for layer in pad.GetLayerSet().Seq()
                            ],
                        }
                        for pad in fp.Pads()
                    ],
                    key=lambda item: item["uuid"],
                ),
            }
        )
    tracks = []
    for track in board.GetTracks():
        via = isinstance(track, pcbnew.PCB_VIA)
        item = {
            "uuid": track.m_Uuid.AsString(),
            "kind": track.GetClass(),
            "net": track.GetNetname(),
            "start_nm": xy(track.GetStart()),
            "end_nm": xy(track.GetEnd()),
            "width_nm": track.GetWidth(pcbnew.F_Cu) if via else track.GetWidth(),
            "layer": board.GetLayerName(track.GetLayer()),
            "locked": bool(track.IsLocked()),
        }
        if via:
            item["drill_nm"] = track.GetDrillValue()
        tracks.append(item)
    if sha(path) != before:
        raise RuntimeError("Native readback changed the source board")
    print(
        json.dumps(
            {
                "native_version": pcbnew.GetBuildVersion(),
                "input_sha256": before,
                "input_unchanged": True,
                "reader_sha256": sha(__file__),
                "pcbnew_wrapper_sha256": sha(pcbnew.__file__),
                "footprints": sorted(footprints, key=lambda item: item["ref"]),
                "tracks": sorted(tracks, key=lambda item: item["uuid"]),
                "zones": sorted(zone.m_Uuid.AsString() for zone in board.Zones()),
                "drawings": sorted(
                    (item.GetClass(), item.m_Uuid.AsString()) for item in board.GetDrawings()
                ),
                "startup": startup_identity(),
                "scope": (
                    "Native semantic readback; independent complete file token comparison protects "
                    "every zone/fill, stackup, setup, graphic, pad and copper property. "
                    "DRC and full-image review are separate."
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
