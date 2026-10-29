"""Public CLI recovery after actual sidecar deletion and process interruption."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from driver_test_utils import grant

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests/fixtures/sch_edit"
FUTURE = (
    "from datetime import datetime,timedelta,timezone; "
    "from kicad_cli import driver_runtime; original_cli=driver_runtime.create_cli; "
    "driver_runtime.create_cli=lambda **k: original_cli("
    "now=lambda:datetime.now(timezone.utc)+timedelta(minutes=2),**k);\n"
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def invoke(env, args, shim="", future=False):
    code = (FUTURE if future else "") + shim
    prefix = [sys.executable, "-B", "-m", "kicad_cli.main"]
    if code:
        prefix = [
            sys.executable,
            "-B",
            "-c",
            code + "\nfrom kicad_cli.main import main; main(__import__('sys').argv[1:])",
        ]
    process = subprocess.run(
        [*prefix, *args, "--compact"], cwd=REPO, env=env, capture_output=True, timeout=90
    )
    return process, json.loads(process.stdout) if process.stdout else None


@pytest.fixture
def scene(tmp_path):
    shutil.copytree(
        FIXTURE, tmp_path, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.py", "__pycache__")
    )
    changes = tmp_path / "changes.json"
    changes.write_text(
        json.dumps(
            {
                "changes": [
                    {"op": "set", "ref": "R1", "value": "12k"},
                    {"op": "set", "ref": "R10", "value": "33k"},
                ]
            }
        ),
        encoding="utf-8",
    )
    env = dict(os.environ, **grant(tmp_path), PYTHONIOENCODING="utf-8")
    args = [
        "sch",
        "edit",
        "--schematic",
        str(tmp_path / "edit.kicad_sch"),
        "--changes",
        str(changes),
    ]
    process, result = invoke(env, args + ["--dry-run"])
    assert process.returncode == 0 and result["ok"], result
    return tmp_path, env, args, result["data"]


def observe_and_resolve(scene):
    root, env, args, data = scene
    operation = data["preview"]["operation_id"]
    rows = data["preview"]["extensions"]["files"]
    saved = {r["path"]: sha(r["path"]) for r in rows}
    process, shown = invoke(env, ["operation", "show", "--id", operation], future=True)
    assert process.returncode == 0 and shown["ok"], shown
    actual = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert actual["completion"] == "completed" and actual["outcome"] == "applied", actual
    process, resolved = invoke(
        env,
        [
            "operation",
            "resolve",
            "--id",
            operation,
            "--outcome",
            "applied",
            "--evidence",
            actual["evidence_file"],
        ],
        future=True,
    )
    assert process.returncode == 0 and resolved["ok"], resolved
    assert {r["path"]: sha(r["path"]) for r in rows} == saved
    assert not list(root.glob("*.kicad-cli.lock")) and not list(root.glob("*.kicad-cli.journal"))
    process, repeated = invoke(
        env,
        [
            "operation",
            "resolve",
            "--id",
            operation,
            "--outcome",
            "applied",
            "--evidence",
            actual["evidence_file"],
        ],
        future=True,
    )
    # The pinned SDK rejects duplicate resolution registration. Its state and
    # saved files must be idempotent; no second cleanup or project write occurs.
    assert process.returncode == 2 and repeated["error"]["code"] == "E_VALIDATION", repeated
    assert {r["path"]: sha(r["path"]) for r in rows} == saved
    process, following = invoke(env, args + ["--dry-run"])
    assert process.returncode == 0 and following["ok"], following


@pytest.mark.parametrize("suffix", [".backup", ".kicad-cli.lock", ".kicad-cli.journal"])
def test_saved_commit_cleanup_interruption_is_resolvable(scene, suffix):
    root, env, args, data = scene
    shim = (
        "import os; from pathlib import Path; original_unlink=Path.unlink;\n"
        "def unlink(self,*a,**k):\n"
        " original_unlink(self,*a,**k)\n"
        f" if str(self).endswith({suffix!r}): os._exit(91)\n"
        "Path.unlink=unlink\n"
    )
    process, result = invoke(env, args + ["--confirm", data["confirm_token"]], shim)
    assert process.returncode == 91 and result is None
    for row in data["preview"]["extensions"]["files"]:
        assert sha(row["path"]) == row["intended"]
    observe_and_resolve(scene)


@pytest.mark.parametrize("index", range(5))
@pytest.mark.parametrize("mode", ["crash", "refuse"])
def test_every_commit_unlink_boundary_resumes(scene, index, mode):
    root, env, args, data = scene
    shim = (
        "import os; from pathlib import Path; original_unlink=Path.unlink; deletes=0;\n"
        "def unlink(self,*a,**k):\n"
        " global deletes\n"
        " selected=str(self).endswith(('.backup','.kicad-cli.lock','.kicad-cli.journal'))\n"
        f" hit=selected and deletes=={index}\n"
        " if selected: deletes+=1\n"
        + (" if hit: raise PermissionError('owned unlink refusal')\n" if mode == "refuse" else "")
        + " result=original_unlink(self,*a,**k)\n"
        + (" if hit: os._exit(91)\n" if mode == "crash" else "")
        + " return result\nPath.unlink=unlink\n"
    )
    process, result = invoke(env, args + ["--confirm", data["confirm_token"]], shim)
    if mode == "crash":
        assert process.returncode == 91 and result is None
    else:
        assert process.returncode == 6 and result["error"]["code"] == "E_OUTCOME_UNKNOWN", result
        assert result["error"]["details"]["receipt"]["outcome"]["change"] == "applied"
    for row in data["preview"]["extensions"]["files"]:
        assert sha(row["path"]) == row["intended"]
    observe_and_resolve(scene)


@pytest.mark.parametrize("index", range(5))
def test_resolve_unlink_interruption_can_resume(scene, index):
    root, env, args, data = scene
    shim = (
        "import os; from pathlib import Path; original_replace=os.replace;\n"
        "def replace(a,b):\n"
        " original_replace(a,b)\n"
        " if str(a).endswith('.pending') and str(b).endswith('.kicad_sch'): os._exit(91)\n"
        "os.replace=replace\n"
    )
    process, _ = invoke(env, args + ["--confirm", data["confirm_token"]], shim)
    assert process.returncode == 91
    operation = data["preview"]["operation_id"]
    process, shown = invoke(env, ["operation", "show", "--id", operation], future=True)
    actual = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert actual["outcome"] == "partial"
    hashes = {r["path"]: sha(r["path"]) for r in data["preview"]["extensions"]["files"]}
    unlink = (
        "import os; from pathlib import Path; original_unlink=Path.unlink; deletes=0;\n"
        "def unlink(self,*a,**k):\n"
        " global deletes\n"
        " selected=str(self).endswith(('.backup','.kicad-cli.lock','.kicad-cli.journal'))\n"
        f" hit=selected and deletes=={index}\n"
        " if selected: deletes+=1\n"
        " result=original_unlink(self,*a,**k)\n"
        " if hit: os._exit(92)\n"
        " return result\nPath.unlink=unlink\n"
    )
    words = [
        "operation",
        "resolve",
        "--id",
        operation,
        "--outcome",
        "partial",
        "--evidence",
        actual["evidence_file"],
    ]
    process, _ = invoke(env, words, unlink, future=True)
    assert process.returncode == 92
    assert {p: sha(p) for p in hashes} == hashes
    process, shown = invoke(env, ["operation", "show", "--id", operation], future=True)
    fresh = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert fresh["outcome"] == "partial" and fresh["cleanup"]["status"] == "attributable"
    words[-1] = fresh["evidence_file"]
    process, result = invoke(env, words, future=True)
    assert process.returncode == 0 and result["ok"], result
    assert {p: sha(p) for p in hashes} == hashes
    archives = list((root / "driver-state/recovery" / operation).glob("baseline-*"))
    assert {sha(p) for p in archives} == {
        r["baseline"] for r in data["preview"]["extensions"]["files"]
    }


@pytest.mark.parametrize(
    "kind", ["ledger", "archive", "journal", "future-lock", "missing-backup", "external-edit"]
)
def test_unattributable_cleanup_still_refuses(scene, kind):
    root, env, args, data = scene
    before = kind in {"journal", "future-lock", "missing-backup"}
    shim = (
        "import os; from pathlib import Path; original_unlink=Path.unlink;\n"
        "def unlink(self,*a,**k):\n"
        + (" if str(self).endswith('.backup'): os._exit(91)\n" if before else "")
        + " result=original_unlink(self,*a,**k)\n"
        + (" if str(self).endswith('.backup'): os._exit(91)\n" if not before else "")
        + " return result\nPath.unlink=unlink\n"
    )
    process, _ = invoke(env, args + ["--confirm", data["confirm_token"]], shim)
    assert process.returncode == 91
    operation = data["preview"]["operation_id"]
    _, shown = invoke(env, ["operation", "show", "--id", operation], future=True)
    actual = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    if kind == "ledger":
        target = next((root / "driver-state/document-transactions").glob("*.json"))
        value = json.loads(target.read_bytes())
        value["cursor"] = 5
        target.write_text(json.dumps(value), encoding="utf-8")
    elif kind == "archive":
        target = next((root / "driver-state/recovery" / operation).glob("baseline-*"))
        target.write_bytes(target.read_bytes() + b"\n")
    elif kind == "journal":
        target = next(root.glob("*.kicad-cli.journal"))
        target.write_bytes(target.read_bytes() + b"tamper")
    elif kind == "future-lock":
        target = next(root.glob("*.kicad-cli.lock"))
        target.write_text('{"operation_id":"foreign","pid":1}', encoding="utf-8")
    elif kind == "missing-backup":
        # The first pending backup may disappear legally; the second may not.
        target = Path(
            data["preview"]["extensions"]["files"][1]["path"] + "." + operation + ".backup"
        )
        target.unlink()
    else:
        target = root / "edit.kicad_sch"
        target.write_bytes(target.read_bytes() + b"\nexternal")
    originals = {p: p.read_bytes() for p in root.iterdir() if p.is_file()}
    process, refused = invoke(
        env,
        [
            "operation",
            "resolve",
            "--id",
            operation,
            "--outcome",
            "applied",
            "--evidence",
            actual["evidence_file"],
        ],
        future=True,
    )
    assert process.returncode != 0 and not refused["ok"], refused
    assert {p: p.read_bytes() for p in originals} == originals


def test_valid_external_bytes_are_unknown_and_not_overwritten(scene):
    root, env, args, data = scene
    shim = (
        "import os; from pathlib import Path; original_unlink=Path.unlink;\n"
        "def unlink(self,*a,**k):\n"
        " original_unlink(self,*a,**k)\n"
        " if str(self).endswith('.backup'): os._exit(91)\n"
        "Path.unlink=unlink\n"
    )
    process, _ = invoke(env, args + ["--confirm", data["confirm_token"]], shim)
    assert process.returncode == 91
    target = root / "edit.kicad_sch"
    target.write_bytes(target.read_bytes() + b"\n")  # Valid native syntax, foreign revision.
    before = target.read_bytes()
    operation = data["preview"]["operation_id"]
    process, shown = invoke(env, ["operation", "show", "--id", operation], future=True)
    assert process.returncode == 0 and shown["ok"], shown
    actual = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert actual["outcome"] == "unknown" and actual["cleanup"]["status"] == "refused"
    process, refused = invoke(
        env,
        [
            "operation",
            "resolve",
            "--id",
            operation,
            "--outcome",
            "applied",
            "--evidence",
            actual["evidence_file"],
        ],
        future=True,
    )
    assert process.returncode != 0 and refused["ok"] is False
    assert target.read_bytes() == before
    assert list((root / "driver-state/recovery" / operation).glob("baseline-*"))


def test_new_transaction_cannot_downgrade_its_signed_journal(scene):
    root, env, args, data = scene
    shim = (
        "import json; from kicad_cli.document_transaction import DocumentTransaction; "
        "original_cleanup=DocumentTransaction.cleanup;\n"
        "def cleanup(self,*a,**k):\n"
        " value=json.loads(self.journal.read_bytes()); value['owner']='kicad-cli.document-set.v1'\n"
        " value.pop('hmac',None); self.journal.write_text(json.dumps(value),encoding='utf-8')\n"
        " return original_cleanup(self,*a,**k)\n"
        "DocumentTransaction.cleanup=cleanup\n"
    )
    process, refused = invoke(env, args + ["--confirm", data["confirm_token"]], shim)
    assert process.returncode == 6 and refused["ok"] is False, refused
    rows = data["preview"]["extensions"]["files"]
    assert {r["path"]: sha(r["path"]) for r in rows} == {r["path"]: r["intended"] for r in rows}
    operation = data["preview"]["operation_id"]
    _, shown = invoke(env, ["operation", "show", "--id", operation], future=True)
    actual = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert actual["outcome"] == "applied" and actual["cleanup"]["status"] == "refused", actual
    assert all(Path(r["path"] + "." + operation + ".backup").is_file() for r in rows)


@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("mode", ["crash", "refuse"])
def test_partial_archive_write_preserves_saved_set_and_resolves(scene, index, mode):
    root, env, args, data = scene
    shim = (
        "import os; from pathlib import Path; from kicad_cli import document_transaction as dt; "
        "original_write=dt.write_new; archive_writes=0;\n"
        "def write(path,payload):\n"
        " global archive_writes\n"
        " selected=Path(path).name.lstrip('.').startswith('baseline-')\n"
        f" hit=selected and archive_writes=={index}\n"
        " if selected: archive_writes+=1\n"
        " if hit:\n"
        "  with Path(path).open('xb') as stream:\n"
        "   stream.write(payload[:max(1,len(payload)//2)])\n"
        "   stream.flush(); os.fsync(stream.fileno())\n"
        + (
            "  os._exit(91)\n"
            if mode == "crash"
            else "  raise OSError('owned partial archive write failure')\n"
        )
        + " return original_write(path,payload)\n"
        "dt.write_new=write\n"
    )
    process, result = invoke(env, args + ["--confirm", data["confirm_token"]], shim)
    if mode == "crash":
        assert process.returncode == 91 and result is None
    else:
        assert process.returncode == 6 and result["error"]["code"] == "E_OUTCOME_UNKNOWN", result
        assert result["error"]["details"]["receipt"]["outcome"]["change"] == "applied"
    rows = data["preview"]["extensions"]["files"]
    assert {r["path"]: sha(r["path"]) for r in rows} == {r["path"]: r["intended"] for r in rows}
    operation = data["preview"]["operation_id"]
    for row in rows:
        backup = Path(row["path"] + "." + operation + ".backup")
        assert sha(backup) == row["baseline"]
    observe_and_resolve(scene)
    formal = list((root / "driver-state/recovery" / operation).glob("baseline-*"))
    assert {sha(p) for p in formal if p.suffix != ".archive-tmp"} == {r["baseline"] for r in rows}


ARCHIVE_BOUNDARIES = [
    "preparation-before",
    "preparation-after",
    "reservation-after",
    "staging-after",
    "verification-after",
    "publish-before",
    "publish-after",
    "ready-after",
    "checkpoint-before",
    "checkpoint-after",
]


def archive_boundary_shim(boundary, mode):
    fault = "os._exit(91)" if mode == "crash" else "raise OSError('owned archive boundary refusal')"
    return (
        "import os; from pathlib import Path; from kicad_cli import document_transaction as dt;\n"
        "original_persist=dt.persist; original_write=dt.write_new; original_rename=os.rename\n"
        "def persist(path,value,key):\n"
        " archive=Path(path).parent.name=='document-archive-preparations'\n"
        " checkpoint=Path(path).parent.name=='document-transactions'\n"
        " attempts=value.get('archives',[{}])[0].get('attempts',[]) if archive else []\n"
        " status=attempts[-1]['status'] if attempts else None\n"
        f" boundary={boundary!r}\n"
        " before=(boundary=='preparation-before' and archive) or "
        "(boundary=='checkpoint-before' and checkpoint)\n"
        f" if before: {fault}\n"
        " original_persist(path,value,key)\n"
        " after=(boundary=='preparation-after' and archive and not attempts) or "
        "(boundary=='reservation-after' and status=='writing') or "
        "(boundary=='verification-after' and status=='verified') or "
        "(boundary=='ready-after' and archive and value.get('phase')=='ready') or "
        "(boundary=='checkpoint-after' and checkpoint)\n"
        f" if after: {fault}\n"
        "def write(path,payload):\n"
        " original_write(path,payload)\n"
        f" if {boundary!r}=='staging-after' and str(path).endswith('.archive-tmp'): {fault}\n"
        "def rename(a,b):\n"
        " archive=str(a).endswith('.archive-tmp')\n"
        f" if archive and {boundary!r}=='publish-before': {fault}\n"
        " original_rename(a,b)\n"
        f" if archive and {boundary!r}=='publish-after': {fault}\n"
        "dt.persist=persist; dt.write_new=write; os.rename=rename\n"
    )


@pytest.mark.parametrize("boundary", ARCHIVE_BOUNDARIES)
@pytest.mark.parametrize("mode", ["crash", "refuse"])
def test_archive_preparation_publication_and_checkpoint_resumes(scene, boundary, mode):
    if os.name != "nt" and boundary in {"publish-before", "publish-after"}:
        pytest.skip("Windows rename publication boundary; other platform not validated here")
    root, env, args, data = scene
    process, result = invoke(
        env, args + ["--confirm", data["confirm_token"]], archive_boundary_shim(boundary, mode)
    )
    if mode == "crash":
        assert process.returncode == 91 and result is None
    else:
        assert process.returncode == 6 and result["error"]["code"] == "E_OUTCOME_UNKNOWN", result
        assert result["error"]["details"]["receipt"]["outcome"]["change"] == "applied"
    operation = data["preview"]["operation_id"]
    for row in data["preview"]["extensions"]["files"]:
        assert sha(row["path"]) == row["intended"]
        assert sha(Path(row["path"] + "." + operation + ".backup")) == row["baseline"]
    observe_and_resolve(scene)


@pytest.mark.parametrize("tamper", ["preparation", "temporary", "formal", "hardlink"])
def test_archive_preparation_tamper_is_preserved_and_refused(scene, tamper):
    root, env, args, data = scene
    shim = (
        "import os; from pathlib import Path; from kicad_cli import document_transaction as dt; "
        "original=dt.write_new;\n"
        "def write(path,payload):\n"
        " if str(path).endswith('.archive-tmp'):\n"
        "  with Path(path).open('xb') as handle: handle.write(payload[:len(payload)//2])\n"
        "  os._exit(91)\n"
        " return original(path,payload)\n"
        "dt.write_new=write\n"
    )
    process, _ = invoke(env, args + ["--confirm", data["confirm_token"]], shim)
    assert process.returncode == 91
    preparation = next((root / "driver-state/document-archive-preparations").glob("*.json"))
    value = json.loads(preparation.read_bytes())
    entry = value["archives"][0]
    temporary = Path(entry["attempts"][0]["path"])
    if tamper == "preparation":
        value["phase"] = "ready"
        preparation.write_text(json.dumps(value), encoding="utf-8")
    elif tamper == "temporary":
        temporary.write_bytes(b"foreign non-baseline staging")
    elif tamper == "formal":
        Path(entry["path"]).write_bytes(b"unrecognized formal archive")
    else:
        os.link(temporary, temporary.with_name("foreign-link"))
    protected = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    operation = data["preview"]["operation_id"]
    _, shown = invoke(env, ["operation", "show", "--id", operation], future=True)
    actual = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    assert actual["outcome"] == "applied" and actual["cleanup"]["status"] == "refused", actual
    process, refused = invoke(
        env,
        [
            "operation",
            "resolve",
            "--id",
            operation,
            "--outcome",
            "applied",
            "--evidence",
            actual["evidence_file"],
        ],
        future=True,
    )
    assert process.returncode != 0 and refused["ok"] is False, refused
    # Queries may add records. Existing documents/staging evidence are unchanged;
    # the operation store's SQLite WAL is a control-plane mutation.
    assert all(
        p.read_bytes() == payload
        for p, payload in protected.items()
        if not p.name.endswith((".db", ".db-wal", ".db-shm"))
    )


def test_archive_publication_never_overwrites_a_racing_foreign_file(scene):
    if os.name != "nt":
        pytest.skip("Windows no-overwrite rename; other platform not validated here")
    root, env, args, data = scene
    shim = (
        "import os; from pathlib import Path; original=os.rename;\n"
        "def rename(a,b):\n"
        " if str(a).endswith('.archive-tmp'): Path(b).write_bytes(b'foreign archive race')\n"
        " return original(a,b)\n"
        "os.rename=rename\n"
    )
    process, result = invoke(env, args + ["--confirm", data["confirm_token"]], shim)
    assert process.returncode == 6 and result["error"]["code"] == "E_OUTCOME_UNKNOWN", result
    operation = data["preview"]["operation_id"]
    archive = next((root / "driver-state/recovery" / operation).glob("baseline-*"))
    assert archive.read_bytes() == b"foreign archive race"
    _, shown = invoke(env, ["operation", "show", "--id", operation], future=True)
    actual = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    process, refused = invoke(
        env,
        [
            "operation",
            "resolve",
            "--id",
            operation,
            "--outcome",
            "applied",
            "--evidence",
            actual["evidence_file"],
        ],
        future=True,
    )
    assert process.returncode != 0 and not refused["ok"]
    assert archive.read_bytes() == b"foreign archive race"
    assert all(sha(r["path"]) == r["intended"] for r in data["preview"]["extensions"]["files"])


@pytest.mark.parametrize("third_link", [False, True])
def test_recorded_publication_pair_resumes_but_extra_link_refuses(scene, third_link):
    """Exercise the link publication seam on this host; not Linux/macOS evidence."""
    root, env, args, data = scene
    shim = (
        "import os; from kicad_cli import document_transaction as dt;\n"
        "def publish(temporary,archive,expected):\n"
        " os.link(temporary,archive,follow_symlinks=False); os._exit(91)\n"
        "dt.publish_archive=publish\n"
    )
    process, _ = invoke(env, args + ["--confirm", data["confirm_token"]], shim)
    assert process.returncode == 91
    operation = data["preview"]["operation_id"]
    archive = next((root / "driver-state/recovery" / operation).glob("baseline-*"))
    if third_link:
        os.link(archive, archive.with_name("foreign-third-link"))
    _, shown = invoke(env, ["operation", "show", "--id", operation], future=True)
    actual = shown["data"]["operation_status"]["extensions"]["recovery_readback"]
    if not third_link:
        assert actual["cleanup"]["status"] == "attributable"
        observe_and_resolve(scene)
        assert archive.stat().st_nlink == 1
    else:
        assert actual["cleanup"]["status"] == "refused"
        protected = archive.read_bytes()
        process, refused = invoke(
            env,
            [
                "operation",
                "resolve",
                "--id",
                operation,
                "--outcome",
                "applied",
                "--evidence",
                actual["evidence_file"],
            ],
            future=True,
        )
        assert process.returncode != 0 and not refused["ok"]
        assert archive.read_bytes() == protected and archive.stat().st_nlink == 3
