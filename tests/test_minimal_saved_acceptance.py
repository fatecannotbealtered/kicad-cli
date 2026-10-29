import copy

import pytest

from bench.persistence_acceptance.minimal_saved import verify_cleaning


def witnesses():
    journal = {"hmac_valid": True, "value": {"operation_id": "op-a", "files": [], "hmac": "local"}}
    checkpoint = {
        "hmac_valid": True,
        "value": {
            "operation_id": "op-a",
            "phase": "cleaning",
            "mode": "commit",
            "journal": {"operation_id": "op-a", "files": []},
            "observed": {"owned": "intended"},
        },
    }
    return journal, checkpoint


def test_journal_without_phase_and_signed_checkpoint_are_separate_records():
    journal, checkpoint = witnesses()
    verify_cleaning(journal, checkpoint, "op-a", {"owned": "intended"})


@pytest.mark.parametrize("mutation", ["signature", "phase", "operation", "journal", "bytes"])
def test_wrong_checkpoint_cannot_certify_saved_cleaning(mutation):
    journal, original = witnesses()
    checkpoint = copy.deepcopy(original)
    if mutation == "signature":
        checkpoint["hmac_valid"] = False
    elif mutation == "phase":
        checkpoint["value"]["phase"] = "cleaned"
    elif mutation == "operation":
        checkpoint["value"]["operation_id"] = "op-b"
    elif mutation == "journal":
        checkpoint["value"]["journal"]["files"] = ["foreign"]
    else:
        checkpoint["value"]["observed"]["owned"] = "drift"
    with pytest.raises(AssertionError):
        verify_cleaning(journal, checkpoint, "op-a", {"owned": "intended"})
