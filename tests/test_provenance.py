"""ADR-014 gate: pinout provenance.

The failure this exists to catch is a TRANSPOSITION — a pin↔pad map that stays
total and bijective, so S001/S002/S003 all pass, while a supply pin sits on the
wrong pad. Every test that matters here starts from a design the rest of the
S-tier calls clean.
"""

from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.import_symbol import import_symbol
from ohm.provenance import _prefix_score, _same_pin, digest_pairs, parse_source

KICAD_SYMBOLS = Path("/usr/share/kicad/symbols")
requires_kicad_sym = pytest.mark.skipif(
    not KICAD_SYMBOLS.is_dir(), reason="KiCad symbol libraries not installed")


def _part(*, pins=8, mpn=True, source=None, digest=None, swap=False):
    """An 8-pad SOIC that passes S001-S006 unconditionally."""
    pads = list(range(1, pins + 1))
    if swap:  # transpose two pads: still total, still bijective, still wrong
        pads[3], pads[6] = pads[6], pads[3]
    lines = ["component Widget:",
             f"    package = generate(SOIC, pins={pins}, pitch=1.27mm)"]
    if mpn:
        lines.append('    mpn = "W-1"')
    if source is not None:
        lines.append(f'    pinout_source = "{source}"')
    if digest is not None:
        lines.append(f'    pinout_digest = "{digest}"')
    lines += [f"    pin P{i}: role=passive, pad={pad}"
              for i, pad in enumerate(pads, start=1)]
    lines += ["", "module Top:", "    w = Widget()"]
    return "\n".join(lines) + "\n"


def codes(src):
    return [d.code for d in compile_source(src, "t.ohm").diagnostics]


def diag(src, code):
    return next(d for d in compile_source(src, "t.ohm").diagnostics if d.code == code)


# ---------- the digest itself ----------

def test_digest_ignores_declaration_order():
    """Reordering the pin block is a no-op; the map is the identity, not the text."""
    assert digest_pairs([("A", "1"), ("B", "2")]) == digest_pairs([("B", "2"), ("A", "1")])


def test_digest_tracks_pad_moves():
    assert digest_pairs([("A", "1"), ("B", "2")]) != digest_pairs([("A", "2"), ("B", "1")])


def test_digest_is_prefixed_and_short():
    d = digest_pairs([("A", "1")])
    assert d.startswith("sha256:") and len(d) == len("sha256:") + 16


# ---------- S009: no provenance ----------

def test_s009_fires_on_specific_part_without_provenance():
    assert "S009" in codes(_part())


def test_s009_silent_without_mpn():
    """A generic shape block is a template, not a claim about silicon."""
    assert "S009" not in codes(_part(mpn=False))


def test_s009_silent_on_small_parts():
    """Gating on pin count keeps two-pin passives out of the avalanche."""
    assert "S009" not in codes(_part(pins=3))


def test_s009_asks_for_a_digest_when_source_is_present():
    d = diag(_part(source="datasheet:XYZ-DS rev2 Table 4"), "S009")
    assert "pinout_digest" in d.message and "sha256:" in (d.help or "")


# ---------- S008: the transposition ----------

def test_clean_when_digest_matches():
    src = _part(source="datasheet:XYZ-DS Table 4")
    stamped = _part(source="datasheet:XYZ-DS Table 4",
                    digest=diag(src, "S009").help.split('"')[1])
    assert [c for c in codes(stamped) if c.startswith("S")] == []


def test_s008_catches_a_transposition_the_rest_of_tier_s_accepts():
    src = _part(source="datasheet:XYZ-DS Table 4")
    good = diag(src, "S009").help.split('"')[1]
    swapped = _part(source="datasheet:XYZ-DS Table 4", digest=good, swap=True)
    found = codes(swapped)
    assert "S008" in found
    # the point of the whole feature: nothing else in the S tier sees this
    assert not {"S001", "S002", "S003"} & set(found)


def test_s008_is_an_error():
    src = _part(source="datasheet:XYZ-DS Table 4")
    good = diag(src, "S009").help.split('"')[1]
    assert diag(_part(source="datasheet:XYZ-DS Table 4", digest=good, swap=True),
                "S008").severity == "error"


# ---------- S007: malformed source ----------

@pytest.mark.parametrize("bad", ["nonsense", "kicad:", "", "web:example.com"])
def test_s007_rejects_unreadable_source(bad):
    assert "S007" in codes(_part(source=bad))


def test_parse_source_round_trip():
    assert parse_source("kicad:Sensor_Motion:MPU-6000") == ("kicad", "Sensor_Motion:MPU-6000")
    assert parse_source("datasheet:DS9405 Table 8") == ("datasheet", "DS9405 Table 8")
    assert parse_source("bogus:x") is None


# ---------- candidate matching ----------

