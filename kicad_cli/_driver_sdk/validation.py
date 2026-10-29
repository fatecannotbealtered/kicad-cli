"""Offline validation using explicitly selected, trusted vendored specification code."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import math
import re
import sys
import threading
from collections import OrderedDict
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource, Unresolvable


class ContractError(ValueError):
    pass


class ContractValidationError(ContractError):
    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__("contract validation failed: " + "; ".join(self.errors[:10]))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate JSON member")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ContractError("non-JSON numeric constant")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ContractError("nonfinite JSON number")
    return number


def parse_json(payload: bytes):
    if not isinstance(payload, bytes):
        raise ContractError("JSON input must be explicit UTF-8 bytes")
    if payload.startswith(b"\xef\xbb\xbf"):
        raise ContractError("UTF-8 BOM is not permitted")
    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_invalid_constant, parse_float=_finite_float)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("one complete finite UTF-8 JSON document is required") from exc


def _check_keys(value):
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ContractError("JSON object keys must be strings")
        for item in value.values():
            _check_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _check_keys(item)


def json_bytes(value) -> bytes:
    try:
        _check_keys(value)
        return (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ContractError("value cannot be represented as strict UTF-8 JSON") from exc


def clone_json(value):
    return parse_json(json_bytes(value))


def _offline_only(uri):
    raise NoSuchResource(ref=uri)


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        return self.source_to_code(self.get_data(self.path), self.path)


_IMPORT_LOCK = threading.RLock()


def _trusted_module(path: Path, name: str):
    loader = _SourceOnlyLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None:
        raise ContractError("trusted specification module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _load_semantics(root: Path):
    """Load caller-trusted vendored Python, never a path taken from a request/registry."""
    scripts = root / "scripts"
    if not (scripts / "check_spec.py").is_file() or not (scripts / "contract_io.py").is_file():
        raise ContractError("vendored specification needs its offline semantic checker")
    identity = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    with _IMPORT_LOCK:
        previous = sys.modules.get("contract_io")
        dependency = _trusted_module(scripts / "contract_io.py", "_td_sdk_io_" + identity)
        sys.modules["contract_io"] = dependency
        try:
            return _trusted_module(scripts / "check_spec.py", "_td_sdk_semantics_" + identity)
        finally:
            if previous is None:
                sys.modules.pop("contract_io", None)
            else:
                sys.modules["contract_io"] = previous


class ContractValidator:
    """The caller must authenticate/pin vendor_root before loading its checker code."""

    def __init__(self, vendor_root: str | Path):
        self.root = Path(vendor_root).resolve(strict=True)
        self.schemas = {}
        self._names = {}
        self._catalogs = OrderedDict()
        self._catalog_lock = threading.RLock()
        for path in sorted((self.root / "schemas").glob("*.schema.json")):
            value = parse_json(path.read_bytes())
            Draft202012Validator.check_schema(value)
            identity = value.get("$id")
            if not isinstance(identity, str) or identity in self.schemas:
                raise ContractError("vendored Schema identities must be unique")
            self.schemas[identity] = value
            self._names[path.name.removesuffix(".schema.json")] = identity
        if "response" not in self._names or "receipt" not in self._names:
            raise ContractError("required public schemas are missing")
        self.registry = self.catalog(self.schemas)
        self._semantic = _load_semantics(self.root)
        self.schema_version = self.schemas[self._names["response"]]["properties"]["schema_version"]["const"]

    def catalog(self, schemas: dict) -> Registry:
        encoded = json_bytes(schemas)
        fingerprint = hashlib.sha256(encoded).digest()
        with self._catalog_lock:
            if fingerprint in self._catalogs:
                self._catalogs.move_to_end(fingerprint)
                return self._catalogs[fingerprint]
        registry = Registry(retrieve=_offline_only)
        # Registry resources keep independent snapshots, not caller-mutable dictionaries.
        for identity, schema in parse_json(encoded).items():
            Draft202012Validator.check_schema(schema)
            if schema.get("$id") != identity:
                raise ContractError("Schema catalog identity mismatch")
            registry = registry.with_resource(identity, Resource.from_contents(schema))
        registry = registry.crawl()
        with self._catalog_lock:
            self._catalogs[fingerprint] = registry
            if len(self._catalogs) > 16:
                self._catalogs.popitem(last=False)
        return registry

    def validate(self, kind: str, value):
        normalized = self.validate_shape(kind, value)
        name = kind.removesuffix(".schema.json")
        errors = self._semantic.semantic_errors(name, normalized)
        if errors:
            raise ContractValidationError(errors)
        return normalized

    def validate_shape(self, kind: str, value):
        """Check an adapter fragment's Schema before complete-response semantic validation."""
        name = kind.removesuffix(".schema.json")
        if name not in self._names:
            raise ContractError("unknown public Schema")
        normalized = self.validate_schema(self._names[name], value, self.schemas)
        return normalized

    def validate_schema(self, identity: str, value, schemas: dict):
        if identity not in schemas:
            raise ContractError("declared Schema is absent from its offline catalog")
        normalized = clone_json(value)
        try:
            checked = Draft202012Validator(schemas[identity], registry=self.catalog(schemas), format_checker=FormatChecker())
            errors = [f"{'/'.join(map(str, error.absolute_path)) or '(root)'}: constraint {error.validator} failed" for error in checked.iter_errors(normalized)]
        except (Unresolvable, RecursionError) as exc:
            raise ContractError("Schema reference must resolve in the offline catalog") from exc
        if errors:
            raise ContractValidationError(errors)
        return normalized

    def validate_reference(self, reference):
        return self.validate("reference", reference)

    def receipt_rule(self, receipt: dict) -> dict:
        """The first matching contract.receipt_codes rule for a receipt: {when, ok, codes}."""
        return self._semantic.receipt_rule(receipt)

    def code_table(self) -> dict:
        """Core codes from the pinned base plus driver extension codes: code -> {exit, retryable}."""
        return self._semantic.code_table()

    def validate_product(self, schema: dict, value, json_schemas: dict):
        """Validate adapter output against a product JSON Schema that may reference public driver schemas."""
        catalog = dict(self.schemas)
        catalog.update(json_schemas)
        wrapper = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:tool-driver-cli-spec:product-output:check", **schema}
        catalog[wrapper["$id"]] = wrapper
        return self.validate_schema(wrapper["$id"], value, catalog)
