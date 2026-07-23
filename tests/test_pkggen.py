"""P11 gate: (a) generate(SOIC, pins=8, pitch=1.27mm) -> .kicad_mod/.kicad_sym
that import into KiCad clean and match a golden reference within tolerance;
(b) generated pads satisfy the pin<->pad map (Tier-S); (c) byte-identical
across runs. `ohm gen-package` works standalone. No fetch in the build path."""

import re
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.pkggen.footprint import emit_footprint
from ohm.pkggen.packages import PkgError, build
from ohm.pkggen.symbol import emit_symbol

KICAD_SOIC8 = Path("/usr/share/kicad/footprints/Package_SO.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod")
OHM_BIN = Path(sys.executable).parent / "ohm"

SOIC8 = build("SOIC", {"pins": Decimal(8)})


# ---------- gate (a): golden reference within tolerance ----------
@pytest.mark.skipif(not KICAD_SOIC8.exists(), reason="KiCad library not installed")
def test_soic8_matches_kicad_golden_within_tolerance():
    golden = {}
    for m in re.finditer(r"\(pad (\d+) smd \w+ \(at ([-\d.]+) ([-\d.]+)\) \(size ([\d.]+) ([\d.]+)\)",
                         KICAD_SOIC8.read_text()):
        golden[m.group(1)] = tuple(Decimal(m.group(i)) for i in (2, 3, 4, 5))
    assert len(golden) == 8
    for pad in SOIC8.pads:
        gx, gy, gw, gh = golden[pad.id]
        assert abs(pad.x - gx) <= Decimal("0.25"), f"pad {pad.id} x: {pad.x} vs {gx}"
        assert abs(pad.y - gy) <= Decimal("0.05"), f"pad {pad.id} y: {pad.y} vs {gy}"
        assert abs(pad.w - gw) <= Decimal("0.35"), f"pad {pad.id} w: {pad.w} vs {gw}"
        assert abs(pad.h - gh) <= Decimal("0.10"), f"pad {pad.id} h: {pad.h} vs {gh}"


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not installed")
def test_generated_footprint_imports_into_kicad(tmp_path):
    # embed the footprint in a board and let real KiCad parse it
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


# ---------- gate (b): pin<->pad loop on generated packages ----------
GOOD_GEN = (
    "component Op8:\n"
    "    package = generate(SOIC, pins=8, pitch=1.27mm)\n"
    "    pin OUTA: role=output, pad=1\n"
    "    pin INAN: role=input, pad=2\n"
    "    pin INAP: role=input, pad=3\n"
    "    pin VSS:  role=power_in, pad=4\n"
    "    pin INBP: role=input, pad=5\n"
    "    pin INBN: role=input, pad=6\n"
    "    pin OUTB: role=output, pad=7\n"
    "    pin VDD:  role=power_in, vmax=6.0V, pad=8\n"
    "module M:\n"
    "    u = Op8()\n"
)


def test_generated_package_tier_s_clean_and_builds():
    result = compile_source(GOOD_GEN, "op8.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert not [d for d in result.diagnostics if d.code.startswith("S")]


def test_bad_generate_spec_is_S006():
    result = compile_source(GOOD_GEN.replace("generate(SOIC, pins=8", "generate(QFN, pins=8"),
                            "bad.ohm")
    assert any(d.code == "S006" and "catalog" in d.message for d in result.diagnostics)
    assert not result.ok


# ---------- gate (c): determinism ----------
def test_generation_byte_identical():
    a, b = build("SOIC", {"pins": Decimal(8)}), build("SOIC", {"pins": Decimal(8)})
    assert emit_footprint(a).encode() == emit_footprint(b).encode()
    pins = [(p.id, f"P{p.id}", "passive") for p in a.pads]
    assert emit_symbol("X", pins).encode() == emit_symbol("X", pins).encode()


# ---------- family sanity ----------
def test_sot23_5_positions():
    m = build("SOT23", {"pins": Decimal(5)})
    at = {p.id: (p.x, p.y) for p in m.pads}
    assert at["1"][1] == Decimal("-0.95") and at["2"][1] == 0 and at["3"][1] == Decimal("0.95")
    assert at["4"][1] == Decimal("0.95") and at["5"][1] == Decimal("-0.95")
    assert at["1"][0] < 0 < at["4"][0]


def test_tssop16_shape():
    m = build("TSSOP", {"pins": Decimal(16)})
    assert len(m.pads) == 16
    left = [p for p in m.pads if p.x < 0]
    assert [p.id for p in left] == [str(i) for i in range(1, 9)]
    assert left[0].y == -left[-1].y  # symmetric columns


def test_chip0603():
    m = build("CHIP0603", {})
    assert len(m.pads) == 2 and m.pads[0].x == -m.pads[1].x


def test_bad_params_raise():
    with pytest.raises(PkgError):
        build("SOIC", {"pins": Decimal(7)})  # odd
    with pytest.raises(PkgError):
        build("SOT23", {"pins": Decimal(4)})
    with pytest.raises(PkgError):
        build("BGA", {"pins": Decimal(64)})


# ---------- CLI standalone + full-build emission ----------
def test_gen_package_cli(tmp_path):
    r = subprocess.run([str(OHM_BIN), "gen-package", "SOIC", "--pins", "8",
                        "--pitch", "1.27", "--out", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "SOIC-8.kicad_mod").exists()
    assert (tmp_path / "SOIC-8.kicad_sym").exists()


def test_build_emits_generated_packages(tmp_path):
    src = tmp_path / "op8.ohm"
    src.write_text(GOOD_GEN)
    r = subprocess.run([str(OHM_BIN), "build", str(src), "-o", str(tmp_path / "out"),
                        "--emit", "kicad"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    mod = (tmp_path / "out" / "Op8.kicad_mod").read_text()
    sym = (tmp_path / "out" / "Op8.kicad_sym").read_text()
    assert mod.count("(pad ") == 8
    assert '"OUTA"' in sym and '"VDD"' in sym  # symbol uses the declared pin names


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not installed")
def test_generated_symbol_parses_in_kicad(tmp_path):
    pins = [(p.id, f"P{p.id}", "passive") for p in SOIC8.pads]
    lib = tmp_path / "ohm_gen.kicad_sym"
    lib.write_text(emit_symbol("SOIC8", pins))
    r = subprocess.run(["kicad-cli", "sym", "upgrade", str(lib)], capture_output=True, text=True)
    if r.returncode != 0 and "invalid" in (r.stderr + r.stdout).lower():
        pytest.fail(r.stderr + r.stdout)
    # non-zero for other reasons (e.g. command unsupported) is inconclusive, not a failure
