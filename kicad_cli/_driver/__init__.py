"""The driver runtime: everything a driver tool shares, copied into every tool unchanged.

A tool defines its domain commands as registrations and its adapter as a subclass of
``Adapter``; ``Tool`` does the rest. See ``tool.py`` for the entry point and ``adapter.py``
for the adapter interface.
"""
from . import trace as _trace

_trace.start()  # a live run notes the code it uses from the start (DVER-043)

from .adapter import Adapter, AdapterError, session_id  # noqa: E402 - after the trace starts
from .registry import RegistrationError  # noqa: E402 - after the trace starts
from .tool import Tool  # noqa: E402 - after the trace starts

__all__ = ["Adapter", "AdapterError", "RegistrationError", "Tool", "session_id"]
