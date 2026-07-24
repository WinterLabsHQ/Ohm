"""Milestone G, part G0 (ADR-010): array instantiation, bounded for-loops,
computed attribute values. Repetition is pure elaboration — checkers see plain
instances; determinism and hermeticity hold (literal bounds, compile-time fold)."""

from decimal import Decimal

from ohm.driver import compile_source
from ohm.emit_netlist import emit_netlist
from ohm.fmt import format_source

LEDBAR = """
use std.interfaces
use std.passives
use std.discrete

module LedBar:
    pwr = io(Power)
    vcc = config(Voltage, default=3.3V)
    leds = 8 * LED()
    rs = 8 * Resistor(value=(vcc - 2V) / 10mA)
    for i in 0..7:
        pwr.v ~ rs[i].P1
        rs[i].P2 ~ leds[i].A
        leds[i].K ~ pwr.gnd
"""


def result_of(src):
    return compile_source(src, "g.ohm")


def codes(src):
    return [d.code for d in result_of(src).diagnostics]


# ---------- arrays + loops elaborate correctly ----------
def test_ledbar_elaborates_16_parts():
    r = result_of(LEDBAR)
    assert r.ok, [d.machine() for d in r.diagnostics]
    refs = [p.ref for p in r.low.parts]
    assert len(refs) == 16 and len(set(refs)) == 16
    assert "leds[0]" in refs and "leds[7]" in refs and "rs[3]" in refs
    designators = [p.designator for p in r.low.parts]
    assert len(set(designators)) == 16  # unique designators per element


def test_loop_wiring_matches_handwritten():
    loop = (
        "use std.passives\ninterface P:\n    v: Signal, role=power_in\n"
        "    gnd: Signal, role=ground\n"
        "module M:\n    p = io(P)\n    rs = 2 * Resistor(value=1kohm)\n"
        "    for i in 0..1:\n        p.v ~ rs[i].P1\n        rs[i].P2 ~ p.gnd\n"
    )
    hand = (
        "use std.passives\ninterface P:\n    v: Signal, role=power_in\n"
        "    gnd: Signal, role=ground\n"
        "module M:\n    p = io(P)\n"
        "    rs0 = Resistor(value=1kohm)\n    rs1 = Resistor(value=1kohm)\n"
        "    p.v ~ rs0.P1\n    rs0.P2 ~ p.gnd\n"
        "    p.v ~ rs1.P1\n    rs1.P2 ~ p.gnd\n"
    )
    a, b = result_of(loop), result_of(hand)
    assert a.ok and b.ok
    # same net structure modulo the [i] naming
    canon_a = emit_netlist(a.low, "x").replace("[0]", "0").replace("[1]", "1")
    assert canon_a == emit_netlist(b.low, "x")


def test_checkers_apply_per_element():
    # per-instance checks (expects tie) fire once per array element
    src = ("kind K:\n    requires g: input\n    expects tie on g\n"
           'component C(K):\n    package = "X"\n    pin G: role=input, pad=1, is=g\n'
           "    pin O: role=output, pad=2\n"
           'component Ld:\n    package = "X"\n    pin IN: role=input, pad=1\n'
           "module M:\n    cs = 3 * C()\n    ld = Ld()\n"
           "    for i in 0..2:\n        cs[i].O ~ ld.IN\n")
    assert codes(src).count("B002") == 3  # each element's gate floats independently


# ---------- computed values ----------
def test_computed_value_folds_exactly():
    r = result_of(LEDBAR)
    rs0 = next(p for p in r.low.parts if p.ref == "rs[0]")
    value = dict(rs0.attrs)["value"]
    assert value.value == Decimal(130) and value.unit == "ohm"


def test_computed_value_with_tolerance_interval():
    src = ("use std.passives\nmodule M:\n"
           "    vcc = config(Voltage, default=5V +/- 10%)\n"
           "    r = Resistor(value=vcc / 10mA)\n")
    r = result_of(src)
    assert r.ok
    v = dict(r.low.parts[0].attrs)["value"]
    assert v.unit == "ohm" and v.tol_lo == Decimal(450) and v.tol_hi == Decimal(550)


def test_component_level_computed_attr():
    src = ('component C:\n    package = "X"\n    value = 2 * 50ohm\n'
           "    pin P1: role=passive, pad=1\n    pin P2: role=passive, pad=2\n"
           "module M:\n    c = C()\n")
    r = result_of(src)
    v = dict(r.low.parts[0].attrs)["value"]
    assert v.value == Decimal(100) and v.unit == "ohm"


