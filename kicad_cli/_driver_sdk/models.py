"""Protocol types shared by the SDK and adapters; native facts stay with the adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

# Effects that change the target or the world outside the tool.
MODIFYING = frozenset({"change", "persist", "deliver", "configure", "physical"})
# Every effect other than observe goes through --dry-run/--confirm and the task scope (COR-008).
GATED = frozenset({"session", "artifact", *MODIFYING})
SCOPED = GATED
DANGEROUS = frozenset({"deliver", "configure", "physical"})
# Base §16.2 names plus the driver additions (COR-016).
OPERATION_STATES = ("planned", "pending", "running", "cancel_requested", "succeeded", "partial", "failed", "cancelled", "unknown")


class Execution(StrEnum):
    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class Change(StrEnum):
    NOT_APPLIED = "not_applied"
    APPLIED = "applied"
    PARTIAL = "partial"
    ROLLED_BACK = "rolled_back"
    UNKNOWN = "unknown"


class Persistence(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    UNCHANGED = "unchanged"
    UNSAVED = "unsaved"
    SAVED = "saved"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class Delivery(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    NOT_ATTEMPTED = "not_attempted"
    DELIVERED = "delivered"
    PARTIAL = "partial"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class VerificationStatus(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class Call:
    """One validated invocation handed to an adapter. Parameters are data, never code."""

    command: str
    capability_id: str
    params: dict[str, Any]
    effects: tuple[str, ...]
    dangerous: bool = False
    dry_run: bool = False
    operation_id: str | None = None
    plan_id: str | None = None
    authorization: dict | None = None
    idempotency_key: str | None = None
    item_key: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class DriverError(Exception):
    """Raised by an adapter (or the SDK) with the facts it actually has.

    dispatch_state says whether anything reached the native side. Raised from run() or plan() it
    defaults to not_started. Raised from apply() or submit() without an explicit dispatch_state, the
    outcome is treated as unknown: only an explicit not_started releases the confirm token. A receipt,
    when present, decides the reported error code through the contract's receipt rules.
    """

    def __init__(self, code: str, message: str, *, details: dict | None = None, receipt: dict | None = None,
                 dispatch_state: str | None = None):
        super().__init__(message)
        if dispatch_state not in {None, "not_started", "dispatched", "unknown"}:
            raise ValueError("dispatch_state must be not_started, dispatched or unknown")
        self.code = code
        self.message = message
        self.details = dict(details or {})
        self.receipt = receipt
        self.explicit = dispatch_state is not None
        self.dispatch_state = dispatch_state or "not_started"

    @property
    def surely_not_started(self) -> bool:
        """The adapter said, explicitly, that nothing reached the native side."""
        return self.explicit and self.dispatch_state == "not_started"


@runtime_checkable
class Adapter(Protocol):
    """The native side. Optional hooks: environment(), doctor_checks(), credentials(), revision(target),
    submit(call, plan), status(record), update(call).

    For a command with the batch profile, plan(call) returns items (one per target, each with key, target,
    base_snapshot, changes, outputs, can_apply) and apply(call, plan) is called once per item with that
    item's own single-target plan and call.item_key set."""

    def run(self, call: Call) -> dict: ...

    def plan(self, call: Call) -> dict: ...

    def apply(self, call: Call, plan: dict) -> dict: ...
