"""Render a full native SVG with explicitly bound PyMuPDF and no site startup."""

import sys

if __name__ == "__main__" and not sys.flags.no_site:
    raise SystemExit("saved_scene_render requires Python -B -S; site startup is not accepted")

import os

# This is a finite dependency boundary for this renderer, not a Python sandbox.
# Avoid script-directory/current-directory and unrelated site-packages imports.
_host_root = os.path.dirname(os.path.abspath(sys.executable))
_stdlib = os.path.join(_host_root, "Lib")
_dlls = os.path.join(_host_root, "DLLs")
_zip = os.path.join(_host_root, f"python{sys.version_info.major}{sys.version_info.minor}.zip")
if __name__ == "__main__":
    sys.path[:] = [_stdlib, _dlls, _zip]

# Deliberately import these only after the initial path boundary is established.
import hashlib  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def within(path, root):
    return path == root or root in path.parents


def module_sources():
    """Reject actual nonstdlib modules outside the two declared bitmap packages."""
    root = Path(_host_root).resolve()
    stdlib = root / "Lib"
    site = stdlib / "site-packages"
    packages = [site / "fitz", site / "pymupdf"]
    this = Path(__file__).resolve()
    archive = Path(_zip).resolve()
    result = []
    for name, module in sorted(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if not filename:
            origin = getattr(getattr(module, "__spec__", None), "origin", None)
            if name in {"typing.io", "typing.re"}:
                typing = sys.modules["typing"]
                if vars(typing)[name.removeprefix("typing.")] is not module:
                    raise ValueError("Unexpected standard-library typing alias")
                provider = Path(typing.__file__).resolve()
                if provider != stdlib / "typing.py":
                    raise ValueError("Unbound typing alias provider")
                result.append(
                    {
                        "module": name,
                        "origin": "exact alias in bound standard-library typing",
                        "provider": str(provider),
                        "provider_sha256": digest(provider),
                    }
                )
                continue
            if name == "swig_runtime_data5":
                expected_keys = {
                    "SwigPyObject",
                    "SwigPyPacked",
                    "SwigVarLink",
                    "__doc__",
                    "__loader__",
                    "__name__",
                    "__package__",
                    "__spec__",
                    "type_pointer_capsule",
                }
                if set(vars(module)) != expected_keys:
                    raise ValueError("Unexpected SWIG registration module content")
                providers = []
                for provider_name in ("pymupdf._mupdf", "pymupdf._extra"):
                    provider = Path(sys.modules[provider_name].__file__).resolve()
                    if provider.suffix != ".pyd" or not within(provider, site / "pymupdf"):
                        raise ValueError("Unbound SWIG registration provider")
                    providers.append(
                        {"module": provider_name, "path": str(provider), "sha256": digest(provider)}
                    )
                result.append(
                    {
                        "module": name,
                        "origin": "dynamic SWIG registration from bound PyMuPDF extensions",
                        "exact_keys": sorted(expected_keys),
                        "providers": providers,
                    }
                )
                continue
            if origin not in {"built-in", "frozen"}:
                raise ValueError("Unbound module without source: " + name)
            result.append({"module": name, "origin": origin})
            continue
        path = Path(filename).resolve()
        allowed = (
            path == this
            or (within(path, stdlib) and not within(path, site))
            or within(path, root / "DLLs")
            or any(within(path, package) for package in packages)
        )
        if allowed and path.is_file():
            result.append({"module": name, "path": str(path), "sha256": digest(path)})
        elif archive in path.parents and archive.is_file():
            result.append({"module": name, "path": str(path), "archive_sha256": digest(archive)})
        else:
            raise ValueError("Renderer imported undeclared code: " + name + " at " + str(path))
    return result


def main():
    if not sys.flags.no_site or not sys.flags.dont_write_bytecode:
        raise SystemExit("saved_scene_render requires Python -B -S")
    source, target = map(Path, sys.argv[1:3])
    scale = float(sys.argv[3])
    if target.exists():
        raise ValueError("Render target already exists")
    sys.path.append(str(Path(_stdlib) / "site-packages"))
    import fitz

    before = digest(source)
    document = fitz.open(stream=source.read_bytes(), filetype="svg")
    page = document[0]
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    module_sources()
    if digest(source) != before:
        raise ValueError("Renderer changed source SVG")
    pixmap.save(str(target))
    modules = module_sources()
    print(
        json.dumps(
            {
                "source": str(source),
                "source_sha256": before,
                "target": str(target),
                "target_sha256": digest(target),
                "page_points": list(page.rect),
                "pixel_size": [pixmap.width, pixmap.height],
                "coverage": "full SVG viewport; no cropping",
                "startup": {
                    "no_site": True,
                    "dont_write_bytecode": True,
                    "site_packages_appended_only": str(Path(_stdlib) / "site-packages"),
                    "pth_user_site_sitecustomize_executed": False,
                },
                "actual_module_sources": modules,
            }
        )
    )


if __name__ == "__main__":
    main()
