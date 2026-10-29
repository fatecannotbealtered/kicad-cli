"""Windows NTFS handle-bound candidate publication, scoped to this output.

Every ancestor is acquired without following reparse points and retained without
write/delete sharing. The created file remains open without write/delete sharing
through write, readback, no-clobber rename and failure cleanup. No mutation below
uses a pathname after acquiring the directory. Other platforms fail closed.

Attribute-only directory handles can still set reparse points; sharing does not
exclude that operation. Relative acquisition also uses OBJ_DONT_REPARSE, so a
retained parent changed to a junction is refused before creating candidate bytes.

NtSetInformationFile is used for the relative rename: the Win32 wrapper rejects
non-null RootDirectory on some supported Windows installations.
"""

from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path


class OutputError(OSError):
    def __init__(self, message, reason):
        super().__init__(message)
        self.reason = reason


def _component(name):
    if (
        not name
        or name in {".", ".."}
        or any(char in name for char in "\\/:\0")
        or name.endswith((".", " "))
    ):
        raise OutputError("Candidate name is not a single ordinary component.", "invalid_path")
    return name


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_UnicodeString)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", wintypes.LPVOID),
        ("SecurityQualityOfService", wintypes.LPVOID),
    ]


class _IoStatus(ctypes.Structure):
    _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]


class _FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", wintypes.DWORD),
        ("created", wintypes.FILETIME),
        ("accessed", wintypes.FILETIME),
        ("written", wintypes.FILETIME),
        ("volume", wintypes.DWORD),
        ("size_high", wintypes.DWORD),
        ("size_low", wintypes.DWORD),
        ("links", wintypes.DWORD),
        ("index_high", wintypes.DWORD),
        ("index_low", wintypes.DWORD),
    ]


class _FileId(ctypes.Structure):
    _fields_ = [("volume", ctypes.c_ulonglong), ("identifier", ctypes.c_ubyte * 16)]


class _DeviceInfo(ctypes.Structure):
    _fields_ = [("device_type", wintypes.ULONG), ("characteristics", wintypes.ULONG)]


class _RenameInfo(ctypes.Structure):
    _fields_ = [
        ("replace", wintypes.BOOLEAN),
        ("root", wintypes.HANDLE),
        ("length", wintypes.DWORD),
        ("name", wintypes.WCHAR * 1),
    ]


