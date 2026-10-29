import os
import subprocess
import sys

import pytest

from bench.audit_program import direct


def test_explicit_executable_takes_precedence_over_display_argv():
    assert direct(sys.executable, ["misleading-display-name"], sys.executable)
    assert not direct("another-program", [sys.executable], sys.executable)


@pytest.mark.parametrize("args", [None, {}, [], "", "kicad-cli.exe", "\0bad"])
def test_missing_or_unresolved_program_is_not_bound(args):
    assert not direct(None, args, sys.executable)


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows Popen audit command-line grammar")
def test_windows_application_name_none_uses_exact_first_program():
    command = subprocess.list2cmdline([sys.executable, "-c", "print('kept argument')"])
    assert direct(None, command, sys.executable)
    assert not direct(None, 'cmd.exe /c "' + command + '"', sys.executable)
    assert not direct(
        None, subprocess.list2cmdline([sys.executable + ".other", sys.executable]), sys.executable
    )


def test_list_form_uses_only_the_program_not_a_later_argument():
    assert direct(None, [sys.executable, "--version"], sys.executable)
    assert not direct(None, ["different", sys.executable], sys.executable)
