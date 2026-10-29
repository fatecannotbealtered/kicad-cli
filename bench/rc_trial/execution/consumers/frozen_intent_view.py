"""Expand predeclared isolated pins; never infer expected nets from a generated file."""

import copy


def expand(intent):
    result = copy.deepcopy(intent)
    isolated = {}
    for part in result["components"]:
        for pin, net in part["pins"].items():
            if net is not None:
                continue
            if intent.get("isolation_policy") != "kicad-10-explicit-no-connect":
                raise ValueError("Null pin intent requires the explicit KiCad 10 no-connect policy")
            name = f"unconnected-({part['ref']}-Pad{pin})"
            identity = f"{part['ref']}.{pin}"
            if name in result["nets"]:
                raise ValueError("Isolated pin name collides with an intended named net")
            result["nets"][name] = [identity]
            part["pins"][pin] = name
            isolated[identity] = name
    return result, isolated
