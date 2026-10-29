"""Explicit grants for isolated test fixtures; never global configuration."""

import json
from datetime import datetime, timezone


def grant(directory, *, effects=None, **updates):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "move-scope.json"
    record = {
        "scope_id": "isolated-move-test",
        "tool": "kicad-cli",
        "granted_by": "test fixture",
        "granted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "expires_at": None,
        "effects": effects or ["observe", "change", "persist"],
        "dangerous": False,
        "roots": [str(directory.resolve())],
        "outputs": [],
        "notes": "Explicit test-only grant for the disposable fixture; "
        "no real project authorization.",
    }
    record.update(updates)
    path.write_text(json.dumps(record), encoding="utf-8")
    return {
        "KICAD_CLI_SCOPE_FILE": str(path),
        "KICAD_CLI_STATE_DIR": str(directory / "driver-state"),
    }


def grant_once(directory):
    """Keep the exact disposable grant stable between dry-run and confirm."""
    path = directory / "move-scope.json"
    if not path.exists():
        return grant(directory)
    return {
        "KICAD_CLI_SCOPE_FILE": str(path),
        "KICAD_CLI_STATE_DIR": str(directory / "driver-state"),
    }


def domain_receipt(value):
    """Inspect domain fields inside a real driver receipt; never relabel CLI output."""
    if value.get("ok") and "receipt" in value.get("data", {}):
        receipt = value["data"]["receipt"]
        if "task" in receipt.get("extensions", {}):
            task = dict(receipt["extensions"]["task"])
            task["status"] = "NOOP" if receipt["outcome"]["change"] == "not_applied" else "PASS"
            return dict(value, data=task, driver_receipt=receipt)
    return value
