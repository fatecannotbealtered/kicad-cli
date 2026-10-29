"""Packaged, read-only C10 consumers and fresh full-view production.

The caller supplies the preparation guard. No product module is imported and no
schematic or board is written here. Historical qualification is reused only
after exact method/runtime matching; all engineering outputs are fresh.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .identity import file_identity, verify_aliases

VIEWS = (
    ("front", ("F.Cu", "F.SilkS", "F.Fab", "Edge.Cuts"), False),
    ("back", ("B.Cu", "B.SilkS", "B.Fab", "Edge.Cuts"), True),
    ("fab-values", ("F.Fab", "Edge.Cuts"), False),
    ("front-silk", ("F.SilkS", "Edge.Cuts"), False),
    ("back-silk", ("B.SilkS", "Edge.Cuts"), True),
    ("front-mask", ("F.Mask", "Edge.Cuts"), False),
    ("back-mask", ("B.Mask", "Edge.Cuts"), True),
    ("front-copper-silk", ("F.Cu", "F.SilkS", "Edge.Cuts"), False),
    ("back-copper-silk", ("B.Cu", "B.SilkS", "Edge.Cuts"), True),
    ("front-copper", ("F.Cu", "Edge.Cuts"), False),
    ("back-copper", ("B.Cu", "Edge.Cuts"), True),
)
STEM = "hier_dual_rc"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def ident(path: str | Path) -> dict[str, Any]:
    path = Path(path).absolute()
    row = file_identity(path)
    return {"path": str(path), "sha256": row["sha256"], "size": row["size"]}


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def qualify_runtime(
    package: Path,
    paths: Mapping[str, str] | None = None,
    aliases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Bind historical qualification to current disk files, including aliases.

    Explicit path mappings relocate external dependencies. Prefix removal is
    never used as alias evidence. Every extended path must have a current
    file-ID/size/SHA proof supplied in aliases, even on the original machine.
    """
    root = package / "trial_tools/resources/reading-tools"
    paths = paths or {}
    runtime, system = load(root / "RUNTIME-123.json"), load(root / "SYSTEM-52.json")
    proofs = verify_aliases(aliases or [])
    proved = {str(item["path"]): row["canonical"] for row in proofs for item in row["aliases"]}
    current = []
    for row in [runtime["node"], *runtime["files"], *system["files"]]:
        old = row["path"]
        actual = paths.get(old, old)
        if old.startswith("\\\\?\\") or str(actual).startswith("\\\\?\\"):
            require(
                str(actual) in proved,
                "Current extended renderer alias proof missing: " + str(actual),
            )
        observed = ident(actual)
        require(
            all(observed[key] == row[key] for key in ("sha256", "size")),
            "Historical renderer provider bytes differ: " + str(actual),
        )
        current.append(observed)
    origin = load(root / "ORIGINS.json")
    for row in origin["members"]:
        actual = ident(root / row["new_method"])
        require(
            all(actual[key] == row["original"][key] for key in ("sha256", "size")),
            "Historical method qualification differs: " + row["new_method"],
        )
    node = ident(paths.get(runtime["node"]["path"], runtime["node"]["path"]))
    for name, method, expected_status in (
        ("pcb", "render_pcb.cjs", "passed_bounded_host_renderer_fixture"),
        ("pages", "render_pages.cjs", "passed_bounded_native_page_renderer"),
    ):
        qualification = load(root / (name + "-qualification.json"))
        require(
            qualification["method"]["sha256"] == ident(root / method)["sha256"],
            "Qualification method digest differs",
        )
        require(
            qualification["node"]["sha256"] == node["sha256"]
            and qualification["qualification"]["status"] == expected_status,
            "Qualification runtime or bounded result differs",
        )
    return {
        "schema": "trial-current-reading-runtime/v1",
        "node": node,
        "sharp_entry": paths.get(runtime["sharp_entry"], runtime["sharp_entry"]),
        "runtime_files": current[1 : 1 + len(runtime["files"])],
        "system_files": current[1 + len(runtime["files"]) :],
        "aliases": proofs,
        "qualification": "exact historical bounded Host qualification reused",
        "limits": "Serial disk identity/hash; no per-load bytes or full font qualification.",
    }


