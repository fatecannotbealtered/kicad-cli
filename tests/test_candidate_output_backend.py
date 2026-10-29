"""Real Windows filesystem API tests; never substitute POSIX semantics for them."""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager

import pytest

from kicad_cli import candidate_output

WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Actual Windows handle API qualification.")


def junction(path, target):
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(path), str(target)],
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        pytest.fail(f"Windows junction creation failed: {completed.returncode}")


@WINDOWS
def test_handle_publication_identity_and_bytes_survive_close(tmp_path):
    raw = b"complete exact candidate bytes\n"
    output = tmp_path / "review.kicad_pcb"
    with candidate_output.Directory(tmp_path) as parent:
        with parent.create(".owned.pending") as opened:
            opened.write(raw)
            assert opened.read() == raw
            opened.publish(output.name)
            expected = opened.identity()
            opened.retained = True
    with candidate_output.observed(output) as (_, opened):
        assert opened.read() == raw
        assert opened.identity() == expected
    assert not (tmp_path / ".owned.pending").exists()


@WINDOWS
@pytest.mark.parametrize("occupied", ["temporary", "destination"])
def test_exclusive_creation_and_destination_winner_are_preserved(tmp_path, occupied):
    temporary, output = tmp_path / ".owned.pending", tmp_path / "review.kicad_pcb"
    foreign = temporary if occupied == "temporary" else output
    foreign.write_bytes(b"external winner")
    with candidate_output.Directory(tmp_path) as parent:
        with pytest.raises(FileExistsError):
            with parent.create(temporary.name) as opened:
                opened.write(b"candidate")
                opened.publish(output.name)
    assert foreign.read_bytes() == b"external winner"
    if occupied == "destination":
        assert not temporary.exists()


@WINDOWS
def test_parent_and_source_replacement_are_blocked_during_write(tmp_path):
    parent_path = tmp_path / "ordinary"
    parent_path.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    temporary = parent_path / ".owned.pending"
    foreign = parent_path / "foreign"
    foreign.write_bytes(b"foreign")
    with candidate_output.Directory(parent_path) as parent, parent.create(temporary.name) as opened:
        with pytest.raises(OSError):
            parent_path.rename(tmp_path / "relocated")
        with pytest.raises(OSError):
            os.replace(foreign, temporary)
        with pytest.raises(OSError):
            temporary.write_bytes(b"foreign mutation")
        opened.write(b"owned")
        assert opened.read() == b"owned"
    assert not temporary.exists()
    assert foreign.read_bytes() == b"foreign"
    assert list(outside.iterdir()) == []


@WINDOWS
def test_junction_parent_is_refused_without_any_outside_file(tmp_path):
    outside, link = tmp_path / "outside", tmp_path / "link"
    outside.mkdir()
    junction(link, outside)
    try:
        with pytest.raises((candidate_output.OutputError, OSError)):
            with candidate_output.Directory(link) as parent, parent.create(".owned.pending"):
                pytest.fail("A junction must never become a candidate parent.")
        assert list(outside.iterdir()) == []
    finally:
        link.rmdir()


@WINDOWS
def test_partial_low_level_write_failure_deletes_only_owned_handle(tmp_path, monkeypatch):
    temporary = tmp_path / ".owned.pending"
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"must survive")
    with candidate_output.Directory(tmp_path) as parent:
        original = parent.api.kernel.WriteFile
        calls = []

        def partial_then_fail(handle, buffer, length, written, overlapped):
            calls.append(length)
            if len(calls) == 1:
                return original(handle, buffer, 1, written, overlapped)
            raise OSError("fixture failure after an actual one-byte WriteFile")

        monkeypatch.setattr(parent.api.kernel, "WriteFile", partial_then_fail)
        with pytest.raises(OSError, match="actual one-byte"):
            with parent.create(temporary.name) as opened:
                opened.write(b"partial candidate")
        assert calls == [17, 16]
    assert not temporary.exists()
    assert foreign.read_bytes() == b"must survive"


@WINDOWS
def test_cleanup_cannot_be_redirected_by_a_foreign_path_name(tmp_path):
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"foreign")
    with candidate_output.Directory(tmp_path) as parent:
        with parent.create(".owned.pending") as opened:
            opened.write(b"owned")
            # Cleanup has no path lookup even if an in-process seam changes its label.
            opened.name = foreign.name
    assert foreign.read_bytes() == b"foreign"
    assert not (tmp_path / ".owned.pending").exists()


