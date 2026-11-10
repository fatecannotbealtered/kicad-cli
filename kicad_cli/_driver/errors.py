"""Errors the runtime returns (DCLI-010 to DCLI-013).

A command fails by raising ``DriverError``. The output layer turns it into the error object:
``retryable`` and the exit code come from the contract, ``details`` always holds
``dispatch_state`` and ``next_action``, and ``untrusted`` names the details that carry text
from outside the tool, so they are listed in ``meta.untrusted`` (DCLI-004, DSEC-021).
"""
from __future__ import annotations

from typing import Any

# What to do next when the raising code gives no next action of its own (DCLI-011).
NEXT_ACTION = {
    "E_USAGE": "Fix the command line as `problem` says, then run it again.",
    "E_VALIDATION": "Change the values the application rejected, then run the command again.",
    "E_AMBIGUOUS": "Pick one of `candidates` only when the user named it, then run the command again with it.",
    "E_UNSUPPORTED": "Do not retry; this tool, mode or application version cannot do it.",
    "E_NOT_FOUND": "Find the right target with a list command, then run the command again.",
    "E_AUTH": "Ask the user to fix the credentials, then run the command again.",
    "E_FORBIDDEN": "Pass `details.scope.missing` and `details.scope.snippet` to the person. Write a scope file for it only "
                   "when the person grants it in their own message, then run the command again.",
    "E_CONFIG": "Fix the local configuration, then run the command again.",
    "E_BACKEND_UNAVAILABLE": "Make sure the application is installed, licensed and running, then run the command again.",
    "E_CONFIRMATION_REQUIRED": "Check the preview, then run the same command again with `--confirm <token>`.",
    "E_CONFLICT": "Read the current state again, then decide what to do.",
    "E_NETWORK": "Wait, then run the same command again.",
    "E_BUSY": "Wait `retry_after_ms`, then run the same command again.",
    "E_TIMEOUT": "Wait, then run the same command again.",
    "E_OUTCOME_UNKNOWN": "Run `lookup` before anything else; never send the write again.",
    "E_HUMAN_REQUIRED": "Ask the person to do `action`, then run `resume`.",
    "E_PARTIAL": "Read the result, then send again only what failed.",
    "E_VERIFY_FAILED": "Read `mismatched` in the receipt, then decide with the user what to do.",
    "E_VERIFY_INCOMPLETE": "Check the target with a read command before you report the change as done.",
    "E_NATIVE_FAILURE": "Read `native_message`, fix the cause, then run the command again.",
    "E_PROTOCOL": "Install matching versions of the tool and its host component.",
    "E_INTEGRITY": "Do not retry; report the integrity failure to the user.",
    "E_IO": "Check that the state directory and the local files are writable, then run the command again.",
    "E_INTERRUPTED": "Check the state, then run the command again.",
    "E_UNKNOWN": "Read `message` and `details`, then report the problem to the user.",
}


class DriverError(Exception):
    """A failed command. ``details`` become ``error.details``."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        next_action: str | None = None,
        dispatch_state: str = "not_started",
        untrusted: tuple[str, ...] | list[str] = (),
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = {
            "dispatch_state": dispatch_state,
            "next_action": next_action or NEXT_ACTION[code],
            **{k: v for k, v in details.items() if v is not None},
        }
        # Paths relative to the error object, such as "details.native_message" or "message".
        self.untrusted = list(untrusted)


def usage(problem: str, parameter: str | None = None, next_action: str | None = None) -> DriverError:
    return DriverError("E_USAGE", problem, problem=problem, parameter=parameter, next_action=next_action)
