"""PyInstaller entry point.

Not `kicad_cli/main.py`. PyInstaller runs its entry script as `__main__` with no
package context, so every relative import in that module fails -- the frozen
binary died on `from . import envelope` before it could emit a single envelope,
and every command exited 1 with a Python traceback on stdout.

Nothing caught it because the whole test suite runs `python -m kicad_cli.main`,
which has the package context the frozen binary does not. The suite was testing
something the user never installs. `scripts/smoke_binary.py` now tests the
artifact itself.
"""

from kicad_cli.main import main

if __name__ == "__main__":
    main()