@contextmanager
def junction_mutator(parent_path, outside):
    import ctypes
    import struct
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel.DeviceIoControl.restype = wintypes.BOOL
    # Attribute-only opens are exempt from sharing restrictions.
    handle = kernel.CreateFileW(str(parent_path), 0x100, 7, None, 3, 0x02200000, None)
    assert handle != ctypes.c_void_p(-1).value
    active = False

    def ioctl(operation, payload):
        returned = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(payload)
        ok = bool(
            kernel.DeviceIoControl(
                handle, operation, buffer, len(payload), None, 0, ctypes.byref(returned), None
            )
        )
        return ok, 0 if ok else ctypes.get_last_error()

    def mutate():
        nonlocal active
        substitute = ("\\??\\" + str(outside)).encode("utf-16-le")
        display = str(outside).encode("utf-16-le")
        paths = substitute + b"\0\0" + display + b"\0\0"
        payload = (
            struct.pack(
                "<IHHHHHH",
                0xA0000003,
                len(paths) + 8,
                0,
                0,
                len(substitute),
                len(substitute) + 2,
                len(display),
            )
            + paths
        )
        ok, error = ioctl(0x900A4, payload)
        active = active or ok
        return ok, error

    try:
        yield mutate
    finally:
        try:
            if active:
                ok, error = ioctl(0x900AC, struct.pack("<IHH", 0xA0000003, 0, 0))
                assert ok, error
        finally:
            kernel.CloseHandle(handle)


@WINDOWS
def test_attribute_only_junction_mutation_is_refused_before_candidate_write(tmp_path):
    parent_path, outside = tmp_path / "parent", tmp_path / "outside"
    parent_path.mkdir()
    outside.mkdir()
    with candidate_output.Directory(parent_path) as parent:
        with junction_mutator(parent_path, outside) as mutate:
            assert mutate() == (True, 0)
            # Exercise the native operation separately from the early handle
            # validation, preserving proof of OBJ_DONT_REPARSE at this boundary.
            with pytest.raises(OSError) as native_failure:
                parent.api.open(
                    ".native.pending", parent.handle, create=True, read=True, owned=True
                )
            assert native_failure.value.winerror == 1921
            with pytest.raises(candidate_output.OutputError) as failure:
                parent.validate()
            assert failure.value.reason == "unsafe_parent"
            with pytest.raises(candidate_output.OutputError):
                parent.create(".owned.pending")
            assert list(outside.iterdir()) == []
        assert list(parent_path.iterdir()) == []
        assert list(outside.iterdir()) == []


@WINDOWS
def test_owned_name_blocks_junction_mutation_through_publish_and_cleanup(tmp_path):
    parent_path, outside = tmp_path / "parent", tmp_path / "outside"
    parent_path.mkdir()
    outside.mkdir()
    with candidate_output.Directory(parent_path) as parent:
        with junction_mutator(parent_path, outside) as mutate:
            with parent.create(".owned.pending") as opened:
                assert mutate() == (False, 145)  # NTFS directory is no longer empty.
                assert list(outside.iterdir()) == []
                opened.write(b"owned candidate")
                assert opened.read() == b"owned candidate"
                assert mutate() == (False, 145)
                assert list(outside.iterdir()) == []
                opened.publish("review.kicad_pcb")
                assert mutate() == (False, 145)
                assert list(outside.iterdir()) == []
                parent.validate()
            assert list(parent_path.iterdir()) == []
            assert list(outside.iterdir()) == []
            # Once cleanup removes the owned name the real mutator works again;
            # retained-handle validation must reject that metadata change.
            assert mutate() == (True, 0)
            with pytest.raises(candidate_output.OutputError):
                parent.validate()
            assert list(outside.iterdir()) == []
        assert list(parent_path.iterdir()) == []


@WINDOWS
def test_mock_non_ntfs_capability_refuses_before_candidate_creation(tmp_path, monkeypatch):
    # Controlled adapter response, not a claim of testing an actual ReFS volume.
    monkeypatch.setattr(candidate_output._WindowsApi, "filesystem", lambda *_: "ReFS")
    with pytest.raises(candidate_output.OutputError) as failure:
        candidate_output.Directory(tmp_path)
    assert failure.value.reason == "unsupported_filesystem"
    assert list(tmp_path.iterdir()) == []


@WINDOWS
@pytest.mark.parametrize("capability", ["remote", "unavailable"])
def test_mock_nonlocal_volume_capability_refuses_before_candidate_creation(
    tmp_path, monkeypatch, capability
):
    # Controlled retained-handle adapter response; no network filesystem is used.
    def characteristics(_api, _handle):
        if capability == "unavailable":
            raise OSError("fixture unavailable FileFsDeviceInformation")
        return 0x10  # FILE_REMOTE_DEVICE

    monkeypatch.setattr(candidate_output._WindowsApi, "device_characteristics", characteristics)
    with pytest.raises(candidate_output.OutputError) as failure:
        candidate_output.Directory(tmp_path)
    assert failure.value.reason == "unsupported_filesystem"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="Actual unsupported-platform behavior.")
def test_non_windows_backend_fails_closed_without_creating_output(tmp_path):
    with pytest.raises(candidate_output.OutputError) as failure:
        candidate_output.Directory(tmp_path)
    assert failure.value.reason == "unsupported_platform"
    assert list(tmp_path.iterdir()) == []
