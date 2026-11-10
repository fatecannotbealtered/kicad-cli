"""The driver runtime: everything a driver tool shares, copied into every tool unchanged.

A tool defines its domain commands as registrations and its adapter as a subclass of
``Adapter``; ``Tool`` does the rest. See ``tool.py`` for the entry point and ``adapter.py``
for the adapter interface.
"""
from .adapter import Adapter, AdapterError, session_id
from .registry import RegistrationError
from .tool import Tool

__all__ = ["Adapter", "AdapterError", "RegistrationError", "Tool", "session_id"]
