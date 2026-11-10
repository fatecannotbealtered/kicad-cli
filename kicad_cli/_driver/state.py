"""The state directory (DSEC-030 to DSEC-032) and the adapter state store (DCLI-260).

One directory per user, at ``<PREFIX>_STATE_DIR`` or ``~/.<tool>``. The runtime creates it
readable and writable by the current user alone: mode 0700 on macOS and Linux, and on
Windows an ACL that grants the current user only, with inheritance from the parent cut.
``protection()`` reports what is actually in place, never what was intended.
"""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import DriverError

WINDOWS = sys.platform == "win32"


class StateDir:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def ensure(self) -> Path:
        """Create the directory with its protection on first use. A directory someone else
        created first, such as a host component writing its discovery file, gets the
        protection too, when it belongs to the current user (DSEC-032)."""
        if not self.path.is_dir():
            try:
                self.path.mkdir(parents=True, mode=0o700)
            except FileExistsError:
                pass
            except OSError as exc:
                raise DriverError("E_IO", f"Cannot create the state directory {self.path}: {exc.strerror}.") from None
            _protect(self.path)
        elif self.protection() == "none" and _owned(self.path):
            _protect(self.path)
        return self.path

    def sub(self, name: str) -> Path:
        """An entry directory such as ``operations/``, created when missing."""
        self.ensure()
        path = self.path / name
        if not path.is_dir():
            try:
                path.mkdir()
            except FileExistsError:
                pass
            except OSError as exc:
                raise DriverError("E_IO", f"Cannot create {path}: {exc.strerror}.") from None
        return path

    def usable(self) -> bool:
        return self.path.is_dir() and os.access(self.path, os.R_OK | os.W_OK | os.X_OK)

    def protection(self) -> str:
        if not self.path.is_dir():
            return "none"
        if WINDOWS:
            try:
                return "windows_acl" if _windows_owner_only(str(self.path)) else "none"
            except OSError:
                return "none"
        mode = stat.S_IMODE(self.path.stat().st_mode)
        return "posix_0700" if mode == 0o700 else "none"


def write_json(path: Path, data: Any) -> None:
    """Write a JSON file atomically: a temporary file in the same directory, then a rename.
    On Windows a rename fails while another process reads the file, so it is retried."""
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        for attempt in range(50):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.02)
    except OSError as exc:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise DriverError("E_IO", f"Cannot write {path}: {exc.strerror}.") from None


def read_json(path: Path) -> Any:
    """Read a JSON file, or ``None`` when it does not exist; retried while it is being replaced."""
    for attempt in range(50):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)
    return None


class AdapterStore:
    """Where an adapter keeps what it must remember between calls, under ``adapter/``."""

    def __init__(self, state: StateDir) -> None:
        self._state = state

    def _file(self, key: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,99}", key):
            raise ValueError(f"adapter store keys are lowercase names, not {key!r}")
        return self._state.sub("adapter") / f"{key}.json"

    def get(self, key: str, default: Any = None) -> Any:
        try:
            value = read_json(self._file(key))
        except ValueError:
            return default
        return default if value is None else value

    def set(self, key: str, value: Any) -> None:
        write_json(self._file(key), value)

    def delete(self, key: str) -> None:
        try:
            self._file(key).unlink()
        except FileNotFoundError:
            pass


# ------------------------------------------------------------------ protection


def _owned(path: Path) -> bool:
    if WINDOWS:
        return True  # the ACL is set for the current user, who can only change what they may
    return path.stat().st_uid == os.getuid()


def _protect(path: Path) -> None:
    if WINDOWS:
        try:
            _windows_restrict(str(path))
        except OSError:
            pass  # protection() reports what is in place
    else:
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


