"""Expose existing preparation checks and their limits before an engineering write."""


def feedback(command, result=None):
    result = result or {}
    value = {
        "definite_violations": [],
        "geometry_check": "not_performed",
        "requires_visual_review": [],
        "unsupported": [],
    }
    if command == "sch create":
        measured = result.get("creation_geometry")
        hierarchy = result.get("hierarchy")
        if hierarchy:
            pages = measured.get("pages") if isinstance(measured, dict) else None
            root = hierarchy.get("root") if isinstance(hierarchy, dict) else None
            children = hierarchy.get("children") if isinstance(hierarchy, dict) else None
            declared = (
                [root["file"], *(row["file"] for row in children)]
                if isinstance(root, dict)
                and isinstance(root.get("file"), str)
                and isinstance(children, list)
                and len(children) == 2
                and all(
                    isinstance(row, dict) and isinstance(row.get("file"), str) for row in children
                )
                else []
            )
            expected_files = set(declared)
            complete_report = (
                isinstance(measured, dict)
                and measured.get("schema") == "sch-create-geometry/v2"
                and measured.get("geometry_check")
                in {"partial_conservative_schematic_bounds", "incomplete"}
                and isinstance(pages, list)
                and len(pages) == len(expected_files) == 3
                and all(_created_page_report(page) for page in pages)
                and {page["source"]["file"] for page in pages} == expected_files
                and isinstance(measured.get("findings"), list)
                and isinstance(measured.get("unmeasured_objects"), list)
            )
            if complete_report and any(page["geometry_check"] == "incomplete" for page in pages):
                complete_report = False
        else:
            complete_report = _created_page_report(measured)
        if complete_report:
            value["geometry_check"] = measured["geometry_check"]
            value["creation_geometry"] = measured
            value["geometry_findings"] = measured["findings"]
            value["unmeasured_objects"] = measured["unmeasured_objects"]
            value["_untrusted"] = ["creation_geometry", "geometry_findings", "unmeasured_objects"]
        else:
            value["geometry_check"] = "incomplete"
            value["unmeasured_objects"] = [
                {
                    "kind": "whole_sheet",
                    "reason": (
                        "Created candidate geometry report or its byte binding "
                        "is missing/incomplete"
                    ),
                }
            ]
        value["requires_visual_review"] = [
            "Inspect creation_geometry before confirmation: conservative measured "
            "body/field/label overlaps and page-bound warnings need review. View the "
            "complete candidate and saved schematic; pin-name/number glyphs, native unit "
            "reference suffixes, unmeasured styles/graphics and full readability remain "
            "visual checks."
        ]
        value["unsupported"] = ["Explicit schematic coordinates, angles and manual layout groups"]
        if hierarchy:
            value["requires_visual_review"] = [
                "Inspect all three complete candidate pages before confirmation and all saved "
                "pages after reopening. Check true sheet pins against child hierarchical labels, "
                "readable port names and root wiring, every physical unit and power pin. "
                "Per-page SHA-bound conservative bounds are not native glyph or electrical proof."
            ]
            value["unsupported"] += [
                "Nested/shared child instances, buses and cross-page physical multi-unit parts"
            ]
    elif command == "sch edit":
        if isinstance(result.get("circuit"), dict):
            value.update(
                geometry_check="exact_scalar_construction_expectation_guard",
                affected_edits=result.get("changes", []),
                circuit=result["circuit"],
                unmeasured_objects=result["circuit"].get("not_measured", []),
                requires_visual_review=[
                    "Inspect each exact created/updated/deleted object, attached field, raw pin "
                    "occurrence, merge/split/name/NC expectation and all affected instances. "
                    "Read the public candidate before confirming; view complete candidate and "
                    "saved affected pages. Glyphs, text/body overlap and overall readability "
                    "are not certified by the scalar connection guard."
                ],
                _untrusted=["circuit", "affected_edits", "unmeasured_objects"],
            )
            guard = result["circuit"].get("guard", {})
            if not guard.get("ok", False):
                value["definite_violations"] = guard.get("differences", [])
            return value
        if isinstance(result.get("geometry"), dict):
            value.update(
                geometry_check="exact_orthogonal_occurrence_connection_guard",
                affected_edits=result.get("changes", []),
                geometry=result["geometry"],
                unmeasured_objects=result.get("unmeasured", []),
                requires_visual_review=[
                    "Inspect before/after geometry and fields for every selected source object "
                    "and every disclosed logical instance before confirmation. Symbol fields "
                    "follow their owner, remain hidden/visible as before, and rotate to upright "
                    "0/90 text; labels retain their true electrical orientation. Native glyphs, "
                    "overlaps, page bounds and complete readability remain separate checks.",
                    "View every affected complete candidate and saved page; unchanged pages "
                    "are protected by their captured bytes. No automatic wire expansion, "
                    "disconnect/reconnect, page copying or PCB change is performed.",
                ],
                unsupported=[
                    "Geometry transforms of bus/sheet/graphic/text objects, arbitrary angle "
                    "or new mirror operations; future scope, not full-application coverage."
                ],
            )
            connection = result["geometry"].get("connections", {})
            if not connection.get("ok", False):
                value["definite_violations"] = connection.get("differences", [])
            return value
        value["requires_visual_review"] = [
            "Existing field positions are retained. A longer value or new field can overlap "
            "wires/text; inspect a newly rendered complete schematic before continuing."
        ]
        value["unsupported"] = ["Automatic full-page relayout after field edits"]
        value["affected_edits"] = []
        value["geometry_findings"] = []
        for change in result.get("changes", []):
            operation = change.get("op")
            item = {
                k: change[k]
                for k in (
                    "index",
                    "op",
                    "ref",
                    "pin",
                    "net",
                    "to",
                    "files",
                    "units",
                    "also_added",
                    "also_removes",
                    "also_connects",
                    "also_disconnects",
                    "also_changes",
                    "also_renames",
                    "matched_pins",
                )
                if k in change
            }
            if operation == "add":
                item.update(
                    placement_mm=change.get("placed", []),
                    layout_effect=(
                        "Adds every pin-bearing unit and its fields/wires/labels; "
                        "unspecified visible pins get no-connect markers."
                    ),
                )
            elif operation == "connect":
                item.update(
                    drawn=change.get("drawn", []),
                    layout_effect=(
                        "Adds a short wire and naming marker; "
                        "removes the selected pin's no-connect marker."
                    ),
                )
            elif operation == "rename":
                item["drawn"] = change.get("drawn")
                item["label_layout"] = change.get("label_layout", [])
                item["layout_effect"] = (
                    "Electrical anchors are retained. Plain default-font local labels may "
                    "turn at that anchor to avoid measured conflicts; inspect label_layout. "
                    "Other naming carriers still require visual review. "
                    "An unnamed net may receive a new label."
                )
            elif operation in {"disconnect", "remove"}:
                item["removed_or_moved_counts"] = change.get("removed", {})
                item["layout_effect"] = (
                    "Removes exclusively used drawings; shared labels/power markers may move "
                    "to retained wire ends. Review the edited region and all coupled sheets."
                )
            else:
                item["field_layout"] = change.get("field_layout", [])
                item["layout_effect"] = (
                    "Field positions retained; inspect field_layout for each unit and all "
                    "shared instances. Potential box conflicts are advisory; unmeasured "
                    "styles and the complete native image still require visual review."
                )
                for field in item["field_layout"]:
                    if field["after"]["geometry_check"] == "partial_conservative_schematic_bounds":
                        value["geometry_check"] = "partial_conservative_schematic_bounds"
                        value["geometry_findings"].append(
                            {
                                "field": field["field"],
                                "unit": field["unit"],
                                "symbol_uuid": field["symbol_uuid"],
                                "instances": field["instances"],
                                "status": field["after"]["status"],
                                "conflicts_before": field["before"]["conflicts"],
                                "conflicts_after": field["after"]["conflicts"],
                                "limit": field["after"]["limit"],
                            }
                        )
            value["affected_edits"].append(item)
            # Existing rename reports one new label as an object, whereas
            # connect reports a list of measured wire/marker objects.
            drawn = change.get("drawn", [])
            measures = change.get("placed", []) + (drawn if isinstance(drawn, list) else [drawn])
            measures += change.get("label_layout", [])
            for measure in measures:
                if isinstance(measure, dict) and "overlaps" in measure:
                    value["geometry_check"] = "partial_conservative_schematic_bounds"
                    value["geometry_findings"].append(
                        {
                            "new_object": measure.get(
                                "pin", measure.get("uuid", change.get("ref"))
                            ),
                            "conflicts_before": measure.get("conflicts_before", []),
                            "unit": measure.get("unit"),
                            "at_mm": measure.get("at_mm"),
                            "wire_mm": measure.get("wire_mm"),
                            "overlap_count": measure["overlaps"],
                            "status": "review_overlap"
                            if measure["overlaps"]
                            else "no_overlap_in_measured_bounds",
                            "limit": (
                                "Conservative existing canvas bounds; not full native "
                                "glyph/page validation or a universal geometry rule."
                            ),
                        }
                    )
        if value["affected_edits"]:
            value["requires_visual_review"].append(
                "Render every affected complete sheet again after saving: inspect new/deleted "
                "symbols, wires, labels, no-connect markers and shared-sheet placements. "
                "Do not reuse a previous snapshot's visual approval."
            )
    elif command == "board from-netlist":
        value["prepared_board_size_mm"] = result.get("board_mm")
        value["requires_visual_review"] = [
            "Inspect the provisional rectangular board outline and initial grid placement "
            "against frozen mechanical requirements before routing."
        ]
        value["unsupported"] = [
            "Exact-size/custom board-outline input; resizing after footprint moves"
        ]
    elif command == "board footprint place":
        value["requires_visual_review"] = [
            "Inspect placement against the saved board outline and pad/field spacing. "
            "Existing copper may disconnect when routed footprints are moved."
        ]
    elif command == "board silkscreen place":
        checks = result.get("checks", [])
        explicit = result.get("selection_mode") == "explicit_targets"
        identities = (
            [(row.get("footprint_uuid"), row.get("reference_uuid")) for row in checks]
            if isinstance(checks, list) and all(isinstance(row, dict) for row in checks)
            else []
        )
        complete = (
            isinstance(checks, list)
            and len(checks) == result.get("references")
            and all(
                isinstance(row, dict) and {"ref", "inside_edge", "clear"} <= row.keys()
                for row in checks
            )
            and len({row["ref"] for row in checks}) == len(checks)
            and (
                not explicit
                or (
                    result.get("schema") == "targeted-silkscreen/v1"
                    and result.get("complete") is True
                    and not result.get("stuck")
                    and len(set(identities)) == len(checks)
                    and all(all(isinstance(v, str) and v for v in pair) for pair in identities)
                    and set(identities)
                    == {
                        (row.get("footprint_uuid"), row.get("reference_uuid"))
                        for row in result.get("targets", [])
                    }
                )
            )
        )
        if complete and explicit:
            from .engineering_silk import validate_result

            try:
                validate_result(result, result.get("targets", []))
            except ValueError:
                complete = False
        value["geometry_check"] = "measured_native_rectangular_bounds" if complete else "incomplete"
        if complete:
            value["definite_violations"] = [
                row for row in checks if row["inside_edge"] is not True or row["clear"] is not True
            ]
        value["clearance_mm"] = result.get("clearance_mm")
        if explicit:
            value["selection_mode"] = "explicit_targets"
            value["target_geometry"] = result
            value["_untrusted"] = ["target_geometry"]
        value["requires_visual_review"] = [
            "Rectangular text/mask/graphic checks do not prove readability. "
            "View the complete saved board and exported silk."
        ]
        value["unsupported"] = [
            "True clipping against arbitrary curved/concave outlines; "
            "only the edge bounding rectangle is checked"
        ]
        if explicit:
            value["requires_visual_review"] = [
                "Before confirmation inspect every full candidate face and target region: "
                "same-side native text/mask/graphic bounds are conservative; copper projection "
                "is a reading advisory, not a manufacturing violation. Check each reference "
                "still clearly belongs to its footprint. Render the final saved snapshot anew."
            ]
            value["unsupported"] += [
                "Explicit target mode is F.SilkS, default KiCad stroke font, single-line, "
                "zero-degree nonmirrored text and rectangular board outline only; "
                "maximum translation 5 mm and maximum owner-body gap 3 mm. "
                "No font, size, angle, content, visibility, footprint or copper changes."
            ]
    return value


def _created_page_report(measured):
    source = measured.get("source") if isinstance(measured, dict) else None
    observed = source.get("sha256") if isinstance(source, dict) else None
    return (
        isinstance(measured, dict)
        and measured.get("schema") == "sch-create-geometry/v1"
        and measured.get("geometry_check")
        in {"partial_conservative_schematic_bounds", "incomplete"}
        and isinstance(measured.get("findings"), list)
        and isinstance(measured.get("unmeasured_objects"), list)
        and isinstance(observed, str)
        and len(observed) == 64
        and all(c in "0123456789abcdef" for c in observed)
        and isinstance(source.get("file"), str)
        and bool(source["file"])
    )


def can_apply(value):
    return not value["definite_violations"] and value["geometry_check"] != "incomplete"
