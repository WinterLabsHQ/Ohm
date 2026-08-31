"""`ohm verify-footprints`: the author-time half of the footprint pad question.

A string `package = "Lib:Footprint"` emits a pad-LESS stub — the geometry comes
from the user's installed library at layout time and only the pad id travels, in
the netlist. `pin SHIELD: pad=11` is therefore a claim about a file the compiler
never opens, and KiCad 10 falsified it wholesale by renaming shield and shell
pads to `"SH"` across 163 footprints. This command opens the file.

Almost every test here runs against a hand-written `.pretty` tree in `tmp_path`,
so the answers are identical on a bare runner and on a developer's machine with
KiCad installed. The two that need a real KiCad are guarded, and say "kicad" in
the skip reason so `OHM_REQUIRE_KICAD` turns them into failures.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from ohm import kicad_env
from ohm.cli import main
from ohm.driver import compile_source
from ohm.verify_footprints import (
    generated_packages,
    pad_ids,
    verify_against_footprints,
)

from tests.conftest import first_footprint, requires_footprint

SOIC8_MOD = "Package_SO.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod"

# KiCad 9/10 spelling: quoted ids, pretty-printed over several lines, and a
# nameless mounting hole that carries no net.
_CONN_4 = """(footprint "Conn_4" (version 20240108) (generator "hand")
  (pad "1" thru_hole rect
    (at 0 0)
    (size 1.7 1.7)
    (drill 1)
    (layers "*.Cu"))
  (pad "2" thru_hole oval (at 2.54 0) (size 1.7 1.7) (drill 1) (layers "*.Cu"))
  (pad "3" thru_hole oval (at 5.08 0) (size 1.7 1.7) (drill 1) (layers "*.Cu"))
  (pad "4" thru_hole oval (at 7.62 0) (size 1.7 1.7) (drill 1) (layers "*.Cu"))
  (pad "SH" smd rect (at 10 0) (size 2 2) (layers "F.Cu"))
  (pad "" np_thru_hole circle (at -3 0) (size 2 2) (drill 2) (layers "*.Cu"))
)
"""

# KiCad 7 spelling: bare ids, one pad per line.
_CONN_4_LEGACY = """(module Conn_4_Legacy (layer F.Cu)
  (pad 1 thru_hole rect (at 0 0) (size 1.7 1.7) (drill 1) (layers *.Cu))
  (pad 2 thru_hole oval (at 2.54 0) (size 1.7 1.7) (drill 1) (layers *.Cu))
  (pad 11 smd rect (at 10 0) (size 2 2) (layers F.Cu))
)
"""


@pytest.fixture(autouse=True)
def _no_ambient_kicad(monkeypatch):
    """Every KICAD* variable cleared, so a developer's real KiCad cannot make a
    test pass that would fail on a bare runner (or vice versa)."""
    for key in list(os.environ):
        if key.startswith("KICAD") or key == "OHM_KICAD_CLI":
            monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def stub_lib(tmp_path, monkeypatch):
    """A `.pretty` tree with the two footprints above, pointed at by the env."""
    root = tmp_path / "footprints"
    pretty = root / "Stub.pretty"
    pretty.mkdir(parents=True)
    (pretty / "Conn_4.kicad_mod").write_text(_CONN_4, encoding="utf-8")
    (pretty / "Conn_4_Legacy.kicad_mod").write_text(_CONN_4_LEGACY, encoding="utf-8")
    monkeypatch.setenv("KICAD_FOOTPRINT_DIR", str(root))
    return root


def _src(package: str, pads=("1", "2", "3", "4"), pin_names=None) -> str:
    names = pin_names or [f"P{i}" for i in range(1, len(pads) + 1)]
    lines = ['component Conn:',
             f'    package = "{package}"',
             '    designator_prefix = "J"']
    lines += [f"    pin {name}: role=passive, pad={pad}"
              for name, pad in zip(names, pads)]
    lines += ["", "module Top:", "    j = Conn()"]
    return "\n".join(lines) + "\n"


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "design.ohm"
    path.write_text(text, encoding="utf-8")
    return str(path)


def _run(tmp_path, capsys, text: str, *args):
    rc = main(["verify-footprints", _write(tmp_path, text), *args])
    return rc, capsys.readouterr().out


# ---------- the pad reader ----------
def test_pad_ids_reads_both_kicad_spellings():
    """KiCad 7 leaves the id bare, KiCad 9/10 quote it. Both are on real disks."""
    assert pad_ids(_CONN_4) == {"1", "2", "3", "4", "SH"}
    assert pad_ids(_CONN_4_LEGACY) == {"1", "2", "11"}


def test_pad_ids_ignores_nameless_pads_and_padstacks():
    """A nameless mounting hole carries no net, so it can never be the pad a pin
    is asking for; `(padstack` is KiCad 9's per-layer stack, not a pad named
    "tack"."""
    text = '(pad "" np_thru_hole circle (at 0 0))\n(padstack (mode front_inner_back))\n'
    assert pad_ids(text) == set()


# ---------- abstention: nothing installed is not a design defect ----------
def test_no_kicad_installed_abstains_with_exit_zero(tmp_path, capsys, monkeypatch):
    """The normal case on a machine with no KiCad. Reporting every footprint as
    missing from a directory that does not exist would be confident nonsense —
    and exiting 1 would make the command unusable in CI."""
    monkeypatch.setattr(kicad_env, "_prefixes", lambda: [])
    rc, out = _run(tmp_path, capsys, _src("Stub:Conn_4"))
    assert rc == 0
    assert "cannot check" in out
    assert "no installed KiCad footprint libraries" in out
    assert "MISMATCH" not in out


def test_explicit_lib_dir_that_is_not_there_also_abstains(tmp_path, capsys, monkeypatch):
    """Same reasoning, and the message names the path so the typo is visible."""
    monkeypatch.setattr(kicad_env, "_prefixes", lambda: [])
    missing = str(tmp_path / "nowhere")
    rc, out = _run(tmp_path, capsys, _src("Stub:Conn_4"), "--lib-dir", missing)
    assert rc == 0
    assert "cannot check" in out and "nowhere" in out


# ---------- the real comparison, against a stub library ----------
def test_clean_design_passes(tmp_path, capsys, stub_lib):
    rc, out = _run(tmp_path, capsys, _src("Stub:Conn_4"))
    assert rc == 0, out
    assert "ok           Conn  (Stub:Conn_4)" in out
    assert "MISMATCH     " not in out
    assert "1 library footprints: 1 verified, 0 MISMATCHED, 0 unverifiable" in out


def test_renamed_shield_pad_is_a_mismatch(tmp_path, capsys, stub_lib):
    """The KiCad 10 shield rename, in miniature: the source says 11, the library
    says SH, and nothing else in the design changed."""
    src = _src("Stub:Conn_4", pads=("1", "2", "3", "4", "11"),
               pin_names=["P1", "P2", "P3", "P4", "SHIELD"])
    rc, out = _run(tmp_path, capsys, src)
    assert rc == 1, out
    assert "MISMATCH     Conn  vs Stub:Conn_4" in out
    assert "pad 11 (pin SHIELD) is not in this footprint" in out
    # actionable: what the footprint DOES have, so the fix is readable off the report
    assert "the footprint has: 1, 2, 3, 4, SH" in out


def test_legacy_unquoted_footprint_is_compared_too(tmp_path, capsys, stub_lib):
    """A KiCad 7-era file on disk must not read as "no pads" and abstain."""
    rc, out = _run(tmp_path, capsys, _src("Stub:Conn_4_Legacy", pads=("1", "11")))
    assert rc == 0, out
    rc, out = _run(tmp_path, capsys, _src("Stub:Conn_4_Legacy", pads=("1", "99")))
    assert rc == 1, out
    assert "pad 99" in out


def test_footprint_missing_from_an_installed_library_is_a_mismatch(tmp_path, capsys, stub_lib):
    """KiCad cannot resolve this either — the board opens with no geometry."""
    rc, out = _run(tmp_path, capsys, _src("Stub:Conn_4_Vertical"))
    assert rc == 1, out
    assert "Stub.pretty has no footprint 'Conn_4_Vertical'" in out
    assert "did you mean: Conn_4" in out


def test_unknown_library_is_unverifiable_not_wrong(tmp_path, capsys, stub_lib):
    """Project-local and vendor `.pretty` directories are not under KiCad's share
    root. Calling those a defect would train people to ignore the command."""
    rc, out = _run(tmp_path, capsys, _src("Vendor:Thing_4"))
    assert rc == 0, out
    assert "UNVERIFIABLE Conn  — no library 'Vendor.pretty'" in out
    assert "unverifiable is not the same as correct" in out


def test_a_package_that_is_not_a_library_reference_abstains(tmp_path, capsys, stub_lib):
    rc, out = _run(tmp_path, capsys, _src("Conn_4"))
    assert rc == 0, out
    assert "does not name a library footprint" in out


def test_generated_packages_are_not_applicable(tmp_path, capsys, stub_lib):
    """`generate(...)` carries its own geometry — Ohm writes the .kicad_mod, and
    test_pkggen already diffs it against KiCad's own reference."""
    src = ("component Widget:\n"
           "    package = generate(SOIC, pins=8, pitch=1.27mm)\n"
           + "".join(f"    pin P{i}: role=passive, pad={i}\n" for i in range(1, 9))
           + "\nmodule Top:\n    w = Widget()\n")
    rc, out = _run(tmp_path, capsys, src)
    assert rc == 0, out
    assert "0 library footprints" in out
    assert "1 generate(...) package not applicable" in out