def _warmup_runtime(ready) -> dict[str, Any]:
    request = ready.record["request"]
    declared = request["config"].get("renderer_runtime")
    require(
        request["config"].get("renderer_warmup") is True and isinstance(declared, dict),
        "Packaged renderer preparation must be prospectively enabled and qualified",
    )
    require(
        declared.get("schema") == "trial-current-reading-runtime/v1",
        "Prospective renderer runtime schema differs",
    )
    # Public complete_request already established historical method/runtime
    # qualification before Native. Recheck that same current provider/alias set
    # here; do not discover a different runtime during preparation.
    require(
        verify_aliases(request["dependencies"].get("aliases", [])) == declared["aliases"],
        "Prospectively declared renderer alias proof changed",
    )
    for row in [declared["node"], *declared["runtime_files"], *declared["system_files"]]:
        require(
            str(Path(row["path"]).absolute()) in ready.record["dependencies"]["files"],
            "Renderer preparation provider was not frozen before Native initialization",
        )
        require(ident(row["path"]) == row, "Prospective renderer provider changed")
    reading = ready.package_root / "trial_tools/resources/reading-tools"
    qualification = load(reading / "pcb-qualification.json")
    require(
        qualification["method"]["sha256"] == ident(reading / "render_pcb.cjs")["sha256"]
        and qualification["node"]["sha256"] == declared["node"]["sha256"]
        and qualification["qualification"]["status"] == "passed_bounded_host_renderer_fixture",
        "Prospective renderer qualification method/runtime binding differs",
    )
    return declared


def renderer_warmup_declaration(ready, profile: str) -> dict[str, Any]:
    """Declare Host tasks for one existing sacrificial SVG; launch no process.

    Core runs these tasks once under its before/after guards. The two later Node
    rounds reuse these same source/proof/check bytes and differ only in fresh
    output locations. These files are preparation artifacts, never acceptance.
    """
    require(profile in ("writer", "consumer"), "Unknown renderer preparation profile")
    runtime = _warmup_runtime(ready)
    reading = ready.package_root / "trial_tools/resources/reading-tools"
    raw = ready.work_root / "output/preparation" / profile / "pcb.svg"
    source = ident(raw)
    root = ready.work_root / "output/preparation" / profile / "renderer-warmup"
    root.mkdir(exist_ok=False)
    derivative, proof = root / "reading.svg", root / "PROOF.json"
    derived, checked = root / "DERIVE-CHECK.json", root / "GEOMETRY-CHECK.json"
    host = ready.record["dependencies"]["paths"]["host_python"]
    script = [host, *ready.python_flags(profile), str(reading / "svg_reading.py")]
    common = ["--raw", str(raw), "--derivative", str(derivative), "--proof", str(proof)]
    declaration = {
        "schema": "trial-sacrificial-renderer-preparation/v1",
        "profile": profile,
        "preparation_only": True,
        "engineering_acceptance": False,
        "actual_view_performed": False,
        "directory": str(root),
        "source_svg": source,
        "runtime": runtime,
        "derivative": str(derivative),
        "proof": str(proof),
        "derive_check": str(derived),
        "geometry_check": str(checked),
        "python_flags": ready.python_flags(profile),
        "layers": ["F.Cu", "B.Cu", "F.SilkS", "B.SilkS", "Edge.Cuts"],
        "mirrored": False,
        "host_tasks": [
            {
                "name": "renderer-derive",
                "argv": [*script, "derive", *common, "--out", str(derived)],
                "expected_outputs": list(map(str, (derivative, proof, derived))),
            },
            {
                "name": "renderer-geometry",
                "argv": [*script, "check", *common, "--out", str(checked)],
                "expected_outputs": [str(checked)],
            },
        ],
    }
    write_new(root / "DECLARATION.json", declaration)
    return declaration


def _warmup_geometry(declaration) -> list[dict[str, Any]]:
    raw, derivative = ident(declaration["source_svg"]["path"]), ident(declaration["derivative"])
    require(raw == declaration["source_svg"], "Sacrificial native SVG changed")
    proof = load(Path(declaration["proof"]))
    require(
        proof["raw_sha256"] == raw["sha256"] and proof["derivative_sha256"] == derivative["sha256"],
        "Preparation derivative/proof input binding differs",
    )
    checks = []
    for key in ("derive_check", "geometry_check"):
        report = load(Path(declaration[key]))
        require(
            report["schema"] == "finite-PCB-reading-check/v1"
            and report["status"] == "geometry_preserved_pending_actual_view"
            and report["raw_sha256"] == raw["sha256"]
            and report["derivative_sha256"] == derivative["sha256"]
            and report["all_original_elements_preserved"] is True
            and report["every_primitive_inside_reading_viewport"] is True
            and report["margin_mm"] == "2"
            and report["geometry_or_opacity_edits"] == 0
            and report["actual_view_performed"] is False,
            "Preparation SVG geometry check is incomplete or failed",
        )
        checks.append(ident(declaration[key]))
    return checks


