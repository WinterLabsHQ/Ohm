"""P11 gate: (a) generate(SOIC, pins=8, pitch=1.27mm) -> .kicad_mod/.kicad_sym
that import into KiCad clean and match a golden reference within tolerance;
(b) generated pads satisfy the pin<->pad map (Tier-S); (c) byte-identical
across runs. `ohm gen-package` works standalone. No fetch in the build path."""

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from tests.kicad_golden import pads_from_kicad_mod
from ohm.driver import compile_source
from ohm.pkggen.footprint import emit_footprint
from ohm.pkggen.packages import PkgError, build
from ohm.pkggen.symbol import emit_symbol

from tests.conftest import (KICAD_CLI, KICAD_SYMBOLS, NOT_APPLICABLE, first_footprint,
                            footprint, requires_footprint, requires_kicad_cli,
                            requires_kicad_sym)

SOIC8_MOD = "Package_SO.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod"
KICAD_SOIC8 = footprint(SOIC8_MOD)
OHM_BIN = Path(sys.executable).parent / "ohm"

SOIC8 = build("SOIC", {"pins": Decimal(8)})


# ---------- gate (a): golden reference within tolerance ----------
@requires_footprint(SOIC8_MOD)
def test_soic8_matches_kicad_golden_within_tolerance():
    golden = pads_from_kicad_mod(KICAD_SOIC8)
    assert len(golden) == 8
    for pad in SOIC8.pads:
        gx, gy, gw, gh = golden[pad.id]
        assert abs(pad.x - gx) <= Decimal("0.25"), f"pad {pad.id} x: {pad.x} vs {gx}"
        assert abs(pad.y - gy) <= Decimal("0.05"), f"pad {pad.id} y: {pad.y} vs {gy}"
        assert abs(pad.w - gw) <= Decimal("0.35"), f"pad {pad.id} w: {pad.w} vs {gw}"
        assert abs(pad.h - gh) <= Decimal("0.10"), f"pad {pad.id} h: {pad.h} vs {gh}"


@requires_kicad_cli
def test_generated_footprint_imports_into_kicad(tmp_path):
    # embed the footprint in a board and let real KiCad parse it
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


QFN32_MOD = ("Package_DFN_QFN.pretty/"
             "QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm.kicad_mod")
KICAD_QFN32 = footprint(QFN32_MOD)


@requires_footprint(QFN32_MOD)
def test_qfn32_matches_kicad_golden_within_tolerance():
    golden = pads_from_kicad_mod(KICAD_QFN32, as_float=True)
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


LQFP48_MOD = "Package_QFP.pretty/LQFP-48_7x7mm_P0.5mm.kicad_mod"
KICAD_LQFP48 = footprint(LQFP48_MOD)


@requires_footprint(LQFP48_MOD)
def test_qfp48_matches_kicad_golden_within_tolerance():
    golden = pads_from_kicad_mod(KICAD_LQFP48, as_float=True)
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


DFN8_MOD = "Package_DFN_QFN.pretty/DFN-8_2x2mm_P0.5mm.kicad_mod"
KICAD_DFN8 = footprint(DFN8_MOD)


@requires_footprint(DFN8_MOD)
def test_dfn8_matches_kicad_golden_within_tolerance():
    golden = pads_from_kicad_mod(KICAD_DFN8, as_float=True)
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


# The name carried a `_Ball0.5mm_Pad0.4mm` suffix that no KiCad ships — verified
# absent from 8.0.9, 9.0.9 and 10.0.5 alike — so this comparison had never once
# run, on any machine, at any version. Found by OHM_REQUIRE_KICAD, which is the
# whole reason a skipped test is not allowed to pass a certification run.
# KiCad renamed this reference between 8 and 9: the ball/pad suffix was
# dropped. Naming both spellings keeps the comparison running across the
# whole supported range instead of silently skipping on one half of it.
# (Pinned to the 8 spelling alone, it had never run on 9 or 10; pinned to
# the 9/10 spelling alone, it stopped running on 8. Both were invisible
# until OHM_REQUIRE_KICAD made a skipped test fail a certification run.)
BGA49_MODS = ("Package_BGA.pretty/Analog_BGA-49_6.25x6.25mm_Layout7x7_P0.8mm.kicad_mod",
              "Package_BGA.pretty/Analog_BGA-49_6.25x6.25mm_Layout7x7_P0.8mm_Ball0.5mm_Pad0.4mm.kicad_mod")
KICAD_BGA49 = first_footprint(*BGA49_MODS)


@requires_footprint(*BGA49_MODS)
def test_bga49_matches_kicad_golden_exactly():
    golden = pads_from_kicad_mod(KICAD_BGA49, as_float=True)
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