def test_instance_package_override_is_checked_at_the_instantiation_site(tmp_path, capsys,
                                                                       stub_lib):
    """R9 lets an instance override `package=`, and the override is the footprint
    that will actually be placed — `check_structural` re-closes the pin↔pad loop
    the same way."""
    src = _src("Stub:Conn_4").replace("    j = Conn()",
                                      '    j = Conn(package="Stub:Conn_4_Legacy")')
    rc, out = _run(tmp_path, capsys, src)
    assert rc == 1, out
    assert "MISMATCH     Top.j (Conn)  vs Stub:Conn_4_Legacy" in out
    assert "pad 3 (pin P3) is not in this footprint" in out
    # the component's own package is still clean, and still says so
    assert "ok           Conn  (Stub:Conn_4)" in out


def test_a_component_with_no_pads_is_only_checked_for_existence(tmp_path, capsys, stub_lib):
    src = ('component Mount:\n    package = "Stub:Conn_4"\n'
           '    designator_prefix = "H"\n\nmodule Top:\n    h = Mount()\n')
    rc, out = _run(tmp_path, capsys, src)
    assert rc == 0, out
    assert "exists, no pads declared" in out


def test_generated_packages_counts_overrides_too(tmp_path):
    src = ("component Widget:\n"
           "    package = generate(SOIC, pins=8, pitch=1.27mm)\n"
           + "".join(f"    pin P{i}: role=passive, pad={i}\n" for i in range(1, 9))
           + "\nmodule Top:\n    w = Widget()\n")
    design = compile_source(src, "t.ohm").design
    assert generated_packages(design) == 1


