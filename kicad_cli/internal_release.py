"""The bounded internal profile of the single product version."""

from . import __version__

SERIES = __version__
TAG = f"v{__version__}"
SUPPORTED = frozenset(
    {
        "reference",
        "context",
        "doctor",
        "changelog",
        "modules",
        "operation list",
        "operation show",
        "operation resolve",
        "library build",
        "library inspect",
        "project context",
        "sch create",
        "sch edit",
        "sch objects",
        "sch symbols",
        "sch connections",
        "sch export",
        "project links inspect",
        "project links repair",
        "project parity",
        "project sync",
        "board from-netlist",
        "board footprint move",
        "board routing inspect",
        "board route patch",
        "sch check",
        "board check",
        "fab gerber",
        "fab drill",
        "board plot",
    }
)


def profile():
    from . import __version__

    return {
        "series": SERIES,
        "tag": TAG,
        "product_version": __version__,
        "qualification": {
            "status": "pending_exact_artifact_acceptance",
            "policy": "Acceptance binds exact artifact hashes; "
            "source tests alone do not qualify a package",
        },
        "audience": "recipients authorized by the separately configured distribution channel",
        "platform": (
            "Native Windows x64, macOS arm64/x64 and Linux x64; "
            "external legal KiCad10.0 patch 6 or later"
        ),
        "platform_targets": ["win32-x64", "darwin-arm64", "darwin-x64", "linux-x64"],
        "supported_commands": sorted(SUPPORTED),
        "limits": [
            "Each command retains its selected-reference geometry and context limits",
            "SCH/PCB check are custom saved-file observers, not official ERC/DRC",
            "Official DRC in route-patch preview; complete independent native reports separately",
            "Single-unit rectangular library assets, integer-nm footprint inputs",
            "Single-root SCH export; zero-degree front-SMD bounded footprint translation",
            "Explicit straight copper patches; arc/via removal and new vias unsupported",
            "No automatic routing/dense escape, general cache refresh or manufacturing signoff",
            "Basic package smoke is recorded per artifact; "
            "full FCC and advanced recovery remain separate",
        ],
        "public_distribution": True,
    }