PLCC28_MOD = "Package_LCC.pretty/PLCC-28.kicad_mod"
KICAD_PLCC28 = footprint(PLCC28_MOD)


@requires_footprint(PLCC28_MOD)
def test_plcc28_matches_kicad_golden_within_tolerance():
    golden = pads_from_kicad_mod(KICAD_PLCC28, as_float=True)
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
        build("PLCC", {"pins": Decimal(24)})  # 6/side (even) and not a rectangular count
    with pytest.raises(PkgError):
        build("PLCC", {"pins": Decimal(30)})  # not a multiple of 4


PLCC32_MOD = "Package_LCC.pretty/PLCC-32_11.4x14.0mm_P1.27mm.kicad_mod"
KICAD_PLCC32 = footprint(PLCC32_MOD)


@requires_footprint(PLCC32_MOD)
def test_plcc32_rectangular_matches_kicad_topology():
    """PLCC-32 is the one JEDEC rectangular PLCC: 7 pads top/bottom, 9 left/right.

    KiCad ships no *generic* PLCC-32 (only the dimensioned 11.4x14.0mm variant, whose
    land style differs from the generic PLCC-NN family Ohm reproduces), so this
    asserts numbering, side split, pitch and pad orientation — not radial offset.
    """
    golden = pads_from_kicad_mod(KICAD_PLCC32, as_float=True)
    model = build("PLCC", {"pins": Decimal(32)})
    assert len(model.pads) == 32
    assert model.courtyard_w != model.courtyard_h  # rectangular courtyard

    ours = {p.id: p for p in model.pads}
    for pid, (gx, gy, gw, gh) in golden.items():
        p = ours[pid]
        # same side, same order along that side, same tall/wide orientation
        assert (float(p.x) > 0) == (gx > 0) and (float(p.y) > 0) == (gy > 0)
        assert (float(p.h) > float(p.w)) == (gh > gw)
        on_tb = abs(gy) > abs(gx) if gw != gh else False
        assert abs((float(p.x) if on_tb else float(p.y))
                   - (gx if on_tb else gy)) <= 0.02  # tangential position is exact

    tall = lambda p: float(p.h) > float(p.w)  # noqa: E731 — top/bottom pads are tall
    top = {p.id for p in model.pads if tall(p) and float(p.y) < 0}
    left = {p.id for p in model.pads if not tall(p) and float(p.x) < 0}
    assert top == {"1", "2", "3", "4", "30", "31", "32"}
    assert left == {str(i) for i in range(5, 14)}  # 9 on the left edge


def test_plcc_square_counts_unchanged_by_rectangular_support():
    """The 20/28/44/… path must be byte-identical after the generalisation."""
    for pins, y1 in ((20, -4.05), (28, -5.32), (44, -7.86)):  # 0.01mm grid
        m = build("PLCC", {"pins": Decimal(pins)})
        assert len(m.pads) == pins and m.courtyard_w == m.courtyard_h
        pad1 = next(p for p in m.pads if p.id == "1")
        assert float(pad1.x) == 0 and float(pad1.y) == y1


SOT223_MOD = "Package_TO_SOT_SMD.pretty/SOT-223.kicad_mod"
KICAD_SOT223 = footprint(SOT223_MOD)


@requires_footprint(SOT223_MOD)
def test_sot223_matches_kicad_golden():
    golden = pads_from_kicad_mod(KICAD_SOT223, as_float=True)
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
    ref_path = footprint(f"Package_TO_SOT_SMD.pretty/{ref_name}.kicad_mod")
    if ref_path is None or not ref_path.exists():
        pytest.skip("KiCad library not installed")
    golden = pads_from_kicad_mod(ref_path, as_float=True)
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


@requires_kicad_cli
def test_generated_symbol_parses_in_kicad(tmp_path):
    pins = [(p.id, f"P{p.id}", "passive") for p in SOIC8.pads]
    lib = tmp_path / "ohm_gen.kicad_sym"
    lib.write_text(emit_symbol("SOIC8", pins))
    r = subprocess.run([KICAD_CLI, "sym", "upgrade", str(lib)], capture_output=True, text=True)
    if r.returncode != 0:
        out = r.stderr + r.stdout
        low = out.lower()
        if any(m in low for m in ("unknown command", "invalid command", "unrecognized",
                                  "no such command", "usage:")):
            # NOT_APPLICABLE: `sym upgrade` is a capability some supported KiCads
            # genuinely lack, not a broken environment. Without the sentinel this
            # skip's reason contains "kicad", so OHM_REQUIRE_KICAD converts it to a
            # FAILURE on exactly the versions where the command does not exist —
            # the certification run would fail for the one reason it must not.
            pytest.skip(f"{NOT_APPLICABLE} kicad-cli 'sym upgrade' unsupported here: "
                        f"{out.strip()[:200]}")
        pytest.fail(out)