# ---------- quarantine: impure, and off the build path ----------
def test_nothing_on_the_build_path_imports_this_module():
    """It reads the user's disk, so it lives with `drc.py` and the verify half of
    `provenance.py` on the author-time side of N1. Only the CLI may reach it."""
    pkg = Path(__file__).resolve().parent.parent / "ohm"
    for py in pkg.rglob("*.py"):
        if py.name == "cli.py":
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n.endswith("verify_footprints") for n in names), \
                f"{py.name} imports verify_footprints, which reads the filesystem"


# ---------- against the KiCad this machine actually has ----------
@requires_footprint(SOIC8_MOD)
def test_real_installed_footprint_verifies(tmp_path, monkeypatch, capsys):
    """The same comparison against a real library, not a stub: SOIC-8 has pads
    1..8 in every KiCad Ohm supports."""
    real = first_footprint(SOIC8_MOD)
    assert real is not None
    monkeypatch.setenv("KICAD_FOOTPRINT_DIR", str(real.parent.parent))
    src = _src(f"Package_SO:{real.stem}", pads=tuple(str(i) for i in range(1, 9)))
    rc, out = _run(tmp_path, capsys, src)
    assert rc == 0, out
    assert f"ok           Conn  (Package_SO:{real.stem})" in out


@requires_footprint(SOIC8_MOD)
def test_real_installed_footprint_catches_a_bad_pad():
    """And reports the pads it really has, read off the installed file."""
    real = first_footprint(SOIC8_MOD)
    assert real is not None
    src = _src(f"Package_SO:{real.stem}", pads=("1", "2", "99"))
    design = compile_source(src, "t.ohm").design
    [(name, status, detail, findings)] = verify_against_footprints(
        design, str(real.parent.parent))
    assert (name, status) == ("Conn", "mismatch"), findings
    assert detail == f"Package_SO:{real.stem}"
    assert any("pad 99" in f for f in findings)
    assert any(f.startswith("the footprint has: 1, 2, 3") for f in findings)
