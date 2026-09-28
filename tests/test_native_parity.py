"""``board parity`` moved off pcbnew: the old implementation is the reference.

Both sides read the schematic through the same export -- KiCad's own binary,
XML -- so what is compared is what each makes of the board and of that
netlist: every field, in order, except one list the payload could not
produce the same way twice.

That list is the sample of schematic nets missing from the board. The
payload took the first ten of a Python set, whose order changes with every
process; the port sorts. Where there are ten or fewer, the two samples must
hold the same nets. Where there are more, the payload's ten were an accident
of hashing and only the count can be held against it.
"""

from __future__ import annotations

import json
import re

import pytest
from payload_reference import DEMO_BOARDS, assert_same, kicad_env, native, needs_payload

SAMPLED = "net_membership_mismatch"


def payload_parity(board) -> dict:
    """The payload launches KiCad's binary once and does not retry, so the
    Windows launch failure that roughly one start in six hits becomes its
    answer. That failure is the machine's, not the board's: ask again."""
    for _ in range(3):
        old = kicad_env.run_payload("parity", ["--board", str(board)], timeout=1800)
        if old.get("ok") or old["error"]["code"] != "E_IO":
            return old
    return old


def split_samples(doc: dict) -> list[tuple[int, list]]:
    """Take the sampled lists out of an envelope, with the count each samples."""
    taken = []
    for diff in (doc.get("data") or {}).get("diffs", []):
        if diff["kind"] == SAMPLED:
            count = int(re.search(r"\d+", diff["detail"]).group())
            taken.append((count, diff["evidence"].pop("samples")))
    return taken


@needs_payload
@pytest.mark.parametrize("board", DEMO_BOARDS, ids=lambda p: p.name)
def test_the_native_parity_check_says_exactly_what_the_payload_said(board):
    old = payload_parity(board)
    new = native("board", "parity", "--board", str(board))
    old_samples, new_samples = split_samples(old), split_samples(new)
    assert_same(old, new)
    assert [count for count, _ in old_samples] == [count for count, _ in new_samples]
    for (count, theirs), (_, ours) in zip(old_samples, new_samples, strict=True):
        assert len(ours) == min(count, 10)
        assert ours == sorted(ours, key=lambda s: s["pins"]), "the sample is sorted"
        if count <= 10:
            key = json.dumps
            assert sorted(theirs, key=key) == sorted(ours, key=key)
