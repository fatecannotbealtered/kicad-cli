"""Identify direct Popen programs from retained Python audit events, fail closed."""

import ctypes
import os
from pathlib import Path


def program(executable, argv):
    if executable is not None:
        return str(executable) if isinstance(executable, (str, bytes, os.PathLike)) else None
    if isinstance(argv, (list, tuple)):
        return str(argv[0]) if argv and isinstance(argv[0], (str, os.PathLike)) else None
    if not isinstance(argv, str) or not argv or any(c in argv for c in "\0\r\n"):
        return None
    if os.name != "nt":
        return None  # Windows command-line quoting must not be guessed with POSIX shlex.
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    result = shell.CommandLineToArgvW(argv, ctypes.byref(count))
    if not result:
        return None
    try:
        return result[0] if count.value and result[0] else None
    finally:
        kernel.LocalFree(result)


def direct(executable, argv, expected):
    selected = program(executable, argv)
    return bool(
        selected
        and Path(selected).is_absolute()
        and Path(selected).resolve() == Path(expected).resolve()
    )
