"""P3 gate: `3.3V + 10kohm` -> error; `10kohm +/- 1%` parses & propagates;
conversions correct."""

from decimal import Decimal
from pathlib import Path

from ohm import ir, units
from ohm.check_units import annotate_units, check_units
from ohm.parse import parse
from ohm.resolve import resolve

CANONICAL = (Path(__file__).parent / "fixtures" / "canonical.ohm").read_text()


def build(src):
    prog, pd = parse(src)
    assert pd == []
    design, rd = resolve(prog)
    assert rd == []
    design, ud = annotate_units(design)
    return design, ud + check_units(design)


def codes(diags):
    return [d.code for d in diags]


# ---------- gate ----------
def test_add_volts_to_ohms_errors():
    _, diags = build("module M:\n    assert 3.3V + 10kohm <= 5V\n")
    assert "U001" in codes(diags)


def test_percent_tolerance_propagates():
    design, diags = build('component R:\n    pin A: role=passive, vmax=10kohm +/- 1%\n')
    assert diags == []
    q = design.components[0].pins[0].vmax
    assert (q.tol_lo, q.tol_hi) == (Decimal("9.9"), Decimal("10.1"))
    # and through interval arithmetic (a divider's two Rs)
    total = units.interval_of(q) + units.interval_of(q)
    assert (total.lo, total.hi) == (Decimal("19.8"), Decimal("20.2"))


def test_conversions_correct():
    assert units.convert(Decimal(1), "kohm", "ohm") == Decimal(1000)
    assert units.convert(Decimal(3300), "mV", "V") == Decimal("3.3")
    assert units.convert(Decimal(1), "mil", "um") == Decimal("25.4")
    assert units.convert(Decimal(1), "V", "A") is None  # incompatible


# ---------- beyond the gate ----------
def test_absolute_cross_unit_tolerance():
    design, diags = build('component R:\n    pin A: role=passive, vmax=10kohm +/- 500ohm\n')
    assert diags == []
    q = design.components[0].pins[0].vmax
    assert (q.tol_lo, q.tol_hi) == (Decimal("9.5"), Decimal("10.5"))


def test_tolerance_dim_mismatch():
    _, diags = build('component R:\n    pin A: role=passive, vmax=10kohm +/- 5V\n')
    assert "U004" in codes(diags)


def test_unknown_unit():
    _, diags = build('component R:\n    pin A: role=passive, vmax=10zorp\n')
    assert "U003" in codes(diags)


def test_compare_dim_mismatch():
    _, diags = build("module M:\n    assert 3.3V <= 100mA\n")
    assert "U002" in codes(diags)


def test_property_dims_checked():
    src = CANONICAL.replace("assert vin.v.voltage <= reg.VIN.vmax",
                            "assert vin.v.voltage <= reg.VIN.imax")
    _, diags = build(src)
    assert "U002" in codes(diags)


def test_canonical_units_clean():
    _, diags = build(CANONICAL)
    assert diags == [], [d.machine() for d in diags]


def test_dimensionless_scaling_ok():
    _, diags = build("module M:\n    assert (3.3V - 1.2V) / 2 <= 1.1V\n")
    assert diags == []


def test_annotate_is_pure_and_deterministic():
    prog, _ = parse(CANONICAL)
    design, _ = resolve(prog)
    before = ir.to_json(design)
    a1, _ = annotate_units(design)
    a2, _ = annotate_units(design)
    assert ir.to_json(design) == before  # input untouched
    assert ir.to_json(a1) == ir.to_json(a2)


def test_interval_mul_div():
    a = units.Interval(Decimal(2), Decimal(3))
    b = units.Interval(Decimal(4), Decimal(5))
    assert (a * b).lo == Decimal(8) and (a * b).hi == Decimal(15)
    q = b / a
    assert (q.lo, q.hi) == (Decimal(4) / Decimal(3), Decimal("2.5"))
