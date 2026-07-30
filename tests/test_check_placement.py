"""S011: parts cannot share board area.

The gap this closes: `emit_kicad.py` claimed no courtyard overlaps "by
construction", which held only for AUTO-placed parts. A `constraint fixed(...)`
puts a part exactly where the source says, and nothing checked whether the
source said something impossible. KiCad's DRC found five overlapping pairs on
the fmuv2 reference board that every Ohm tier had passed.
"""

from ohm.driver import compile_source

SRC = """component Hole:
    package = generate(CHIP0805)
    designator_prefix = "H"
    pin P1: role=passive, pad=1
    pin P2: role=passive, pad=2

component Part:
    package = generate(SOIC, pins=8)
    designator_prefix = "U"
    pin VDD: role=power_in, pad=1
    pin GND: role=ground, pad=2
    pin A: role=passive, pad=3
    pin B: role=passive, pad=4
    pin C: role=passive, pad=5
    pin D: role=passive, pad=6
    pin E: role=passive, pad=7
    pin F: role=passive, pad=8

module Board:
    h1 = Hole()
    h2 = Hole()
    u  = Part()
    h1.P1 ~ u.A
    h2.P1 ~ u.B
    h1.P2 ~ u.GND
    h2.P2 ~ u.VDD
    constraint fixed(h1, {h1x}, 10mm)
    constraint fixed(h2, {h2x}, 10mm)
"""


def _codes(h1x: str, h2x: str) -> list[str]:
    result = compile_source(SRC.format(h1x=h1x, h2x=h2x), "t.ohm")
    return [d.code for d in result.diagnostics]


def test_fixed_parts_on_top_of_each_other_are_an_error():
    codes = _codes("10mm", "10.2mm")  # 0.2mm apart, courtyards ~2mm wide
    assert "S011" in codes


def test_fixed_parts_far_apart_are_fine():
    assert "S011" not in _codes("10mm", "30mm")


def test_the_error_names_both_parts_and_their_poses():
    result = compile_source(SRC.format(h1x="10mm", h2x="10.2mm"), "t.ohm")
    msg = next(d.message for d in result.diagnostics if d.code == "S011")
    assert "'h1' (fixed)" in msg and "'h2' (fixed)" in msg
    assert "(10, 10)" in msg and "(10.2, 10)" in msg


def test_overlap_is_an_error_so_it_blocks_emit():
    """A board whose parts collide must not reach the emitter — the whole point
    is to stop before copper, not to annotate it afterwards."""
    result = compile_source(SRC.format(h1x="10mm", h2x="10.2mm"), "t.ohm")
    assert not result.ok
    assert result.low is None


def test_auto_placed_designs_are_not_re_litigated():
    """No `fixed` constraint means the placer owns every pose, and its cells are
    disjoint by construction; scanning them would only ever produce false alarms
    or confirm what the algorithm already guarantees."""
    src = SRC.split("    constraint fixed")[0]
    result = compile_source(src, "t.ohm")
    assert "S011" not in [d.code for d in result.diagnostics]


STUB_SRC = """component Hole:
    package = "MountingHole:MountingHole_3.2mm_M3"
    designator_prefix = "H"
    pin P1: role=passive, pad=1

component Part:
    package = generate(SOIC, pins=8)
    designator_prefix = "U"
    pin VDD: role=power_in, pad=1
    pin GND: role=ground, pad=2
    pin A: role=passive, pad=3
    pin B: role=passive, pad=4
    pin C: role=passive, pad=5
    pin D: role=passive, pad=6
    pin E: role=passive, pad=7
    pin F: role=passive, pad=8

module Board:
    h1 = Hole()
    h2 = Hole()
    u  = Part()
    h1.P1 ~ u.A
    h2.P1 ~ u.B
    constraint fixed(h1, 10mm, 10mm)
    constraint fixed(h2, 10.2mm, 10mm)
"""


def test_a_string_footprint_overlap_is_a_warning_not_a_claim():
    """Ohm has never read a string footprint's land pattern — it places the part
    on a 5x5mm placeholder. Calling that overlap an error would assert a fact
    about geometry Ohm does not have, and would block a build over a guess.
    """
    result = compile_source(STUB_SRC, "t.ohm")
    hits = [d for d in result.diagnostics if d.code == "S011"]
    assert hits, [d.code for d in result.diagnostics]
    assert all(d.severity == "warning" for d in hits)
    assert "placeholder" in hits[0].message
    assert result.ok  # a guess must not block the build
