"""Expressiveness E0 (ADR-012): config-driven counts/bounds, pure `fn`
functions, and compile-time `if` variants — all folding at elaborate, all
override-guarded so nothing silently lies."""

from decimal import Decimal

from ohm.driver import compile_source
from ohm.emit_netlist import emit_netlist
from ohm.fmt import format_source


def result_of(src):
    return compile_source(src, "e.ohm")


def codes(src):
    return [d.code for d in result_of(src).diagnostics]


# ---------- E0a: config-driven counts and bounds ----------
CFG_BAR = """
use std.interfaces
use std.passives

module Bar:
    pwr = io(Power)
    n = config(Count, default=4)
    rs = n * Resistor(value=1kohm)
    for i in 0..n-1:
        pwr.v ~ rs[i].P1
        rs[i].P2 ~ pwr.gnd
"""


def test_config_count_elaborates():
    r = result_of(CFG_BAR)
    assert r.ok, [d.machine() for d in r.diagnostics]
    assert len(r.low.parts) == 4
    assert "rs[3]" in [p.ref for p in r.low.parts]


def test_config_count_override_is_guarded():
    src = CFG_BAR + (
        "\nmodule Top:\n    pwr = io(Power)\n    b = Bar(n=8)\n"
        "    pwr.v ~ b.pwr.v\n    pwr.gnd ~ b.pwr.gnd\n")
    diags = result_of(src).diagnostics
    assert any(d.code == "R022" and "'n'" in d.message for d in diags)


def test_quantity_config_as_count_rejected():
    src = ("use std.passives\nmodule M:\n    v = config(Voltage, default=3.3V)\n"
           "    rs = v * Resistor(value=1kohm)\n")
    assert "R021" in codes(src)


# ---------- E0b: pure fn functions ----------
FN_SRC = """
use std.interfaces
use std.passives
use std.discrete

fn led_r(vcc, vf, i) = (vcc - vf) / i
fn series(a, b) = a + b

module M:
    pwr = io(Power)
    d = LED()
    r = Resistor(value=led_r(3.3V, 2V, 10mA))
    r2 = Resistor(value=series(1kohm, 2kohm))
    pwr.v ~ r.P1
    r.P2 ~ d.A
    d.K ~ pwr.gnd
    r2.P1 ~ pwr.v
    r2.P2 ~ pwr.gnd
"""


def test_fn_folds_in_values():
    r = result_of(FN_SRC)
    assert r.ok, [d.machine() for d in r.diagnostics]
    vals = {p.ref: dict(p.attrs).get("value") for p in r.low.parts}
    assert vals["r"].value == Decimal(130) and vals["r"].unit == "ohm"
    assert vals["r2"].value == Decimal(3000)


def test_fn_calls_fn_and_asserts():
    src = ("fn double(x) = x * 2\nfn quad(x) = double(double(x))\n"
           "use std.passives\nmodule M:\n    r = Resistor(value=1kohm)\n"
           "    assert quad(1V) == 4V\n    r.P1 ~ r.P2\n")
    r = result_of(src)
    assert r.ok, [d.machine() for d in r.diagnostics]


def test_fn_assert_failure_detected():
    src = ("fn double(x) = x * 2\n"
           "use std.passives\nmodule M:\n    r = Resistor(value=1kohm)\n"
           "    assert double(2V) == 5V\n    r.P1 ~ r.P2\n")
    assert "C001" in codes(src)


def test_r023_impure_fn():
    assert "R023" in codes("fn bad(a) = a + other\n")


def test_r023_recursive_fn():
    assert "R023" in codes("fn loop_a(x) = loop_b(x)\nfn loop_b(x) = loop_a(x)\n")


def test_r023_duplicate_fn():
    assert "R023" in codes("fn f(x) = x\nfn f(y) = y\n")


def test_r023_unknown_call_in_fn():
    assert "R023" in codes("fn f(x) = mystery(x)\n")


def test_r022_wrong_arity_at_use_site():
    src = ("fn f(a, b) = a + b\nuse std.passives\n"
           "module M:\n    r = Resistor(value=f(1kohm))\n    r.P1 ~ r.P2\n")
    assert "R022" in codes(src)


