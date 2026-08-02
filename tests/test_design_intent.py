"""The design-intent layer: what the document IS, not just what it connects.

Nine of the ten drafting tells a reviewer named on the fmuv2 board came from one
root cause — Ohm had a vocabulary for the circuit and none for the document. It
could not say what the board is called, what revision it is, what a net is
*named*, why a pin is unused, or what layout must honour. So the emitter invented
substitutes: a blank title block, a net named after a capacitor pin, silence
where a note belongs. These cases pin the vocabulary that replaced the
inventions.
"""

import re

import pytest

from ohm.driver import compile_source
from ohm.emit_kicad import emit_kicad_sch, schematic_value

BOARD = '''board demo:
    title = "Demo Board"
    rev = "C"
    company = "Upcheck Technologies"
    designer = "Kiran Sekar"

'''

SRC = '''use std.interfaces
use std.passives

component Buf:
    package = generate(SOIC, pins=8)
    mpn = "BUF-1"
    unused_pins = open
    pin IN:  role=bidirectional, pad=1
    pin OUT: role=bidirectional, pad=2
    pin VCC: role=power_in, pad=8
    pin GND: role=ground, pad=4
    pin S1: role=passive, pad=3
    pin S2: role=passive, pad=5
    pin S3: role=passive, pad=6
    pin S4: role=passive, pad=7

module Top:
    vdd = io(Power)
    u = Buf()
    c = Capacitor(value=100nF, vmax=16V, dielectric="X7R")
    r = Resistor(value=10kohm)
    vdd.v ~ u.VCC
    vdd.gnd ~ u.GND
    vdd.v ~ c.P1
    vdd.gnd ~ c.P2
    u.IN ~ r.P1
    r.P2 ~ u.OUT
'''


def _compile(src: str):
    result = compile_source(src, "t.ohm")
    assert result.low is not None, [d.machine() for d in result.diagnostics]
    return result


# ---------- board: the document is citable ----------
def test_title_block_carries_the_board_declaration():
    result = _compile(BOARD + SRC)
    sch = emit_kicad_sch(result.low, "t.ohm", result.design)
    assert '(title "Demo Board")' in sch
    assert '(rev "C")' in sch
    assert '(company "Upcheck Technologies")' in sch
    assert "Drawn by: Kiran Sekar" in sch


def test_a_blank_title_block_is_reported_not_silently_shipped():
    """The tell that reads loudest to a reviewer, and the cheapest to fix."""
    codes = [d.code for d in compile_source(SRC, "t.ohm").diagnostics]
    assert "M009" in codes


def test_a_board_without_a_revision_cannot_be_cited():
    src = 'board demo:\n    title = "Demo"\n\n' + SRC
    hits = [d for d in compile_source(src, "t.ohm").diagnostics if d.code == "M009"]
    assert hits and "rev" in hits[0].message


def test_unknown_board_field_is_an_error_not_a_silent_drop():
    src = 'board demo:\n    titel = "typo"\n\n' + SRC
    codes = [d.code for d in compile_source(src, "t.ohm").diagnostics]
    assert "R027" in codes


def test_one_design_is_one_document():
    src = 'board a:\n    rev = "1"\n\nboard b:\n    rev = "2"\n\n' + SRC
    codes = [d.code for d in compile_source(src, "t.ohm").diagnostics]
    assert "R026" in codes


def test_sheet_n_of_m_is_computed_not_declared():
    result = _compile(BOARD + SRC)
    sch = emit_kicad_sch(result.low, "t.ohm", result.design)
    assert "Sheet 1 of 1" in sch


# ---------- net naming: the signal has a name ----------
def test_an_explicit_name_beats_the_label_election():
    src = SRC.replace("    u.IN ~ r.P1", "    net FEEDBACK = r.P1\n    u.IN ~ r.P1")
    names = [n.name for n in _compile(src).low.nets]
    assert "FEEDBACK" in names
    assert "r.P1" not in names


def test_a_net_named_after_a_pin_is_reported():
    """`r.P1` names the net after a part it happens to touch — the name says
    where the net lands, not what it is, and it breaks when the part is
    renumbered."""
    hits = [d for d in compile_source(SRC, "t.ohm").diagnostics if d.code == "M010"]
    assert hits and "r.P1" in hits[0].message


def test_one_net_cannot_have_two_names():
    src = SRC.replace("    u.IN ~ r.P1",
                      "    net FEEDBACK = r.P1\n    net FB2 = u.IN\n    u.IN ~ r.P1")
    codes = [d.code for d in compile_source(src, "t.ohm").diagnostics]
    assert "R028" in codes


RAIL = '''interface Rail:
    v: Signal, role=power_in

'''


