"""Two witnessed representatives of one frozen fixture's illegal bus junction.

This is test-only identity matching, not a product rule or an ignored finding.
The input geometry and both UUID pairs are fixed independently of ERC output.
"""

import hashlib
from decimal import Decimal

from kicad_cli.fileformat.sexpr import Document

FIXTURE_SHA256 = "131d72988dd0381a727f150e73f49ff796f6b90c22be31b46db8b5f4cf0667b8"
BUS = "9920fab9-c5f1-5857-8edb-93f398bb5cbf"
BUS_LABEL = "24a5b1a2-1180-5dd1-a01f-bb63212fc107"
WIRE = "cbf0e1dc-ec32-5f3c-8e2d-68f10e46c7f2"
NET_LABEL = "311fb860-157e-51d5-a1ec-f952bbc171a6"
JUNCTION = "b501d33f-4190-5178-a759-54a218522651"
PAIRS = {frozenset((BUS, WIRE)), frozenset((BUS_LABEL, NET_LABEL))}
BARE_BUS = "88cd6b9c-33ec-56b1-9d8e-7bfb7985e097"
BARE_LABEL = "e472124e-b2ab-52c4-86be-67461ca12280"
BARE_WIRE = "1ce35dbd-3f28-566d-9f18-0d75c9d098b4"
BARE_JUNCTION = "b3d083cf-7c20-5977-8de3-16d40a14dff9"
BARE_PAIRS = {frozenset((BARE_WIRE, BARE_BUS)), frozenset((BARE_WIRE, BARE_LABEL))}


def checked_geometry(path):
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != FIXTURE_SHA256:
        raise ValueError("ERC representative fixture bytes changed")
    selected = {}
    for node in Document.parse(raw.decode("utf-8")).root.lists():
        identity = node.find("uuid")
        if identity is not None and identity.value() in {BUS, BUS_LABEL, WIRE, NET_LABEL, JUNCTION}:
            if identity.value() in selected:
                raise ValueError("Duplicate fixture identity")
            selected[identity.value()] = node
    assert set(selected) == {BUS, BUS_LABEL, WIRE, NET_LABEL, JUNCTION}

    def xy(node):
        return tuple(Decimal(v) for v in node.values()[:2])

    bus = selected[BUS]
    wire = selected[WIRE]
    assert bus.head == "bus" and wire.head == "wire"
    a, b = [xy(p) for p in bus.find("pts").find_all("xy")]
    c, d = [xy(p) for p in wire.find("pts").find_all("xy")]
    junction = selected[JUNCTION]
    assert junction.head == "junction"
    point = xy(junction.find("at"))
    assert a == (Decimal("25.4"), Decimal("182.88"))
    assert b == (Decimal("50.8"), Decimal("182.88"))
    assert c == (Decimal("38.1"), Decimal("177.8"))
    assert d == (Decimal("38.1"), Decimal("191.77"))
    assert point == (c[0], a[1]) and a[0] < point[0] < b[0] and c[1] < point[1] < d[1]
    for identity, text, start, end in (
        (BUS_LABEL, "WJ[0..1]", a, b),
        (NET_LABEL, "WJ0", c, d),
    ):
        node = selected[identity]
        assert node.head == "label" and node.value() == text
        x, y = xy(node.find("at"))
        assert (end[0] - start[0]) * (y - start[1]) == (end[1] - start[1]) * (x - start[0])
        assert min(start[0], end[0]) <= x <= max(start[0], end[0])
        assert min(start[1], end[1]) <= y <= max(start[1], end[1])


def checked_bare_geometry(path):
    if hashlib.sha256(path.read_bytes()).hexdigest() != FIXTURE_SHA256:
        raise ValueError("ERC representative fixture bytes changed")
    document = Document.load(path)
    identifiers = {BARE_BUS, BARE_LABEL, BARE_WIRE, BARE_JUNCTION}
    selected = {}
    for node in document.root.lists():
        identity = node.find("uuid")
        if identity is not None and identity.value() in identifiers:
            assert identity.value() not in selected
            selected[identity.value()] = node
    assert set(selected) == identifiers

    def xy(node):
        return tuple(Decimal(v) for v in node.values()[:2])

    assert selected[BARE_BUS].head == "bus" and selected[BARE_WIRE].head == "wire"
    bus = [xy(p) for p in selected[BARE_BUS].find("pts").find_all("xy")]
    wire = [xy(p) for p in selected[BARE_WIRE].find("pts").find_all("xy")]
    assert bus == [(Decimal("254"), Decimal("182.88")), (Decimal("279.4"), Decimal("182.88"))]
    assert wire == [(Decimal("266.7"), Decimal("182.88")), (Decimal("266.7"), Decimal("191.77"))]
    label = selected[BARE_LABEL]
    assert label.head == "label" and label.value() == "WK[0..1]"
    assert xy(label.find("at")) == (Decimal("256.54"), Decimal("182.88"))
    assert selected[BARE_JUNCTION].head == "junction"
    assert xy(selected[BARE_JUNCTION].find("at")) == wire[0]
    assert bus[0][0] < wire[0][0] < bus[1][0] and wire[0][1] == bus[0][1]