# ---------- R021/R022 ----------
def test_r021_out_of_range():
    src = ("use std.passives\nmodule M:\n    rs = 3 * Resistor(value=1kohm)\n"
           "    rs[5].P1 ~ rs[0].P2\n")
    assert "R021" in codes(src)


def test_r021_loop_var_outside_loop():
    src = ("use std.passives\nmodule M:\n    rs = 3 * Resistor(value=1kohm)\n"
           "    rs[i].P1 ~ rs[0].P2\n")
    assert "R021" in codes(src)


def test_r021_bare_array_in_connect():
    src = ("use std.passives\ninterface P:\n    v: Signal, role=power_in\n"
           "module M:\n    p = io(P)\n    rs = 3 * Resistor(value=1kohm)\n    rs ~ p.v\n")
    diags = result_of(src).diagnostics
    (d,) = [d for d in diags if d.code == "R021"]
    assert "index it" in d.message


def test_r021_count_bounds():
    assert "R021" in codes("use std.passives\nmodule M:\n    rs = 2000 * Resistor(value=1kohm)\n")
    assert "R021" in codes("use std.passives\nmodule M:\n    rs = 0 * Resistor(value=1kohm)\n")


def test_r021_bad_for_range():
    src = ("use std.passives\nmodule M:\n    rs = 2 * Resistor(value=1kohm)\n"
           "    for i in 5..2:\n        rs[0].P1 ~ rs[1].P1\n")
    assert "R021" in codes(src)


def test_r022_division_by_zero():
    assert "R022" in codes("use std.passives\nmodule M:\n    r = Resistor(value=1kohm / 0)\n")


def test_r022_unfoldable_ref():
    src = ("use std.passives\nmodule M:\n    r1 = Resistor(value=1kohm)\n"
           "    r2 = Resistor(value=r1.value + 1ohm)\n")  # instance refs don't fold in v1
    assert "R022" in codes(src)


# ---------- determinism + fmt ----------
def test_double_build_byte_identical():
    a, b = result_of(LEDBAR), result_of(LEDBAR)
    assert emit_netlist(a.low, "x").encode() == emit_netlist(b.low, "x").encode()


def test_fmt_preserves_repetition_forms():
    once, diags = format_source(LEDBAR.strip() + "\n")
    assert not diags
    assert "leds = 8 * LED()" in once
    assert "for i in 0..7:" in once
    assert "value=(vcc - 2V) / 10mA" in once  # NOT unrolled/folded in fmt
    twice, _ = format_source(once)
    assert once == twice


# ---------- G1: loop-var arithmetic (chains/ladders) ----------
CHAIN = (
    "use std.passives\ninterface P:\n    v: Signal, role=power_in\n"
    "    gnd: Signal, role=ground\n"
    "module M:\n    p = io(P)\n    rs = 3 * Resistor(value=1kohm)\n"
    "    p.v ~ rs[0].P1\n"
    "    for i in 0..1:\n        rs[i].P2 ~ rs[i+1].P1\n"
    "    rs[2].P2 ~ p.gnd\n"
)


def test_chain_matches_handwritten():
    hand = (
        "use std.passives\ninterface P:\n    v: Signal, role=power_in\n"
        "    gnd: Signal, role=ground\n"
        "module M:\n    p = io(P)\n"
        "    rs0 = Resistor(value=1kohm)\n    rs1 = Resistor(value=1kohm)\n"
        "    rs2 = Resistor(value=1kohm)\n"
        "    p.v ~ rs0.P1\n    rs0.P2 ~ rs1.P1\n    rs1.P2 ~ rs2.P1\n"
        "    rs2.P2 ~ p.gnd\n"
    )
    a, b = result_of(CHAIN), result_of(hand)
    assert a.ok, [d.machine() for d in a.diagnostics]
    canon = emit_netlist(a.low, "x")
    for i in range(3):
        canon = canon.replace(f"[{i}]", str(i))
    assert canon == emit_netlist(b.low, "x")


def test_negative_offset_works():
    src = ("use std.passives\nmodule M:\n    rs = 3 * Resistor(value=1kohm)\n"
           "    for i in 1..2:\n        rs[i-1].P2 ~ rs[i].P1\n")
    assert result_of(src).ok


def test_r021_negative_index():
    src = ("use std.passives\nmodule M:\n    rs = 3 * Resistor(value=1kohm)\n"
           "    for i in 0..2:\n        rs[i-1].P2 ~ rs[i].P1\n")
    diags = result_of(src).diagnostics
    r021 = [d for d in diags if d.code == "R021"]
    assert len(r021) == 1 and "-1" in r021[0].message  # once per loop, not per iteration


