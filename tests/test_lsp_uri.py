"""`ohm lsp` turns the editor's `file://` URI into the path the loader resolves
relative `use` imports against.

`file:///D:/x/a.ohm` used to become `/D:/x/a.ohm`. On Windows a path that starts
with `/` and names no drive is relative to the *current drive's* root, so
`use "./parts.ohm"` resolved only when the server's working directory happened
to be that root, and an editor otherwise showed R015 plus an R002 for every
part the import declared.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ohm import lsp
from ohm.lsp import _uri_path

# (uri, as read on Windows, as read on POSIX)
CASES = [
    ("file:///D:/x/a.ohm", "D:/x/a.ohm", "/D:/x/a.ohm"),
    ("file:///d%3A/x/a%20b.ohm", "D:/x/a b.ohm", "/d:/x/a b.ohm"),  # VS Code's form
    ("file:///C|/x/a.ohm", "C:/x/a.ohm", "/C|/x/a.ohm"),            # the old `|` form
    ("file:///D:", "D:/", "/D:"),
    ("file://localhost/D:/x/a.ohm", "D:/x/a.ohm", "/D:/x/a.ohm"),
    ("file:///home/u/a.ohm", "/home/u/a.ohm", "/home/u/a.ohm"),
    ("file:///t.ohm", "/t.ohm", "/t.ohm"),
    ("file://srv/share/a.ohm", "//srv/share/a.ohm", "//srv/share/a.ohm"),  # UNC
    ("file:///tmp/caf%C3%A9.ohm", "/tmp/caf\u00e9.ohm", "/tmp/caf\u00e9.ohm"),  # UTF-8
    ("file:///tmp/100%25.ohm", "/tmp/100%.ohm", "/tmp/100%.ohm"),
    ("untitled:Untitled-1", "untitled:Untitled-1", "untitled:Untitled-1"),
]


@pytest.mark.parametrize("uri,on_windows,on_posix", CASES)
def test_uri_path_reads_a_file_uri_as_url2pathname_does(uri, on_windows, on_posix):
    assert _uri_path(uri, windows=True) == on_windows
    assert _uri_path(uri, windows=False) == on_posix


def test_uri_path_defaults_to_the_host_os():
    uri = "file:///D:/x/a.ohm"
    assert _uri_path(uri) == _uri_path(uri, windows=os.name == "nt")


def test_uri_path_round_trips_pathlib_as_uri(tmp_path):
    f = tmp_path / "sub dir" / "caf\u00e9.ohm"
    assert Path(_uri_path(f.as_uri())) == f


_PARTS = """\
component R:
    package = "0603"
    pin a: role=passive
    pin b: role=passive
"""

_MAIN = """\
use "./parts.ohm"

module Top:
    r1 = R()
    r2 = R()
    r1.b ~ r2.a
"""


def test_relative_use_resolves_from_the_uri_whatever_the_cwd(tmp_path, monkeypatch):
    """The regression itself: the working directory is a sibling, not a root."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "parts.ohm").write_text(_PARTS, encoding="utf-8")
    (proj / "main.ohm").write_text(_MAIN, encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    diags = lsp._lsp_diagnostics(_MAIN, (proj / "main.ohm").as_uri())
    codes = [d["code"] for d in diags]
    assert "R015" not in codes and "R002" not in codes, diags
