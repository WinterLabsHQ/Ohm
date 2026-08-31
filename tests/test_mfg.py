"""Milestone F, Part C: DFM/DFT lint (M001-M008). Each code fires on its
seeded fixture and clears on the corrected one; summary codes emit exactly ONE
diagnostic; M-codes are never errors; the pass is gated to wired, BOM-stage
designs so the std/example corpus stays clean; --strict fails on M-warnings."""

from pathlib import Path

from ohm.cli import main as cli_main
from ohm.driver import compile_source

STD = Path(__file__).parent.parent / "ohm" / "std"
EXAMPLES = Path(__file__).parent / "examples"

RESISTOR = (
    "component Resistor:\n    package = generate(CHIP0603)\n    value = 0ohm\n"
    "    pin P1: role=passive, pad=1\n    pin P2: role=passive, pad=2\n"
)
# passive category WITHOUT a default value (for M003)
BARECAP = (
    "component Capacitor:\n    package = generate(CHIP0603)\n"
    "    pin P1: role=passive, pad=1\n    pin P2: role=passive, pad=2\n"
)
TESTPOINT = (
    'component TestPoint:\n    package = "TestPoint:TestPoint_Pad_1.0x1.0mm"\n'
    "    pin PAD: role=passive, pad=1\n"
)
FIDUCIAL = 'component Fiducial:\n    package = "Fiducial:Fiducial_1mm_Mask2mm"\n'


def diags(src):
    return compile_source(src, "m.ohm").diagnostics


def m_diags(src):
    return [d for d in diags(src) if d.code.startswith("M")]


def m_codes(src):
    return [d.code for d in m_diags(src)]


# ---------- gates: unwired / pre-BOM designs are silent ----------
def test_unwired_design_is_silent():
    src = RESISTOR + "module M:\n    r1 = Resistor(mpn=\"X\")\n    r2 = Resistor()\n"
    assert m_codes(src) == []  # no connections anywhere -> whole pass skipped


def test_pre_bom_design_is_silent():
    src = (RESISTOR + "module M:\n    r1 = Resistor(value=1kohm)\n"
           "    r2 = Resistor(value=2kohm)\n    r1.P1 ~ r2.P1\n")
    assert m_codes(src) == []  # nothing has mpn/dnp -> schematic stage


# ---------- M001 missing mpn (summary) ----------
M001_SRC = (RESISTOR + "module M:\n    r1 = Resistor(value=1kohm, mpn=\"RC0603\")\n"
            "    r2 = Resistor(value=2kohm)\n    r3 = Resistor(value=3kohm)\n"
            "    r1.P1 ~ r2.P1\n")


def test_m001_fires_once_with_refs():
    found = [d for d in m_diags(M001_SRC) if d.code == "M001"]
    assert len(found) == 1  # ONE summary, not one per part
    assert found[0].severity == "warning"
    assert "r2" in found[0].message and "r3" in found[0].message
    assert "r1" not in found[0].message.split(":")[1]


def test_m001_clears_with_mpn_and_dnp():
    src = (RESISTOR + "module M:\n    r1 = Resistor(value=1kohm, mpn=\"A\")\n"
           "    r2 = Resistor(value=2kohm, mpn=\"B\")\n"
           "    r3 = Resistor(value=3kohm, dnp=1)\n    r1.P1 ~ r2.P1\n")
    assert "M001" not in m_codes(src)


def test_m001_pcb_features_exempt():
    src = (RESISTOR + TESTPOINT +
           "module M:\n    r1 = Resistor(value=1kohm, mpn=\"A\")\n"
           "    tp = TestPoint()\n    r1.P1 ~ tp.PAD\n")
    assert "M001" not in m_codes(src)  # TestPoint needs no MPN


