"""P8 gate: (a) closed-form value bit-identical across runs/machines;
(b) an `obligation` becomes a Hole and no solver is importable in the build."""

from decimal import Decimal
from pathlib import Path

from ohm import ir
from ohm.closedform import (FUNCTIONS, Qty, divider, microstrip_z0, parallel,
                            quantity_qty, rc_tau)
from ohm.driver import compile_source
from ohm.units import DIMENSIONLESS, Interval

CANONICAL = (Path(__file__).parent / "fixtures" / "canonical.ohm").read_text()


def q(value, unit):
    return quantity_qty(ir.Quantity(Decimal(value), unit))


def num(value):
    v = Decimal(value)
    return Qty(Interval(v, v), DIMENSIONLESS)


def codes(result):
    return [d.code for d in result.diagnostics]


# ---------- gate (a): deterministic closed-form values ----------
def test_microstrip_bit_identical_golden():
    w, h, er = q("0.3", "mm"), q("0.16", "mm"), num("4.3")
    z1 = microstrip_z0(w, h, er)
    z2 = microstrip_z0(w, h, er)
    assert z1.iv.lo == z1.iv.hi == Decimal("51.3159")  # frozen golden
    assert z1 == z2


def test_divider_interval_exact():
    r = ir.Quantity(Decimal(10), "kohm", Decimal("9.9"), Decimal("10.1"))
    out = divider(q(5, "V"), quantity_qty(r), quantity_qty(r))
    assert (out.iv.lo, out.iv.hi) == (Decimal("2.475"), Decimal("2.525"))
    assert out.dim == "voltage"


def test_parallel_and_rc():
    p = parallel(q(10, "kohm"), q(10, "kohm"))
    assert (p.iv.lo, p.iv.hi) == (Decimal(5000), Decimal(5000))
    tau = rc_tau(q(10, "kohm"), q(100, "nF"))
    assert tau.dim == "time" and tau.iv.lo == Decimal("0.0010000")


def test_wrong_dims_refuse():
    assert divider(q(5, "V"), q(5, "V"), q(10, "kohm")) is None
    assert FUNCTIONS["p_i2r"](q(5, "V"), q(10, "kohm")) is None


# ---------- assert folding ----------
_REG = (
    "component Reg:\n"
    '    package = "SOT-23-5"\n'
    "    pin VIN: role=power_in, vmax=6.0V, pad=1\n"
    "    pin GND: role=ground, pad=2\n"
)


def test_assert_holds_clean():
    src = _REG + "module M:\n    vcc = config(Voltage, default=5V)\n    r = Reg()\n    assert vcc <= r.VIN.vmax\n"
    result = compile_source(src, "ok.ohm")
    assert result.ok and "C001" not in codes(result) and "C003" not in codes(result)


def test_assert_certainly_false_C001():
    src = _REG + "module M:\n    vcc = config(Voltage, default=6.5V)\n    r = Reg()\n    assert vcc <= r.VIN.vmax\n"
    result = compile_source(src, "bad.ohm")
    (d,) = [d for d in result.diagnostics if d.code == "C001"]
    assert d.severity == "error" and not result.ok and result.low is None
    assert "6.5" in d.message and "6.0" in d.message


def test_assert_may_fail_C002():
    src = _REG + "module M:\n    vcc = config(Voltage, default=5V +/- 10%)\n    r = Reg()\n    assert vcc <= 5.2V\n"
    result = compile_source(src, "may.ohm")
    (d,) = [d for d in result.diagnostics if d.code == "C002"]
    assert d.severity == "error"


def test_assert_unevaluable_C003_info_still_builds():
    result = compile_source(CANONICAL, "canonical.ohm")
    infos = [d for d in result.diagnostics if d.code == "C003"]
    assert len(infos) == 1 and infos[0].severity == "info"
    assert "vin.v.voltage" in infos[0].message
    assert result.ok and result.low is not None


def test_config_override_checked_per_context():
    src = (
        "module Sub:\n"
        "    v = config(Voltage, default=3V)\n"
        "    assert v <= 5V\n"
        "module Top:\n"
        "    s = Sub(v=8V)\n"
    )
    result = compile_source(src, "ctx.ohm")
    fails = [d for d in result.diagnostics if d.code == "C001"]
    assert len(fails) == 1 and "(in s)" in fails[0].message


def test_divider_via_instance_attrs():
    src = (
        "component R0402:\n"
        '    package = "R_0402"\n'
        "    designator_prefix = \"R\"\n"
        "    pin P1: role=passive, pad=1\n"
        "    pin P2: role=passive, pad=2\n"
        "module M:\n"
        "    vcc = config(Voltage, default=5V)\n"
        "    r1 = R0402(value=10kohm +/- 1%)\n"
        "    r2 = R0402(value=10kohm +/- 1%)\n"
        "    r1.P2 ~ r2.P1\n"
        "    assert divider(vcc, r1.value, r2.value) <= 2.6V\n"
    )
    result = compile_source(src, "div.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert "C003" not in codes(result)  # fully evaluated, worst case 2.525V <= 2.6V


# ---------- gate (b): obligation → Hole; solver never in the build ----------
def test_obligation_becomes_hole_not_evaluated():
    result = compile_source(CANONICAL, "canonical.ohm")
    assert result.low.holes == (
        ir.Hole("ripple", (("vout", "v"),), "<", "50mV"),)


def test_hole_paths_prefixed_through_hierarchy():
    src = (
        "module Sub:\n"
        "    out = config(Voltage, default=3V)\n"
        "    obligation ripple(out) < 10mV\n"
        "module Top:\n"
        "    s = Sub()\n"
    )
    result = compile_source(src, "h.ohm")
    assert result.low.holes == (ir.Hole("ripple", (("s", "out"),), "<", "10mV"),)


def test_no_solver_modules_in_build_path():
    ohm_pkg = Path(__file__).parent.parent / "ohm"
    for py in ohm_pkg.rglob("*.py"):
        text = py.read_text()
        for solver in ("scipy", "numpy", "PySpice", "ngspice", "spicelib"):
            assert solver not in text, f"{py.name} references solver '{solver}' (D2/N2)"
