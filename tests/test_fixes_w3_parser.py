"""W3 audit-fix gate: BOM tolerated, top-level indentation → clean P001 + recovery,
control chars rejected in strings, kicad emitters escape quotes, generated
footprints carry silkscreen outline + pin-1 marker, `python -m ohm` works,
version is single-sourced with an uninstalled fallback."""

import re
import subprocess
import sys
from decimal import Decimal

from ohm import ir
from ohm.emit_kicad import emit_kicad_pcb, emit_kicad_sch
from ohm.parse import parse
from ohm.pkggen.footprint import emit_footprint
from ohm.pkggen.packages import build

from tests.conftest import KICAD_CLI, requires_kicad_cli


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
    # the net needs a node: the schematic emits net names as labels AT PINS, so a
    # node-less net has nowhere to appear and could not exercise the escaping
    low = ir.IRLow(
        parts=(ir.PartLow(ref="m.r1", designator="R1", component='We"ird',
                          package=None, attrs=()),),
        nets=(ir.NetLow(name='NET"1',
                        nodes=(ir.NetNode("m.r1", "R1", "P1", "1"),), ports=()),),
    )
    sch = emit_kicad_sch(low, "t.ohm")
    pcb = emit_kicad_pcb(low, "t.ohm")
    assert 'We\\"ird' in sch and 'NET\\"1' in sch
    assert '"NET\\"1"' in pcb
    assert '"NET"1"' not in pcb  # a raw quote would corrupt the s-expression


# ---------- finding 5: footprint silkscreen outline + pin-1 marker ----------
SOIC8 = build("SOIC", {"pins": Decimal(8)})


def test_footprint_has_silk_outline_and_pin1_marker():
    """Silk marks the body and NEVER lands on a pad.

    This used to assert a single fp_rect at courtyard - 0.25mm. On any package
    whose pads reach the courtyard that rectangle lay across them: 76
    `silk_over_copper` violations on the fmuv2 board, and in fabrication silk on
    bare copper is either clipped by the mask or ends up on the solderable
    surface. The outline is now cut around the pads, so the invariant worth
    asserting is the clearance itself, not the shape it happens to leave.
    """
    fp = emit_footprint(SOIC8)
    silk = [ln for ln in fp.splitlines() if "fp_line" in ln and "F.SilkS" in ln]
    assert silk, "no silkscreen body outline at all"

    clear = Decimal("0.2")
    for line in silk:
        nums = [Decimal(v) for v in re.findall(r"-?\d+\.?\d*", line.split("(layer")[0])]
        (x1, y1, x2, y2) = nums[:4]
        for pad in SOIC8.pads:
            # segments are axis-aligned, so overlap is a box test on both axes
            on_x = (min(x1, x2) < pad.x + pad.w / 2 + clear
                    and max(x1, x2) > pad.x - pad.w / 2 - clear)
            on_y = (min(y1, y2) < pad.y + pad.h / 2 + clear
                    and max(y1, y2) > pad.y - pad.h / 2 - clear)
            assert not (on_x and on_y), f"silk {line.strip()} runs over pad {pad.id}"

    circles = [ln for ln in fp.splitlines() if "fp_circle" in ln and "F.SilkS" in ln]
    assert len(circles) == 1
    # SOIC pad 1 sits in the -x/-y quadrant → marker at that corner
    assert "(center -" in circles[0]
    # determinism intact
    assert emit_footprint(build("SOIC", {"pins": Decimal(8)})) == fp


@requires_kicad_cli
def test_footprint_with_silk_parses_in_kicad(tmp_path):
    fp = emit_footprint(SOIC8)
    body = "\n".join("  " + line for line in fp.splitlines())
    pcb = ('(kicad_pcb (version 20221018) (generator "ohm") (general (thickness 1.6))'
           ' (paper "A4") (layers (0 "F.Cu" signal) (31 "B.Cu" signal)) (net 0 "")\n'
           + body + "\n)")
    board = tmp_path / "t.kicad_pcb"
    board.write_text(pcb)
    r = subprocess.run([KICAD_CLI, "pcb", "export", "pos", "-o", str(tmp_path / "t.pos"),
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
        # must track the hardcoded fallback in ohm/__init__.py, which the release
        # commit bumps — 0.1.1 shipped with this assertion still on 0.1.0
        assert importlib.reload(ohm).__version__ == "0.1.1"
    finally:
        importlib.metadata.version = real
        importlib.reload(ohm)
    assert ohm.__version__  # restored, still a real version string