def test_m001_ref_list_capped_at_10():
    lines = "".join(f"    r{i} = Resistor(value=1kohm)\n" for i in range(12))
    src = (RESISTOR + "module M:\n    m1 = Resistor(value=1kohm, mpn=\"A\")\n"
           + lines + "    m1.P1 ~ r0.P1\n")
    found = [d for d in m_diags(src) if d.code == "M001"]
    assert len(found) == 1
    assert "and 2 more" in found[0].message


# ---------- M002 one mpn, different value/package ----------
def test_m002_fires_per_conflicting_mpn():
    src = (RESISTOR + "module M:\n    r1 = Resistor(value=10kohm, mpn=\"SAME\")\n"
           "    r2 = Resistor(value=22kohm, mpn=\"SAME\")\n    r1.P1 ~ r2.P1\n")
    found = [d for d in m_diags(src) if d.code == "M002"]
    assert len(found) == 1 and found[0].severity == "warning"
    assert "SAME" in found[0].message


def test_m002_package_conflict_and_clear():
    conflict = (RESISTOR + "module M:\n    r1 = Resistor(value=10kohm, mpn=\"S\")\n"
                "    r2 = Resistor(value=10kohm, mpn=\"S\", package=generate(CHIP0805))\n"
                "    r1.P1 ~ r2.P1\n")
    assert m_codes(conflict).count("M002") == 1
    clean = (RESISTOR + "module M:\n    r1 = Resistor(value=10kohm, mpn=\"S\")\n"
             "    r2 = Resistor(value=10kohm, mpn=\"S\")\n    r1.P1 ~ r2.P1\n")
    assert "M002" not in m_codes(clean)


# ---------- M003 passive with no value (summary) ----------
def test_m003_fires_once_and_clears():
    src = (BARECAP + "module M:\n    c1 = Capacitor(mpn=\"GRM188\")\n"
           "    c2 = Capacitor()\n    c1.P1 ~ c2.P1\n")
    found = [d for d in m_diags(src) if d.code == "M003"]
    assert len(found) == 1 and found[0].severity == "warning"
    assert "c1" in found[0].message and "c2" in found[0].message
    fixed = (BARECAP + "module M:\n    c1 = Capacitor(mpn=\"GRM188\", value=100nF)\n"
             "    c2 = Capacitor(value=1uF)\n    c1.P1 ~ c2.P1\n")
    assert "M003" not in m_codes(fixed)


def test_m003_matches_kind_lineage():
    src = ("kind Inductor\n"
           "component PowerChoke(Inductor):\n    package = generate(CHIP0805)\n"
           "    pin P1: role=passive, pad=1\n    pin P2: role=passive, pad=2\n"
           + RESISTOR +
           "module M:\n    l1 = PowerChoke(mpn=\"X\")\n"
           "    r1 = Resistor(value=1kohm, mpn=\"Y\")\n    l1.P1 ~ r1.P1\n")
    found = [d for d in m_diags(src) if d.code == "M003"]
    assert len(found) == 1 and "l1" in found[0].message


# ---------- M004 unprobeable power rail ----------
REG = ("component Reg:\n    package = generate(CHIP0603)\n"
       "    pin OUT: role=power_out, voltage=3.3V, pad=1\n"
       "    pin GND: role=ground, pad=2\n")
LOAD = ("component Load:\n    package = generate(CHIP0603)\n"
        "    pin VIN: role=power_in, pad=1\n    pin GND: role=ground, pad=2\n")
M004_SRC = (REG + LOAD + "module M:\n    u = Reg(mpn=\"X\")\n    l = Load(mpn=\"Y\")\n"
            "    u.OUT ~ l.VIN\n    u.GND ~ l.GND\n")


def test_m004_fires_on_unprobeable_rail_only():
    found = [d for d in m_diags(M004_SRC) if d.code == "M004"]
    assert len(found) == 1 and found[0].severity == "warning"  # ground net exempt
    assert "power rail" in found[0].message  # the one rail net (flat name varies)


