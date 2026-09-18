"""The write gate must cost a decision, not an extra round trip.

No KiCad is needed: the store is standard library and the shell gate is
exercised through `envelope`, which is where both the shell and the payload
adapters meet it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from kicad_cli import envelope  # noqa: E402
from kicad_cli.payload import confirm_store  # noqa: E402

PREVIEW = {"tracks": 59, "will": "加宽 3 段"}
OPERATION = "pcb_widen:demo.kicad_pcb"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(confirm_store.STATE_ENV, str(tmp_path / "state"))
    return tmp_path


@pytest.fixture
def board(tmp_path):
    path = tmp_path / "demo.kicad_pcb"
    path.write_text("(kicad_pcb original)", encoding="utf-8")
    return path


def test_two_dry_runs_do_not_hand_out_the_same_token(store):
    """A derived token is a pure function of public inputs, so it is not evidence.

    The old gate returned `sha256(operation + preview)`. Anyone could compute it
    without running the dry run at all, which is precisely what the gate is
    supposed to make impossible.
    """
    first = confirm_store.issue(OPERATION, PREVIEW)
    second = confirm_store.issue(OPERATION, PREVIEW)
    assert first != second
    assert confirm_store.TOKEN_RE.match(first)


def test_a_token_is_redeemed_exactly_once(store):
    token = confirm_store.issue(OPERATION, PREVIEW)
    confirm_store.redeem(token, OPERATION, PREVIEW)
    with pytest.raises(confirm_store.ConfirmError) as exc:
        confirm_store.redeem(token, OPERATION, PREVIEW)
    assert exc.value.code == "E_CONFLICT"


def test_an_expired_token_is_refused(store, monkeypatch):
    token = confirm_store.issue(OPERATION, PREVIEW)
    later = time.time() + confirm_store.TTL_SECONDS + 60
    monkeypatch.setattr(time, "time", lambda: later)
    with pytest.raises(confirm_store.ConfirmError) as exc:
        confirm_store.redeem(token, OPERATION, PREVIEW)
    assert exc.value.code == "E_CONFLICT"
    assert "expired" in str(exc.value)


def test_a_token_just_inside_the_window_still_works(store, monkeypatch):
    """The expiry must be a deadline, not a coin toss about which side is refused."""
    token = confirm_store.issue(OPERATION, PREVIEW)
    later = time.time() + confirm_store.TTL_SECONDS - 5
    monkeypatch.setattr(time, "time", lambda: later)
    confirm_store.redeem(token, OPERATION, PREVIEW)


def test_editing_the_target_between_preview_and_confirm_is_refused(store, board):
    token = confirm_store.issue(OPERATION, PREVIEW, str(board))
    board.write_text("(kicad_pcb edited under us)", encoding="utf-8")
    with pytest.raises(confirm_store.ConfirmError) as exc:
        confirm_store.redeem(token, OPERATION, PREVIEW, str(board))
    assert "changed" in str(exc.value)


def test_a_different_board_with_an_identical_preview_is_refused(store, board, tmp_path):
    """The preview is a summary; two boards can summarise identically."""
    other = tmp_path / "other.kicad_pcb"
    other.write_text(board.read_text(encoding="utf-8"), encoding="utf-8")
    token = confirm_store.issue(OPERATION, PREVIEW, str(board))
    with pytest.raises(confirm_store.ConfirmError):
        confirm_store.redeem(token, OPERATION, PREVIEW, str(other))


@pytest.mark.parametrize(
    "token",
    [
        "ct_94d5eecc93ced2cb",  # the shape the previous build derived
        "ct_" + "z" * 32,
        "../../etc/passwd",
        "ct_../../../secret",
        "",
        None,
    ],
)
def test_tokens_this_tool_did_not_issue_are_refused(store, token):
    """The token names a file, so its shape is checked before it is used as one."""
    with pytest.raises(confirm_store.ConfirmError) as exc:
        confirm_store.redeem(token, OPERATION, PREVIEW)
    assert exc.value.code == "E_CONFLICT"


def test_a_half_written_record_is_not_redeemable(store):
    token = confirm_store.issue(OPERATION, PREVIEW)
    (confirm_store.state_dir() / token).write_text("{not json", encoding="utf-8")
    with pytest.raises(confirm_store.ConfirmError):
        confirm_store.redeem(token, OPERATION, PREVIEW)


def test_only_one_of_two_concurrent_confirms_wins(store):
    """Claim-by-rename: the source can disappear only once."""
    token = confirm_store.issue(OPERATION, PREVIEW)
    outcomes = []
    for _ in range(2):
        try:
            confirm_store.redeem(token, OPERATION, PREVIEW)
            outcomes.append("applied")
        except confirm_store.ConfirmError:
            outcomes.append("refused")
    assert outcomes == ["applied", "refused"]


def test_an_unwritable_store_refuses_rather_than_deriving_a_token(monkeypatch, tmp_path):
    """Degrading to a derived token would silently restore the replayable gate."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv(confirm_store.STATE_ENV, str(blocker / "state"))
    with pytest.raises(confirm_store.ConfirmError) as exc:
        confirm_store.issue(OPERATION, PREVIEW)
    assert exc.value.code == "E_CONFIG"
    assert confirm_store.STATE_ENV in json.dumps(exc.value.details)


def test_expired_records_are_swept_and_do_not_accumulate(store, monkeypatch):
    stale = confirm_store.issue(OPERATION, PREVIEW)
    path = confirm_store.state_dir() / stale
    old = time.time() - confirm_store.TTL_SECONDS - 60
    os.utime(path, (old, old))
    confirm_store.issue(OPERATION, PREVIEW)
    assert not path.exists()


def test_the_shell_gate_reports_the_lifecycle_it_now_has(store, capsys):
    with pytest.raises(SystemExit):
        envelope.check_confirm(None, OPERATION, PREVIEW)
    details = json.loads(capsys.readouterr().out)["error"]["details"]
    assert details["single_use"] is True
    assert details["expires_in_s"] == confirm_store.TTL_SECONDS
    assert details["preview"] == PREVIEW  # Never confirm what you cannot see.
    confirm_store.redeem(details["confirm_token"], OPERATION, PREVIEW)


def test_the_shell_gate_refuses_a_replay_without_dispatching(store, capsys):
    with pytest.raises(SystemExit):
        envelope.check_confirm(None, OPERATION, PREVIEW)
    token = json.loads(capsys.readouterr().out)["error"]["details"]["confirm_token"]
    envelope.check_confirm(token, OPERATION, PREVIEW)  # Returns: the write may proceed.
    with pytest.raises(SystemExit) as exc:
        envelope.check_confirm(token, OPERATION, PREVIEW)
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "E_CONFLICT"
    assert exc.value.code != 0


def test_the_payload_adapter_shares_the_store(store, tmp_path):
    """The payload runs in KiCad's interpreter and imports the module as a sibling."""
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys, json\n"
        f"sys.path.insert(0, {str(REPO / 'kicad_cli' / 'payload')!r})\n"
        "import kicad_lib as K\n"
        "K.check_confirm(sys.argv[1] or None, {'a': 1}, 'op')\n"
        "print(json.dumps({'applied': True}))\n",
        encoding="utf-8",
    )

    def run(token: str) -> dict:
        proc = subprocess.run(
            [sys.executable, str(script), token],
            capture_output=True,
            timeout=60,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"),
        )
        return json.loads(proc.stdout.decode("utf-8").splitlines()[0])

    issued = run("")["error"]["details"]["confirm_token"]
    assert run(issued) == {"applied": True}
    assert run(issued)["error"]["code"] == "E_CONFLICT"