def test_prefix_score_rejects_a_one_letter_symbol():
    """DF13-6P-1.25DSA must not 'verify' against Device:D."""
    assert _prefix_score("DF13-6P-1.25DSA", "D") == 0
    assert _prefix_score("RC0402FR-0710KL", "R") == 0


def test_prefix_score_accepts_a_variant_family():
    assert _prefix_score("MS5611", "MS5611-01BA") >= 6
    assert _prefix_score("STM32F427VIT6", "STM32F427VITx") >= 6


@pytest.mark.parametrize("a,b", [("VI", "IN"), ("VOUT", "OUT"), ("VREF_P", "VREFP"),
                                 ("I_O2_2", "IO2_B"), ("VDD_2", "VDD"), ("PB3_SWO", "PB3")])
def test_same_pin_tolerates_naming(a, b):
    assert _same_pin(a, b)


@pytest.mark.parametrize("a,b", [("PD6", "VSS_3"), ("CS", "AUX_CL"),
                                 ("VDD", "INT"), ("BOOT0", "PB6")])
def test_same_pin_never_tolerates_position(a, b):
    """Every pair here is a real fmuv2 defect. None may be explained away."""
    assert not _same_pin(a, b)


# ---------- import-symbol stamps provenance ----------

@requires_kicad_sym
def test_import_symbol_stamps_source_and_digest():
    lib = KICAD_SYMBOLS / "Sensor_Motion.kicad_sym"
    text = import_symbol(lib.read_text(), "MPU-6000", lib.stem)
    assert 'pinout_source = "kicad:Sensor_Motion:MPU-6000"' in text
    assert "pinout_digest = \"sha256:" in text


@requires_kicad_sym
def test_imported_component_checks_clean():
    """The whole loop: real library in, no provenance diagnostics out."""
    lib = KICAD_SYMBOLS / "Sensor_Motion.kicad_sym"
    src = import_symbol(lib.read_text(), "MPU-6000", lib.stem) + "\nmodule Top:\n    u = MPU_6000()\n"
    assert not [c for c in codes(src) if c in ("S007", "S008", "S009")]


@requires_kicad_sym
def test_editing_an_imported_pad_is_an_error():
    lib = KICAD_SYMBOLS / "Sensor_Motion.kicad_sym"
    src = import_symbol(lib.read_text(), "MPU-6000", lib.stem)
    src = src.replace("pin VDD: role=power_in, pad=13", "pin VDD: role=power_in, pad=14")
    assert "S008" in codes(src + "\nmodule Top:\n    u = MPU_6000()\n")


# ---------- B008: load caps must be the right SIZE, not merely present ----------

_XTAL = """use std.kinds
use std.passives

interface G:
    g: Signal, role=ground

component Cx(Capacitor):
    package = generate(CHIP0402)
    designator_prefix = "C"
    value = {cap}
    pin P1: role=passive, pad=1, is=a
    pin P2: role=passive, pad=2, is=b

component X(Crystal):
    package = "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm"
    designator_prefix = "Y"
    freq = 24MHz
{cl}
    pin P1:   role=passive, pad=1, is=a
    pin GND1: role=ground,  pad=2
    pin P2:   role=passive, pad=3, is=b
    pin GND2: role=ground,  pad=4

module Top:
    gnd = io(G)
    y = X()
    c1 = Cx()
    c2 = Cx()
    y.GND1 ~ gnd.g
    y.GND2 ~ gnd.g
    c1.P1 ~ y.P1
    c1.P2 ~ gnd.g
    c2.P1 ~ y.P2
    c2.P2 ~ gnd.g
"""


def _xtal(cap, cl="18pF"):
    return _XTAL.format(cap=cap, cl=f"    cl = {cl}" if cl else "")


def test_b008_flags_undersized_load_caps():
    """The fmuv2 bug: 10pF caps on an 18pF crystal passed the old presence test."""
    d = [x for x in compile_source(_xtal("10pF"), "t.ohm").diagnostics if x.code == "B008"]
    assert len(d) == 2  # one per terminal
    assert "30pF" in d[0].message and "10pF" in d[0].message


def test_b008_accepts_correct_load_caps():
    assert not [x for x in compile_source(_xtal("30pF"), "t.ohm").diagnostics
                if x.code == "B008"]


def test_b008_stays_opt_in_without_cl():
    """Parts with integrated load caps must stay quiet (ADR-009 gating)."""
    assert not [x for x in compile_source(_xtal("10pF", cl=""), "t.ohm").diagnostics
                if x.code == "B008"]


def test_b008_still_catches_a_cap_that_is_not_to_ground():
    """The original presence check must survive: a cap whose far side floats is
    not a load cap, however correct its value."""
    src = _xtal("30pF").replace("    c1.P2 ~ gnd.g\n", "")
    assert [x for x in compile_source(src, "t.ohm").diagnostics if x.code == "B008"]