class _WindowsApi:
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.native = ctypes.WinDLL("ntdll")
        definitions = {
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "GetFileInformationByHandle": (
                [wintypes.HANDLE, ctypes.POINTER(_FileInfo)],
                wintypes.BOOL,
            ),
            "GetFileInformationByHandleEx": (
                [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "GetVolumeInformationByHandleW": (
                [
                    wintypes.HANDLE,
                    wintypes.LPWSTR,
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                    ctypes.POINTER(wintypes.DWORD),
                    ctypes.POINTER(wintypes.DWORD),
                    wintypes.LPWSTR,
                    wintypes.DWORD,
                ],
                wintypes.BOOL,
            ),
            "WriteFile": (
                [
                    wintypes.HANDLE,
                    wintypes.LPVOID,
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                    wintypes.LPVOID,
                ],
                wintypes.BOOL,
            ),
            "ReadFile": (
                [
                    wintypes.HANDLE,
                    wintypes.LPVOID,
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.DWORD),
                    wintypes.LPVOID,
                ],
                wintypes.BOOL,
            ),
            "SetFilePointerEx": (
                [wintypes.HANDLE, ctypes.c_longlong, wintypes.LPVOID, wintypes.DWORD],
                wintypes.BOOL,
            ),
            "FlushFileBuffers": ([wintypes.HANDLE], wintypes.BOOL),
            "SetFileTime": (
                [wintypes.HANDLE, wintypes.LPVOID, wintypes.LPVOID, wintypes.LPVOID],
                wintypes.BOOL,
            ),
            "SetFileInformationByHandle": (
                [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD],
                wintypes.BOOL,
            ),
        }
        for name, (arguments, result) in definitions.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.kernel.GetSystemTimeAsFileTime.argtypes = [ctypes.POINTER(wintypes.FILETIME)]
        self.native.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            ctypes.POINTER(_ObjectAttributes),
            ctypes.POINTER(_IoStatus),
            wintypes.LPVOID,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.ULONG,
            wintypes.LPVOID,
            wintypes.ULONG,
        ]
        self.native.NtCreateFile.restype = wintypes.LONG
        self.native.NtSetInformationFile.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_IoStatus),
            wintypes.LPVOID,
            wintypes.ULONG,
            wintypes.ULONG,
        ]
        self.native.NtSetInformationFile.restype = wintypes.LONG
        self.native.NtQueryVolumeInformationFile.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_IoStatus),
            wintypes.LPVOID,
            wintypes.ULONG,
            wintypes.ULONG,
        ]
        self.native.NtQueryVolumeInformationFile.restype = wintypes.LONG
        self.native.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
        self.native.RtlNtStatusToDosError.restype = wintypes.ULONG

    def check(self, result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def status(self, status):
        if status < 0:
            raise ctypes.WinError(self.native.RtlNtStatusToDosError(status))

    def open(self, name, parent, *, create=False, read=False, owned=False):
        buffer = ctypes.create_unicode_buffer(name)
        encoded = name.encode("utf-16-le")
        if len(encoded) > 65532:
            raise OutputError("Candidate path component is too long.", "invalid_path")
        string = _UnicodeString(
            len(encoded), len(encoded) + 2, ctypes.cast(buffer, wintypes.LPWSTR)
        )
        attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes), parent, ctypes.pointer(string), 0x1040, None, None
        )  # OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE
        handle, ios = wintypes.HANDLE(), _IoStatus()
        access = 0x00100080  # SYNCHRONIZE | FILE_READ_ATTRIBUTES
        if read:
            access |= 1  # FILE_READ_DATA
        if owned:
            access |= 0x00010103  # DELETE | FILE_WRITE_ATTRIBUTES | READ/WRITE_DATA
        options = 0x00200020  # FILE_OPEN_REPARSE_POINT | FILE_SYNCHRONOUS_IO_NONALERT
        if read or owned:
            options |= 0x40  # FILE_NON_DIRECTORY_FILE
        self.status(
            self.native.NtCreateFile(
                ctypes.byref(handle),
                access,
                ctypes.byref(attributes),
                ctypes.byref(ios),
                None,
                0x80 if create else 0,
                1,
                2 if create else 1,
                options,
                None,
                0,
            )
        )  # FILE_SHARE_READ only; FILE_CREATE/FILE_OPEN, never OPEN_IF
        return handle

    def information(self, handle):
        info, file_id = _FileInfo(), _FileId()
        self.check(self.kernel.GetFileInformationByHandle(handle, ctypes.byref(info)))
        self.check(
            self.kernel.GetFileInformationByHandleEx(
                handle, 18, ctypes.byref(file_id), ctypes.sizeof(file_id)
            )
        )
        parent = {
            "device": str(file_id.volume),
            "inode": str(int.from_bytes(bytes(file_id.identifier), "little")),
        }
        return info, parent

    def filesystem(self, handle):
        name = ctypes.create_unicode_buffer(32)
        self.check(
            self.kernel.GetVolumeInformationByHandleW(
                handle, None, 0, None, None, None, name, len(name)
            )
        )
        return name.value

    def device_characteristics(self, handle):
        info, ios = _DeviceInfo(), _IoStatus()
        self.status(
            self.native.NtQueryVolumeInformationFile(
                handle, ctypes.byref(ios), ctypes.byref(info), ctypes.sizeof(info), 4
            )
        )  # FileFsDeviceInformation on the retained root, not a DOS path lookup.
        return info.characteristics


def _nanoseconds(value):
    return ((value.dwHighDateTime << 32) + value.dwLowDateTime - 116444736000000000) * 100


