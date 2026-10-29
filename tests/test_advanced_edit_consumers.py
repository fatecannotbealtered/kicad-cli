"""Isolated pin intent is explicit, not derived from a generated graph."""

import copy
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench/rc_trial/execution/consumers"))
from compare_native_netlist import compare  # noqa: E402
from frozen_intent_view import expand  # noqa: E402


def fixture():
    intent = {
        "components": [
            {
                "ref": "R1",
                "symbol": "Device:R",
                "footprint": "R",
                "value": "1k",
                "pins": {"1": "N", "2": "N"},
            },
            {
                "ref": "TP1",
                "symbol": "Connector:TestPoint",
                "footprint": "TP",
                "value": "MON2",
                "pins": {"1": None},
                "in_bom": False,
            },
        ],
        "nets": {"N": ["R1.1", "R1.2"]},
        "isolation_policy": "kicad-10-explicit-no-connect",
    }
    xml = ET.fromstring("""<export><components>
      <comp ref="R1"><value>1k</value><footprint>R</footprint>
        <libsource lib="Device" part="R"/></comp>
      <comp ref="TP1"><value>MON2</value><footprint>TP</footprint>
        <libsource lib="Connector" part="TestPoint"/><property name="exclude_from_bom"/></comp>
    </components><nets>
      <net name="N"><node ref="R1" pin="1"/><node ref="R1" pin="2"/></net>
      <net name="unconnected-(TP1-Pad1)">
        <node ref="TP1" pin="1" pintype="passive+no_connect"/></net>
    </nets></export>""")
    return intent, xml


def test_declared_singleton_requires_native_nc_marker_and_preserves_original_intent():
    intent, xml = fixture()
    before = copy.deepcopy(intent)
    assert compare(xml, intent)["passed"]
    assert intent == before
    xml.find("./nets/net/node[@ref='TP1']").set("pintype", "passive")
    assert not compare(xml, intent)["passed"]


def test_nc_cannot_hide_a_wrong_pin_merge_or_bom_change():
    intent, xml = fixture()
    xml.find("./nets/net[@name='N']/node").set("ref", "TP1")
    assert not compare(xml, intent)["passed"]
    intent, xml = fixture()
    part = xml.find("./components/comp[@ref='TP1']")
    part.remove(part.find("property"))
    assert not compare(xml, intent)["passed"]


def test_null_is_not_silently_enabled_for_existing_intent_files():
    intent, _ = fixture()
    del intent["isolation_policy"]
    with pytest.raises(ValueError, match="explicit"):
        expand(intent)
