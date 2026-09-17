"""Canonical error codes.

``contract/contract.json`` -> ``error_codes.core`` is the single source of truth
for the fleet, and ``contract_gen.py`` is generated from it -- CI regenerates and
compares, so a hand edit to either turns red.

This module used to restate the same sixteen rows by hand and claim it was
checked against the contract. It was not; the two agreed because someone kept
them agreeing. Deriving removes the question: there is now no second copy that
can drift.

Never invent an ``E_*`` outside core plus ``contract-ext.json``.
"""

from __future__ import annotations

from .contract_gen import CODES as _CONTRACT

# name -> (exit_code, retryable)
CORE: dict[str, tuple[int, bool]] = {
    name: (spec["exit"], spec["retryable"]) for name, spec in _CONTRACT.items()
}


def exit_code(code: str) -> int:
    return CORE.get(code, CORE["E_UNKNOWN"])[0]


def retryable(code: str) -> bool:
    return CORE.get(code, CORE["E_UNKNOWN"])[1]
