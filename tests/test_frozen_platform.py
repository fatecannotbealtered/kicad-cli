"""Kernel-derived process-local fallback, independent of inherited claims."""

import pytest

from kicad_cli.frozen_platform import seed_architecture


@pytest.mark.parametrize(
    "prior", [{}, {"PROCESSOR_ARCHITECTURE": "wrong", "PROCESSOR_ARCHITEW6432": "wrong"}]
)
@pytest.mark.parametrize(
    "process,native,expected", [(0, 0x8664, "AMD64"), (0, 0xAA64, "ARM64"), (0x014C, 0x8664, "x86")]
)
def test_kernel_identity_seeds_missing_or_inaccurate_environment(prior, process, native, expected):
    env = dict(prior, PRESERVED="value")
    assert seed_architecture(env, lambda: (process, native))
    assert env["PROCESSOR_ARCHITECTURE"] == expected
    assert env.get("PROCESSOR_ARCHITEW6432") == ("AMD64" if process else None)
    assert env["PRESERVED"] == "value" and prior.get("PRESERVED") is None


@pytest.mark.parametrize("failure", ["unsupported", "api"])
def test_unavailable_identity_stays_unknown(failure):
    def query():
        if failure == "api":
            raise OSError("Unavailable kernel observation")
        return 0, 0xFFFF

    env = {"PROCESSOR_ARCHITECTURE": "untrusted", "PROCESSOR_ARCHITEW6432": "untrusted"}
    assert not seed_architecture(env, query)
    assert env == {}