if WINDOWS:
    import ctypes
    from ctypes import wintypes

    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _SE_FILE_OBJECT = 1
    _DACL = 0x4
    _PROTECTED_DACL = 0x80000000
    _SE_DACL_PROTECTED = 0x1000
    _TOKEN_QUERY = 0x8
    _TOKEN_USER = 1
    _ACL_REVISION = 2
    _INHERIT = 0x1 | 0x2  # objects and containers inherit
    _FILE_ALL_ACCESS = 0x1F01FF
    _ALLOWED = 0

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", wintypes.WORD)]

    class _ACL_SIZE(ctypes.Structure):
        _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD), ("AclBytesFree", wintypes.DWORD)]

    _H, _D, _P = wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p
    _kernel.GetCurrentProcess.restype = _H
    _kernel.CloseHandle.argtypes = [_H]
    _kernel.LocalFree.argtypes = [_P]
    _advapi.OpenProcessToken.argtypes = [_H, _D, ctypes.POINTER(_H)]
    _advapi.GetTokenInformation.argtypes = [_H, ctypes.c_int, _P, _D, ctypes.POINTER(_D)]
    _advapi.GetLengthSid.argtypes = [_P]
    _advapi.GetLengthSid.restype = _D
    _advapi.ConvertSidToStringSidW.argtypes = [_P, ctypes.POINTER(wintypes.LPWSTR)]
    _advapi.InitializeAcl.argtypes = [_P, _D, _D]
    _advapi.AddAccessAllowedAceEx.argtypes = [_P, _D, _D, _D, _P]
    _advapi.SetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, _D, _P, _P, _P, _P]
    _advapi.SetNamedSecurityInfoW.restype = _D
    _advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, _D, _P, _P, ctypes.POINTER(_P), _P, ctypes.POINTER(_P),
    ]
    _advapi.GetNamedSecurityInfoW.restype = _D
    _advapi.GetSecurityDescriptorControl.argtypes = [_P, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(_D)]
    _advapi.GetAclInformation.argtypes = [_P, _P, _D, ctypes.c_int]
    _advapi.GetAce.argtypes = [_P, _D, ctypes.POINTER(_P)]

    def _check(ok: int) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def _user_sid() -> tuple[ctypes.Array, int]:
        token = wintypes.HANDLE()
        _check(_advapi.OpenProcessToken(_kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)))
        try:
            size = wintypes.DWORD()
            _advapi.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
            buf = ctypes.create_string_buffer(size.value)
            _check(_advapi.GetTokenInformation(token, _TOKEN_USER, ctypes.addressof(buf), size, ctypes.byref(size)))
        finally:
            _kernel.CloseHandle(token)
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        return buf, sid

    def _sid_string(sid: int) -> str:
        out = wintypes.LPWSTR()
        _check(_advapi.ConvertSidToStringSidW(sid, ctypes.byref(out)))
        try:
            return out.value or ""
        finally:
            _kernel.LocalFree(out)

    def _windows_restrict(path: str) -> None:
        buf, sid = _user_sid()
        size = 8 + 12 + _advapi.GetLengthSid(sid) + 8
        acl = ctypes.create_string_buffer(size)
        _check(_advapi.InitializeAcl(ctypes.addressof(acl), size, _ACL_REVISION))
        _check(_advapi.AddAccessAllowedAceEx(ctypes.addressof(acl), _ACL_REVISION, _INHERIT, _FILE_ALL_ACCESS, sid))
        err = _advapi.SetNamedSecurityInfoW(path, _SE_FILE_OBJECT, _DACL | _PROTECTED_DACL, None, None,
                                            ctypes.addressof(acl), None)
        if err:
            raise ctypes.WinError(err)
        del buf

    def _windows_owner_only(path: str) -> bool:
        dacl, sd = ctypes.c_void_p(), ctypes.c_void_p()
        err = _advapi.GetNamedSecurityInfoW(path, _SE_FILE_OBJECT, _DACL, None, None, ctypes.byref(dacl), None, ctypes.byref(sd))
        if err:
            raise ctypes.WinError(err)
        try:
            control, revision = wintypes.WORD(), wintypes.DWORD()
            _check(_advapi.GetSecurityDescriptorControl(sd, ctypes.byref(control), ctypes.byref(revision)))
            if not control.value & _SE_DACL_PROTECTED or not dacl.value:
                return False
            info = _ACL_SIZE()
            _check(_advapi.GetAclInformation(dacl, ctypes.addressof(info), ctypes.sizeof(info), 2))
            buf, me = _user_sid()
            mine = _sid_string(me)
            for i in range(info.AceCount):
                ace = ctypes.c_void_p()
                _check(_advapi.GetAce(dacl, i, ctypes.byref(ace)))
                header = ctypes.cast(ace, ctypes.POINTER(_ACE_HEADER)).contents
                if header.AceType != _ALLOWED or _sid_string(ace.value + 8) != mine:
                    return False
            del buf
            return info.AceCount > 0
        finally:
            _kernel.LocalFree(sd)
