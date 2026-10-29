"""Operation-owned saved document set; observed restore, never assumed atomic."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import uuid
from pathlib import Path

from .move_transaction import digest, process_alive, regular, write_new

OWNER = "kicad-cli.document-set.v1"
SIGNED_OWNER = "kicad-cli.document-set.v2"
PHASE_OWNER = "kicad-cli.document-cleanup.v2"
ARCHIVE_OWNER = "kicad-cli.document-archives.v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def seal(value, key):
    value = dict(value)
    value["hmac"] = hmac.new(key, canonical(value), hashlib.sha256).hexdigest()
    return value


def authenticated(path, key):
    regular(path)
    if path.stat().st_size > 1024 * 1024:
        raise OSError("Document cleanup record too large")
    value = json.loads(path.read_bytes())
    signature = value.pop("hmac", "")
    expected = hmac.new(key, canonical(value), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise OSError("Document cleanup record authentication failed")
    return value


def checkpoint_path(state_dir, operation):
    # Operation IDs are store identities, never request-supplied filesystem paths.
    name = hashlib.sha256(operation.encode()).hexdigest() + ".json"
    return Path(state_dir) / "document-transactions" / name


def archive_preparation_path(state_dir, operation):
    name = hashlib.sha256(operation.encode()).hexdigest() + ".json"
    return Path(state_dir) / "document-archive-preparations" / name


def persist(path, value, key):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        regular(path)
    pending = path.with_name(path.name + "." + uuid.uuid4().hex + ".pending")
    write_new(pending, canonical(seal(value, key)))
    os.replace(pending, path)


def revision(path):
    path = Path(path)
    try:
        regular(path)
        return digest(path)
    except FileNotFoundError:
        return "absent"


def names(primary):
    return tuple(
        Path(str(primary) + suffix) for suffix in (".kicad-cli.lock", ".kicad-cli.journal")
    )


def owned(primary, operation, expected_files=None, *, state_dir=None, key=None, required=False):
    if state_dir is not None:
        checkpoint = checkpoint_path(state_dir, operation)
        if checkpoint.exists():
            data = authenticated(checkpoint, key)
            if (
                data.get("owner") != PHASE_OWNER
                or data.get("operation_id") != operation
                or data.get("primary") != str(primary)
                or data.get("phase") not in {"cleaning", "cleaned"}
            ):
                raise OSError("Document cleanup checkpoint belongs to another operation")
            original = data["journal"]
            if required and original.get("owner") != SIGNED_OWNER:
                raise OSError("Cleanup evidence cannot downgrade a signed document transaction")
            expected = DocumentTransaction(primary, operation, expected_files).files
            if original.get("files") != expected:
                raise OSError("Document cleanup checkpoint differs from the authenticated plan")
            if data["observed"] != {r["path"]: revision(r["path"]) for r in expected}:
                raise OSError("Saved documents changed after the cleanup decision")
            for row in data["archives"]:
                archive = archive_path(state_dir, operation, row["index"], expected[row["index"]])
                if row["path"] != str(archive) or revision(archive) != row["sha256"]:
                    raise OSError("Archived document baseline unavailable or changed")
            expected_archives = [
                {
                    "index": i,
                    "path": str(archive_path(state_dir, operation, i, r)),
                    "sha256": r["baseline"],
                }
                for i, r in enumerate(expected)
                if r["baseline"] != "absent"
            ]
            if data["archives"] != expected_archives:
                raise OSError("Document cleanup baseline evidence is incomplete")
            actions = data["actions"]
            cursor, pending = data["cursor"], data["pending"]
            if (
                not isinstance(cursor, int)
                or not 0 <= cursor <= len(actions)
                or pending not in {None, cursor}
                or (data["phase"] == "cleaned" and (cursor != len(actions) or pending is not None))
            ):
                raise OSError("Invalid document cleanup progress")
            expected_paths = cleanup_paths(primary, expected)
            if [r["path"] for r in actions] != [str(p) for p in expected_paths]:
                raise OSError("Document cleanup action escaped its attributed sidecar")
            for index, row in enumerate(actions):
                actual = revision(row["path"])
                if index < cursor:
                    if actual != "absent":
                        raise OSError("A deleted sidecar was recreated; preserve external file")
                elif actual != row["sha256"] and not (index == pending and actual == "absent"):
                    raise OSError("Sidecar missing or changed outside recorded cleanup intent")
            return original
    lock, journal = names(primary)
    if not journal.exists():
        backups = (
            [
                Path(r["backup"])
                for r in DocumentTransaction(primary, operation, expected_files).files
            ]
            if expected_files is not None
            else []
        )
        if required or lock.exists() or any(p.exists() for p in backups):
            raise OSError("Unattributed document lock requires inspection")
        return None
    regular(journal)
    if journal.stat().st_size > 1024 * 1024:
        raise OSError("Document journal too large")
    data = json.loads(journal.read_bytes())
    if required and data.get("owner") != SIGNED_OWNER:
        raise OSError("A signed document journal is required for this operation")
    if data.get("owner") == SIGNED_OWNER:
        data = authenticated(journal, key)
    if (
        data.get("owner") not in {OWNER, SIGNED_OWNER}
        or data.get("operation_id") != operation
        or data.get("primary") != str(primary)
    ):
        raise OSError("Document journal belongs to another operation")
    if expected_files is not None:
        expected = DocumentTransaction(primary, operation, expected_files).files
        if data.get("files") != expected:
            raise OSError("Document journal differs from the authenticated operation plan")
    regular(lock)
    holder = json.loads(lock.read_bytes())
    if holder.get("pid") != data.get("pid") or holder.get("operation_id") != operation:
        raise OSError("Document lock/journal identity differs")
    for row in data["files"]:
        if Path(row["backup"]) != Path(row["path"]).with_name(
            Path(row["path"]).name + "." + operation + ".backup"
        ):
            raise OSError("Document backup escaped its attributed target")
        secondary_lock = names(row["path"])[0]
        regular(secondary_lock)
        if json.loads(secondary_lock.read_bytes()) != holder:
            raise OSError("Document set lock differs from its writer")
        if row["baseline"] != "absent":
            regular(Path(row["backup"]))
            if digest(Path(row["backup"])) != row["baseline"]:
                raise OSError("Document backup differs from its baseline")
    if state_dir is not None:
        preparation = archive_preparation_path(state_dir, operation)
        if preparation.exists():
            validate_archive_preparation(preparation, primary, operation, data, state_dir, key)
    return data


def archive_path(state_dir, operation, index, row):
    return (
        Path(state_dir)
        / "recovery"
        / operation
        / (f"baseline-{index}-{row['baseline']}{Path(row['path']).suffix}")
    )


def expected_archives(state_dir, operation, files):
    return [
        {"index": i, "path": str(archive_path(state_dir, operation, i, r)), "sha256": r["baseline"]}
        for i, r in enumerate(files)
        if r["baseline"] != "absent"
    ]


def archive_temporary(archive, nonce):
    if len(nonce) != 32 or any(c not in "0123456789abcdef" for c in nonce):
        raise OSError("Invalid attributed archive staging identity")
    archive = Path(archive)
    return archive.with_name("." + archive.name + "." + nonce + ".archive-tmp")


def archive_link_pair(temporary, archive, expected):
    """Only the recorded no-clobber POSIX publication may have two links.

    This does not relax regular() for targets, backups, journals or locks.
    A third link, reparse point, different inode or changed bytes is refused.
    """
    infos = [p.lstat() for p in (temporary, archive)]
    if any(
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
        or info.st_nlink != 2
        for info in infos
    ) or (infos[0].st_dev, infos[0].st_ino) != (infos[1].st_dev, infos[1].st_ino):
        raise OSError("Archive publication pair is not its recorded temporary inode")
    if any(digest(p) != expected for p in (temporary, archive)):
        raise OSError("Archive publication pair differs from the complete baseline")


def validate_archive_preparation(path, primary, operation, journal, state_dir, key):
    data = authenticated(path, key)
    wanted = expected_archives(state_dir, operation, journal["files"])
    if (
        data.get("owner") != ARCHIVE_OWNER
        or data.get("primary") != str(primary)
        or data.get("operation_id") != operation
        or data.get("journal") != journal
        or data.get("mode") not in {"commit", "rollback", "resolve"}
        or data.get("phase") not in {"preparing", "ready"}
        or data.get("observed") != {r["path"]: revision(r["path"]) for r in journal["files"]}
        or [{k: r[k] for k in ("index", "path", "sha256")} for r in data["archives"]] != wanted
    ):
        raise OSError("Archive preparation differs from its authenticated document operation")
    for entry in data["archives"]:
        archive = Path(entry["path"])
        source = Path(journal["files"][entry["index"]]["backup"]).read_bytes()
        attempts = entry["attempts"]
        paths = set()
        for attempt in attempts:
            temporary = archive_temporary(archive, attempt["nonce"])
            if attempt["path"] != str(temporary) or str(temporary) in paths:
                raise OSError("Archive staging escaped its operation-owned reservation")
            paths.add(str(temporary))
            status = attempt["status"]
            present = temporary.exists()
            if status == "publishing" and present and archive.exists():
                archive_link_pair(temporary, archive, entry["sha256"])
            elif present:
                regular(temporary)
                payload = temporary.read_bytes()
                if status == "writing":
                    if not source.startswith(payload):
                        raise OSError(
                            "Partial archive staging is not a prefix of its bound baseline"
                        )
                elif status == "retained":
                    if len(payload) != attempt["bytes"] or digest(temporary) != attempt["sha256"]:
                        raise OSError("Retained partial archive staging was changed")
                elif status in {"verified", "publishing"}:
                    if digest(temporary) != entry["sha256"]:
                        raise OSError("Verified archive staging was changed")
                else:
                    raise OSError("Published archive temporary was recreated")
            elif status not in {"writing", "publishing", "published"}:
                raise OSError("Archive staging disappeared outside its publication intent")
            if status not in {"writing", "retained", "verified", "publishing", "published"}:
                raise OSError("Unknown archive preparation progress")
            if status == "verified" and archive.exists():
                raise OSError("A formal archive appeared before its publication intent")
            if status in {"publishing", "published"} and not present:
                if revision(archive) != entry["sha256"]:
                    raise OSError("Published archive unavailable or changed")
        pair = (
            attempts
            and attempts[-1]["status"] == "publishing"
            and Path(attempts[-1]["path"]).exists()
            and archive.exists()
        )
        if archive.exists() and not pair and revision(archive) != entry["sha256"]:
            raise OSError("Formal archive differs from its bound complete baseline")
        if data["phase"] == "ready" and revision(archive) != entry["sha256"]:
            raise OSError("Complete baseline archive required before document cleanup")
    return data


def publish_archive(temporary, archive, expected):
    """Atomic no-overwrite publication; a complete staging file comes first."""
    if archive.exists():
        if temporary.exists():
            archive_link_pair(temporary, archive, expected)
            temporary.unlink()
        elif revision(archive) != expected:
            raise OSError("Existing formal archive cannot be replaced")
        return
    if revision(temporary) != expected:
        raise OSError("Only a complete verified archive may be published")
    if os.name == "nt":
        # Windows rename fails if the destination exists; never os.replace.
        os.rename(temporary, archive)
    else:
        # link is exclusive and atomic. A crash before unlink is attributed
        # only by the signed publishing stage plus this exact inode pair.
        os.link(temporary, archive, follow_symlinks=False)
        archive_link_pair(temporary, archive, expected)
        temporary.unlink()
    if revision(archive) != expected or temporary.exists():
        raise OSError("Published baseline archive readback differs")


def prepare_archives(primary, operation, journal, state_dir, key, mode, current):
    path = archive_preparation_path(state_dir, operation)
    if path.exists():
        data = validate_archive_preparation(path, primary, operation, journal, state_dir, key)
    else:
        data = {
            "owner": ARCHIVE_OWNER,
            "primary": str(primary),
            "operation_id": operation,
            "journal": journal,
            "mode": mode,
            "observed": current,
            "phase": "preparing",
            "archives": [
                dict(row, attempts=[])
                for row in expected_archives(state_dir, operation, journal["files"])
            ],
        }
        # Before a temporary byte is written, persist its document-set decision.
        persist(path, data, key)
    for entry in data["archives"]:
        archive = Path(entry["path"])
        archive.parent.mkdir(parents=True, exist_ok=True)
        attempts = entry["attempts"]
        active = attempts[-1] if attempts and attempts[-1]["status"] != "retained" else None
        if active is None and archive.exists():
            if revision(archive) != entry["sha256"]:
                raise OSError("Existing formal archive is unrecognized; preserve it")
            continue
        if active and active["status"] == "published":
            continue
        if active and active["status"] == "writing" and Path(active["path"]).exists():
            temporary = Path(active["path"])
            # A signed reservation and exact baseline prefix attribute this
            # partial write. Preserve it; retry into a fresh exclusive name.
            active.update(
                status="retained", bytes=temporary.stat().st_size, sha256=digest(temporary)
            )
            persist(path, data, key)
            active = None
        if active is None:
            nonce = uuid.uuid4().hex
            active = {
                "nonce": nonce,
                "path": str(archive_temporary(archive, nonce)),
                "status": "writing",
            }
            attempts.append(active)
            persist(path, data, key)
        temporary = Path(active["path"])
        if active["status"] == "writing":
            write_new(temporary, Path(journal["files"][entry["index"]]["backup"]).read_bytes())
            if revision(temporary) != entry["sha256"]:
                raise OSError("Staged archive differs from its complete bound baseline")
            active["status"] = "verified"
            persist(path, data, key)
        if active["status"] == "verified":
            active["status"] = "publishing"
            persist(path, data, key)
        if active["status"] == "publishing":
            publish_archive(temporary, archive, entry["sha256"])
            active["status"] = "published"
            persist(path, data, key)
    data["phase"] = "ready"
    persist(path, data, key)
    return expected_archives(state_dir, operation, journal["files"])


def cleanup_paths(primary, files):
    # Deduplicate the primary lock; each destructive boundary has one identity.
    return list(
        dict.fromkeys(
            [Path(r["backup"]) for r in files if r["baseline"] != "absent"]
            + [names(r["path"])[0] for r in files]
            + list(names(primary))
        )
    )


class DocumentTransaction:
    def __init__(self, primary, operation, files, *, state_dir=None, key=None, require_signed=True):
        self.primary, self.operation = Path(primary), operation
        self.lock, self.journal = names(primary)
        self.state_dir, self.key = state_dir, key
        self.require_signed = require_signed
        self.checkpoint = checkpoint_path(state_dir, operation) if state_dir is not None else None
        self.files = [
            dict(
                row,
                backup=str(
                    Path(row["path"]).with_name(
                        Path(row["path"]).name + "." + operation + ".backup"
                    )
                ),
            )
            for row in files
        ]

    def arm(self):
        if self.journal.exists():
            raise FileExistsError("Unfinished document transaction requires observation")
        write_new(
            self.lock, json.dumps({"operation_id": self.operation, "pid": os.getpid()}).encode()
        )
        created = []
        locks = [self.lock]
        try:
            for row in self.files:
                path = Path(row["path"])
                if (
                    not path.parent.is_dir()
                    or path.parent.resolve() != path.parent
                    or path.parent.is_symlink()
                ):
                    raise OSError("An existing regular output directory is required")
                lock, journal = names(path)
                if lock != self.lock:
                    if journal.exists():
                        raise FileExistsError("A document in the set has an unresolved journal")
                    write_new(lock, self.lock.read_bytes())
                    locks.append(lock)
                if revision(path) != row["baseline"]:
                    raise ValueError("Document drift after acquiring the write lock")
                if row["baseline"] != "absent":
                    backup = Path(row["backup"])
                    write_new(backup, path.read_bytes())
                    created.append(backup)
            write_new(
                self.journal,
                canonical(
                    seal(
                        {
                            "owner": SIGNED_OWNER,
                            "primary": str(self.primary),
                            "operation_id": self.operation,
                            "pid": os.getpid(),
                            "files": self.files,
                        },
                        self.key,
                    )
                ),
            )
        except BaseException:
            for path in created:
                path.unlink()
            for lock in locks:
                lock.unlink()
            raise

    def cleanup(self, mode="commit", observed=None):
        if self.checkpoint is None or self.key is None:
            raise OSError("Durable authenticated cleanup storage is required")
        original = owned(
            self.primary,
            self.operation,
            self.files,
            state_dir=self.state_dir,
            key=self.key,
            required=self.require_signed,
        )
        if original is None:
            raise OSError("Document cleanup has no attributable journal or checkpoint")
        current = {r["path"]: revision(r["path"]) for r in self.files}
        if observed is not None and current != observed:
            raise OSError("Document changed before cleanup registration")
        if self.checkpoint.exists():
            data = authenticated(self.checkpoint, self.key)
        else:
            expected = (
                "intended" if mode == "commit" else "baseline" if mode == "rollback" else None
            )
            if expected and current != {r["path"]: r[expected] for r in self.files}:
                raise OSError("Document cleanup outcome differs from its durable decision")
            archives = prepare_archives(
                self.primary, self.operation, original, self.state_dir, self.key, mode, current
            )
            data = {
                "owner": PHASE_OWNER,
                "primary": str(self.primary),
                "operation_id": self.operation,
                "phase": "cleaning",
                "mode": mode,
                "journal": original,
                "observed": current,
                "archives": archives,
                "actions": [
                    {"path": str(p), "sha256": revision(p)}
                    for p in cleanup_paths(self.primary, self.files)
                ],
                "cursor": 0,
                "pending": None,
            }
            persist(self.checkpoint, data, self.key)
        while data["cursor"] < len(data["actions"]):
            owned(
                self.primary,
                self.operation,
                self.files,
                state_dir=self.state_dir,
                key=self.key,
                required=self.require_signed,
            )
            index = data["cursor"]
            # Persist intent before unlink. A missing file is legal only for
            # this signed pending action or an acknowledged earlier deletion.
            if data["pending"] is None:
                data["pending"] = index
                persist(self.checkpoint, data, self.key)
            path = Path(data["actions"][index]["path"])
            if path.exists():
                regular(path)
                if revision(path) != data["actions"][index]["sha256"]:
                    raise OSError("Sidecar changed before its authorized unlink")
                path.unlink()
            data["cursor"], data["pending"] = index + 1, None
            persist(self.checkpoint, data, self.key)
        data["phase"] = "cleaned"
        persist(self.checkpoint, data, self.key)

    def rollback(self):
        if self.checkpoint is not None and (
            self.checkpoint.exists()
            or archive_preparation_path(self.state_dir, self.operation).exists()
        ):
            raise OSError(
                "A durable cleanup decision exists; observe and resume without target rewrite"
            )
        owned(
            self.primary,
            self.operation,
            self.files,
            state_dir=self.state_dir,
            key=self.key,
            required=self.require_signed,
        )
        # All targets must be attributable before restoring any target.
        for row in self.files:
            if revision(row["path"]) not in {row["baseline"], row["intended"]}:
                raise OSError("Unrecognized document bytes; preserve external edit")
            if row["baseline"] != "absent" and revision(row["backup"]) != row["baseline"]:
                raise OSError("Document baseline backup unavailable or changed")
        for row in reversed(self.files):
            path = Path(row["path"])
            if revision(path) == row["baseline"]:
                continue
            if row["baseline"] == "absent":
                path.unlink()
            else:
                pending = path.with_name(path.name + "." + self.operation + ".restore-pending")
                write_new(pending, Path(row["backup"]).read_bytes())
                os.replace(pending, path)
        if any(revision(row["path"]) != row["baseline"] for row in self.files):
            raise OSError("Saved document restore readback differs from baseline")
        self.cleanup(mode="rollback")


def resolve(
    primary,
    operation,
    archive_dir,
    observed,
    expected_files=None,
    *,
    state_dir,
    key,
    required=False,
):
    data = owned(
        primary, operation, expected_files, state_dir=state_dir, key=key, required=required
    )
    if data is None:
        return
    if process_alive(data["pid"]):
        raise OSError("Document writer has not been proven stopped")
    if archive_dir != Path(state_dir) / "recovery" / operation:
        raise OSError("Unexpected document baseline archive directory")
    transaction = DocumentTransaction(
        primary, operation, expected_files, state_dir=state_dir, key=key, require_signed=required
    )
    transaction.cleanup(mode="resolve", observed=observed)
