"""The machine contract, read as data (contract/driver-contract.json).

The runtime takes every fixed value from the contract: error codes and exit codes, effects
and tiers, units, the confirm-token bindings, the receipt dimensions and the derivation
table. Nothing here repeats a value the contract already holds.
"""
from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path
from typing import Any


class Contract:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    @property
    def version(self) -> str:
        return self.data["version"]

    @property
    def schema_version(self) -> str:
        return self.data["schema_version"]

    @cached_property
    def errors(self) -> dict[str, dict[str, Any]]:
        return {e["code"]: e for e in self.data["error_codes"]}

    @cached_property
    def effects(self) -> dict[str, dict[str, Any]]:
        return {e["effect"]: e for e in self.data["effects"]}

    @cached_property
    def verbs(self) -> dict[str, str | None]:
        return {v["verb"]: v["effect"] for v in self.data["verbs"]}

    @cached_property
    def units(self) -> dict[str, dict[str, Any]]:
        return {u["quantity"]: u for u in self.data["units"]}

    @cached_property
    def global_flags(self) -> dict[str, str | None]:
        return {f["flag"]: f["value"] for f in self.data["global_flags"]}

    @cached_property
    def shared_flags(self) -> dict[str, str]:
        return {f["flag"]: f["value"] for f in self.data["shared_flags"]}

    @cached_property
    def target_flags(self) -> dict[str, str]:
        return {f["flag"]: f["object"] for f in self.data["target_flags"]}

    @cached_property
    def capabilities(self) -> dict[str, dict[str, Any]]:
        return {c["name"]: c for c in self.data["adapter_capabilities"]}

    @cached_property
    def standard(self) -> dict[str, dict[str, Any]]:
        return {c["path"]: c for c in self.data["standard_commands"]}

    @cached_property
    def builtin(self) -> dict[str, dict[str, Any]]:
        return {c["path"]: c for c in self.data["builtin_commands"]}

    @cached_property
    def session_states(self) -> dict[str, str | None]:
        return {s["state"]: s["error"] for s in self.data["session_states"]}

    @cached_property
    def device_states(self) -> dict[str, str | None]:
        return {s["state"]: s["error"] for s in self.data["device_states"]}

    @cached_property
    def disk_states(self) -> dict[str, dict[str, Any]]:
        return {s["state"]: s for s in self.data["disk_states"]}

    def tier_rank(self, tier: str) -> int:
        return ["none", *self.data["tiers"]].index(tier)

    def risk_rank(self, risk: str) -> int:
        return self.data["risk_tiers"].index(risk)