def reconcile_pair(missing, extra, *, rule, severity, pairs):
    """Consume only one matching two-sided representative, never a whole rule."""
    missing, extra = list(missing), list(extra)
    for index, finding in enumerate(missing):
        if (
            finding.get("type") != rule
            or finding.get("severity") != severity
            or finding.get("sheet_path") != "/"
            or len(finding["items"]) != 2
            or frozenset(finding["items"]) not in pairs
        ):
            continue
        for other_index, other in enumerate(extra):
            if (
                other.rule == rule
                and other.severity == severity
                and other.sheet == "/"
                and len(other.items) == 2
                and frozenset(item.uuid for item in other.items) in pairs
            ):
                return missing[:index] + missing[index + 1 :], extra[:other_index] + extra[
                    other_index + 1 :
                ]
    return missing, extra


def reconcile(path, missing, extra):
    """Two frozen junctions; at most one finding each, with exact input geometry."""
    checked_geometry(path)
    checked_bare_geometry(path)
    for pairs in (PAIRS, BARE_PAIRS):
        missing, extra = reconcile_pair(
            missing, extra, rule="bus_to_net_conflict", severity="error", pairs=pairs
        )
    return missing, extra


ONE_AIR_SHA256 = "45b5148dc310a0a9f1bee896f95cea578fe91be5f85cbabd3c23ce0a4821b5cc"
VBUS_LABELS = {
    "527d13ed-9c5e-4c3b-b24b-1a9c5f8ee7c1": ("57.15", "31.75"),
    "5a9389cd-fc93-475e-9a60-1debf50c6662": ("57.15", "54.61"),
}
USB_POWER_PINS = {
    "#PWR06": ("912d9979-ec3b-4631-ad56-34dce107fe93", "62.23", "30.48"),
    "#PWR0108": ("41c221eb-cba6-46f4-9ce2-f288d4d3541b", "113.03", "72.39"),
    "#PWR0110": ("22774d65-e970-4922-af59-937d4f6a1f52", "187.96", "72.39"),
    "#PWR0106": ("3418209c-cb44-4267-9735-3e8e949347eb", "180.34", "26.67"),
}


def checked_one_air_names(path):
    """Input-derived equivalence, not a pair inferred from the native response.

    KiCad10 same-sheet labels share a net; flagged power symbols use Value.
    https://docs.kicad.org/10.0/en/eeschema/eeschema.html
    The fixed root wires independently join #PWR06 to the first VBUS label.
    """
    if hashlib.sha256(path.read_bytes()).hexdigest() != ONE_AIR_SHA256:
        raise ValueError("One-Air-Max frozen input bytes changed")
    document = Document.load(path)
    assert document.root.find("uuid").value() == "dd437b17-cf31-411e-a879-d9d26676cd44"

    def xy(node):
        return tuple(Decimal(v) for v in node.values()[:2])

    labels = {}
    for node in document.root.find_all("label"):
        if node.value() == "VBUS":
            identity = node.find("uuid").value()
            assert identity not in labels
            labels[identity] = xy(node.find("at"))
    assert labels == {key: tuple(map(Decimal, value)) for key, value in VBUS_LABELS.items()}
    definition = next(
        n for n in document.root.find("lib_symbols").find_all("symbol") if n.value() == "power:+5V"
    )
    assert definition.find("power") is not None
    pins = list(definition.walk("pin"))
    assert len(pins) == 1 and pins[0].value() == "power_in"
    assert pins[0].find("number").value() == "1"
    assert xy(pins[0].find("at")) == (Decimal(0), Decimal(0))
    powers = {}
    for symbol in document.root.find_all("symbol"):
        properties = {p.value(): p.value(2) for p in symbol.find_all("property")}
        if properties.get("Value") != "+5VUSB":
            continue
        reference = properties["Reference"]
        assert reference not in powers and symbol.find("lib_id").value() == "power:+5V"
        assert symbol.find("at").value(3) == "0"
        actual_pins = symbol.find_all("pin")
        assert len(actual_pins) == 1 and actual_pins[0].value() == "1"
        powers[reference] = (actual_pins[0].find("uuid").value(), *xy(symbol.find("at")))
    assert powers == {
        ref: (pin, Decimal(x), Decimal(y)) for ref, (pin, x, y) in USB_POWER_PINS.items()
    }
    wires = {n.find("uuid").value(): n for n in document.root.find_all("wire")}
    expected = {
        "6cfaab1b-6008-4487-ab90-77ff979d3793": (("50.8", "31.75"), ("62.23", "31.75")),
        "fdf5a99b-fef5-42f6-870e-14d245ea53ca": (("62.23", "30.48"), ("62.23", "31.75")),
    }
    for identity, points in expected.items():
        assert [xy(p) for p in wires[identity].find("pts").find_all("xy")] == [
            tuple(map(Decimal, point)) for point in points
        ]
    return {frozenset((power[0], label)) for power in powers.values() for label in labels}


def reconcile_one_air(path, missing, extra):
    """One frozen root-level +5VUSB/VBUS conflict; keep other findings intact."""
    pairs = checked_one_air_names(path)
    return reconcile_pair(
        missing, extra, rule="multiple_net_names", severity="warning", pairs=pairs
    )
