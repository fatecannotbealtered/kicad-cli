"""A JSON document cannot substitute for a successful public process exit."""

import subprocess
from types import SimpleNamespace

import native_process
import pytest
import test_native_zones as zones


@pytest.mark.parametrize("code", [5, 0xC0000008])
def test_zone_cli_refuses_nonzero_exit_with_success_json(monkeypatch, code):
    stdout = b'{"ok":true,"schema_version":"2.0","data":{}}'
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=code, stdout=stdout, stderr=b""),
    )
    monkeypatch.setattr(
        native_process,
        "run",
        lambda *args, **kwargs: ({"exit": code}, stdout, b""),
    )
    with pytest.raises(AssertionError):
        zones.cli(["reference"])
