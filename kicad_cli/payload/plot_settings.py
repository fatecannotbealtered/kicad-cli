"""Copy saved SDK plot settings before choosing an output file/format.

PLOT_CONTROLLER owns separate defaults. Unsupported non-default settings or a
failed native comparison refuse the export instead of silently changing it.
"""

from __future__ import annotations


def copy_saved(source, target):
    copied = []
    for name in sorted(dir(source)):
        if not name.startswith("Get"):
            continue
        setter = "Set" + name[3:]
        if name == "GetSvgFitPagetoBoard":
            setter = "SetSvgFitPageToBoard"
        if not callable(getattr(target, setter, None)):
            continue
        value = getattr(source, name)()
        try:
            getattr(target, setter)(value)
        except TypeError:
            # SWIG exposes this vector getter as a tuple, but this reference
            # setter does not accept it. Empty state is already the default.
            if name != "GetLayersToExport" or value:
                raise
            continue
        copied.append(name[3:])
    for name in sorted(dir(type(source))):
        if name.startswith("m_") and isinstance(getattr(type(source), name), property):
            setattr(target, name, getattr(source, name))
            copied.append(name)
    if callable(getattr(source, "ColorSettings", None)):
        target.SetColorSettings(source.ColorSettings())
    # KiCad's native board loader migrates the old optional via-mask flag to
    # design settings. Its SWIG getter is an opaque optional pointer, not a
    # Python None/bool; it is not a controller setting to copy or interpret.
    if not target.IsSameAs(source):
        raise ValueError("Native saved plot settings comparison failed")
    return copied
