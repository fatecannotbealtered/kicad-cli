"""Identity actually loaded by the existing external KiCad interpreter."""

import hashlib
import os
import sys
from pathlib import Path


def describe():
    import _pcbnew
    import pcbnew

    paths = [Path(sys.executable), Path(pcbnew.__file__), Path(_pcbnew.__file__)]
    loaded_path = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        kernel.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
        kernel.GetModuleFileNameW.restype = wintypes.DWORD
        handle = kernel.GetModuleHandleW(paths[2].name)
        buffer = ctypes.create_unicode_buffer(32768)
        if not handle or not kernel.GetModuleFileNameW(handle, buffer, len(buffer)):
            raise OSError("loaded native module path cannot be observed")
        loaded_path = str(Path(buffer.value).resolve())
        if Path(loaded_path) != paths[2].resolve():
            raise OSError("loaded native module differs from its Python module path")
    return {
        "version": pcbnew.GetBuildVersion(),
        "python": str(paths[0].resolve()),
        "loaded_native_path": loaded_path,
        "loaded_path_checked": loaded_path is not None,
        "components": [
            {"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
            for p in paths
        ],
    }
