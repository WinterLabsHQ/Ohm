"""STEP body envelope.

The oracle here is a real STEP reader: gmsh embeds OpenCASCADE, so if it can load
the file, form a solid from the shell and mesh it, the geometry is genuinely valid —
not merely well-formed text. Where gmsh is absent these tests skip honestly rather
than pretending the format was checked.
"""

import re
import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.ir import Quantity
from ohm.pkggen.footprint import emit_footprint
from ohm.pkggen.packages import PkgError, build
from ohm.pkggen.step import emit_step_box

GMSH = shutil.which("gmsh")
needs_gmsh = pytest.mark.skipif(GMSH is None, reason="gmsh (STEP reader) not installed")


def _q(v: str) -> Quantity:
    return Quantity(Decimal(v), "mm")


BODY = {"body_x": _q("4.9"), "body_y": _q("3.9"), "height": _q("1.75")}


def _mesh(step: Path) -> tuple[float, int]:
    """Mesh the solid with gmsh; returns (volume, solid count). Meshing only
    succeeds on a watertight, correctly-oriented shell."""
    out = subprocess.run(
        [GMSH, str(step), "-3", "-o", str(step.with_suffix(".msh")), "-format", "msh2"],
        capture_output=True, text=True, timeout=180)
    assert "Error" not in out.stdout, out.stdout
    vol = re.search(r"volume = ([\d.eE+-]+)", out.stdout)
    solids = re.search(r"Meshing (\d+) volume", out.stdout)
    assert vol and solids, out.stdout
    return float(vol.group(1)), int(solids.group(1))


# ---------- the geometry is real ----------
@needs_gmsh
def test_step_is_a_watertight_solid_of_the_right_volume(tmp_path):
    step = tmp_path / "b.step"
    step.write_text(emit_step_box("SOIC-8", Decimal("4.9"), Decimal("3.9"),
                                  Decimal("1.75")))
    volume, solids = _mesh(step)
    assert solids == 1
    assert volume == pytest.approx(4.9 * 3.9 * 1.75, rel=1e-9)


@needs_gmsh
def test_body_sits_on_the_board_and_is_centred(tmp_path):
    step = tmp_path / "b.step"
    step.write_text(emit_step_box("X", Decimal("6"), Decimal("4"), Decimal("2")))
    geo = tmp_path / "r.geo"
    geo.write_text(f'SetFactory("OpenCASCADE");\nMerge "{step}";\n'
                   'bb() = BoundingBox Volume{1};\n'
                   'Printf("BB %g %g %g %g %g %g", bb(0), bb(1), bb(2),'
                   ' bb(3), bb(4), bb(5));\n')
    out = subprocess.run([GMSH, str(geo), "-0"], capture_output=True, text=True,
                         timeout=120)
    bb = re.search(r"BB ([-\d.e+ ]+)", out.stdout)
    assert bb, out.stdout + out.stderr
    x0, y0, z0, x1, y1, z1 = (float(v) for v in bb.group(1).split())
    assert (x0, x1) == pytest.approx((-3.0, 3.0), abs=1e-6)
    assert (y0, y1) == pytest.approx((-2.0, 2.0), abs=1e-6)
    assert (z0, z1) == pytest.approx((0.0, 2.0), abs=1e-6)  # seats on the board


@needs_gmsh
def test_a_generated_package_body_meshes(tmp_path):
    model = build("QFN", {"pins": Decimal(16), "pitch": _q("0.5"), "body": _q("3"),
                          **{"body_x": _q("3"), "body_y": _q("3"),
                             "height": _q("0.9")}})
    step = tmp_path / f"{model.name}.step"
    step.write_text(emit_step_box(model.name, *model.body))
    volume, solids = _mesh(step)
    assert solids == 1 and volume == pytest.approx(3 * 3 * 0.9, rel=1e-9)


# ---------- structure / determinism ----------
def test_header_is_iso_10303_21_ap214():
    text = emit_step_box("X", Decimal(2), Decimal(2), Decimal(1))
    assert text.startswith("ISO-10303-21;")
    assert text.rstrip().endswith("END-ISO-10303-21;")
    assert "AUTOMOTIVE_DESIGN" in text
    assert "SI_UNIT(.MILLI.,.METRE.)" in text  # dimensions are millimetres


def test_six_faces_one_closed_shell():
    text = emit_step_box("X", Decimal(2), Decimal(2), Decimal(1))
    assert text.count("ADVANCED_FACE") == 6
    assert text.count("CLOSED_SHELL") == 1
    assert text.count("EDGE_CURVE") == 12  # a prism has 12 edges, each shared once


def test_emission_is_deterministic():
    a = emit_step_box("X", Decimal("1.5"), Decimal(2), Decimal(1))
    b = emit_step_box("X", Decimal("1.5"), Decimal(2), Decimal(1))
    assert a.encode() == b.encode()
    assert "1970-01-01" in a  # fixed stamp, never "now"


@pytest.mark.parametrize("dims", [(0, 1, 1), (1, -1, 1), (1, 1, 0)])
def test_non_positive_dimensions_rejected(dims):
    with pytest.raises(ValueError):
        emit_step_box("X", *(Decimal(d) for d in dims))


# ---------- the package + footprint wiring ----------
def test_body_is_never_inferred():
    """No body dimensions in, no 3D model out — the whole point."""
    model = build("SOIC", {"pins": Decimal(8)})
    assert model.body is None
    assert "(model" not in emit_footprint(model)


def test_partial_body_is_an_error_not_a_guess():
    with pytest.raises(PkgError, match="body_x, body_y and height"):
        build("SOIC", {"pins": Decimal(8), "height": _q("1.75")})


def test_body_becomes_part_of_the_package_identity():
    """Two heights must not collapse onto one shared footprint file."""
    a = build("SOIC", {"pins": Decimal(8), **BODY})
    b = build("SOIC", {"pins": Decimal(8), **{**BODY, "height": _q("1.2")}})
    assert a.name == "SOIC-8_4.9x3.9x1.75mm"
    assert a.name != b.name


def test_footprint_references_the_step_model():
    model = build("SOIC", {"pins": Decimal(8), **BODY})
    fp = emit_footprint(model)
    assert f'(model "${{KIPRJMOD}}/{model.name}.step"' in fp
    assert "(offset (xyz 0 0 0))" in fp


def test_cli_writes_the_step_next_to_the_footprint():
    src = ("component U1:\n"
           "    package = generate(SOIC, pins=8, pitch=1.27mm,"
           " body_x=4.9mm, body_y=3.9mm, height=1.75mm)\n"
           + "".join(f"    pin P{i}: role=passive, pad={i}\n" for i in range(1, 9))
           + "module M:\n    u = U1()\n")
    result = compile_source(src, "u1.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    from ohm.cli import _emit_generated_packages

    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        _emit_generated_packages(result, out)
        names = sorted(p.name for p in out.iterdir())
        step = next(p for p in out.iterdir() if p.suffix == ".step")
        mod = next(p for p in out.iterdir() if p.suffix == ".kicad_mod")
        assert step.stem == mod.stem  # the model reference resolves
        assert step.read_text().startswith("ISO-10303-21;")
    assert names == ["SOIC-8_4.9x3.9x1.75mm.kicad_mod",
                     "SOIC-8_4.9x3.9x1.75mm.step", "U1.kicad_sym"]
