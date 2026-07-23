"""W3 audit-fix gate: BOM tolerated, top-level indentation → clean P001 + recovery,
control chars rejected in strings, kicad emitters escape quotes, generated
footprints carry silkscreen outline + pin-1 marker, `python -m ohm` works,
version is single-sourced with an uninstalled fallback."""

import shutil
import subprocess
import sys
from decimal import Decimal

import pytest

from ohm import ir
from ohm.emit_kicad import emit_kicad_pcb, emit_kicad_sch
from ohm.parse import parse
from ohm.pkggen.footprint import emit_footprint
from ohm.pkggen.packages import build


# ---------- finding 1: UTF-8 BOM ----------
def test_bom_file_parses_clean():
    prog, diags = parse('\ufeffcomponent C:\n    mpn = "x"\n')
    assert diags == []
    assert len(prog.decls) == 1 and prog.decls[0].name == "C"


# ---------- finding 2: indented first declaration ----------
def test_indented_first_line_is_p001_and_rest_recovers():
    src = "    r = R()\n    x = Y()\nmodule M:\n    a = X()\n"
    prog, diags = parse(src, "t.ohm")
    assert [d.code for d in diags] == ["P001"]
    assert "indentation" in diags[0].message
    assert (diags[0].span.line, diags[0].span.col) == (1, 1)
    assert [d.name for d in prog.decls] == ["M"]  # rest of the file recovered


def test_indented_first_line_after_comment_points_at_code_line():
    src = "# header\n\n    pin A: role=input\ncomponent C:\n    mpn = \"x\"\n"
    prog, diags = parse(src)
    assert [d.code for d in diags] == ["P001"]
    assert diags[0].span.line == 3
    assert [d.name for d in prog.decls] == ["C"]


# ---------- finding 3: control chars in STRING ----------
def test_control_char_string_is_p001():
    prog, diags = parse('component C:\n    mpn = "a\x00b"\n')
    assert any(d.code == "P001" for d in diags)
    prog, diags = parse('component C:\n    mpn = "a\rb"\n')  # lone CR
    assert any(d.code == "P001" for d in diags)


def test_tab_in_string_still_parses():
    prog, diags = parse('component C:\n    mpn = "a\tb"\n')
    assert diags == []


# ---------- finding 4: kicad emitters escape names ----------
def test_kicad_emitters_escape_quotes():
    low = ir.IRLow(
        parts=(ir.PartLow(ref="m.r1", designator="R1", component='We"ird',
                          package=None, attrs=()),),
        nets=(ir.NetLow(name='NET"1', nodes=(), ports=()),),
    )
    sch = emit_kicad_sch(low, "t.ohm")
    pcb = emit_kicad_pcb(low, "t.ohm")
    assert 'We\\"ird' in sch and 'NET\\"1' in sch
    assert '"NET\\"1"' in pcb
    assert '"NET"1"' not in pcb  # a raw quote would corrupt the s-expression


# ---------- finding 5: footprint silkscreen outline + pin-1 marker ----------
SOIC8 = build("SOIC", {"pins": Decimal(8)})


def test_footprint_has_silk_outline_and_pin1_marker():
    fp = emit_footprint(SOIC8)
    silk_rects = [ln for ln in fp.splitlines() if "fp_rect" in ln and "F.SilkS" in ln]
    assert len(silk_rects) == 1
    # outline is courtyard minus 0.25mm margin
    half_w = SOIC8.courtyard_w / 2 - Decimal("0.25")
    assert f"(start -{half_w}" in silk_rects[0]
    circles = [ln for ln in fp.splitlines() if "fp_circle" in ln and "F.SilkS" in ln]
    assert len(circles) == 1
    # SOIC pad 1 sits in the -x/-y quadrant → marker at that corner
    assert "(center -" in circles[0]
    # determinism intact
    assert emit_footprint(build("SOIC", {"pins": Decimal(8)})) == fp


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not installed")
def test_footprint_with_silk_parses_in_kicad(tmp_path):
    fp = emit_footprint(SOIC8)
    body = "\n".join("  " + line for line in fp.splitlines())
    pcb = ('(kicad_pcb (version 20221018) (generator "ohm") (general (thickness 1.6))'
           ' (paper "A4") (layers (0 "F.Cu" signal) (31 "B.Cu" signal)) (net 0 "")\n'
           + body + "\n)")
    board = tmp_path / "t.kicad_pcb"
    board.write_text(pcb)
    r = subprocess.run(["kicad-cli", "pcb", "export", "pos", "-o", str(tmp_path / "t.pos"),
                        str(board)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---------- finding 7: python -m ohm ----------
def test_python_dash_m_ohm_version():
    r = subprocess.run([sys.executable, "-m", "ohm", "--version"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("ohm ")


# ---------- finding 6: single-sourced version + uninstalled fallback ----------
def test_version_falls_back_when_not_installed():
    import importlib
    import importlib.metadata

    import ohm

    real = importlib.metadata.version

    def raiser(_name):
        raise importlib.metadata.PackageNotFoundError

    importlib.metadata.version = raiser
    try:
        assert importlib.reload(ohm).__version__ == "0.1.0"
    finally:
        importlib.metadata.version = real
        importlib.reload(ohm)
    assert ohm.__version__  # restored, still a real version string
