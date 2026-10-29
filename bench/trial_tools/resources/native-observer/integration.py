"""Explicit unique-module entry point for the new finite observer method."""

import hashlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAMES = (
    "observer02_support",
    "observer02_metadata",
    "observer02_owned_process",
    "observer02_win_debugging",
    "observer02_native_observation",
    "observer02_process_tree",
)
LOADED = {}


def observer():
    for name in NAMES:
        path = ROOT / (name + ".py")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if name in sys.modules:
            module = sys.modules[name]
            if (
                Path(module.__file__).resolve() != path
                or getattr(module, "_observer02_loaded_sha256", None) != digest
            ):
                raise RuntimeError(
                    "Observer module identity changed; use a fresh declared process"
                )
        else:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            module._observer02_loaded_sha256 = digest
            module._observer02_loaded_source = {"path": str(path), "sha256": digest}
            sys.modules[name] = module
            try:
                exec(compile(raw, str(path), "exec"), module.__dict__)
            except BaseException:
                del sys.modules[name]
                raise
        LOADED[name] = {"path": str(path), "sha256": digest}
    producer = sys.modules["observer02_process_tree"]
    classifier = sys.modules["observer02_native_observation"]
    return producer.observe, classifier.inspect_trace
