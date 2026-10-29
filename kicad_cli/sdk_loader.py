"""Locate pinned SDK sources; execute them only after the runtime authenticates them."""

import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path


def root(package):
    local = package / "_driver_sdk"
    value = os.environ.get("KICAD_CLI_DRIVER_SDK_PATH")
    if value is None and local.is_dir():
        return local
    if not value:
        raise ValueError("An existing pinned SDK is required: set KICAD_CLI_DRIVER_SDK_PATH")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("KICAD_CLI_DRIVER_SDK_PATH must be an absolute package directory")
    path = path.resolve(strict=True)
    if not (path / "__init__.py").is_file():
        raise ValueError("External driver SDK must be the pinned package directory")
    return path


class _SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """Compile the authenticated source, without a PYZ or bytecode-cache fallback."""

    def get_code(self, fullname):
        return compile(self.get_data(self.path), self.path, "exec", dont_inherit=True)


class _SDKSourceFinder(importlib.abc.MetaPathFinder):
    def __init__(self, root):
        self.root = Path(root)

    def find_spec(self, fullname, path=None, target=None):
        prefix = "kicad_cli._driver_sdk."
        if not fullname.startswith(prefix):
            return None
        relative = fullname.removeprefix(prefix).split(".")
        source = self.root.joinpath(*relative).with_suffix(".py")
        if not source.is_file():
            raise ModuleNotFoundError("No authenticated SDK module", name=fullname)
        return importlib.util.spec_from_file_location(
            fullname, source, loader=_SourceOnlyLoader(fullname, str(source))
        )


def load(path, package):
    if "kicad_cli._driver_sdk" in sys.modules:
        return
    # Called only after the complete SDK/spec pin and schema inventory pass,
    # with supported Python dependency paths ready for __init__ imports.
    path = Path(path)
    finder = _SDKSourceFinder(path)
    sys.meta_path.insert(0, finder)
    name = "kicad_cli._driver_sdk"
    source = path / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        name,
        source,
        loader=_SourceOnlyLoader(name, str(source)),
        submodule_search_locations=[str(path)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        sys.meta_path.remove(finder)
        raise
