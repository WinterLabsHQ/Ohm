"""W2 audit fixes: bidirectional declarations in Tier-Q, Q003 same-net skip,
Q005 coarse thermal, I006 floating-input-behind-series-R."""

from pathlib import Path

import pytest

from ohm.driver import compile_source

GOLDEN = Path(__file__).parent / "golden"


def codes(result):
    return [d.code for d in result.diagnostics]


# --- finding 1: bidirectional pins with declarations count in Tier-Q ---

def test_bidi_declared_voltage_fires_q001():
    src = (
        "component Xcvr:\n"
        '    package = "SOT-23"\n'
        "    pin IO: role=bidirectional, voltage=5.0V, pad=1\n"
        "component Mcu:\n"
        '    package = "QFN-16"\n'
        "    pin VDD: role=power_in, vmax=3.6V, pad=1\n"
        "module M:\n"
        "    x = Xcvr()\n"
        "    u = Mcu()\n"
        "    x.IO ~ u.VDD\n"
    )
    assert "Q001" in codes(compile_source(src, "bidi_v.ohm"))


def test_bidi_declared_current_counts_in_q002():
    # 60mA sink + 60mA bidirectional draw > 100mA driver imax
    src = (
        "component Reg:\n"
        '    package = "SOT-23"\n'
        "    pin OUT: role=power_out, voltage=3.3V, imax=100mA, pad=1\n"
        "component Load:\n"
        '    package = "X"\n'
        "    pin VIN: role=power_in, current=60mA, pad=1\n"
        "component Bidi:\n"
        '    package = "X"\n'
        "    pin IO: role=bidirectional, current=60mA, pad=1\n"
        "module M:\n"
        "    r = Reg()\n"
        "    a = Load()\n"
        "    b = Bidi()\n"
        "    r.OUT ~ a.VIN\n"
        "    r.OUT ~ b.IO\n"
    )
    assert "Q002" in codes(compile_source(src, "bidi_i.ohm"))


# --- finding 2: Q003 skipped when both pins land on the same net ---

def test_same_net_resistor_no_q003():
    src = (
        "component Src:\n"
        '    package = "TO-220"\n'
        "    pin OUT: role=power_out, voltage=5.0V +/- 10%, pad=1\n"
        "component R1:\n"
        '    package = "R_0603"\n'
        "    value = 1ohm\n"
        "    pin P1: role=passive, pmax=250mW, pad=1\n"
        "    pin P2: role=passive, pad=2\n"
        "module M:\n"
        "    s = Src()\n"
        "    r = R1()\n"
        "    s.OUT ~ r.P1\n"
        "    s.OUT ~ r.P2\n"
    )
    assert "Q003" not in codes(compile_source(src, "same_net.ohm"))


# --- finding 3: Q005 coarse thermal (fixed 25degC ambient) ---

def _thermal_src(tmax):
    # 12V across 100ohm -> 1.44W; theta_ja=50 -> 25 + 72 = 97degC junction
    return (
        "component Src12:\n"
        '    package = "TO-220"\n'
        "    pin OUT: role=power_out, voltage=12V, pad=1\n"
        "    pin GND: role=ground, pad=2\n"
        "component RHot:\n"
        '    package = "R_2512"\n'
        "    value = 100ohm\n"
        "    theta_ja = 50\n"
        f"    tmax = {tmax}\n"
        "    pin P1: role=passive, pad=1\n"
        "    pin P2: role=passive, pad=2\n"
        "module M:\n"
        "    s = Src12()\n"
        "    r = RHot()\n"
        "    s.OUT ~ r.P1\n"
        "    r.P2 ~ s.GND\n"
    )


def test_q005_over_temperature_fires():
    result = compile_source(_thermal_src("85degC"), "hot.ohm")
    (d,) = [d for d in result.diagnostics if d.code == "Q005"]
    assert d.severity == "error" and not result.ok
    assert "97" in d.message and "50" in d.message and "85" in d.message


def test_q005_under_limit_clean():
    result = compile_source(_thermal_src("125degC"), "warm.ohm")
    assert result.ok
    assert not [c for c in codes(result) if c.startswith("Q")]


# --- finding 4: I006 catches floating input behind a series R ---

_BUF = (
    "component Buf:\n"
    '    package = "SOT-23-5"\n'
    "    pin IN:  role=input, pad=1\n"
    "    pin OUT: role=output, pad=4\n"
)


def test_series_r_floating_input_warns_i006():
    src = _BUF + (
        "component R0402:\n"
        '    package = "R_0402"\n'
        "    pin P1: role=passive, pad=1\n"
        "    pin P2: role=passive, pad=2\n"
        "module M:\n"
        "    a = Buf()\n"
        "    r = R0402()\n"
        "    a.IN ~ r.P1\n"
    )
    result = compile_source(src, "float.ohm")
    hits = [d for d in result.diagnostics if d.code == "I006"]
    assert hits and hits[0].severity == "warning" and result.ok


def test_input_on_port_net_no_i006():
    src = (
        "interface Power:\n"
        "    v:   Signal, role=power_in\n"
        "    gnd: Signal, role=ground\n"
    ) + _BUF + (
        "module M:\n"
        "    p = io(Power)\n"
        "    a = Buf()\n"
        "    p.v ~ a.IN\n"
    )
    assert "I006" not in codes(compile_source(src, "ported.ohm"))


# --- golden designs: no new Tier-I/Tier-Q diagnostics from any fix ---

@pytest.mark.parametrize("design", sorted(GOLDEN.glob("*.ohm")), ids=lambda p: p.stem)
def test_golden_designs_no_iq_codes(design):
    result = compile_source(design.read_text(), str(design))
    assert result.ok
    assert not [c for c in codes(result) if c.startswith(("I", "Q"))]
