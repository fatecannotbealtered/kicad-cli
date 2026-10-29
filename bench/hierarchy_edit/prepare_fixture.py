"""Copy the existing synthetic hierarchy and materialize its embedded local library.

Input preparation only. This does not claim that the public CLI creates sheets,
reused instances, symbols or footprints. Never run against a user project.
"""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


def raw_parse(text):
    tokens = re.findall(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+', text)
    stack, root = [], None
    for token in tokens:
        if token == "(":
            node = []
            if stack:
                stack[-1].append(node)
            else:
                if root is not None:
                    raise ValueError("Multiple roots")
                root = node
            stack.append(node)
        elif token == ")":
            stack.pop()
        else:
            stack[-1].append(token)
    if stack:
        raise ValueError("Unclosed fixture expression")
    return root


def render(node):
    return "(" + " ".join(render(x) if isinstance(x, list) else x for x in node) + ")"


def prepare(source, destination):
    if destination.exists():
        raise ValueError("Use a new owned destination; original fixtures are never modified")
    shutil.copytree(
        source, destination, ignore=shutil.ignore_patterns("*.py", "__pycache__", ".pytest_cache")
    )
    symbols = {}
    for path in sorted(destination.glob("*.kicad_sch")):
        root = raw_parse(path.read_text(encoding="utf-8"))
        library = next(x for x in root if isinstance(x, list) and x[0] == "lib_symbols")
        for symbol in library[1:]:
            name = json.loads(symbol[1])
            if not name.startswith("fixture:"):
                raise ValueError("Only the existing fixture's authored symbols are accepted")
            symbol[1] = json.dumps(name.split(":", 1)[1])
            text = render(symbol)
            if name in symbols and symbols[name] != text:
                raise ValueError("Different embedded definitions for one library symbol")
            symbols[name] = text
    (destination / "fixture.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20241209) (generator "synthetic_fixture")\n'
        + "\n".join(symbols[k] for k in sorted(symbols))
        + "\n)\n",
        encoding="utf-8",
    )
    (destination / "sym-lib-table").write_text(
        '(sym_lib_table (version 7) (lib (name "fixture") (type "KiCad") '
        '(uri "${KIPRJMOD}/fixture.kicad_sym") (options "") '
        '(descr "Authored synthetic fixture")))\n',
        encoding="utf-8",
    )
    return {
        "preparation": "Existing authored synthetic fixture copied; embedded symbols copied "
        "verbatim apart from the library prefix into a project-local symbol library/table",
        "public_cli_creation_claimed": False,
        "files": {
            p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(destination.rglob("*"))
            if p.is_file()
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    record = prepare(args.input.resolve(), args.output.resolve())
    with args.record.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, indent=2)
    print(json.dumps({"prepared": True, "files": len(record["files"]), "cli_creation": False}))
