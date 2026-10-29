"""Seed Python's non-WMI Windows architecture fallback before runtime hooks.

Some launchers omit PROCESSOR_* and platform.machine() then returns empty.
Only this process's environment changes, using kernel identity, never the SDK.
"""

import ctypes
import os
import sys

MACHINES = {0x014C: "x86", 0x8664: "AMD64", 0xAA64: "ARM64"}


def native_identity():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.IsWow64Process2.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel.IsWow64Process2.restype = ctypes.c_int
    process, native = ctypes.c_ushort(), ctypes.c_ushort()
    if not kernel.IsWow64Process2(
        kernel.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return process.value, native.value


def seed_architecture(environment, query=native_identity):
    try:
        process, native = query()
        native_name, process_name = MACHINES[native], MACHINES[process or native]
    except (OSError, AttributeError, KeyError):
        # Unknown is preferable to a caller-supplied architecture claim.
        environment.pop("PROCESSOR_ARCHITECTURE", None)
        environment.pop("PROCESSOR_ARCHITEW6432", None)
        return False
    environment["PROCESSOR_ARCHITECTURE"] = process_name
    if process:
        environment["PROCESSOR_ARCHITEW6432"] = native_name
    else:
        environment.pop("PROCESSOR_ARCHITEW6432", None)
    return True


if sys.platform == "win32" and getattr(sys, "frozen", False):
    seed_architecture(os.environ)