def build_renderer_warmup_node_plan(ready, declaration, round_name: str) -> dict[str, Any]:
    """Mechanically bind completed Host outputs to one fresh Node render."""
    require(round_name in ("initial", "stability"), "Unsupported renderer preparation round")
    runtime = _warmup_runtime(ready)
    require(runtime == declaration["runtime"], "Preparation renderer runtime drifted")
    root = Path(declaration["directory"])
    require(load(root / "DECLARATION.json") == declaration, "Warmup declaration changed")
    _warmup_geometry(declaration)
    reading = ready.package_root / "trial_tools/resources/reading-tools"
    directory = root / round_name
    (directory / "render/images").mkdir(parents=True, exist_ok=False)
    source = Path(declaration["source_svg"]["path"])
    files = [
        source,
        Path(declaration["derivative"]),
        Path(declaration["proof"]),
        Path(declaration["derive_check"]),
        Path(declaration["geometry_check"]),
        root / "DECLARATION.json",
        reading / "render_pcb.cjs",
        reading / "svg_reading.py",
        reading / "pcb-qualification.json",
        *sorted(ready.preparation_project.iterdir()),
    ]
    plan = {
        "directory": str(directory / "render"),
        "node": runtime["node"],
        "sharp_entry": runtime["sharp_entry"],
        "runtime_files": runtime["runtime_files"],
        "fixed_files": [ident(path) for path in files] + list(runtime["system_files"]),
        "density": 576,
        "qualification": ident(reading / "pcb-qualification.json"),
        "views": [
            {
                "name": "warmup",
                "layers": declaration["layers"],
                "mirrored": False,
                "native": ident(source),
                "derivative": ident(declaration["derivative"]),
                "check": ident(declaration["geometry_check"]),
            }
        ],
    }
    plan_path = directory / "PLAN.json"
    write_new(plan_path, plan)
    task = {
        "name": "renderer-node-" + round_name,
        "round": round_name,
        "preparation_only": True,
        "plan": ident(plan_path),
        "declaration": ident(root / "DECLARATION.json"),
        "argv": [
            runtime["node"]["path"],
            str(reading / "render_pcb.cjs"),
            str(plan_path),
            ident(plan_path)["sha256"],
            "render-bound",
        ],
        "expected_outputs": [
            str(directory / "render/images/warmup.png"),
            str(directory / "render/RESULT.json"),
        ],
    }
    return task


def validate_renderer_warmup(ready, declaration, node_plan) -> dict[str, Any]:
    """Validate bound PNG/result metadata; Core independently owns Job evidence."""
    runtime = _warmup_runtime(ready)
    checks = _warmup_geometry(declaration)
    root = Path(declaration["directory"])
    require(
        load(root / "DECLARATION.json") == declaration
        and ident(node_plan["plan"]["path"]) == node_plan["plan"]
        and ident(root / "DECLARATION.json") == node_plan["declaration"],
        "Preparation render plan/declaration binding changed",
    )
    plan = load(Path(node_plan["plan"]["path"]))
    result_path = Path(plan["directory"]) / "RESULT.json"
    result = load(result_path)
    require(
        result["schema"] == "finite-sharp-host-renderer/v1"
        and result["phase"] == "render-bound"
        and result["plan_sha256"] == node_plan["plan"]["sha256"]
        and result["node"] == runtime["node"]
        and result["method"]
        == ident(ready.package_root / "trial_tools/resources/reading-tools/render_pcb.cjs")
        and result["child_dispatches"] == 0
        and result["KiCad_Native_calls"] == 0
        and result["formal_engineering_writes"] == 0
        and len(result["outputs"]) == 1,
        "Preparation Node render result is incomplete or bound to different inputs",
    )
    output = result["outputs"][0]
    view = plan["views"][0]
    image = Path(plan["directory"]) / "images/warmup.png"
    require(
        output["name"] == "warmup"
        and output["native"] == view["native"]
        and output["derivative"] == view["derivative"]
        and output["independent_geometry_check"] == view["check"]
        and output["layers"] == view["layers"]
        and output["mirrored"] is False
        and output["output"] == ident(image)
        and output["density"] == 576
        and output["channels"] == 4,
        "Preparation PNG metadata binding differs",
    )
    png = image.read_bytes()
    require(
        len(png) >= 33
        and png[:8] == b"\x89PNG\r\n\x1a\n"
        and png[12:16] == b"IHDR"
        and int.from_bytes(png[16:20], "big") == output["width"]
        and int.from_bytes(png[20:24], "big") == output["height"]
        and 0 < output["width"] * output["height"] <= 20_000_000,
        "Preparation PNG framing/dimensions differ",
    )
    for row in [*plan["runtime_files"], *plan["fixed_files"]]:
        require(ident(row["path"]) == row, "Preparation render fixed input/provider changed")
    return {
        "schema": "trial-renderer-preparation-result/v1",
        "profile": declaration["profile"],
        "round": node_plan["round"],
        "status": "passed_preparation_only",
        "preparation_only": True,
        "engineering_acceptance": False,
        "actual_view_performed": False,
        "plan": node_plan["plan"],
        "declaration": node_plan["declaration"],
        "result": ident(result_path),
        "image": ident(image),
        "geometry_checks": checks,
        "limit": "Sacrificial cache initialization and stability; not candidate or final review.",
    }


