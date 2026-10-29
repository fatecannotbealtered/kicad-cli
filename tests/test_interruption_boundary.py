"""Guard the external observer's refusal to consume application debug causes."""

import os

import pytest

if os.name != "nt":
    pytest.skip("Windows x64 controller qualification", allow_module_level=True)

from bench.interruption_acceptance.controller import dword_argument, own_single_step


def test_only_the_owned_first_chance_execution_breakpoint_is_handled():
    address = 0x7FFE12345670
    assert own_single_step(True, 0x80000004, 0xFFFF0FF1, address, address)
    assert not own_single_step(False, 0x80000004, 1, address, address)
    assert not own_single_step(True, 0x80000003, 1, address, address)
    assert not own_single_step(True, 0x80000004, 0, address, address)
    assert not own_single_step(True, 0x80000004, 1, address + 1, address)


@pytest.mark.parametrize("other_cause", [2, 4, 8, 0x2000, 0x4000, 0x8000])
def test_mixed_application_debug_causes_are_not_swallowed(other_cause):
    assert not own_single_step(True, 0x80000004, 1 | other_cause, 1234, 1234)


def test_writefile_length_uses_the_right_justified_dword():
    assert dword_argument(0xFEDCBA9800000123) == 0x123