# ---------- symbol: the fields DRC/ERC and BOM actually consume ----------
def test_symbol_is_linked_to_its_footprint():
    """An empty Footprint property means the part lands in the schematic
    unassigned and cannot be pushed to a board — it must arrive linked."""
    src = ("component U1:\n"
           "    package = generate(SOIC, pins=8, pitch=1.27mm)\n"
           + "".join(f"    pin P{i}: role=passive, pad={i}\n" for i in range(1, 9))
           + "module M:\n    u = U1()\n")
    result = compile_source(src, "u1.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    import tempfile

    from ohm.cli import _emit_generated_packages

    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        _emit_generated_packages(result, out)
        sym = (out / "U1.kicad_sym").read_text()
        mods = [p.stem for p in out.glob("*.kicad_mod")]
    assert mods == ["SOIC-8"]
    assert '(property "Footprint" "ohm:SOIC-8"' in sym
    assert '(property "Footprint" ""' not in sym


def test_symbol_carries_bom_attrs():
    src = ('component U1:\n'
           '    package = generate(SOIC, pins=8, pitch=1.27mm)\n'
           '    mpn = "MCP6002"\n'
           '    manufacturer = "Microchip"\n'
           '    datasheet = "https://example.com/ds.pdf"\n'
           + "".join(f"    pin P{i}: role=passive, pad={i}\n" for i in range(1, 9))
           + "module M:\n    u = U1()\n")
    result = compile_source(src, "u1.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    import tempfile

    from ohm.cli import _emit_generated_packages

    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        _emit_generated_packages(result, out)
        sym = (out / "U1.kicad_sym").read_text()
    assert '(property "MPN" "MCP6002"' in sym
    assert '(property "Manufacturer" "Microchip"' in sym
    assert '(property "Datasheet" "https://example.com/ds.pdf"' in sym


def test_every_role_maps_to_a_real_electrical_type():
    """ERC is only as good as this map — a role falling through to `passive`
    silently disables the checks the reviewer is relying on."""
    from ohm.pkggen.symbol import _ETYPE
    from ohm.resolve import VALID_ROLES

    assert set(_ETYPE) >= VALID_ROLES, VALID_ROLES - set(_ETYPE)
    # these three used to degrade to passive
    assert _ETYPE["open_collector"] == "open_collector"
    assert _ETYPE["open_drain"] == "open_collector"  # KiCad has no open_drain
    assert _ETYPE["free"] == "free"


def test_open_drain_pin_emits_open_collector():
    pins = [("1", "SDA", "open_drain"), ("2", "SCL", "open_drain"),
            ("3", "NC", "free"), ("4", "IRQ", "open_collector")]
    sym = emit_symbol("X", pins, footprint="ohm:SOIC-4")
    assert sym.count("(pin open_collector line") == 3
    assert sym.count("(pin free line") == 1
    assert "(pin passive line" not in sym


# a symbol guard, not a footprint one: this reads the symbol libraries
@requires_kicad_sym
def test_emitted_electrical_types_exist_in_kicad():
    """Every type we emit must be one KiCad actually understands."""
    import re as _re

    real = set()
    for lib in KICAD_SYMBOLS.glob("*.kicad_sym"):
        real |= set(_re.findall(r"\(pin (\w+) (?:line|inverted|clock)", lib.read_text()))
    assert real, "no KiCad symbol libraries parsed"
    from ohm.pkggen.symbol import _ETYPE

    assert set(_ETYPE.values()) <= real, set(_ETYPE.values()) - real


def test_sim_attrs_become_kicad_sim_properties():
    """Ohm never invents a SPICE model — it carries one through so ngspice can
    find it. The Sim.* names are what KiCad's simulator reads."""
    sym = emit_symbol("U1", [("1", "A", "input"), ("2", "B", "output")],
                      footprint="ohm:SOIC-2",
                      attrs={"sim_device": "SUBCKT", "sim_library": "MCP6002.lib",
                             "sim_name": "MCP6002", "sim_pins": "1=1 2=2"})
    assert '(property "Sim.Device" "SUBCKT"' in sym
    assert '(property "Sim.Library" "MCP6002.lib"' in sym
    assert '(property "Sim.Name" "MCP6002"' in sym
    assert '(property "Sim.Pins" "1=1 2=2"' in sym


def test_no_sim_properties_without_a_model():
    sym = emit_symbol("U1", [("1", "A", "input")], footprint="ohm:X")
    assert "Sim." not in sym