class Readers:
    def __init__(
        self,
        package: Path,
        work: Path,
        dependencies: Mapping[str, Any],
        runtime: Mapping[str, Any],
        run: Callable[..., Any],
        bind: Callable[..., Any],
        *,
        python_flags: Sequence[str],
    ):
        self.package, self.work, self.dependencies = package, work, dependencies
        self.runtime, self.run, self.bind = runtime, run, bind
        self.python_flags = tuple(python_flags)
        self.methods = package / "physical-footprint-eco/methods"
        self.resources = package / "trial_tools/resources"
        self.reading = self.resources / "reading-tools"

    def _new_json(self, path: Path, value: Any) -> None:
        write_new(path, value)
        self.bind(str(path), path)

    def _stdout(self, result: Mapping[str, Any], destination: Path) -> None:
        # The guard's observer owns its raw stdout. Preserve exact bytes without
        # interpreting a diagnostic stream as a successful report.
        source = Path(result["stdout_path"])
        with destination.open("xb") as stream:
            stream.write(source.read_bytes())
        self.bind(str(destination), destination)

    def checks(self, stage: str, project: Path) -> dict[str, Any]:
        out = self.work / "output" / stage / "checks"
        out.mkdir(parents=True, exist_ok=False)
        board, sch = project / (STEM + ".kicad_pcb"), project / (STEM + ".kicad_sch")
        pro = project / (STEM + ".kicad_pro")
        base = self.methods / "requirements/BASELINE-INPUTS.json"
        intent = self.methods / "requirements/PHYSICAL-PIN-INTENT42.json"
        native = self.dependencies["native_python"]
        official = self.dependencies["official_cli"]
        host = self.dependencies["host_python"]
        hole_intent = load(self.resources / "HOLE-INTENT.json")
        # Only mechanical identity slots are filled after candidate publication.
        hole_intent["input_PCB"], hole_intent["input_PRO"] = ident(board), ident(pro)
        self._new_json(out / "HOLE-INTENT.json", hole_intent)
        result = self.run(
            stage + "-native",
            [
                native,
                *self.python_flags,
                self.methods / "saved_board_native_read.py",
                board,
                base,
                intent,
            ],
        )
        self._stdout(result, out / "native.json")
        for name, args in (
            (
                "xml",
                [
                    "sch",
                    "export",
                    "netlist",
                    "--format",
                    "kicadxml",
                    "--output",
                    out / "official.xml",
                    sch,
                ],
            ),
            (
                "erc",
                [
                    "sch",
                    "erc",
                    "--format",
                    "json",
                    "--severity-all",
                    "--output",
                    out / "erc.json",
                    sch,
                ],
            ),
            (
                "drc",
                [
                    "pcb",
                    "drc",
                    "--format",
                    "json",
                    "--severity-all",
                    "--schematic-parity",
                    "--output",
                    out / "drc.json",
                    board,
                ],
            ),
        ):
            expected = out / ("official.xml" if name == "xml" else name + ".json")
            self.run(stage + "-" + name, [official, *args], expected_outputs=[expected])
        result = self.run(
            stage + "-hole",
            [
                native,
                *self.python_flags,
                self.resources / "hole/native_hole_read_02.py",
                "--board",
                board,
                "--intent",
                out / "HOLE-INTENT.json",
            ],
        )
        self._stdout(result, out / "hole.json")
        hole = load(out / "hole.json")
        pads, vias = {x["uuid"] for x in hole["pads"]}, {x["uuid"] for x in hole["vias"]}
        pairs = {(x["via_uuid"], x["pad_uuid"], x["layer"]) for x in hole["pairs"]}
        require(
            hole["status"] == "observed" and not hole["incomplete_reasons"], "Hole read incomplete"
        )
        require(
            len(pads) == 42
            and len(vias) == 12
            and len(hole["pads"]) == 42
            and len(hole["vias"]) == 12
            and len(hole["pairs"]) == 504,
            "Hole object/pair count differs",
        )
        require(
            pairs == {(v, p, "F.Cu") for v, p in itertools.product(vias, pads)},
            "Hole pair matrix incomplete or duplicate",
        )
        require(
            all(
                not x["native_hole_intersects_material"]
                and not x["native_hole_with_margin_intersects_material"]
                and x["collision_query_clearance_nm"] == 200001
                for x in hole["pairs"]
            ),
            "Hole material collision or incorrect query margin",
        )
        require(
            hole["input_sha256"] == ident(board)["sha256"]
            and hole["project_sha256"] == ident(pro)["sha256"]
            and hole["input_unchanged"]
            and hole["project_unchanged"],
            "Hole input binding differs",
        )
        self._new_json(
            out / "HOLE-ACCEPTANCE.json",
            {
                "schema": "trial-finite-hole-acceptance/v1",
                "status": "passed",
                "report": ident(out / "hole.json"),
                "pads": 42,
                "vias": 12,
                "unique_pairs": 504,
                "minimum_material_gap_nm": 200000,
                "native_query_nm": 200001,
                "limit": "Finite F.Cu SMD/through-via material test; no manufacturing approval.",
            },
        )
        self.run(
            stage + "-host-check",
            [
                host,
                *self.python_flags,
                self.methods / "check_footprint_eco.py",
                "--current-dir",
                project,
                "--native",
                out / "native.json",
                "--xml",
                out / "official.xml",
                "--erc",
                out / "erc.json",
                "--drc",
                out / "drc.json",
                "--output",
                out / "CHECK.json",
            ],
            expected_outputs=[out / "CHECK.json"],
        )
        require(
            load(out / "CHECK.json")["status"] == "passed", "Finite physical C10 acceptance failed"
        )
        return {"physical": ident(out / "CHECK.json"), "hole": ident(out / "HOLE-ACCEPTANCE.json")}

    def _plan(
        self,
        path: Path,
        directory: Path,
        views: list[dict[str, Any]],
        fixed: list[Path],
        density: int,
        qualification: str,
        **extra: Any,
    ) -> dict[str, Any]:
        plan = {
            "directory": str(directory),
            "node": self.runtime["node"],
            "sharp_entry": self.runtime["sharp_entry"],
            "runtime_files": self.runtime["runtime_files"],
            "fixed_files": [ident(x) for x in fixed] + list(self.runtime["system_files"]),
            "density": density,
            "qualification": ident(self.reading / qualification),
            "views": views,
            **extra,
        }
        self._new_json(path, plan)
        return plan

    def pcb_images(self, stage: str, project: Path, *, final: bool = False) -> list[dict[str, Any]]:
        out = self.work / "output" / stage / "pcb"
        for name in ("native", "derivative", "render/images"):
            (out / name).mkdir(parents=True, exist_ok=False)
        rows, fixed = [], []
        views = VIEWS if final else VIEWS[:7]
        board = project / (STEM + ".kicad_pcb")
        for name, layers, mirrored in views:
            raw, derivative = out / "native" / (name + ".svg"), out / "derivative" / (name + ".svg")
            proof = out / "derivative" / (name + "-proof.json")
            derive = out / "derivative" / (name + "-derive.json")
            check = out / "derivative" / (name + "-check.json")
            args = [
                self.dependencies["official_cli"],
                "pcb",
                "export",
                "svg",
                "--output",
                raw,
                "--layers",
                ",".join(layers),
                "--mode-single",
                "--page-size-mode",
                "1",
                "--exclude-drawing-sheet",
            ]
            if mirrored:
                args.append("--mirror")
            self.run(stage + "-svg-" + name, [*args, board], expected_outputs=[raw])
            script = [
                self.dependencies["host_python"],
                *self.python_flags,
                self.reading / "svg_reading.py",
            ]
            self.run(
                stage + "-derive-" + name,
                [
                    *script,
                    "derive",
                    "--raw",
                    raw,
                    "--derivative",
                    derivative,
                    "--proof",
                    proof,
                    "--out",
                    derive,
                ],
                expected_outputs=[derivative, proof, derive],
            )
            self.run(
                stage + "-geometry-" + name,
                [
                    *script,
                    "check",
                    "--raw",
                    raw,
                    "--derivative",
                    derivative,
                    "--proof",
                    proof,
                    "--out",
                    check,
                ],
                expected_outputs=[check],
            )
            result = load(check)
            require(
                result["status"] == "geometry_preserved_pending_actual_view"
                and result["all_original_elements_preserved"]
                and result["every_primitive_inside_reading_viewport"]
                and result["geometry_or_opacity_edits"] == 0,
                "Fresh SVG geometry check failed",
            )
            rows.append(
                {
                    "name": name,
                    "layers": list(layers),
                    "mirrored": mirrored,
                    "native": ident(raw),
                    "derivative": ident(derivative),
                    "check": ident(check),
                }
            )
            fixed.extend((raw, derivative, proof, derive, check))
        fixed.extend(
            [
                self.reading / "render_pcb.cjs",
                self.reading / "svg_reading.py",
                self.reading / "pcb-qualification.json",
                *sorted(project.iterdir()),
            ]
        )
        plan = out / "PLAN.json"
        self._plan(plan, out / "render", rows, fixed, 576, "pcb-qualification.json")
        images = [out / "render/images" / (x[0] + ".png") for x in views]
        self.run(
            stage + "-pcb-render",
            [
                self.runtime["node"]["path"],
                self.reading / "render_pcb.cjs",
                plan,
                ident(plan)["sha256"],
                "render-bound",
            ],
            expected_outputs=[*images, out / "render/RESULT.json"],
        )
        return [ident(x) for x in images]

    def schematic_images(self, stage: str, project: Path) -> list[dict[str, Any]]:
        out = self.work / "output" / stage / "schematic"
        for name in ("native", "render/images"):
            (out / name).mkdir(parents=True, exist_ok=False)
        self.run(
            stage + "-sch-svg",
            [
                self.dependencies["official_cli"],
                "sch",
                "export",
                "svg",
                "--output",
                out / "native",
                project / (STEM + ".kicad_sch"),
            ],
        )
        svg_files = sorted((out / "native").glob("*.svg"))
        require(len(svg_files) == 3, "Expected three freshly exported full schematic pages")
        for path in svg_files:
            self.bind(str(path), path)
        report = out / "PAGE-IDENTITY.json"
        self.run(
            stage + "-page-identity",
            [
                self.dependencies["host_python"],
                *self.python_flags,
                self.reading / "svg_page_identity.py",
                "--spec",
                self.reading / "hier_dual_rc.json",
                "--intent",
                self.reading / "FROZEN-INTENT-01.json",
                "--project",
                project,
                "--svgs",
                out / "native",
                "--out",
                report,
            ],
            expected_outputs=[report],
        )
        result = load(report)
        require(
            result["status"] == "passed" and set(result["pages"]) == {"root", "A", "B"},
            "Fresh Native page identities failed",
        )
        rows = [
            {
                "name": name,
                "native": ident(result["pages"][name]["source_svg"]["path"]),
                "derivative": ident(result["pages"][name]["source_svg"]["path"]),
                "check": ident(report),
            }
            for name in ("root", "A", "B")
        ]
        plan = out / "PLAN.json"
        fixed = [
            self.reading / "render_pages.cjs",
            self.reading / "pages-qualification.json",
            self.reading / "hier_dual_rc.json",
            self.reading / "FROZEN-INTENT-01.json",
            report,
            *svg_files,
            *sorted(project.iterdir()),
        ]
        self._plan(
            plan,
            out / "render",
            rows,
            fixed,
            288,
            "pages-qualification.json",
            page_identity=ident(report),
            caller_additional_inputs=[ident(self.work / "intent/PLAN.json")],
        )
        images = [out / "render/images" / (x + ".png") for x in ("root", "A", "B")]
        self.run(
            stage + "-page-render",
            [
                self.runtime["node"]["path"],
                self.reading / "render_pages.cjs",
                plan,
                ident(plan)["sha256"],
                "render-pages",
            ],
            expected_outputs=[*images, out / "render/RESULT.json"],
        )
        return [ident(x) for x in images]