def test_m004_clears_with_testpoint():
    src = (REG + LOAD + TESTPOINT +
           "module M:\n    u = Reg(mpn=\"X\")\n    l = Load(mpn=\"Y\")\n"
           "    tp = TestPoint()\n    u.OUT ~ l.VIN\n    u.OUT ~ tp.PAD\n"
           "    u.GND ~ l.GND\n")
    assert "M004" not in m_codes(src)


def test_m004_clears_with_connector_kind():
    src = ("kind Connector\n" + REG +
           "component Hdr(Connector):\n    package = generate(CHIP0603)\n"
           "    pin V: role=passive, pad=1\n    pin G: role=passive, pad=2\n"
           "module M:\n    u = Reg(mpn=\"X\")\n    j = Hdr(mpn=\"J\")\n"
           "    u.OUT ~ j.V\n    u.GND ~ j.G\n")
    assert "M004" not in m_codes(src)


# ---------- M005 0402 passives (summary info) ----------
def test_m005_fires_once_and_clears():
    src = (RESISTOR + "module M:\n"
           "    r1 = Resistor(value=1kohm, mpn=\"A\", package=generate(CHIP0402))\n"
           "    r2 = Resistor(value=2kohm, mpn=\"B\", package=generate(CHIP0402))\n"
           "    r1.P1 ~ r2.P1\n")
    found = [d for d in m_diags(src) if d.code == "M005"]
    assert len(found) == 1 and found[0].severity == "info"
    assert "r1" in found[0].message and "r2" in found[0].message
    clean = (RESISTOR + "module M:\n    r1 = Resistor(value=1kohm, mpn=\"A\")\n"
             "    r2 = Resistor(value=2kohm, mpn=\"B\")\n    r1.P1 ~ r2.P1\n")
    assert "M005" not in m_codes(clean)


# ---------- M006 mixed SMT + THT (one info) ----------
def test_m006_fires_once_and_clears():
    tht = ('component Hdr:\n'
           '    package = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"\n'
           "    pin P1: role=passive, pad=1\n    pin P2: role=passive, pad=2\n")
    src = (RESISTOR + tht + "module M:\n    r1 = Resistor(value=1kohm, mpn=\"A\")\n"
           "    j1 = Hdr(mpn=\"J\")\n    r1.P1 ~ j1.P1\n")
    found = [d for d in m_diags(src) if d.code == "M006"]
    assert len(found) == 1 and found[0].severity == "info"
    smt_only = (RESISTOR + "module M:\n    r1 = Resistor(value=1kohm, mpn=\"A\")\n"
                "    r2 = Resistor(value=2kohm, mpn=\"B\")\n    r1.P1 ~ r2.P1\n")
    assert "M006" not in m_codes(smt_only)


# ---------- M007 SMT without fiducial (one info) ----------
def test_m007_fires_once_and_clears_with_fiducial():
    src = (RESISTOR + "module M:\n    r1 = Resistor(value=1kohm, mpn=\"A\")\n"
           "    r2 = Resistor(value=2kohm, mpn=\"B\")\n    r1.P1 ~ r2.P1\n")
    found = [d for d in m_diags(src) if d.code == "M007"]
    assert len(found) == 1 and found[0].severity == "info"
    fixed = (RESISTOR + FIDUCIAL +
             "module M:\n    r1 = Resistor(value=1kohm, mpn=\"A\")\n"
             "    r2 = Resistor(value=2kohm, mpn=\"B\")\n    fid = Fiducial()\n"
             "    r1.P1 ~ r2.P1\n")
    assert "M007" not in m_codes(fixed)


# ---------- M008 fine-pitch generated packages (summary info) ----------
MCU4 = ("component Mcu:\n    package = generate(TSSOP, pins=4)\n"
        "    pin A: role=passive, pad=1\n    pin B: role=passive, pad=2\n"
        "    pin C: role=passive, pad=3\n    pin D: role=passive, pad=4\n")


