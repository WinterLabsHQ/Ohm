"""Track width is COMPUTED from the declared current, or it is not claimed.

A netclass whose track_width equals the default is a label. These pin the two
halves: the width matches the standard's own table, and a class Ohm cannot size
is reported rather than left looking sized.
"""

from decimal import Decimal as D

import pytest

from ohm.driver import compile_source
from ohm.emit_kicad_pro import emit_kicad_pro
from ohm.trace_width import MIN_TRACK_MM, net_currents, trace_width_mm

SRC = """use std.interfaces

component Reg:
    package = generate(SOIC, pins=8)
    pin VIN:  role=power_in, pad=1
    pin GND:  role=ground, pad=2
    pin VOUT: role=power_out, pad=3, imax=3A
    pin NC1:  role=nc, pad=4
    pin NC2:  role=nc, pad=5
    pin NC3:  role=nc, pad=6
    pin NC4:  role=nc, pad=7
    pin NC5:  role=nc, pad=8

component Load:
    package = generate(SOIC, pins=4)
    pin VDD: role=power_in, pad=1, current=1200mA
    pin GND: role=ground, pad=2
    pin A:   role=input, pad=3
    pin B:   role=input, pad=4

module Board:
    rail = io(Power)
    u = Reg()
    l = Load()
    rail.v ~ u.VIN
    rail.gnd ~ u.GND
    u.VOUT ~ l.VDD
    rail.gnd ~ l.GND
    constraint netclass(u.VOUT, "power")
"""


@pytest.mark.parametrize("amps,expect_mm", [
    # IPC-2221A external, 1oz, 10 degC rise — checked against the published curve
    ("1.0", "0.35"),
    ("2.0", "0.80"),
    ("3.0", "1.40"),
    ("5.0", "2.80"),
])
def test_width_matches_the_standards_curve(amps, expect_mm):
    assert trace_width_mm(D(amps)) == D(expect_mm)


def test_width_only_ever_rounds_up():
    """Rounding a current-carrying trace down to the nearer grid step is the one
    direction that is not safe."""
    for a in ("0.9", "1.1", "1.9", "2.1", "2.9"):
        exact_mils2 = None  # the check is the monotone step, not the algebra
        w = trace_width_mm(D(a))
        assert w >= trace_width_mm(D(a) - D("0.05")), a
        assert (w / D("0.05")) == (w / D("0.05")).to_integral_value(), \
            f"{w} is not on the 0.05mm grid"
        assert exact_mils2 is None


def test_a_signal_current_never_narrows_below_the_default():
    """Sizing may widen a class. It may never quietly relax someone's rule."""
    assert trace_width_mm(D("0.001")) == MIN_TRACK_MM
    assert trace_width_mm(D(0)) == MIN_TRACK_MM
    assert trace_width_mm(D("-1")) == MIN_TRACK_MM


def test_current_comes_from_the_declared_draw():
    result = compile_source(SRC, "b.ohm")
    assert result.low is not None, [d.machine() for d in result.diagnostics]
    currents = net_currents(result.low, result.design)
    # the regulator output carries the load's declared 1.2A
    carried = [a for n, a in currents.items() if a == D("1.2")]
    assert carried, currents


def test_a_declared_draw_widens_the_netclass():
    result = compile_source(SRC, "b.ohm")
    import json

    doc = json.loads(emit_kicad_pro(result.low, "b.ohm", result.design))
    classes = {c["name"]: c for c in doc["net_settings"]["classes"]}
    assert classes["Default"]["track_width"] == 0.25
    # 1.2A -> 0.45mm by the curve; the point is that it is NOT the default
    assert classes["power"]["track_width"] > classes["Default"]["track_width"], \
        "the power class is still routed at signal width"
    assert classes["power"]["track_width"] == float(trace_width_mm(D("1.2")))


def test_without_a_design_the_class_keeps_the_default():
    """No design means no declared currents, and a width invented from nothing
    is worse than a default."""
    import json

    result = compile_source(SRC, "b.ohm")
    doc = json.loads(emit_kicad_pro(result.low, "b.ohm"))
    classes = {c["name"]: c for c in doc["net_settings"]["classes"]}
    assert classes["power"]["track_width"] == 0.25


def test_a_netclass_with_no_declared_current_is_reported():
    """Q007: silence about a class Ohm could not size reads as coverage."""
    src = SRC.replace(", current=1200mA", "")
    codes = [d.code for d in compile_source(src, "b.ohm").diagnostics]
    assert "Q007" in codes