def test_b008_respects_a_declared_c_stray():
    """cl=18pF with c_stray=8pF needs 2x(18-8)=20pF, so 20pF caps are correct."""
    src = _xtal("20pF").replace("    cl = 18pF", "    cl = 18pF\n    c_stray = 8pF")
    assert not [x for x in compile_source(src, "t.ohm").diagnostics if x.code == "B008"]


# ---------- ADR-015: connection intent (must_connect targets, B011) ----------

_SENS = """use std.kinds

interface G:
    g: Signal, role=ground

component Rail:
    package = generate(SOIC, pins=4, pitch=1.27mm)
    pin V:  role=power_out, pad=1, voltage=3.3V
    pin G1: role=ground,    pad=2
    pin G2: role=ground,    pad=3
    pin G3: role=ground,    pad=4

component Sens:
    package = generate(SOIC, pins=8, pitch=1.27mm)
    mpn = "S-1"
    pinout_source = "datasheet:x"
    pin VDD:  role=power_in, pad=1{vdd}
    pin GND:  role=ground,   pad=2, must_connect=ground
    pin RES1: role=passive,  pad=3, must_connect=ground
    pin RES2: role=passive,  pad=4{res2}
    pin SIG:  role=passive,  pad=5
    pin SPARE: role=passive, pad=6{spare}
    pin NC1:  role=nc,       pad=7
    pin NC2:  role=nc,       pad=8

module M:
    gnd = io(G)
    u = Sens()
    r = Rail()
    r.G1   ~ gnd.g
    u.VDD  ~ r.V
    u.GND  ~ gnd.g
    u.RES1 ~ gnd.g
    u.SIG  ~ gnd.g
{extra}"""


def _sens(vdd="", res2="", spare="", extra="    u.RES2 ~ gnd.g\n"):
    return _SENS.format(vdd=vdd, res2=res2, spare=spare, extra=extra)


def test_must_connect_false_no_longer_means_true():
    """It was tested for PRESENCE, so writing the thing that means 'I checked,
    this floats' produced a B003 error."""
    assert "B003" not in codes(_sens(spare=", must_connect=false"))


def test_must_connect_ground_rejects_a_signal_net():
    """A datasheet 'RES — connect to GND' pin on a signal net is as wrong as
    floating it, and no other check can see the difference."""
    src = _sens(res2=", must_connect=ground", extra="    u.RES2 ~ u.SPARE\n")
    d = next(x for x in compile_source(src, "t.ohm").diagnostics if x.code == "B003")
    assert d.severity == "error" and "not a ground" in d.message


def test_must_connect_ground_accepts_a_ground_net():
    assert "B003" not in codes(_sens(res2=", must_connect=ground"))


def test_must_connect_rail_names_the_target_when_unconnected():
    src = _sens(vdd=", must_connect=rail").replace("    u.VDD  ~ r.V\n", "")
    d = next(x for x in compile_source(src, "t.ohm").diagnostics
             if x.code == "B003" and "VDD" in x.message)
    assert "must connect to rail" in d.message


def test_r025_rejects_an_unknown_target():
    assert "R025" in codes(_sens(spare=", must_connect=maybe"))


def test_b011_reports_undeclared_intent_only():
    """SPARE is neither wired nor declared; nc pins and declared pins stay out."""
    d = next(x for x in compile_source(_sens(), "t.ohm").diagnostics if x.code == "B011")
    assert "SPARE" in d.message and "NC1" not in d.message and d.severity == "info"


def test_b011_is_silenced_by_declaring_the_intent():
    assert "B011" not in codes(_sens(spare=", must_connect=false"))


def test_b011_stays_quiet_without_mpn():
    assert "B011" not in codes(_sens().replace('    mpn = "S-1"\n', ""))


# ---------- S010: grid family standing in for a perimeter part ----------

def _lga(rows, cols, border=1):
    pads = [f"{r}{c}" for r in "ABCDE"[:rows] for c in range(1, cols + 1)
            if not (border <= "ABCDE".index(r) < rows - border
                    and border <= c - 1 < cols - border)]
    pins = "\n".join(f"    pin P{p}: role=passive, pad={p}" for p in pads)
    return (f"component G:\n    package = generate(LGA, rows={rows}, cols={cols}, "
            f"pitch=0.5mm, pad=0.3mm, border={border})\n    mpn = \"X\"\n"
            f'    pinout_source = "datasheet:x"\n{pins}\n\nmodule M:\n    g = G()\n')


def test_s010_flags_an_unequal_perimeter_ring():
    """5x5 border=1 gives 5/3/3/5 for a part that is 4 pads per side — and the
    pin<->pad map stays total, so S003 cannot see it."""
    d = next(x for x in compile_source(_lga(5, 5), "t.ohm").diagnostics if x.code == "S010")
    assert "5/3/3/5" in d.message and "QFN" in (d.help or "")


def test_s010_silent_on_a_full_grid():
    assert "S010" not in codes(_lga(4, 4, border=0).replace("border=0", "border=0"))