def test_m008_fires_once_on_tssop_default_pitch():
    src = (MCU4 + RESISTOR + "module M:\n    u1 = Mcu(mpn=\"X\")\n"
           "    r1 = Resistor(value=1kohm, mpn=\"Y\")\n    u1.A ~ r1.P1\n")
    found = [d for d in m_diags(src) if d.code == "M008"]
    assert len(found) == 1 and found[0].severity == "info"
    assert "u1" in found[0].message and "TSSOP" in found[0].message


def test_m008_explicit_pitch_and_coarse_clear():
    fine = (MCU4.replace("generate(TSSOP, pins=4)", "generate(SOIC, pins=4, pitch=0.5)")
            + RESISTOR + "module M:\n    u1 = Mcu(mpn=\"X\")\n"
            "    r1 = Resistor(value=1kohm, mpn=\"Y\")\n    u1.A ~ r1.P1\n")
    assert m_codes(fine).count("M008") == 1
    coarse = (MCU4.replace("generate(TSSOP, pins=4)", "generate(SOIC, pins=4)")
              + RESISTOR + "module M:\n    u1 = Mcu(mpn=\"X\")\n"
              "    r1 = Resistor(value=1kohm, mpn=\"Y\")\n    u1.A ~ r1.P1\n")
    assert "M008" not in m_codes(coarse)


# ---------- M012 no board outline ----------
# The board Ohm emits declares Edge.Cuts and, until this check existed, could
# draw nothing on it — a gerber package with an empty profile layer, which plots
# perfectly and which no fab can quote. M012 is asked of `board_extents`, the
# same function the emitter draws from, so the warning and the file agree.
M012_SRC = ("interface Pair:\n"
            "    a: signal\n"
            "    b: signal\n"
            "module top:\n"
            "    p = io(Pair)\n"
            "    q = io(Pair)\n"
            "    p ~ q\n")


def test_m012_fires_once_on_a_board_with_nothing_to_enclose():
    found = [d for d in m_diags(M012_SRC) if d.code == "M012"]
    assert len(found) == 1 and found[0].severity == "warning"
    assert "not orderable" in found[0].message


def test_m012_clears_as_soon_as_there_is_a_part():
    src = (RESISTOR + "module M:\n    r1 = Resistor(value=1kohm)\n"
           "    r2 = Resistor(value=2kohm)\n    r1.P1 ~ r2.P1\n")
    assert "M012" not in m_codes(src)


def test_m012_sits_above_the_bom_stage_gate():
    """It is the one M-code that is not BOM lint, so gate 2 must not swallow it.

    `M012_SRC` has no part at all and therefore no mpn/dnp anywhere, which is
    exactly the shape the pre-BOM gate returns early on. Whether the board has a
    profile for the fab to cut is not a property of the BOM, and a board with no
    outline is unorderable at every stage of the design — including the stage
    where nobody has chosen orderable parts yet.
    """
    assert m_codes(M012_SRC) == ["M012"]  # nothing else escapes the gate with it


# ---------- corpus stays clean: std + examples, no M-code ERRORS ----------
def test_std_and_examples_emit_no_m_errors():
    files = sorted(STD.glob("*.ohm")) + sorted(EXAMPLES.glob("*.ohm"))
    assert files
    for f in files:
        result = compile_source(f.read_text(), str(f))
        bad = [d.machine() for d in result.diagnostics
               if d.code.startswith("M") and d.severity == "error"]
        assert not bad, (f.name, bad)


def test_m_codes_are_never_errors_on_fixtures():
    for src in (M001_SRC, M004_SRC):
        assert all(d.severity in ("warning", "info") for d in m_diags(src))


# ---------- --strict: an M-warning fails the build ----------
def test_strict_fails_on_m_warning(tmp_path):
    f = tmp_path / "m.ohm"
    f.write_text(M001_SRC)
    assert cli_main(["check", str(f)]) == 0  # warnings alone pass
    assert cli_main(["check", str(f), "--strict"]) == 1  # --strict fails
