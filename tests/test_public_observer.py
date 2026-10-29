"""Only the attributed OS-version query is exempt from native/other-child refusal."""

import pytest

from bench.persistence_acceptance.process_tree import bootstrap_breakpoint
from bench.persistence_acceptance.public_contract import infrastructure, selection_readback


@pytest.mark.parametrize(
    "args,allowed",
    [
        ("cmd.exe /c ver", True),
        ('cmd.exe /c "ver"', True),
        ("cmd.exe /c ver & python native.py", False),
        ("cmd.exe /c other", False),
        (None, False),
        ("cmd.exe /c echo harmless & cmd.exe /c ver", False),
        ("echo harmless & cmd.exe /c ver", False),
        ("cmd.exe /c echo harmless | cmd.exe /c ver", False),
        ('cmd.exe /c "cmd.exe /c ver"', False),
        ("cmd.exe /c ver > NUL", False),
        ("cmd.exe < input /c ver", False),
        ("cmd.exe /c ver\n", False),
        ("cmd.exe /c (ver)", False),
        ("cmd.exe /c %VER% /c ver", False),
        ("cmd.exe /c 'ver'", False),
        ("cmd.exe /d /d /c ver", False),
        ('CMD.EXE /D /S /C "VER"', True),
        ('"cmd.exe" /c ver', True),
        ("cmd /C VER", True),
        ({"ambiguous": "cmd /c ver"}, False),
    ],
)
def test_metadata_helper_requires_the_exact_observed_command(tmp_path, args, allowed):
    process = {"image": str(tmp_path / "System32/cmd.exe"), "metadata": {"CommandLine": args}}
    assert infrastructure(process, tmp_path / "candidate.exe", tmp_path) is allowed


def test_native_python_and_renamed_shell_are_never_infrastructure(tmp_path):
    for name in ("python.exe", "cmd.exe"):
        assert not infrastructure(
            {"image": str(tmp_path / name), "metadata": {"CommandLine": "cmd.exe /c ver"}},
            tmp_path / "candidate.exe",
            tmp_path,
        )


def test_full_shell_path_and_child_are_classified_separately(tmp_path):
    shell = tmp_path / "System32/cmd.exe"
    parent = {"image": str(shell), "metadata": {"CommandLine": f'"{shell}" /c "ver"'}}
    child = {"image": str(tmp_path / "native.exe"), "metadata": {"CommandLine": "native.exe"}}
    assert infrastructure(parent, tmp_path / "candidate.exe", tmp_path)
    assert not infrastructure(child, tmp_path / "candidate.exe", tmp_path)


@pytest.mark.parametrize("metadata", [None, [], {}, {"CommandLine": ""}])
def test_missing_or_ambiguous_metadata_is_not_whitelisted(tmp_path, metadata):
    assert not infrastructure(
        {"image": str(tmp_path / "System32/cmd.exe"), "metadata": metadata},
        tmp_path / "candidate.exe",
        tmp_path,
    )


def test_only_first_first_chance_loader_breakpoint_is_handled():
    seen = set()
    assert not bootstrap_breakpoint(0xC0000008, True, 1, seen)
    assert seen == set()
    assert not bootstrap_breakpoint(0x80000003, False, 1, seen)
    assert seen == set()
    assert bootstrap_breakpoint(0x80000003, True, 1, seen)
    assert not bootstrap_breakpoint(0x80000003, True, 1, seen)
    assert bootstrap_breakpoint(0x80000003, True, 2, seen)


@pytest.mark.parametrize("values", [["/OUT1"], ["/OUT1", "/OUT1"], ["/OUT1", "/OUT3"]])
def test_positive_preview_must_contain_the_actual_expanded_selection(values):
    plan = {"extensions": {"params": {"nets": values}, "selected": values}}
    if values == ["/OUT1", "/OUT3"]:
        assert selection_readback("unique-nets-repeat", plan)["actual_values"] == values
    else:
        with pytest.raises(ValueError):
            selection_readback("unique-nets-repeat", plan)
