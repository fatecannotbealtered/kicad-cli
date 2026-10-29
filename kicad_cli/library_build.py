"""Project-local, source-bound single-unit symbols and rectangular SMD land patterns.

No user/global library writes. Existing unrelated symbols and table entries survive.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from .fileformat.sexpr import Document, copy, quote, render
from .module_project import sha

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
PIN_TYPES = {
    "input",
    "output",
    "bidirectional",
    "tri_state",
    "passive",
    "power_in",
    "power_out",
    "open_collector",
    "open_emitter",
    "no_connect",
}


def validate_spec(spec):
    from jsonschema import Draft202012Validator

    from .module_contract import LIBRARY_SPEC

    errors = list(Draft202012Validator(LIBRARY_SPEC).iter_errors(spec))
    if errors:
        raise ValueError(
            "Invalid library specification: " + errors[0].json_path + ": " + errors[0].message
        )
    for name in (spec["library"], spec["symbol"]["name"], spec["footprint"]["name"]):
        if not NAME.fullmatch(name):
            raise ValueError("Library and asset names must be bounded identifiers")
    if any(v <= 0 for item in (spec["symbol"], spec["footprint"]) for v in item["body_mm"]):
        raise ValueError("Body dimensions must be positive mm")
    pins, pads = spec["symbol"]["pins"], spec["footprint"]["pads"]
    pnums, dnums = [p["number"] for p in pins], [p["number"] for p in pads]
    if len(set(pnums)) != len(pnums) or len(set(dnums)) != len(dnums):
        raise ValueError("This bounded authoring path requires unique pin/pad numbers")
    if set(pnums) != set(spec["mapping"]) or set(dnums) != set(spec["mapping"].values()):
        raise ValueError("Every pin and pad must have an explicit bijective mapping")
    if any(k != v for k, v in spec["mapping"].items()):
        raise ValueError("KiCad pin/pad numbers must agree; renumber the definition explicitly")
    if any(p["type"] not in PIN_TYPES for p in pins):
        raise ValueError("Unsupported electrical pin type")
    # Native PCB positions/sizes are integer nm. Do not accept a sub-nm
    # declaration and then let independent centre/size rounding shrink its
    # requested free Courtyard margin.
    f = spec["footprint"]
    measures = [("body_mm", f["body_mm"]), ("courtyard_mm", [f["courtyard_mm"]])]
    for index, pad in enumerate(pads):
        measures += [
            (f"pads[{index}].at_mm", pad["at_mm"]),
            (f"pads[{index}].size_mm", pad["size_mm"]),
        ]
    for field, values in measures:
        if any((nm := Decimal(str(v)) * 1_000_000) != nm.to_integral_value() for v in values):
            raise ValueError(
                f"footprint.{field} requires integer-nm precision (0.000001mm); "
                "sub-nm geometry is unsupported"
            )


def output_paths(directory, spec):
    directory = Path(directory)
    name = spec["library"]
    return [
        directory / (name + ".kicad_sym"),
        directory / (name + ".pretty") / (spec["footprint"]["name"] + ".kicad_mod"),
        directory / "sym-lib-table",
        directory / "fp-lib-table",
        directory / (name + ".provenance.json"),
    ]


def expr(text):
    return copy(Document.parse(text).root)


def effects(size=1.27):
    return f"(effects (font (size {size} {size})))"


def symbol(spec):
    s = spec["symbol"]
    ident = spec["library"] + ":" + spec["footprint"]["name"]
    x, y = s["body_mm"]
    fields = "".join(
        f"(property {quote(k)} {quote(v)} (at 0 {pos} 0) {effects()})"
        for k, v, pos in [
            ("Reference", s["reference"], -y / 2 - 2.54),
            ("Value", s["name"], y / 2 + 2.54),
            ("Footprint", ident, y / 2 + 5.08),
            ("Datasheet", spec["sources"][0]["url"], y / 2 + 7.62),
        ]
    )
    pins = "".join(
        f"(pin {p['type']} line (at {p['at_mm'][0]} {p['at_mm'][1]} {p['angle_deg']})"
        f"(length {p['length_mm']}) (name {quote(p['name'])} {effects(1.016)})"
        f"(number {quote(p['number'])} {effects(1.016)}))"
        for p in s["pins"]
    )
    return expr(
        f"(symbol {quote(s['name'])} (pin_names (offset 0.508))"
        f"(in_bom yes) (on_board yes) {fields}"
        f"(symbol {quote(s['name'] + '_0_1')} (rectangle (start {-x / 2} {y / 2})"
        f"(end {x / 2} {-y / 2}) (stroke (width 0.254) (type default))"
        "(fill (type background))))"
        f"(symbol {quote(s['name'] + '_1_1')} {pins}))"
    )


def footprint(spec):
    s = spec["footprint"]
    bx, by = s["body_mm"]
    pads = "".join(
        f"(pad {quote(p['number'])} smd rect "
        f"(at {p['at_mm'][0]} {p['at_mm'][1]})"
        f"(size {p['size_mm'][0]} {p['size_mm'][1]})"
        '(layers "F.Cu" "F.Paste" "F.Mask"))'
        for p in s["pads"]
    )

    # body_mm is the nominal centred mechanical rectangle, not the Fab pen.
    # courtyard_mm is free space to the INNER edge of a 0.05mm court pen.
    # Round the court outward to KiCad's integer-nm storage, never inward.
    def d(value):
        return Decimal(str(value))

    x0 = min(-d(bx) / 2, *(d(p["at_mm"][0]) - d(p["size_mm"][0]) / 2 for p in s["pads"]))
    y0 = min(-d(by) / 2, *(d(p["at_mm"][1]) - d(p["size_mm"][1]) / 2 for p in s["pads"]))
    x1 = max(d(bx) / 2, *(d(p["at_mm"][0]) + d(p["size_mm"][0]) / 2 for p in s["pads"]))
    y1 = max(d(by) / 2, *(d(p["at_mm"][1]) + d(p["size_mm"][1]) / 2 for p in s["pads"]))
    excess = d(s["courtyard_mm"]) + Decimal("0.025")

    def edge(value, rounding):
        nm = (value * 1_000_000).to_integral_value(rounding=rounding)
        return format(nm / 1_000_000, ".6f")

    left, top = edge(x0 - excess, ROUND_FLOOR), edge(y0 - excess, ROUND_FLOOR)
    right, bottom = edge(x1 + excess, ROUND_CEILING), edge(y1 + excess, ROUND_CEILING)
    cy = max(abs(float(top)), abs(float(bottom)))
    return expr(
        f'(footprint {quote(s["name"])} (version 20241229) (generator "kicad-cli")'
        '(layer "F.Cu") (attr smd)'
        f'(property "Reference" "REF**" (at 0 {-cy - 1.2} 0) (layer "F.SilkS") {effects(1)})'
        f'(property "Value" {quote(s["name"])} (at 0 {cy + 1.2} 0) (layer "F.Fab") {effects(1)})'
        f"(fp_rect (start {-bx / 2} {-by / 2}) (end {bx / 2} {by / 2})"
        '(stroke (width 0.1) (type default)) (fill none) (layer "F.Fab"))'
        f"(fp_rect (start {left} {top}) (end {right} {bottom})"
        '(stroke (width 0.05) (type default)) (fill none) (layer "F.CrtYd"))'
        f"{pads})"
    )


def table(raw, kind, name, uri):
    doc = Document.parse(raw.decode()) if raw else Document.parse(f"({kind} (version 7))")
    selected = [n for n in doc.root.find_all("lib") if n.find("name").value(1) == name]
    if len(selected) > 1:
        raise ValueError("Ambiguous duplicate project library nickname")
    if selected:
        if selected[0].find("uri").value(1) != uri or selected[0].find("type").value(1) != "KiCad":
            raise ValueError("Existing library nickname has a different binding")
    else:
        doc.root.append(
            expr(
                f'(lib (name {quote(name)}) (type "KiCad") (uri {quote(uri)})'
                '(options "") (descr "Project-local source-bound library"))'
            )
        )
    return doc.dumps().encode()


def build(spec, directory, original_bytes):
    validate_spec(spec)
    paths = output_paths(directory, spec)
    baseline = {
        p.name if p.parent == Path(directory) else p.relative_to(directory).as_posix(): sha(
            original_bytes[p]
        )
        if original_bytes[p] is not None
        else "absent"
        for p in paths
    }
    if baseline != spec["baseline"]:
        from .sync_inputs import Drift

        raise Drift("Library output baseline differs; query current bytes before re-preparing")
    library_raw = original_bytes[paths[0]]
    doc = (
        Document.parse(library_raw.decode())
        if library_raw
        else Document.parse('(kicad_symbol_lib (version 20250114) (generator "kicad-cli"))')
    )
    if doc.root.head != "kicad_symbol_lib":
        raise ValueError("Expected a KiCad symbol library")
    matches = [n for n in doc.root.find_all("symbol") if n.value(1) == spec["symbol"]["name"]]
    if len(matches) > 1:
        raise ValueError("Ambiguous symbol identity")
    new = symbol(spec)
    if matches:
        doc.root.replace(matches[0], new)
    else:
        doc.root.append(new)
    metadata = json.loads(original_bytes[paths[4]]) if original_bytes[paths[4]] else {"assets": {}}
    asset = spec["symbol"]["name"]
    metadata["assets"][asset] = {
        k: spec[k] for k in ("revision", "symbol", "footprint", "mapping", "sources")
    }
    values = [
        doc.dumps().encode(),
        (render(footprint(spec)) + "\n").encode(),
        table(
            original_bytes[paths[2]],
            "sym_lib_table",
            spec["library"],
            "${KIPRJMOD}/" + spec["library"] + ".kicad_sym",
        ),
        table(
            original_bytes[paths[3]],
            "fp_lib_table",
            spec["library"],
            "${KIPRJMOD}/" + spec["library"] + ".pretty",
        ),
        (json.dumps(metadata, indent=2) + "\n").encode(),
    ]
    return dict(zip(paths, values, strict=True)), {
        "library": spec["library"],
        "asset": asset,
        "revision": spec["revision"],
        "mapping": spec["mapping"],
        "sources": spec["sources"],
        "native_consumption": "Requires SCH placement, native netlist and PCB reopen acceptance",
        "global_libraries_modified": False,
    }
