"""Reject selected locked footprints before any move is dispatched."""

from __future__ import annotations

from ._driver_sdk.models import DriverError


def _saved_identity(footprint):
    node = footprint.node
    if node is not None:
        for kind in ("uuid", "tstamp"):
            identity = node.find(kind)
            if identity is not None and identity.value(1):
                return identity.value(1), kind
    return None, None


def _target(footprint, observed):
    identity, kind = _saved_identity(footprint)
    if observed is not None:
        matches = [item for item in observed.objects if item["ref"] == footprint.reference]
        if len(matches) != 1 or (
            identity is not None
            and (matches[0]["id"], matches[0]["identity_kind"]) != (identity, kind)
        ):
            raise DriverError(
                "E_CONFLICT",
                "The locked footprint's saved and observed identities differ.",
                details={
                    "conflict": "state_drift",
                    "reason": "locked_footprint_identity_mismatch",
                    "ref": footprint.reference,
                },
                dispatch_state="not_started",
            )
        identity, kind = matches[0]["id"], matches[0]["identity_kind"]
    return {
        "ref": footprint.reference,
        "id": identity,
        "identity_kind": kind,
        "locked": True,
    }


def require_unlocked(board, requested, observed=None) -> None:
    """Check the complete selection using the board model's existing lock parser.

    ``requested`` supplies exact reference names, including a move mapping's keys.
    When available, the independent observation supplies each reported identity;
    an explicit saved UUID/tstamp must agree with it. Without an observation, a
    missing saved identity stays null rather than becoming a fabricated UUID.
    This function changes neither the document nor any footprint lock.
    """
    selected = set(requested)
    footprints = [fp for fp in board.footprints if fp.reference in selected]
    ambiguous = [fp for fp in footprints if len(fp.node.find_all("locked")) > 1]
    if ambiguous:
        raise DriverError(
            "E_VALIDATION",
            "Selected footprint lock fields are ambiguous; no footprint will be moved.",
            details={
                "problem": "invalid_value",
                "parameter": "--board",
                "reason": "ambiguous_lock_encoding",
                "_untrusted": ["targets"],
                "targets": [
                    {**_target(fp, observed), "locked": None}
                    for fp in sorted(ambiguous, key=lambda fp: fp.reference)
                ],
            },
            dispatch_state="not_started",
        )
    # Keep the model's modern parser. A direct legacy bare marker cannot become
    # an unlock merely because a saved file reordered its header fields.
    locked = sorted(
        (
            fp
            for fp in footprints
            if fp.locked
            or any(isinstance(item, str) and item == "locked" for item in fp.node.items[2:])
        ),
        key=lambda fp: fp.reference,
    )
    if not locked:
        return
    targets = [_target(footprint, observed) for footprint in locked]
    raise DriverError(
        "E_VALIDATION",
        "Selected footprints are locked; no footprint will be moved.",
        details={
            "problem": "invalid_value",
            "parameter": "--moves",
            "reason": "locked_footprint",
            "locked_targets": targets,
            "_untrusted": ["locked_targets"],
            "next_action": "Keep the locks; choose an unlocked target and request a new preview.",
        },
        dispatch_state="not_started",
    )