# ---------- E0c: compile-time if variants ----------
VARIANT = """
use std.interfaces
use std.passives

module Node:
    bus = io(RS485)
    pwr = io(Power)
    endpoint = config(Count, default=1)
    if endpoint:
        rt = Resistor(value=120ohm)
        rt.P1 ~ bus.a
        rt.P2 ~ bus.b
    else:
        rp = Resistor(value=10kohm)
        rp.P1 ~ bus.a
        rp.P2 ~ pwr.gnd
"""


def test_if_taken_branch_elaborates():
    r = result_of(VARIANT)
    assert r.ok, [d.machine() for d in r.diagnostics]
    refs = [p.ref for p in r.low.parts]
    assert refs == ["rt"]  # else-branch does not exist downstream


def test_else_branch_when_false():
    r = result_of(VARIANT.replace("default=1", "default=0"))
    assert r.ok, [d.machine() for d in r.diagnostics]
    assert [p.ref for p in r.low.parts] == ["rp"]


def test_if_comparison_and_nested_loop():
    src = ("use std.interfaces\nuse std.passives\n"
           "module M:\n    pwr = io(Power)\n    n = config(Count, default=3)\n"
           "    if n > 2:\n        rs = n * Resistor(value=1kohm)\n"
           "        for i in 0..n-1:\n            pwr.v ~ rs[i].P1\n"
           "            rs[i].P2 ~ pwr.gnd\n")
    r = result_of(src)
    assert r.ok, [d.machine() for d in r.diagnostics]
    assert len(r.low.parts) == 3


def test_if_condition_config_override_guarded():
    src = VARIANT + (
        "\nmodule Top:\n    pwr = io(Power)\n    bus = io(RS485)\n"
        "    node = Node(endpoint=0)\n    pwr.v ~ node.pwr.v\n"
        "    pwr.gnd ~ node.pwr.gnd\n    bus.a ~ node.bus.a\n    bus.b ~ node.bus.b\n")
    diags = result_of(src).diagnostics
    assert any(d.code == "R022" and "endpoint" in d.message for d in diags)


def test_r024_dimensioned_condition():
    src = ("use std.passives\nmodule M:\n    v = config(Voltage, default=3.3V)\n"
           "    if v:\n        r = Resistor(value=1kohm)\n        r.P1 ~ r.P2\n")
    assert "R024" in codes(src)


def test_r024_config_inside_if():
    src = ("use std.passives\nmodule M:\n"
           "    if 1:\n        v = config(Voltage, default=3.3V)\n"
           "        r = Resistor(value=1kohm)\n        r.P1 ~ r.P2\n")
    assert "R024" in codes(src)


# ---------- fmt + determinism ----------
def test_fmt_preserves_all_e0_forms():
    src = ("use std.passives\n\nfn led_r(vcc, vf, i) = (vcc - vf) / i\n\n"
           "module M:\n    n = config(Count, default=2)\n"
           "    rs = n * Resistor(value=led_r(3.3V, 2V, 10mA))\n"
           "    if n > 1:\n        rs[0].P1 ~ rs[1].P1\n"
           "    else:\n        rs[0].P1 ~ rs[0].P2\n"
           "    for i in 0..n-1:\n        rs[i].P1 ~ rs[i].P2\n")
    once, diags = format_source(src)
    assert not diags
    assert "fn led_r(vcc, vf, i) = (vcc - vf) / i" in once
    assert "rs = n * Resistor" in once
    assert "if n > 1:" in once and "    else:" in once
    assert "for i in 0..n-1:" in once
    twice, _ = format_source(once)
    assert once == twice


def test_double_build_byte_identical():
    a, b = result_of(CFG_BAR), result_of(VARIANT)
    a2, b2 = result_of(CFG_BAR), result_of(VARIANT)
    assert emit_netlist(a.low, "x") == emit_netlist(a2.low, "x")
    assert emit_netlist(b.low, "x") == emit_netlist(b2.low, "x")
