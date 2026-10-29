"""Independent, bounded consumer of saved footprint poses.

This does not import the writer's board model, lexer or coordinate conversion.
It reads disk bytes and uses Decimal for measurements. It is a file consumer,
not KiCad, a DRC checker, or a guarantee that the whole board is manufacturable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

VERSION = "1"
NM_PER_MM = Decimal(1_000_000)
TOLERANCE_NM = Decimal(1)
_TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|[()]|[^\s()"]+')


class ReadbackError(ValueError):
    """The consumer cannot establish a complete pose observation."""


@dataclass
class _Atom:
    value: str


@dataclass
class _List:
    items: list
    start: int
    end: int = -1

    @property
    def head(self) -> str:
        return self.items[0].value if self.items and isinstance(self.items[0], _Atom) else ""

    def children(self, head: str) -> list[_List]:
        return [item for item in self.items if isinstance(item, _List) and item.head == head]

    def one(self, head: str, *, optional: bool = False) -> _List | None:
        nodes = self.children(head)
        if len(nodes) == 1:
            return nodes[0]
        if not nodes and optional:
            return None
        raise ReadbackError(f"expected one direct {head} in {self.head}, found {len(nodes)}")

    def atom(self, index: int) -> str:
        if index >= len(self.items) or not isinstance(self.items[index], _Atom):
            raise ReadbackError(f"missing atom {index} in {self.head}")
        return self.items[index].value


def _decode(token: str) -> str:
    if not token.startswith('"'):
        return token
    escapes = {"n": "\n", "r": "\r", "t": "\t"}
    return re.sub(r"\\(.)", lambda match: escapes.get(match[1], match[1]), token[1:-1])


def _parse(text: str, *, expected_head="kicad_pcb") -> _List:
    stack: list[_List] = []
    roots = []
    end = 0
    for match in _TOKENS.finditer(text):
        gap = text[end : match.start()]
        if gap.strip() and not (end == 0 and gap == "\ufeff"):
            raise ReadbackError("unrecognised text or unterminated quoted string")
        end = match.end()
        token = match[0]
        if token == "(":
            node = _List([], match.start())
            if stack:
                stack[-1].items.append(node)
            else:
                roots.append(node)
            stack.append(node)
        elif token == ")":
            if not stack:
                raise ReadbackError("unexpected closing parenthesis")
            stack.pop().end = match.end()
        elif stack:
            stack[-1].items.append(_Atom(_decode(token)))
        else:
            raise ReadbackError("atom outside the board")
    if stack or text[end:].strip() or len(roots) != 1 or roots[0].head != expected_head:
        raise ReadbackError("incomplete or non-board expression")
    return roots[0]


def _decimal(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ReadbackError("invalid coordinate") from exc
    if not number.is_finite():
        raise ReadbackError("non-finite coordinate")
    return number


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + "(pose-checked-separately)" + text[end:]
    return text


@dataclass
class Observation:
    snapshot: dict
    objects: list[dict]
    protected_sha256: str
    source: dict


def observe(path: str | Path, references: list[str]) -> Observation:
    """Read a fresh disk snapshot, selecting unique references and stable identities.

    The protected hash covers every byte except selected footprint-level `at`
    expressions. Their position and orientation are checked separately. Missing
    UUIDs use a content identity excluding that expression, not a made-up UUID.
    """
    target = Path(path).resolve()
    raw = target.read_bytes()
    text = raw.decode("utf-8")
    root = _parse(text)
    version = root.one("version").atom(1)
    if not version.isdigit():
        raise ReadbackError("invalid board format version")
    objects, spans, identities = [], [], set()
    footprints = [
        item
        for item in root.items
        if isinstance(item, _List) and item.head in ("footprint", "module")
    ]
    for reference in sorted(references):
        matches = []
        for footprint in footprints:
            properties = [p for p in footprint.children("property") if p.atom(1) == "Reference"]
            legacy = [p for p in footprint.children("fp_text") if p.atom(1) == "reference"]
            names = [p.atom(2) for p in properties or legacy]
            if reference in names:
                if len(names) != 1:
                    raise ReadbackError(f"ambiguous reference property: {reference}")
                matches.append(footprint)
        if len(matches) != 1:
            raise ReadbackError(
                f"reference {reference}: expected one footprint, found {len(matches)}"
            )
        footprint = matches[0]
        at = footprint.one("at")
        x, y = _decimal(at.atom(1)), _decimal(at.atom(2))
        nm = [x * NM_PER_MM, y * NM_PER_MM]
        if any(value != value.to_integral_value() for value in nm):
            raise ReadbackError("file coordinates finer than the declared nanometre grid")
        angle = _decimal(at.atom(3)) if len(at.items) > 3 else Decimal(0)
        layer = footprint.one("layer").atom(1)
        uuid = footprint.one("uuid", optional=True)
        legacy_id = footprint.one("tstamp", optional=True)
        if uuid is not None or legacy_id is not None:
            identity = (uuid or legacy_id).atom(1)
            identity_kind = "uuid" if uuid else "tstamp"
            if not identity:
                raise ReadbackError("empty footprint identity")
            if (
                sum(
                    1
                    for item in footprints
                    if (
                        item.one(identity_kind, optional=True) is not None
                        and item.one(identity_kind).atom(1) == identity
                    )
                )
                != 1
            ):
                raise ReadbackError("footprint identity is not unique in this board")
        else:
            protected = _mask(
                text[footprint.start : footprint.end],
                [(at.start - footprint.start, at.end - footprint.start)],
            )
            identity, identity_kind = "content:" + _hash(protected), "content_sha256"
        key = (identity_kind, identity)
        if key in identities:
            raise ReadbackError("duplicate selected object identity")
        identities.add(key)
        objects.append(
            {
                "ref": reference,
                "id": identity,
                "identity_kind": identity_kind,
                "position_nm": [int(v) for v in nm],
                "position_mm": [float(x), float(y)],
                "rotation_deg": float(angle),
                "layer": layer,
            }
        )
        spans.append((at.start, at.end))
    digest = hashlib.sha256(raw).hexdigest()
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source_file = Path(__file__)
    source = {
        "kind": "independent_consumer",
        "name": "kicad_cli.move_readback",
        "version": VERSION,
        "completed": True,
        "input_sha256": digest,
        "format_version": version,
        "observed_at": observed_at,
        "code_sha256": hashlib.sha256(source_file.read_bytes()).hexdigest()
        if source_file.is_file()
        else None,
        "limits": "Independent lexical/Decimal pose consumer; "
        "not KiCad, DRC, connectivity, or visual validation.",
    }
    snapshot = {
        "id": "disk:" + digest,
        "source": "disk",
        "revision": digest,
        "observed_at": observed_at,
        "dependencies": [],
        "extensions": {"path": str(target), "board_format_version": version},
    }
    return Observation(snapshot, objects, _hash(_mask(text, spans)), source)


def compare(
    before: Observation, after: Observation, requested: dict[str, tuple[float, float]]
) -> tuple[list[dict], list[dict]]:
    """Compare intended physical coordinates independently of writer conversion."""
    old = {item["ref"]: item for item in before.objects}
    actual = {item["ref"]: item for item in after.objects}
    expected, failures = [], []
    for ref, xy in sorted(requested.items()):
        prior, found = old[ref], actual.get(ref)
        target_nm = [Decimal(str(value)) * NM_PER_MM for value in xy]
        wanted = {
            **prior,
            "position_mm": list(xy),
            "position_nm": [float(v) for v in target_nm],
            "tolerance_nm": float(TOLERANCE_NM),
        }
        expected.append(wanted)
        if found is None:
            failures.append({"ref": ref, "criterion": "object_present"})
            continue
        if any(
            found[key] != prior[key] for key in ("id", "identity_kind", "rotation_deg", "layer")
        ):
            failures.append({"ref": ref, "criterion": "identity_rotation_layer"})
        delta = [
            Decimal(value) - target
            for value, target in zip(found["position_nm"], target_nm, strict=True)
        ]
        if any(abs(value) > TOLERANCE_NM for value in delta):
            failures.append(
                {"ref": ref, "criterion": "position", "delta_nm": [float(v) for v in delta]}
            )
    if before.protected_sha256 != after.protected_sha256:
        failures.append(
            {
                "criterion": "non_pose_content_unchanged",
                "before_sha256": before.protected_sha256,
                "after_sha256": after.protected_sha256,
            }
        )
    return expected, failures