def test_r021_offset_overruns_array():
    src = ("use std.passives\nmodule M:\n    rs = 3 * Resistor(value=1kohm)\n"
           "    for i in 0..2:\n        rs[i].P2 ~ rs[i+1].P1\n")
    diags = result_of(src).diagnostics
    assert any(d.code == "R021" and "out of range" in d.message for d in diags)


def test_r021_non_integer_offset():
    src = ("use std.passives\nmodule M:\n    rs = 3 * Resistor(value=1kohm)\n"
           "    for i in 0..1:\n        rs[i+1.5].P2 ~ rs[i].P1\n")
    assert "R021" in codes(src)


def test_r021_non_integer_literal_index():
    src = ("use std.passives\nmodule M:\n    rs = 3 * Resistor(value=1kohm)\n"
           "    rs[1.5].P1 ~ rs[0].P2\n")
    diags = result_of(src).diagnostics
    assert any(d.code == "R021" and "must be an integer" in d.message for d in diags)


def test_fmt_preserves_index_arithmetic():
    once, diags = format_source(CHAIN)
    assert not diags
    assert "rs[i].P2 ~ rs[i+1].P1" in once
    twice, _ = format_source(once)
    assert once == twice


# ---------- adversarial-verify regression locks (2026-07-24) ----------
def test_r022_config_override_of_folded_value():
    # THE silent-wrong-netlist bug: folds bake the default in; an override
    # must be an error, never a 130ohm resistor on a 5V rail.
    src = ("use std.interfaces\nuse std.passives\n"
           "module Child:\n    pwr = io(Power)\n"
           "    vcc = config(Voltage, default=3.3V)\n"
           "    r = Resistor(value=(vcc - 2V) / 10mA)\n"
           "    pwr.v ~ r.P1\n    r.P2 ~ pwr.gnd\n"
           "module Top:\n    pwr = io(Power)\n    c = Child(vcc=5V)\n"
           "    pwr.v ~ c.pwr.v\n    pwr.gnd ~ c.pwr.gnd\n")
    diags = result_of(src).diagnostics
    assert any(d.code == "R022" and "overriding" in d.message for d in diags)


def test_config_override_without_folded_use_is_fine():
    src = ("use std.interfaces\nuse std.passives\n"
           "module Child:\n    pwr = io(Power)\n"
           "    vcc = config(Voltage, default=3.3V)\n"
           "    r = Resistor(value=1kohm)\n"
           "    assert vcc < 6V\n"
           "    pwr.v ~ r.P1\n    r.P2 ~ pwr.gnd\n"
           "module Top:\n    pwr = io(Power)\n    c = Child(vcc=5V)\n"
           "    pwr.v ~ c.pwr.v\n    pwr.gnd ~ c.pwr.gnd\n")
    r = result_of(src)
    assert r.ok, [d.machine() for d in r.diagnostics]


def test_r004_plain_binding_vs_array_same_name():
    src = ("use std.passives\nmodule M:\n"
           "    rs = Resistor(value=1kohm)\n"
           "    rs = 3 * Resistor(value=2kohm)\n")
    diags = result_of(src).diagnostics
    assert any(d.code == "R004" and "array" in d.message for d in diags)


def test_r021_io_and_config_cannot_be_arrayed():
    src_io = ("use std.interfaces\nmodule M:\n    ps = 2 * io(Power)\n"
              "    ps[0].v ~ ps[1].v\n")
    src_cfg = ("use std.passives\nmodule M:\n    vs = 2 * config(Voltage, default=3.3V)\n"
               "    r = Resistor(value=1kohm)\n")
    assert "R021" in codes(src_io)
    assert "R021" in codes(src_cfg)


def test_module_arrays_lower_correctly():
    src = ("use std.interfaces\nuse std.passives\n"
           "module Leg:\n    pwr = io(Power)\n    r = Resistor(value=1kohm)\n"
           "    pwr.v ~ r.P1\n    r.P2 ~ pwr.gnd\n"
           "module Top:\n    pwr = io(Power)\n    legs = 2 * Leg()\n"
           "    for i in 0..1:\n        pwr.v ~ legs[i].pwr.v\n"
           "        pwr.gnd ~ legs[i].pwr.gnd\n")
    r = result_of(src)
    assert r.ok
    assert sorted(p.ref for p in r.low.parts) == ["legs[0].r", "legs[1].r"]