class File:
    def __init__(self, parent, name, handle, *, owned=False):
        self.parent, self.name, self.handle = parent, name, handle
        self.owned, self.retained = owned, False

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        try:
            if self.owned and not self.retained:
                self.discard()
        finally:
            self.parent.api.kernel.CloseHandle(self.handle)

    def identity(self):
        info, identity = self.parent.api.information(self.handle)
        if info.attributes & (0x10 | 0x400) or info.links != 1:
            raise OutputError("Candidate is not a single regular file.", "unsafe_file")
        return {
            **identity,
            "size": (info.size_high << 32) + info.size_low,
            "mtime_ns": str(_nanoseconds(info.written)),
            # Windows path stat's historical ctime denotes creation, not ChangeTime.
            "ctime_ns": str(_nanoseconds(info.created)),
        }

    def write(self, raw):
        api, offset = self.parent.api, 0
        while offset < len(raw):
            chunk = raw[offset : offset + 1024 * 1024]
            written = wintypes.DWORD()
            api.check(
                api.kernel.WriteFile(
                    self.handle,
                    ctypes.create_string_buffer(chunk),
                    len(chunk),
                    ctypes.byref(written),
                    None,
                )
            )
            if not 0 < written.value <= len(chunk):
                raise OSError("Candidate write made no valid progress.")
            offset += written.value
        api.check(api.kernel.FlushFileBuffers(self.handle))
        # Explicit time prevents deferred write-time updates on handle close from
        # invalidating the review's identity immediately after publication.
        timestamp = wintypes.FILETIME()
        api.kernel.GetSystemTimeAsFileTime(ctypes.byref(timestamp))
        api.check(api.kernel.SetFileTime(self.handle, None, None, ctypes.byref(timestamp)))

    def read(self):
        api = self.parent.api
        api.check(api.kernel.SetFilePointerEx(self.handle, 0, None, 0))
        chunks = []
        while True:
            buffer, count = ctypes.create_string_buffer(1024 * 1024), wintypes.DWORD()
            api.check(
                api.kernel.ReadFile(self.handle, buffer, len(buffer), ctypes.byref(count), None)
            )
            if not count.value:
                return b"".join(chunks)
            chunks.append(buffer.raw[: count.value])

    def publish(self, name):
        self.parent.validate()
        name = _component(name)
        encoded = name.encode("utf-16-le")
        buffer = ctypes.create_string_buffer(_RenameInfo.name.offset + len(encoded))
        info = _RenameInfo.from_buffer(buffer)
        info.replace, info.root, info.length = False, self.parent.handle, len(encoded)
        ctypes.memmove(ctypes.addressof(buffer) + _RenameInfo.name.offset, encoded, len(encoded))
        ios = _IoStatus()
        self.parent.api.status(
            self.parent.api.native.NtSetInformationFile(
                self.handle, ctypes.byref(ios), buffer, len(buffer), 10
            )
        )  # FileRenameInformation; retained parent + single name; no replacement
        self.name = name

    def discard(self):
        if not self.owned:
            raise OSError("An observed candidate cannot be deleted.")
        disposition = wintypes.BOOLEAN(True)
        self.parent.api.check(
            self.parent.api.kernel.SetFileInformationByHandle(
                self.handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)
            )
        )  # FileDispositionInfo targets the owned handle, including partial files.


class Directory:
    def __init__(self, path):
        if os.name != "nt":
            raise OutputError(
                "Handle-bound candidate publication is supported only on Windows.",
                "unsupported_platform",
            )
        self.path, self.api, self.handles = Path(path), _WindowsApi(), []
        if not self.path.is_absolute() or len(self.path.drive) != 2:
            raise OutputError("Candidate requires a local drive path.", "invalid_path")
        try:
            handle = self.api.open("\\??\\" + self.path.anchor, None)
            self._retain_directory(handle)
            try:
                characteristics = self.api.device_characteristics(handle)
            except OSError as exc:
                raise OutputError(
                    "Candidate output volume locality could not be established.",
                    "unsupported_filesystem",
                ) from exc
            if characteristics & 0x10:  # FILE_REMOTE_DEVICE
                raise OutputError(
                    "Handle-bound candidate publication requires a local NTFS volume.",
                    "unsupported_filesystem",
                )
            if self.api.filesystem(handle).upper() != "NTFS":
                raise OutputError(
                    "Handle-bound candidate publication requires a Windows NTFS volume.",
                    "unsupported_filesystem",
                )
            for name in self.path.parts[1:]:
                handle = self.api.open(_component(name), self.handle)
                self._retain_directory(handle)
        except BaseException:
            self.close()
            raise

    def _retain_directory(self, handle):
        self.handles.append(handle)
        info, self.identity = self.api.information(handle)
        if not info.attributes & 0x10 or info.attributes & 0x400:
            raise OutputError("Candidate parent is not an ordinary directory.", "unsafe_parent")
        self.handle = handle

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()

    def close(self):
        for handle in reversed(self.handles):
            self.api.kernel.CloseHandle(handle)
        self.handles.clear()

    def validate(self):
        """Re-read every retained ancestor handle; metadata sharing is not a lock."""
        for handle in self.handles:
            info, _ = self.api.information(handle)
            if not info.attributes & 0x10 or info.attributes & 0x400:
                raise OutputError("Candidate parent became a reparse point.", "unsafe_parent")

    def create(self, name):
        self.validate()
        name = _component(name)
        handle = self.api.open(name, self.handle, create=True, read=True, owned=True)
        return File(self, name, handle, owned=True)

    def open(self, name):
        self.validate()
        name = _component(name)
        handle = self.api.open(name, self.handle, read=True)
        return File(self, name, handle)

    def exists(self, name):
        try:
            handle = self.api.open(_component(name), self.handle)
        except FileNotFoundError:
            return False
        except OSError:
            # Sharing/access refusal does not establish absence.
            return True
        self.api.kernel.CloseHandle(handle)
        return True


@contextmanager
def observed(path):
    with Directory(Path(path).parent) as parent, parent.open(Path(path).name) as opened:
        yield parent, opened
