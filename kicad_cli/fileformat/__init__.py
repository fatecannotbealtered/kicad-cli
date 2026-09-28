"""KiCad's file formats, read and written by this tool itself.

Nothing here imports KiCad. Everything the tool knows about a design comes
through this package: the files are KiCad's long-term contract, while the
SWIG bindings are gone in KiCad 11 and the official binary is not something
this tool calls.
"""
