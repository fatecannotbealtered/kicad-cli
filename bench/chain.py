"""Run the whole chain on one specification and record what came out.

A number is only worth quoting if someone else can make it again. The figures
this project has leaned on -- HPWL 404.5 -> 176.3 mm, twenty silkscreen
warnings to none, 23 vias down to 11 -- came from a 15-part ATmega328P board
whose specification was never checked in, so none of them can be reproduced,
and a change that made the chain worse would have nothing to be measured
against. This is that measurement, kept in the repository.

It drives the CLI exactly as the Skill tells an agent to ("Building a board
from nothing"): every write is a dry run, then the same command with the token
it returned. Nothing is called that an agent could not call.

    python bench/chain.py bench/specs/atmega328p.json --work <scratch dir>

prints one JSON document: every step with its outcome and time, and the board
the chain ended with, measured by the tool's own read commands. The work
directory is left in place so the board can be opened and looked at, which
finds things no metric does.

This is a development benchmark of command outcomes and the custom file
checker. It does not supply an independent design-graph or native acceptance.
Migrated tasks need an existing user-granted KICAD_CLI_SCOPE_FILE covering the
work/output directories; this benchmark never grants scope by itself.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def cli(argv: list[str]) -> dict:
    """One invocation of the source tree, parsed. Never raises on ok:false."""
    env = dict(os.environ, KICAD_CLI_STRICT="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "kicad_cli.main", *argv, "--compact"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {
            "ok": False,
            "error": {
                "code": "BENCH_UNPARSEABLE",
                "message": proc.stdout[-2000:] or proc.stderr[-2000:],
            },
        }


def confirmed(argv: list[str]) -> dict:
    plan = cli([*argv, "--dry-run"])
    data = plan.get("data") or {}
    if plan.get("ok") and data.get("confirm_token"):
        return cli([*argv, "--confirm", data["confirm_token"]])
    error = plan.get("error") or {}
    if error.get("code") != "E_CONFIRMATION_REQUIRED":
        return plan
    return cli([*argv, "--confirm", error["details"]["confirm_token"]])


def pick(data: dict, keys: tuple[str, ...]) -> dict:
    return {k: data.get(k) for k in keys if k in data}


class Chain:
    def __init__(self) -> None:
        self.steps: list[dict] = []

    def step(self, name: str, argv: list[str], keep: tuple[str, ...], write: bool = True) -> dict:
        started = time.perf_counter()
        doc = confirmed(argv) if write else cli(argv)
        record = {
            "step": name,
            "ok": doc.get("ok"),
            "seconds": round(time.perf_counter() - started, 1),
        }
        if doc.get("ok"):
            record["data"] = pick(doc.get("data") or {}, keep)
            receipt = (doc.get("data") or {}).get("receipt")
            if receipt:
                record["receipt"] = {
                    "operation_id": receipt["operation_id"],
                    "outcome": receipt["outcome"],
                    "quality": receipt.get("extensions", {}).get("quality"),
                    "coverage": receipt.get("extensions", {}).get("coverage"),
                }
        else:
            error = doc.get("error") or {}
            record["error"] = {"code": error.get("code"), "message": error.get("message")}
        self.steps.append(record)
        print(
            f"  {name}: {'ok' if doc.get('ok') else record['error']['code']} "
            f"({record['seconds']} s)",
            file=sys.stderr,
        )
        return doc


def run(spec: Path, work: Path, width_mm: float, power_nets: str) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    name = spec.stem
    board = work / f"{name}.kicad_pcb"
    chain = Chain()

    created = chain.step(
        "sch create",
        ["sch", "create", "--spec", str(spec), "--out", str(work)],
        ("drawing",),
    )
    if not created.get("ok"):
        return {"spec": spec.name, "result": {"every_step_ok": False}, "steps": chain.steps}
    netlist = created["data"]["written"]["netlist"]
    chain.steps[-1]["data"]["parts"] = len(created["data"].get("parts") or [])

    chain.step(
        "board from-netlist",
        ["board", "from-netlist", "--netlist", netlist, "--out", str(board)],
        ("footprints", "nets", "pads_connected", "board_mm"),
    )
    chain.step(
        "board footprint place",
        ["board", "footprint", "place", "--board", str(board)],
        (
            "improved",
            "moved",
            "hpwl_before_mm",
            "hpwl_after_mm",
            "hpwl_reduction_pct",
            "courtyard_clash",
            "verify",
        ),
    )
    zone = chain.step(
        "board zone create",
        ["board", "zone", "create", "--board", str(board), "--net", "GND", "--layer", "B.Cu"],
        ("zones", "verification"),
    )
    if not zone.get("ok"):
        return {"spec": spec.name, "result": {"every_step_ok": False}, "steps": chain.steps}
    chain.step(
        "board zone refill",
        [
            "board",
            "zone",
            "refill",
            "--board",
            str(board),
            "--zones",
            zone["data"]["zones"][0]["uuid"],
        ],
        ("zones", "verification"),
    )
    route_keep = (
        "engine",
        "routed",
        "failed",
        "vias",
        "track_len_mm",
        "unconnected_before",
        "unconnected_after",
        "plane_served",
        "verify",
    )
    routed = chain.step(
        "board route rebuild --use-planes",
        ["board", "route", "rebuild", "--board", str(board), "--use-planes"],
        route_keep,
    )
    route_attempt = chain.steps[-1]
    error = routed.get("error") or {}
    if (
        error.get("code") == "E_INTEGRITY"
        and (error.get("details") or {}).get("write_state") == "rolled_back"
    ):
        recovered = chain.step(
            "board route rebuild",
            ["board", "route", "rebuild", "--board", str(board)],
            route_keep,
        )
        if recovered.get("ok"):
            route_attempt["superseded_by"] = "board route rebuild"
    chain.step(
        "project netclass set",
        [
            "project",
            "netclass",
            "set",
            "--board",
            str(board),
            "--name",
            "Power",
            "--width",
            str(width_mm),
            "--nets",
            power_nets,
        ],
        ("netclass", "assigned", "ampacity_a"),
    )
    chain.step(
        "board route rewidth",
        ["board", "route", "rewidth", "--board", str(board), "--classes", "Power"],
        ("rewidth_ok", "rewidth_total", "rewidth_reverted", "unconnected_after", "verify"),
    )
    chain.step(
        "board silkscreen place",
        ["board", "silkscreen", "place", "--board", str(board)],
        ("references", "crowded", "moved", "stuck", "max_move_mm", "mean_move_mm", "verify"),
    )

    # The finished board, measured by the tool's own read commands.
    drc = chain.step(
        "board check",
        ["board", "check", "--board", str(board)],
        ("counts", "unconnected_count", "ok_to_fabricate", "violations_total"),
        write=False,
    )
    audit = chain.step(
        "board analyze ampacity",
        ["board", "analyze", "ampacity", "--board", str(board)],
        (
            "copper_mm",
            "copper_by_layer_mm",
            "track_count",
            "via_count",
            "width_compliance",
            "summary",
        ),
        write=False,
    )
    plane = chain.step(
        "board analyze reference-plane",
        ["board", "analyze", "reference-plane", "--board", str(board)],
        (
            "samples",
            "backed_fraction",
            "segments_without_reference",
            "segments_crossing_split",
            "status",
        ),
        write=False,
    )

    fab = work / "fab"
    plotted = chain.step(
        "fab gerber", ["fab", "gerber", "--board", str(board), "--out", str(fab / "gerber")], ()
    )
    if plotted.get("ok"):
        # Explicit expectation and observed files remain separate measurements.
        receipt = plotted["data"].get("receipt") or {}
        extension = receipt.get("extensions") or {}
        chain.steps[-1]["data"] = {
            "requested_layers": (extension.get("expected") or {}).get("layers"),
            "observed_formats": [f.get("format") for f in extension.get("readback") or []],
            "artifact_count": len(receipt.get("artifacts") or []),
        }
    chain.step(
        "fab drill",
        ["fab", "drill", "--board", str(board), "--out", str(fab / "drill")],
        ("receipt",),
    )

    def data(doc: dict) -> dict:
        return doc.get("data") or {} if doc.get("ok") else {}

    d, a, p = data(drc), data(audit), data(plane)
    counts = d.get("counts") or {}
    return {
        "spec": spec.name,
        "result": {
            "every_step_ok": all(s["ok"] or s.get("superseded_by") for s in chain.steps),
            "native_acceptance": "not_measured",
            "drc_errors": counts.get("error", 0) if d else None,
            "drc_warnings": counts.get("warning", 0) if d else None,
            "unconnected": d.get("unconnected_count"),
            "ok_to_fabricate": d.get("ok_to_fabricate"),
            "copper_mm": a.get("copper_mm"),
            "copper_by_layer_mm": a.get("copper_by_layer_mm"),
            "vias": a.get("via_count"),
            "tracks": a.get("track_count"),
            "width_compliance": a.get("width_compliance"),
            "backed_fraction": p.get("backed_fraction"),
        },
        "steps": chain.steps,
    }


SPREAD = (
    "drc_errors",
    "drc_warnings",
    "unconnected",
    "copper_mm",
    "vias",
    "tracks",
    "backed_fraction",
)


def provenance() -> dict:
    """What produced these numbers: the tree, and the KiCad it ran against."""

    def git(*argv: str) -> str:
        proc = subprocess.run(["git", *argv], cwd=REPO, capture_output=True, text=True)
        return proc.stdout.strip()

    context = cli(["context"]).get("data") or {}
    return {
        "commit": git("rev-parse", "--short=12", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--", "kicad_cli")),
        "kicad_version": (context.get("config") or {}).get("kicad_version"),
        "platform": sys.platform,
    }


def main() -> int:
    # The report carries whatever text the tool emitted; on a zh-CN console the
    # default stdout encoding would garble it on the way out.
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", type=Path)
    parser.add_argument(
        "--work",
        type=Path,
        required=True,
        help="scratch directory; kept afterwards so the board can be inspected",
    )
    parser.add_argument("--power-nets", default="+5V,VIN")
    parser.add_argument("--width", type=float, default=0.5, help="Power netclass track width, mm")
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help="repeat the whole chain; the spread is part of the result",
    )
    parser.add_argument("--out", type=Path, help="also write the JSON here")
    args = parser.parse_args()

    # One run is not a measurement when the tool is not deterministic, and the
    # SWIG-era router is not: equal-length paths are chosen in an order that
    # depends on object identity, so two runs of one specification can end
    # with different boards -- and on this one, with rewidth succeeding in one
    # and rolling back in the other. Report every run and the range.
    reports = []
    for index in range(args.runs):
        work = args.work.resolve() / f"run{index + 1}" if args.runs > 1 else args.work.resolve()
        print(f"run {index + 1}/{args.runs}", file=sys.stderr)
        reports.append(run(args.spec.resolve(), work, args.width, args.power_nets))

    results = [r["result"] for r in reports]
    spread = {}
    for key in SPREAD:
        values = [r.get(key) for r in results if r.get(key) is not None]
        if values:
            spread[key] = [min(values), max(values)]
    document = {
        "spec": args.spec.name,
        "provenance": provenance(),
        "runs": args.runs,
        "every_run_ok": all(r.get("every_step_ok") for r in results),
        "spread": spread,
        "results": results,
        "steps": [r["steps"] for r in reports],
    }
    text = json.dumps(document, indent=2, ensure_ascii=False)
    if args.out:
        # LF on every platform, so a baseline recorded on Windows diffs cleanly.
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0 if document["every_run_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
