"""Fresh-session scope and exact module compatibility are publicly discoverable."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def public(*args):
    proc = subprocess.run(
        [sys.executable, "-B", str(ROOT / "entry.py"), *args, "--compact"],
        cwd=ROOT,
        capture_output=True,
        timeout=60,
    )
    value = json.loads(proc.stdout)
    assert value["ok"], value
    return value["data"]


def test_scope_example_and_binding_are_consistent():
    observed = public("modules")["observation"]
    from kicad_cli.driver_runtime import PROTOCOL_REVISION, _pin

    validation_root = _pin()
    from kicad_cli._driver_sdk.validation import ContractValidator

    ContractValidator(validation_root).validate("scope", observed["operation_scope"]["example"])
    assert observed["operation_scope"]["writes_scope"] is False
    reference = public("reference")
    uri = "urn:tool-driver-cli-spec:scope:2.0"
    assert reference["authorization"]["scope_schema"] == uri
    assert reference["authorization"]["schema"] == observed["operation_scope"]["schema"]
    root = public("reference")
    assert root["protocol_revision"] == PROTOCOL_REVISION
    assert root["runtime_migration"]["protocol_revision"] == PROTOCOL_REVISION
    assert root["runtime_migration"]["total"] == len(root["commands"])
    assert all(v["protocol"] == PROTOCOL_REVISION for v in observed["module_bindings"].values())


def test_reference_views_validate_and_keep_offline_schema_and_module_discovery(tmp_path):
    from kicad_cli.driver_runtime import MIGRATED, create_cli
    from kicad_cli.module_contract import MODULES

    annotations = (
        "selection_schema",
        "preflight_schema",
        "routing_constraint_schema",
        "candidate_review_schema",
    )
    with create_cli(home=tmp_path, environ={}) as cli:

        def read(argv, schema=None):
            result = cli.run(argv)
            assert result.exit_code == 0 and result.envelope["ok"], result.envelope
            cli.validator.validate("response", result.envelope)
            data = result.envelope["data"]
            if schema:
                cli.validator.validate(schema, data)
            return data

        full = read(["reference"], "reference")
        index = read(["reference", "--index"], "reference-index")
        observed = read(["modules"])["observation"]
        scope = full["authorization"]
        assert scope["scope_schema"] == "urn:tool-driver-cli-spec:scope:2.0"
        assert scope["schema"] == observed["operation_scope"]["schema"]
        cli.validator.validate("scope", observed["operation_scope"]["example"])
        assert observed["operation_scope"]["writes_scope"] is False
        paths = {command["path"] for command in full["commands"]}
        assert {command["path"] for command in index["commands"]} == paths == MIGRATED
        assert set(observed["module_bindings"]) == set(observed["modules"]) == set(MODULES)
        assert {
            path for module in observed["modules"].values() for path in module["commands"]
        } == paths
        assert "authorization" not in index

        covered_annotations = set()
        full_catalog = cli.validator.catalog({**cli.validator.schemas, **full["json_schemas"]})
        for command in full["commands"]:
            selected = read(["reference", "--command", command["path"]], "reference-command")
            assert selected["command"] == command
            assert "authorization" not in selected
            label = command["output_schema"]
            assert selected["schemas"][label] == full["schemas"][label]
            required = {
                command["driver"][key]
                for key in ("request_schema", "result_schema")
                if command["driver"].get(key)
            }
            output_ref = full["schemas"][label].get("json_schema", {}).get("$ref")
            if output_ref:
                required.add(output_ref)
            catalog = cli.validator.catalog({**cli.validator.schemas, **selected["json_schemas"]})
            for uri in required:
                identity = uri.split("#", 1)[0]
                if identity in full["json_schemas"]:
                    assert selected["json_schemas"][identity] == full["json_schemas"][identity]
                else:
                    assert identity in selected["public_schemas"]
                assert isinstance(catalog.resolver().lookup(uri).contents, dict)
            for key in annotations:
                if command.get(key):
                    covered_annotations.add(key)
                    uri = command[key]
                    assert (
                        catalog.resolver().lookup(uri).contents
                        == full_catalog.resolver().lookup(uri).contents
                    )
        assert covered_annotations == set(annotations)
