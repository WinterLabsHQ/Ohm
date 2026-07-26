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
    result = compile_source(GOOD_GEN.replace("generate(SOIC, pins=8", "generate(TO220, pins=8"),
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


def _q(x: str, unit: str = "mm"):
    from ohm.ir import Quantity

    return Quantity(Decimal(x), unit)


KICAD_QFN32 = Path("/usr/share/kicad/footprints/Package_DFN_QFN.pretty/"
                   "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm.kicad_mod")


@pytest.mark.skipif(not KICAD_QFN32.exists(), reason="KiCad library not installed")
def test_qfn32_matches_kicad_golden_within_tolerance():
    golden = {}
    for m in re.finditer(r"\(pad (\d+) smd \w+ \(at ([-\d.]+) ([-\d.]+)\) \(size ([\d.]+) ([\d.]+)\)",
                         KICAD_QFN32.read_text()):
        pid, x, y, w, h = m.groups()
        golden[pid] = (float(x), float(y), float(w), float(h))
    model = build("QFN", {"pins": Decimal(32), "pitch": _q("0.5"), "body": _q("5")})
    assert len(model.pads) == 32
    for p in model.pads:
        gx, gy, gw, gh = golden[p.id]
        assert abs(float(p.x) - gx) < 0.02 and abs(float(p.y) - gy) < 0.02
        assert abs(float(p.w) - gw) < 0.02 and abs(float(p.h) - gh) < 0.02


def test_qfn_four_sides_and_thermal_pad():
    m = build("QFN", {"pins": Decimal(16), "pitch": _q("0.5"), "body": _q("3"),
                      "thermal_pad": _q("1.45")})
    assert len(m.pads) == 17  # 16 perimeter + EP
    assert m.pads[0].id == "1" and m.pads[0].x < 0 and m.pads[0].y < 0  # pin 1 top-left
    ep = m.pads[-1]
    assert ep.id == "17" and ep.x == 0 and ep.y == 0 and ep.w == Decimal("1.45")
    # counterclockwise: pins 1-4 left, 5-8 bottom, 9-12 right, 13-16 top
    assert m.pads[4].y == max(p.y for p in m.pads[:16])   # pad 5 on the bottom edge
    assert m.pads[8].x == max(p.x for p in m.pads[:16])   # pad 9 on the right edge


def test_qfn_bad_params_raise():
    with pytest.raises(PkgError):
        build("QFN", {"pins": Decimal(30), "body": _q("5")})  # not a multiple of 4
    with pytest.raises(PkgError):
        build("QFN", {"pins": Decimal(32)})  # missing body


KICAD_LQFP48 = Path("/usr/share/kicad/footprints/Package_QFP.pretty/LQFP-48_7x7mm_P0.5mm.kicad_mod")


@pytest.mark.skipif(not KICAD_LQFP48.exists(), reason="KiCad library not installed")
def test_qfp48_matches_kicad_golden_within_tolerance():
    golden = {}
    for m in re.finditer(r"\(pad (\d+) smd \w+ \(at ([-\d.]+) ([-\d.]+)\) \(size ([\d.]+) ([\d.]+)\)",
                         KICAD_LQFP48.read_text()):
        golden[m.group(1)] = tuple(float(x) for x in m.groups()[1:])
    model = build("QFP", {"pins": Decimal(48), "pitch": _q("0.5"), "span": _q("9")})
    assert len(model.pads) == 48
    for p in model.pads:
        gx, gy, gw, gh = golden[p.id]
        assert abs(float(p.x) - gx) <= 0.05 and abs(float(p.y) - gy) <= 0.05
        assert abs(float(p.w) - gw) <= 0.06 and abs(float(p.h) - gh) <= 0.06


def test_qfp_four_sides():
    m = build("QFP", {"pins": Decimal(32), "pitch": _q("0.8"), "span": _q("9")})
    assert len(m.pads) == 32
    assert m.pads[0].id == "1" and m.pads[0].x < 0 and m.pads[0].y < 0  # pin 1 top-left
    assert m.pads[8].y == max(p.y for p in m.pads)   # pad 9 on the bottom edge
    assert m.pads[16].x == max(p.x for p in m.pads)  # pad 17 on the right edge


def test_qfp_bad_params_raise():
    with pytest.raises(PkgError):
        build("QFP", {"pins": Decimal(30), "span": _q("9")})  # not a multiple of 4
    with pytest.raises(PkgError):
        build("QFP", {"pins": Decimal(32)})  # missing span


KICAD_DFN8 = Path("/usr/share/kicad/footprints/Package_DFN_QFN.pretty/DFN-8_2x2mm_P0.5mm.kicad_mod")


@pytest.mark.skipif(not KICAD_DFN8.exists(), reason="KiCad library not installed")
def test_dfn8_matches_kicad_golden_within_tolerance():
    golden = {}
    for m in re.finditer(r'\(pad "(\d+)" smd \w+ \(at ([-\d.]+) ([-\d.]+)\) \(size ([\d.]+) ([\d.]+)\)',
                         KICAD_DFN8.read_text()):
        golden[m.group(1)] = tuple(float(x) for x in m.groups()[1:])
    model = build("DFN", {"pins": Decimal(8), "pitch": _q("0.5"), "body": _q("2")})
    assert len(model.pads) == 8
    for p in model.pads:
        gx, gy, gw, gh = golden[p.id]
        assert abs(float(p.x) - gx) <= 0.05 and abs(float(p.y) - gy) <= 0.05
        assert abs(float(p.w) - gw) <= 0.1 and abs(float(p.h) - gh) <= 0.1


def test_dfn_son_two_columns():
    for pkg in ("DFN", "SON"):
        m = build(pkg, {"pins": Decimal(8), "pitch": _q("0.5"), "body": _q("2")})
        assert len(m.pads) == 8
        assert m.pads[0].x < 0 and m.pads[0].y < 0     # pin 1 top-left
        assert m.pads[4].x > 0 and m.pads[4].y > 0     # pin 5 bottom-right (counterclockwise)
        assert {round(float(p.x), 2) for p in m.pads} == {-0.94, 0.94}  # exactly two columns


KICAD_BGA49 = Path("/usr/share/kicad/footprints/Package_BGA.pretty/"
                   "Analog_BGA-49_6.25x6.25mm_Layout7x7_P0.8mm_Ball0.5mm_Pad0.4mm.kicad_mod")


@pytest.mark.skipif(not KICAD_BGA49.exists(), reason="KiCad library not installed")
def test_bga49_matches_kicad_golden_exactly():
    golden = {}
    for m in re.finditer(r"\(pad (\w+) smd circle \(at ([-\d.]+) ([-\d.]+)\) \(size ([\d.]+) ([\d.]+)\)",
                         KICAD_BGA49.read_text()):
        golden[m.group(1)] = tuple(float(x) for x in m.groups()[1:])
    model = build("BGA", {"rows": Decimal(7), "cols": Decimal(7), "pitch": _q("0.8"), "ball": _q("0.5")})
    assert len(model.pads) == 49
    for p in model.pads:
        assert p.shape == "circle"
        gx, gy, gw, gh = golden[p.id]
        assert abs(float(p.x) - gx) < 0.01 and abs(float(p.y) - gy) < 0.01
        assert abs(float(p.w) - gw) < 0.01


def test_bga_jedec_lettering_skips_forbidden_rows():
    m = build("BGA", {"rows": Decimal(10), "cols": Decimal(2), "pitch": _q("1.0"), "pad": _q("0.4")})
    rows = [p.id[:-1] for p in m.pads if p.id.endswith("1")]
    assert rows == list("ABCDEFGHJK")  # I skipped
    assert m.pads[0].id == "A1" and m.pads[-1].id == "K2"


def test_bga_needs_ball_or_pad():
    with pytest.raises(PkgError):
        build("BGA", {"rows": Decimal(4), "cols": Decimal(4), "pitch": _q("0.8")})


KICAD_PLCC28 = Path("/usr/share/kicad/footprints/Package_LCC.pretty/PLCC-28.kicad_mod")


@pytest.mark.skipif(not KICAD_PLCC28.exists(), reason="KiCad library not installed")
def test_plcc28_matches_kicad_golden_within_tolerance():
    golden = {}
    for m in re.finditer(r'\(pad "(\d+)" smd \w+ \(at ([-\d.]+) ([-\d.]+)\) \(size ([\d.]+) ([\d.]+)\)',
                         KICAD_PLCC28.read_text()):
        golden[m.group(1)] = tuple(float(x) for x in m.groups()[1:])
    model = build("PLCC", {"pins": Decimal(28)})
    assert len(model.pads) == 28
    for p in model.pads:
        gx, gy, gw, gh = golden[p.id]
        assert abs(float(p.x) - gx) <= 0.02 and abs(float(p.y) - gy) <= 0.02
        assert abs(float(p.w) - gw) <= 0.02 and abs(float(p.h) - gh) <= 0.02


def test_plcc_pin1_top_center_counterclockwise():
    m = build("PLCC", {"pins": Decimal(44)})
    at = {p.id: (round(float(p.x), 2), round(float(p.y), 2)) for p in m.pads}
    assert at["1"][0] == 0 and at["1"][1] < 0        # pin 1 top-center
    assert at["2"][0] < 0 and at["2"][1] == at["1"][1]  # pin 2 to the left
    assert at["11"][0] < 0 and at["23"][1] > 0       # left edge, bottom edge
    assert at["34"][0] > 0                             # right edge


def test_plcc_needs_odd_per_side():
    with pytest.raises(PkgError):
        build("PLCC", {"pins": Decimal(32)})  # 8/side (even) — rectangular, unsupported
    with pytest.raises(PkgError):
        build("PLCC", {"pins": Decimal(30)})  # not a multiple of 4


KICAD_SOT223 = Path("/usr/share/kicad/footprints/Package_TO_SOT_SMD.pretty/SOT-223.kicad_mod")


@pytest.mark.skipif(not KICAD_SOT223.exists(), reason="KiCad library not installed")
def test_sot223_matches_kicad_golden():
    golden = {}
    for m in re.finditer(r'\(pad "(\d+)" smd \w+ \(at ([-\d.]+) ([-\d.]+)\) \(size ([\d.]+) ([\d.]+)\)',
                         KICAD_SOT223.read_text()):
        golden[m.group(1)] = tuple(float(x) for x in m.groups()[1:])
    model = build("SOT223", {})
    assert len(model.pads) == 4
    for p in model.pads:
        gx, gy, gw, gh = golden[p.id]
        assert (abs(float(p.x) - gx) < 0.01 and abs(float(p.y) - gy) < 0.01
                and abs(float(p.w) - gw) < 0.01 and abs(float(p.h) - gh) < 0.01)
    tab = model.pads[3]  # pad 4 is the wide tab
    assert float(tab.h) > float(model.pads[0].h)


def test_sot223_takes_no_args():
    with pytest.raises(PkgError):
        build("SOT223", {"pins": Decimal(3)})


_FIXED_REFS = {
    "SOT89": ("SOT-89-3", ["1", "3"]),
    "TO252": ("TO-252-3_TabPin2", ["1", "3"]),
    "TO263": ("TO-263-3_TabPin2", ["1", "3"]),
}


@pytest.mark.parametrize("pkg,ref_leads", list(_FIXED_REFS.items()))
def test_fixed_power_package_signal_leads_match_kicad(pkg, ref_leads):
    ref_name, leads = ref_leads
    ref_path = Path(f"/usr/share/kicad/footprints/Package_TO_SOT_SMD.pretty/{ref_name}.kicad_mod")
    if not ref_path.exists():
        pytest.skip("KiCad library not installed")
    golden = {}
    for m in re.finditer(r'\(pad "(\d+)" smd \w+ \(at ([-\d.]+) ([-\d.]+)\) \(size ([\d.]+) ([\d.]+)\)',
                         ref_path.read_text()):
        golden.setdefault(m.group(1), tuple(float(x) for x in m.groups()[1:]))
    model = build(pkg, {})
    assert len(model.pads) == 4  # 3 signal leads + solid tab (pad 4)
    byid = {p.id: p for p in model.pads}
    for lead in leads:  # tab is intentionally a solid rect; validate the signal leads
        gx, gy, gw, gh = golden[lead]
        p = byid[lead]
        assert (abs(float(p.x) - gx) < 0.01 and abs(float(p.y) - gy) < 0.01
                and abs(float(p.w) - gw) < 0.01 and abs(float(p.h) - gh) < 0.01)
    assert float(byid["4"].w) * float(byid["4"].h) > float(byid["1"].w) * float(byid["1"].h)


def test_lga_grid_and_border():
    full = build("LGA", {"rows": Decimal(4), "cols": Decimal(4), "pitch": _q("0.5"), "pad": _q("0.3")})
    assert len(full.pads) == 16
    assert all(p.shape == "roundrect" for p in full.pads)  # LGA lands are rectangular
    assert full.pads[0].id == "A1"
    ring = build("LGA", {"rows": Decimal(5), "cols": Decimal(5), "pitch": _q("0.5"),
                         "pad": _q("0.3"), "border": Decimal(1)})
    assert len(ring.pads) == 16  # 5x5 perimeter = 25 - 9


def test_bga_border_depopulates_center():
    full = build("BGA", {"rows": Decimal(8), "cols": Decimal(8), "pitch": _q("0.8"), "ball": _q("0.4")})
    assert len(full.pads) == 64
    ring = build("BGA", {"rows": Decimal(8), "cols": Decimal(8), "pitch": _q("0.8"),
                        "ball": _q("0.4"), "border": Decimal(2)})
    assert len(ring.pads) == 64 - 16  # hollow 4x4 center removed
    assert all(p.shape == "circle" for p in ring.pads)


def test_bad_params_raise():
    with pytest.raises(PkgError):
        build("SOIC", {"pins": Decimal(7)})  # odd
    with pytest.raises(PkgError):
        build("SOT23", {"pins": Decimal(4)})
    with pytest.raises(PkgError):
        build("TO220", {"pins": Decimal(64)})  # not in the catalog


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
    mod = (tmp_path / "out" / "SOIC-8.kicad_mod").read_text()  # footprint is spec-named, shared
    sym = (tmp_path / "out" / "Op8.kicad_sym").read_text()
    assert mod.count("(pad ") == 8
    assert '"OUTA"' in sym and '"VDD"' in sym  # symbol uses the declared pin names


@pytest.mark.skipif(shutil.which("kicad-cli") is None, reason="kicad-cli not installed")
def test_generated_symbol_parses_in_kicad(tmp_path):
    pins = [(p.id, f"P{p.id}", "passive") for p in SOIC8.pads]
    lib = tmp_path / "ohm_gen.kicad_sym"
    lib.write_text(emit_symbol("SOIC8", pins))
    r = subprocess.run(["kicad-cli", "sym", "upgrade", str(lib)], capture_output=True, text=True)
    if r.returncode != 0:
        out = r.stderr + r.stdout
        low = out.lower()
        if any(m in low for m in ("unknown command", "invalid command", "unrecognized",
                                  "no such command", "usage:")):
            pytest.skip(f"kicad-cli 'sym upgrade' unsupported here: {out.strip()[:200]}")
        pytest.fail(out)
