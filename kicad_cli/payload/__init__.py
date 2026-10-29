"""Payloads that execute inside KiCad's bundled Python interpreter.

Nothing here may be imported by the CLI shell: these modules import pcbnew,
which only exists inside a KiCad install. They are launched as subprocesses by
kicad_env.run_payload and speak the same envelope as the shell.
"""
