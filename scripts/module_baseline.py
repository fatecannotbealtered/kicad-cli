"""Preserve local input identities and extract a bounded, owned regression copy."""

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parent / "kicad-modules-validation-20261026"
ZIP = (
    REPO.parent
    / "phone-design-20261025/delivery/compression-trials-r02-lite/phone-a1-review-delivery-r02-lite.zip"
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ROOT.mkdir(exist_ok=False)
    assert sha(ZIP) == "6136585088cc76ebf1f15dc86de6e1141a4e5a3f85c8dde856e2d877fcda4ab1"
    with zipfile.ZipFile(ZIP) as archive:
        assert archive.testzip() is None
        for info in archive.infolist():
            target = (ROOT / "phone-input" / info.filename).resolve()
            assert target.is_relative_to((ROOT / "phone-input").resolve())
            archive.extract(info, ROOT / "phone-input")
    boards = list((ROOT / "phone-input").rglob("PHONE_A1_MAIN.kicad_pcb"))
    assert len(boards) == 1
    assert sha(boards[0]) == "0bc266ab2fc24e353fe62eb590d132bcc00bf87c5d60661f764060bb5860c0e8"
    commands = json.loads((REPO.parent / "kicad-modules-reference-baseline.json").read_bytes())[
        "data"
    ]["commands"]
    record = {
        "baseline": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO)
        .decode()
        .strip(),
        "product_source": "ab283fa4bafe4c1e1505d8a6ec05eedae4ed4190",
        "phone_zip": str(ZIP),
        "phone_zip_sha256": sha(ZIP),
        "phone_board": str(boards[0]),
        "phone_board_sha256": sha(boards[0]),
        "commands": commands,
        "frozen_package_sha256": sha(
            REPO.parent
            / "kicad-first-design-20261024/kicad-internal-design-windows-20261024-05.zip"
        ),
        "unknown_operations": "preserved; not inspected or replayed",
    }
    (ROOT / "BASELINE.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(boards[0])


if __name__ == "__main__":
    main()
