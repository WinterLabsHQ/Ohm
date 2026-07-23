"""Milestone K: the component-type `kind` system (ADR-008). Conformance is
checked (nominal claim, structural check); multiple kinds compose as a union;
conflicts error; open-world base (no kind = unchecked)."""


from ohm.driver import compile_source
from ohm.emit_netlist import emit_netlist
from ohm.fmt import format_source

OPAMP = """
kind OpAmp:
    requires inp: input
    requires inn: input
    requires out: output
    requires vpos: power_in
    optional vneg: power_in
    param gain_bw: frequency
component TLV9061(OpAmp):
    package = generate(SOT23, pins=5)
    gain_bw = 1MHz
    pin OUT: role=output, pad=1, is=out
    pin VSS: role=power_in, pad=2, is=vneg
    pin INP: role=input, pad=3, is=inp
    pin INN: role=input, pad=4, is=inn
    pin VDD: role=power_in, pad=5, is=vpos
module M:
    u = TLV9061()
"""


def codes(src):
    return [d.code for d in compile_source(src, "k.ohm").diagnostics]


# ---------- conformance ----------
def test_conforming_component_is_clean():
    result = compile_source(OPAMP, "k.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    (comp,) = [c for c in result.design.components if c.name == "TLV9061"]
    assert comp.kinds == ("OpAmp",)
    assert {p.satisfies for p in comp.pins} == {"out", "vneg", "inp", "inn", "vpos"}


def test_optional_member_may_be_omitted():
    # a single-supply op-amp has no separate V- pin — must still conform
    src = OPAMP.replace("    pin VSS: role=power_in, pad=2, is=vneg\n", "")
    assert "K002" not in codes(src)


def test_no_kind_is_unchecked_open_world():
    # base Component: a part with no declared kind is never kind-checked (backcompat)
    src = 'component Weird:\n    package = "P"\n    pin X: role=passive, pad=1\nmodule M:\n    w = Weird()\n'
    assert not [c for c in codes(src) if c.startswith("K")]


# ---------- the five K codes ----------
def test_k001_conflict_between_kinds():
    src = ("kind KA:\n    requires ep: passive\nkind KB:\n    requires ep: ground\n"
           'component X(KA, KB):\n    package = "P"\n    pin E: role=passive, pad=1, is=ep\n')
    assert "K001" in codes(src)


def test_good_diamond_shared_member_is_clean():
    src = ("kind KA:\n    requires ep: passive\nkind KB:\n    requires ep: passive\n"
           'component X(KA, KB):\n    package = "P"\n    pin E: role=passive, pad=1, is=ep\n')
    assert not [c for c in codes(src) if c.startswith("K")]


def test_k002_missing_required_member():
    src = OPAMP.replace(", is=out", "")
    (d,) = [d for d in compile_source(src, "k.ohm").diagnostics if d.code == "K002"]
    assert "out" in d.message and "output" in d.message


def test_k003_role_mismatch():
    src = OPAMP.replace("pin INP: role=input, pad=3, is=inp",
                        "pin INP: role=input, pad=3, is=out")
    assert "K003" in codes(src)


def test_k004_missing_param():
    src = OPAMP.replace("    gain_bw = 1MHz\n", "")
    (d,) = [d for d in compile_source(src, "k.ohm").diagnostics if d.code == "K004"]
    assert "gain_bw" in d.message


def test_k004_wrong_param_dimension():
    src = OPAMP.replace("gain_bw = 1MHz", "gain_bw = 5V")
    assert "K004" in codes(src)


def test_k005_stray_binding():
    src = OPAMP.replace("is=inp", "is=bogus")
    assert "K005" in codes(src)


# ---------- multiple kinds (union) ----------
def test_multiple_kinds_union_conformance():
    src = (
        "kind MOSFET:\n    requires gate: input\n    requires drain: passive\n"
        "    requires source: passive\nkind ThermalPad:\n    requires ep: passive\n"
        'component Q(MOSFET, ThermalPad):\n    package = "TO-252"\n'
        "    pin G: role=input, pad=1, is=gate\n    pin D: role=passive, pad=2, is=drain\n"
        "    pin S: role=passive, pad=3, is=source\n    pin EP: role=passive, pad=4, is=ep\n"
    )
    assert not [c for c in codes(src) if c.startswith("K")]


def test_refinement_inherits_parent_contract():
    # LED extends Diode -> must satisfy Diode's anode/cathode
    src = ("kind Diode:\n    requires anode: passive\n    requires cathode: passive\n"
           "kind LED(Diode)\n"
           'component D1(LED):\n    package = "P"\n    pin A: role=passive, pad=1, is=anode\n')
    assert "K002" in codes(src)  # cathode unbound


# ---------- resolver-level errors ----------
def test_r017_unknown_kind():
    assert "R017" in codes('component X(NoSuchKind):\n    package = "P"\n    pin P: role=passive, pad=1\n')


def test_r018_kind_cycle():
    assert "R018" in codes("kind A(B)\nkind B(A)\n")


def test_r019_unknown_dimension():
    assert "R019" in codes("kind K:\n    param x: floops\n")


def test_duplicate_kind_and_component_name_coexist():
    # a kind and a component may share a name (separate namespaces)
    src = ("kind Resistor:\n    param value: resistance\n"
           'component Resistor(Resistor):\n    package = generate(CHIP0603)\n'
           "    value = 0ohm\n    pin P1: role=passive, pad=1\n    pin P2: role=passive, pad=2\n"
           "module M:\n    r = Resistor(value=10kohm)\n")
    assert "R001" not in codes(src)


# ---------- kinds are author-time only: no effect on emitted netlist ----------
def test_kinds_do_not_change_emit():
    kinded = OPAMP + "    u.OUT ~ u.INP\n"  # add a wire so there's a net
    plain = kinded.replace("(OpAmp)", "").replace(", is=out", "").replace(", is=vneg", "") \
                  .replace(", is=inp", "").replace(", is=inn", "").replace(", is=vpos", "") \
                  .replace("    gain_bw = 1MHz\n", "").replace(
        "kind OpAmp:\n    requires inp: input\n    requires inn: input\n    requires out: output\n"
        "    requires vpos: power_in\n    optional vneg: power_in\n    param gain_bw: frequency\n", "")
    a = compile_source(kinded, "k.ohm")
    b = compile_source(plain, "k.ohm")
    assert a.ok and b.ok
    assert emit_netlist(a.low, "x") == emit_netlist(b.low, "x")


def test_determinism_double_build():
    a = compile_source(OPAMP, "k.ohm")
    b = compile_source(OPAMP, "k.ohm")
    assert [d.machine() for d in a.diagnostics] == [d.machine() for d in b.diagnostics]


# ---------- fmt ----------
def test_fmt_round_trips_kinds():
    once, diags = format_source(OPAMP.strip() + "\n")
    assert not diags
    assert "kind OpAmp:" in once and "component TLV9061(OpAmp):" in once and "is=out" in once
    twice, _ = format_source(once)
    assert once == twice


def test_fmt_bodyless_refinement():
    once, diags = format_source("kind Diode:\n    requires a: passive\nkind LED(Diode)\n")
    assert not diags and "kind LED(Diode)" in once


# ---------- std.kinds taxonomy loads and is usable ----------
def test_std_kinds_loads_and_conforms(tmp_path):
    src = (
        "use std.kinds\n"
        "component MyR(Resistor):\n    package = generate(CHIP0603)\n    value = 0ohm\n"
        "    pin P1: role=passive, pad=1, is=a\n    pin P2: role=passive, pad=2, is=b\n"
        "module M:\n    r = MyR(value=10kohm)\n"
    )
    f = tmp_path / "d.ohm"
    f.write_text(src)
    result = compile_source(src, str(f))
    assert result.ok, [d.machine() for d in result.diagnostics]


# ---------- K2 adversarial-audit fixes: contracts must not false-reject real parts ----------
def test_lm317_ldo_without_gnd_conforms():
    # LM317/LT3080 floating regulators have IN/OUT/ADJ, no GND — gnd is optional
    src = ("use std.kinds\ncomponent LM317(LDO):\n    package = generate(SOT23, pins=3)\n"
           "    pin ADJ: role=input, pad=1\n    pin OUT: role=power_out, pad=2, is=vout\n"
           "    pin IN: role=power_in, pad=3, is=vin\n")
    assert not [c for c in codes(src) if c.startswith("K")]


def test_open_drain_output_satisfies_required_output():
    # LM393/LM339-class open-drain comparator: open_collector role satisfies out:output
    src = ("use std.kinds\ncomponent Cmp(Comparator):\n    package = generate(SOT23, pins=5)\n"
           "    pin OUT: role=open_collector, pad=1, is=out\n"
           "    pin VSS: role=power_in, pad=2, is=vneg\n    pin INP: role=input, pad=3, is=inp\n"
           "    pin INN: role=input, pad=4, is=inn\n    pin VDD: role=power_in, pad=5, is=vpos\n")
    assert not [c for c in codes(src) if c.startswith("K")]


def test_i2c_sensor_without_output_conforms():
    # digital I2C sensors carry data on bidirectional SDA — Sensor.signal is optional
    src = ("use std.kinds\ncomponent Dig(Sensor):\n    package = generate(SOT23, pins=6)\n"
           "    pin SDA: role=bidirectional, pad=1\n    pin SCL: role=input, pad=2\n"
           "    pin VDD: role=power_in, pad=3\n    pin GND: role=ground, pad=4\n"
           "    pin A0: role=input, pad=5\n    pin INT: role=output, pad=6\n")
    assert not [c for c in codes(src) if c.startswith("K")]


def test_integrated_buck_without_sw_conforms():
    # Recom R-78 / TPSM modules: integrated inductor, no SW node — sw is optional
    src = ("use std.kinds\ncomponent Buck(SwitchingConverter):\n    package = \"X\"\n"
           "    pin VIN: role=power_in, pad=1, is=vin\n    pin GND: role=ground, pad=2, is=gnd\n"
           "    pin VOUT: role=power_out, pad=3\n")
    assert not [c for c in codes(src) if c.startswith("K")]
