"""Scoped, no-clobber publication of exact sync bytes for public read-only review.

The signed manifest is evidence, not a second confirmation mechanism. The SDK
binds the complete public review and manifest revision into its ordinary plan.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import ExitStack, nullcontext
from pathlib import Path

from . import candidate_output, document_transaction
from ._driver_sdk import tokens
from .move_transaction import digest

SCHEMA = "kicad-cli://schemas/sync-candidate-review/v1"
OWNER = "kicad-cli.sync-candidate.v1"


class CandidateError(ValueError):
    def __init__(self, message, reason, **details):
        super().__init__(message)
        self.details = {
            "candidate_conflict": reason,
            **details,
            "_untrusted": [key for key in details if key == "path"],
        }


def identity(path):
    with candidate_output.observed(path) as (_, opened):
        return opened.identity()


def directories(path):
    """Acquire each component without following a junction; return handle identity."""
    try:
        with candidate_output.Directory(path) as parent:
            return parent.identity
    except candidate_output.OutputError as exc:
        raise CandidateError(str(exc), exc.reason) from exc


def destination(value, inputs, state_dir, grant_file):
    path = Path(value).expanduser()
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise CandidateError("--candidate-out requires an absolute local path.", "invalid_path")
    path = Path(os.path.abspath(path))
    if path.suffix != ".kicad_pcb":
        raise CandidateError("Candidate output must end in .kicad_pcb.", "invalid_suffix")
    try:
        parent = directories(path.parent)
    except FileNotFoundError as exc:
        raise CandidateError(
            "Candidate output requires an existing parent directory.", "parent_missing"
        ) from exc
    if path.resolve() != path:
        raise CandidateError("Candidate output must not traverse a redirected path.", "unsafe_path")
    protected = {Path(row[key]) for row in inputs["files"] for key in ("path", "resolved")} | {
        Path(grant_file).resolve()
    }
    if path in protected or path.is_relative_to(Path(state_dir).resolve()):
        raise CandidateError(
            "Candidate output conflicts with inputs or runtime state.", "path_conflict"
        )
    if os.path.lexists(path):
        raise CandidateError(
            "Candidate output already exists; it will not be overwritten.", "already_exists"
        )
    return path, parent


def workspace(state_dir, operation_id):
    return Path(state_dir) / "sync-candidates" / hashlib.sha256(operation_id.encode()).hexdigest()


def temporary_path(path, operation_id):
    name = "." + path.name + "." + hashlib.sha256(operation_id.encode()).hexdigest() + ".pending"
    return path.with_name(name)


def _manifest(call, candidate, inputs, baseline, changes, intended):
    return {
        "owner": OWNER,
        "operation_id": call.operation_id,
        "plan_id": call.plan_id,
        "candidate": candidate,
        "inputs_sha256": tokens.digest(inputs),
        "scope_sha256": tokens.digest(call.extras["scope"].record),
        "baseline_sha256": baseline,
        "changes_sha256": tokens.digest(changes),
        "intended_sha256": intended,
        "temporary": {
            "path": str(temporary_path(Path(candidate["path"]), call.operation_id)),
            "sha256": intended,
            "publication": "same_parent_no_clobber",
        },
    }


def prepare(call, prepared, inputs, changes, *, state_dir, key, recheck):
    """Publish complete bytes atomically; never write an input or existing output."""
    scope = call.extras["scope"]
    path, parent = destination(call.params["candidate-out"], inputs, state_dir, scope.grant_file)
    root = workspace(state_dir, call.operation_id)
    temporary = temporary_path(path, call.operation_id)
    outputs = [
        {"path": str(path), "effect": "persist", "exists": False},
        {"path": str(root), "effect": "persist", "exists": False},
        {"path": str(temporary), "effect": "persist", "exists": False},
    ]
    # Both public and private materialization targets are authorized before mkdir/write.
    scope.require(effects=call.effects, outputs=outputs)
    recheck(outputs)
    if os.path.lexists(temporary):
        raise CandidateError(
            "Candidate temporary output already exists; it will not be overwritten.",
            "temporary_exists",
            path=str(temporary),
        )
    root.parent.mkdir(parents=True, exist_ok=True)
    directories(root.parent)
    root.mkdir(exist_ok=False)
    raw = prepared["intended"]
    sha = hashlib.sha256(raw).hexdigest()
    request = _manifest(
        call,
        {"path": str(path), "parent_identity": parent},
        inputs,
        prepared["baseline"],
        changes,
        sha,
    )
    document_transaction.persist(root / "preparation.json", request, key)
    with ExitStack() as stack:
        try:
            bound_parent = stack.enter_context(candidate_output.Directory(path.parent))
        except candidate_output.OutputError as exc:
            raise CandidateError(str(exc), exc.reason) from exc
        if bound_parent.identity != parent:
            raise CandidateError("Candidate parent changed during preparation.", "parent_replaced")
        if temporary.parent != path.parent:
            raise CandidateError(
                "Candidate temporary must share its output parent.", "unsafe_parent"
            )
        try:
            owned = stack.enter_context(bound_parent.create(temporary.name))
        except FileExistsError as exc:
            raise CandidateError(
                "Candidate temporary output was occupied before creation.",
                "temporary_exists",
                path=str(temporary),
            ) from exc
        owned.identity()
        owned.write(raw)
        temporary_identity = owned.identity()
        if hashlib.sha256(owned.read()).hexdigest() != sha:
            raise OSError("Private candidate readback differs from prepared sync bytes.")
        request["temporary"]["identity"] = temporary_identity
        document_transaction.persist(root / "preparation.json", request, key)
        recheck(outputs)
        if (
            owned.identity() != temporary_identity
            or hashlib.sha256(owned.read()).hexdigest() != sha
        ):
            raise CandidateError(
                "Candidate temporary bytes or identity changed before publication.",
                "temporary_changed",
                path=str(temporary),
            )
        owned.publish(path.name)
        published = owned.identity()
        candidate = {
            "path": str(path),
            "sha256": sha,
            "bytes": len(raw),
            "identity": published,
            "parent_identity": parent,
        }
        if hashlib.sha256(owned.read()).hexdigest() != sha:
            raise CandidateError("Published candidate changed during preparation.", "changed")
        manifest = _manifest(call, candidate, inputs, prepared["baseline"], changes, sha)
        manifest_path = root / "manifest.json"
        document_transaction.persist(manifest_path, manifest, key)
        review = {
            "schema": SCHEMA,
            "schema_version": "1.0",
            "operation_id": call.operation_id,
            "plan_id": call.plan_id,
            "source": {
                "method": "sync_plan.prepare.captured_inputs",
                "inputs_sha256": tokens.digest(inputs),
                "schematic_path": inputs["schematic"],
                "schematic_sha256": prepared["schematic_identity"]["sha256"],
            },
            "board": {"path": inputs["board"], "baseline_sha256": prepared["baseline"]},
            "candidate": candidate,
            "manifest": {"path": str(manifest_path), "sha256": digest(manifest_path)},
            "companions": [row for row in inputs["files"] if row["path"] != inputs["board"]],
            "change_scope": {
                "changes": [{k: row[k] for k in ("action", "resource", "id")} for row in changes],
                "renamed_nets": prepared["renamed_nets"],
                "protected_semantics_sha256": prepared["protected_sha256"],
                "preservation": "Matched poses/identities and unrelated board/copper semantics; "
                "uniform net renames are explicit. Serialization may change whitespace.",
            },
            "confirmation_binding": {
                "mechanism": "SDK HMAC binds whole plan, operation, request, scope, "
                "inputs and manifest",
                "scope_sha256": manifest["scope_sha256"],
                "intended_sha256": sha,
            },
            "validity": {
                "expires_at_ref": "preview.expires_at",
                "conditions": "Original confirmation remains unconsumed and unexpired; original "
                "inputs, candidate identity/bytes, signed manifest and authorization "
                "are unchanged.",
                "retention": "Retained review evidence does not extend or renew "
                "confirmation validity.",
            },
            "usage": {
                "purpose": "Read-only candidate review before confirming the original PCB write",
                "native_review": "Independent official renderer may read candidate.path. Copy only "
                "byte-matching present companions into a separate owned observation project; "
                "retain absent companions, bound configuration and library resolution. Record "
                "every original to observation path relocation. Never mix another project's "
                "sidecars or save over inputs.",
            },
            "render_binding": {
                "status": "not_generated",
                "required": [
                    "candidate.path",
                    "candidate.sha256",
                    "layers",
                    "mirror",
                    "renderer identity/configuration",
                    "companion hashes and path relocations",
                ],
            },
            "_untrusted": [
                "source.schematic_path",
                "board.path",
                "candidate.path",
                "manifest.path",
                "companions",
                "change_scope",
            ],
        }
        validate(
            review,
            inputs,
            changes,
            state_dir=state_dir,
            key=key,
            _opened=(bound_parent, owned),
        )
        recheck(outputs)
        try:
            bound_parent.validate()
        except candidate_output.OutputError as exc:
            raise CandidateError(str(exc), exc.reason) from exc
        owned.retained = True
        return review, outputs


def validate(review, inputs, changes, *, state_dir, key, _opened=None):
    path = Path(review["candidate"]["path"])
    try:
        context = nullcontext(_opened) if _opened else candidate_output.observed(path)
        with context as (parent, opened):
            return _validate_opened(review, inputs, changes, state_dir, key, parent, opened)
    except FileNotFoundError as exc:
        raise CandidateError("Reviewed candidate is missing.", "missing", path=str(path)) from exc
    except candidate_output.OutputError as exc:
        raise CandidateError(str(exc), exc.reason) from exc
    except OSError as exc:
        raise CandidateError(
            "Reviewed candidate could not be acquired safely.", "unsafe_file"
        ) from exc


def _validate_opened(review, inputs, changes, state_dir, key, parent, opened):
    path = Path(review["candidate"]["path"])
    candidate = review["candidate"]
    current = opened.identity()
    if parent.identity != candidate["parent_identity"]:
        raise CandidateError("Reviewed candidate parent was replaced.", "parent_replaced")
    raw = opened.read()
    actual = hashlib.sha256(raw).hexdigest()
    if current != candidate["identity"]:
        raise CandidateError(
            "Reviewed candidate file identity changed.",
            "identity_changed",
            bytes_unchanged=actual == candidate["sha256"],
            path=str(path),
        )
    if actual != candidate["sha256"] or current["size"] != candidate["bytes"]:
        raise CandidateError("Reviewed candidate bytes changed.", "bytes_changed", path=str(path))
    manifest_path = workspace(state_dir, review["operation_id"]) / "manifest.json"
    if str(manifest_path) != review["manifest"]["path"]:
        raise CandidateError("Candidate manifest escaped its operation workspace.", "manifest_path")
    try:
        if digest(manifest_path) != review["manifest"]["sha256"]:
            raise OSError("manifest bytes differ")
        manifest = document_transaction.authenticated(manifest_path, key)
    except (OSError, ValueError) as exc:
        raise CandidateError(
            "Reviewed candidate manifest is missing or changed.", "manifest_changed"
        ) from exc
    expected = {
        "owner": OWNER,
        "operation_id": review["operation_id"],
        "plan_id": review["plan_id"],
        "candidate": candidate,
        "inputs_sha256": tokens.digest(inputs),
        "scope_sha256": review["confirmation_binding"]["scope_sha256"],
        "baseline_sha256": review["board"]["baseline_sha256"],
        "changes_sha256": tokens.digest(changes),
        "intended_sha256": candidate["sha256"],
        "temporary": {
            "path": str(temporary_path(path, review["operation_id"])),
            "sha256": candidate["sha256"],
            "publication": "same_parent_no_clobber",
        },
    }
    if manifest != expected or review["source"]["inputs_sha256"] != expected["inputs_sha256"]:
        raise CandidateError(
            "Candidate manifest differs from its approved operation.", "manifest_mismatch"
        )
    if parent.exists(Path(expected["temporary"]["path"]).name):
        raise CandidateError(
            "Candidate temporary output reappeared after publication.", "temporary_reappeared"
        )
    if opened.identity() != current:
        raise CandidateError("Reviewed candidate changed during its read.", "changed_during_read")
    parent.validate()
    return raw
