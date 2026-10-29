"""Read an owned hidden GUI with existing enabled test preferences, then close it."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO.parent / "kicad-cli-runtime/native/gui-ipc-v4/configuration/10.0"
APP = Path("D:/software/kicad/home/bin/pcbnew.exe")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--binary", type=Path)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(exist_ok=False)
    project = root / "project"
    shutil.copytree(args.project, project)
    board = project / "decoupling.kicad_pcb"
    before = sha(board)
    preferences = json.loads((CONFIG / "kicad_common.json").read_bytes())
    assert preferences["api"]["enable_server"] is True
    shutil.copytree(CONFIG, root / "configuration/10.0")
    assert sha(CONFIG / "kicad_common.json") == sha(root / "configuration/10.0/kicad_common.json")
    for name in ("temp", "cache", "documents"):
        (root / name).mkdir()
    env = dict(
        os.environ,
        KICAD_CONFIG_HOME=str(root / "configuration"),
        TEMP=str(root / "temp"),
        TMP=str(root / "temp"),
        KICAD_CACHE_HOME=str(root / "cache"),
        KICAD_DOCUMENTS_HOME=str(root / "documents"),
        KICAD_API_SOCKET="ipc://" + str(root / "temp/kicad/api.sock"),
        KICAD_API_TOKEN="",
        KICAD_CLI_DRIVER_SDK_PATH=str(REPO / "kicad_cli/_driver_sdk"),
        KICAD_CLI_IPC_CLIENT_PATH=str(REPO / ".vendor"),
        PYTHONIOENCODING="utf-8",
        PYTHONDONTWRITEBYTECODE="1",
    )
    launch = root / "launch.json"
    launch.write_text(json.dumps({"executable": str(APP), "board": str(board)}), encoding="utf-8")
    env["KICAD_TASK_LAUNCH_INPUT"] = str(launch)
    command = (
        "$v = Get-Content -LiteralPath "
        "$env:KICAD_TASK_LAUNCH_INPUT -Raw | ConvertFrom-Json; "
        "$p = Start-Process -FilePath $v.executable "
        "-ArgumentList ('\"' + $v.board + '\"') -WindowStyle "
        "Hidden -PassThru; "
        "@{pid=$p.Id;started=$p.StartTime.ToUniversalTime().ToString('o')} "
        "| ConvertTo-Json -Compress"
    )
    launch_result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command], env=env, capture_output=True, timeout=20
    )
    (root / "launch.stdout").write_bytes(launch_result.stdout)
    (root / "launch.stderr").write_bytes(launch_result.stderr)
    assert launch_result.returncode == 0
    owned = json.loads(launch_result.stdout)
    record = {
        "owned": owned,
        "application": {"path": str(APP), "sha256": sha(APP)},
        "input_sha256": before,
        "copied_existing_configuration_sha256": sha(CONFIG / "kicad_common.json"),
        "original_configuration_changed": False,
        "probes": [],
    }
    cli = (
        [str(args.binary.resolve())]
        if args.binary
        else [sys.executable, "-B", "-m", "kicad_cli.main"]
    )
    try:
        for index in range(30):
            process = subprocess.run(
                cli + ["session", "inspect", "--compact"],
                cwd=REPO,
                env=env,
                capture_output=True,
                timeout=15,
            )
            (root / f"probe-{index}.stdout").write_bytes(process.stdout)
            (root / f"probe-{index}.stderr").write_bytes(process.stderr)
            value = json.loads(process.stdout)
            record["probes"].append({"index": index, "exit": process.returncode, "value": value})
            if value.get("ok") and value["data"].get("board", {}).get("name") == board.name:
                assert value["data"]["board"]["footprints"] == 3
                assert value["data"]["board"]["zones"] == 2
                record["status"] = "passed"
                break
            time.sleep(1)
        assert record.get("status") == "passed", record["probes"][-1]
    finally:
        # Close only the exact instance started here; no user instance is touched.
        env["KICAD_TASK_OWNED_PID"] = str(owned["pid"])
        close = (
            "$p = Get-CimInstance Win32_Process -Filter ('ProcessId "
            "= ' + $env:KICAD_TASK_OWNED_PID); $v = Get-Content "
            "-LiteralPath $env:KICAD_TASK_LAUNCH_INPUT -Raw | "
            "ConvertFrom-Json; if ($p.Name -ne 'pcbnew.exe' -or "
            "$p.ExecutablePath -ne $v.executable -or $p.CommandLine "
            "-notlike ('*' + $v.board + '*')) { throw 'Owned process "
            "identity could not be confirmed' }; $q = Get-Process "
            "-Id $p.ProcessId; $q.CloseMainWindow() | Out-Null"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", close],
            env=env,
            capture_output=True,
            timeout=20,
        )
        record["close_request_exit"] = result.returncode
        record["saved_input_unchanged"] = before == sha(board)
        record["original_configuration_changed"] = (
            sha(CONFIG / "kicad_common.json") != record["copied_existing_configuration_sha256"]
        )
        (root / "summary.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": record["status"], "output": str(root)}))


if __name__ == "__main__":
    main()
