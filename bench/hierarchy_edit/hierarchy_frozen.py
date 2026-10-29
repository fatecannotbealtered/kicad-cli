"""Read the predeclared baseline digest before interpreting its exact bytes."""

import hashlib
import json
import re
from pathlib import Path


class BaselineBindingError(ValueError):
    pass


def read_verified_baseline(frozen_path, baseline_path):
    frozen_path, baseline_path = Path(frozen_path), Path(baseline_path)
    if frozen_path.is_symlink() or baseline_path.is_symlink():
        raise BaselineBindingError("Linked frozen baseline inputs are unsupported")
    frozen_bytes = frozen_path.read_bytes()
    frozen = json.loads(frozen_bytes)
    expected = frozen.get("input_preparation", {}).get("baseline_observation_sha256")
    if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        raise BaselineBindingError("Frozen baseline observation digest is missing or invalid")
    baseline_bytes = baseline_path.read_bytes()
    observed = hashlib.sha256(baseline_bytes).hexdigest()
    if observed != expected:
        raise BaselineBindingError(
            "Baseline observation differs from the original frozen digest; "
            f"expected {expected}, observed {observed}. "
            "Restore the original observation; do not regenerate it from edited inputs "
            "or refresh the frozen requirement."
        )
    # Never re-open the path after hashing: only the verified bytes are parsed.
    baseline = json.loads(baseline_bytes)
    return (
        frozen,
        baseline,
        {
            "frozen_sha256": hashlib.sha256(frozen_bytes).hexdigest(),
            "baseline_observation_sha256": expected,
        },
    )