def test_a_rail_drops_its_interface_member_suffix():
    """`VDD_3V3.v` is an Ohm implementation detail; the drawing wants the rail
    name. One net hangs off the port instance, so the member adds nothing."""
    src = RAIL + SRC.replace("    vdd = io(Power)", "    vdd = io(Rail)\n    gnd = io(Power)")
    src = src.replace("vdd.gnd ~", "gnd.gnd ~")
    names = [n.name for n in _compile(src).low.nets]
    assert "vdd" in names and "vdd.v" not in names


def test_an_ambiguous_suffix_is_kept():
    """Two nets under one port instance: there the member IS what tells them
    apart, so collapsing would merge two different signals into one name."""
    names = [n.name for n in _compile(SRC).low.nets]
    assert "vdd.v" in names and "vdd.gnd" in names


# ---------- unused pins, values, notes ----------
def test_unused_pins_open_marks_every_spare_pin():
    """One line on the component instead of a must_connect=false per pin — and
    a pin nobody accounted for is then visible, because it has no marker."""
    result = _compile(BOARD + SRC)
    sch = emit_kicad_sch(result.low, "t.ohm", result.design)
    assert sch.count("(no_connect") == 4  # S1..S4
    assert "B011" not in [d.code for d in result.diagnostics]


def test_value_shows_what_qualifies_it():
    result = _compile(BOARD + SRC)
    cap = next(p for p in result.low.parts if p.designator.startswith("C"))
    assert schematic_value(cap) == "100nF 16V X7R"


def test_a_rail_cap_with_no_rating_is_reported():
    src = SRC.replace('value=100nF, vmax=16V, dielectric="X7R"', "value=100nF")
    hits = [d for d in compile_source(src, "t.ohm").diagnostics if d.code == "M011"]
    assert hits and "c" in hits[0].message


def test_notes_reach_the_sheet():
    src = BOARD + SRC.replace("    u = Buf()",
                              '    note "C1 within 2mm of U1 pin 8"\n    u = Buf()')
    sch = emit_kicad_sch(_compile(src).low, "t.ohm", _compile(src).design)
    assert "NOTES" in sch and "C1 within 2mm of U1 pin 8" in sch


def test_a_near_constraint_says_itself_on_the_sheet():
    """Layout reads `near()` from constraints.json; the reviewer reading the PDF
    cannot. Same fact, so it belongs on the drawing in words."""
    src = BOARD + SRC + "    constraint near(c, u, 2mm)\n"
    result = _compile(src)
    sch = emit_kicad_sch(result.low, "t.ohm", result.design)
    assert re.search(r"PLACEMENT: c must sit within 2mm of u", sch)


@pytest.mark.parametrize("statement", ['net FOO = r.P1', 'note "keep me"'])
def test_fmt_never_drops_the_new_statements(statement):
    """A formatter that silently deletes source is worse than no formatter."""
    from ohm.fmt import format_source

    src = f"module M:\n    {statement}\n    r = R()\n"
    out, _ = format_source(src, "t.ohm")
    assert statement in out


# ---------- components that declare no pins at all ----------
# Regression: a fiducial and a mounting hole declare ZERO pins, but the emitter
# synthesizes one anchor pin so KiCad has something to place. That pin was
# emitted bare, so KiCad's ERC reported an unconnected pin the source never
# declared — and `unused_pins = open` could not silence it, because there was
# no pin in the source to mark. Seven of them on the ECG triage board.

PINLESS = BOARD + '''use std.interfaces

component Fid:
    package = "Fiducial:Fiducial_1mm_Mask2mm"
    designator_prefix = "FID"

component Hole:
    package = "MountingHole:MountingHole_3.2mm_M3"
    designator_prefix = "H"

module Top:
    pwr = io(Power)
    fid1 = Fid()
    fid2 = Fid()
    h1 = Hole()
'''


def test_a_pinless_part_gets_a_no_connect_on_its_anchor_pin():
    """The anchor pin exists for the emitter, not the design, so it says so."""
    result = compile_source(PINLESS, "t.ohm")
    assert not [d for d in result.diagnostics if d.severity == "error"]
    sch = emit_kicad_sch(result.low, "t.ohm", result.design)
    assert sch.count("(no_connect") == 3, "one per pinless part (2 fiducials + 1 hole)"


def test_a_pinless_part_needs_no_unused_pins_declaration():
    """`unused_pins = open` cannot reach a pin the source never declared, so
    the emitter must handle it without the author having to say anything."""
    assert "unused_pins" not in PINLESS
    result = compile_source(PINLESS, "t.ohm")
    sch = emit_kicad_sch(result.low, "t.ohm", result.design)
    assert "(no_connect" in sch
