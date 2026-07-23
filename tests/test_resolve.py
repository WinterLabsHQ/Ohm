"""P2 gate: canonical 3.8 -> IR; IR JSON byte-identical across two runs."""

from decimal import Decimal
from pathlib import Path

from ohm import ir
from ohm.parse import parse
from ohm.resolve import resolve

CANONICAL = (Path(__file__).parent / "fixtures" / "canonical.ohm").read_text()


def build(src):
    prog, pdiags = parse(src)
    assert pdiags == []
    return resolve(prog)


def codes(diags):
    return [d.code for d in diags]


# ---------- gate ----------
def test_canonical_resolves_clean():
    design, diags = build(CANONICAL)
    assert diags == [], [d.machine() for d in diags]
    assert [i.name for i in design.interfaces] == ["I2C", "Power"]
    (ncp,) = design.components
    assert ncp.package == "TSOT-23-5"
    assert dict(ncp.attrs)["mpn"] == "NCP1529ASNT1G"
    assert [(p.name, p.role) for p in ncp.pins] == [
        ("EN", "input"), ("GND", "ground"), ("SW", "power_out"),
        ("VIN", "power_in"), ("FB", "input"),
    ]
    assert ncp.pins[3].vmax == ir.Quantity(Decimal("6.0"), "V")
    (supply,) = design.modules
    assert design.top == "Supply"
    assert [p.name for p in supply.ports] == ["vin", "vout"]
    assert [i.def_ref for i in supply.instances] == ["NCP1529"]
    assert [(c.left, c.right) for c in supply.connections] == [
        (("vin", "v"), ("reg", "VIN")),
        (("vin", "gnd"), ("reg", "GND")),
    ]
    assert len(supply.asserts) == 1
    (ob,) = supply.obligations
    assert ob.kind == "ripple" and ob.op == "<" and ob.args == (("vout", "v"),)


def test_ir_json_byte_identical():
    j1 = ir.to_json(build(CANONICAL)[0])
    j2 = ir.to_json(build(CANONICAL)[0])
    assert j1.encode() == j2.encode()


def test_quantity_tolerance_raw():
    design, diags = build('component R:\n    package = "R_0402"\n    pin A: role=passive, vmax=10kohm +/- 1%\n')
    assert diags == []
    q = design.components[0].pins[0].vmax
    assert q.tol_raw == (Decimal("1"), None) and q.tol_lo is None


def test_generate_spec():
    design, _ = build('component U:\n    package = generate(SOIC, pins=8)\n    pin A: role=passive\n')
    pkg = design.components[0].package
    assert isinstance(pkg, ir.GenerateSpec)
    assert pkg.package_type == "SOIC" and dict(pkg.kwargs)["pins"] == Decimal("8")


# ---------- error codes ----------
def test_duplicate_toplevel():
    _, diags = build("module M:\n    a = X()\nmodule M:\n    a = X()\n")
    assert "R001" in codes(diags)


def test_unknown_ctor_with_suggestion():
    _, diags = build('component NCP1529:\n    pin A: role=input\nmodule M:\n    reg = NCP1592()\n')
    (d,) = [d for d in diags if d.code == "R002"]
    assert "NCP1529" in (d.help or "")


def test_unknown_member_did_you_mean():
    src = CANONICAL.replace("vin.v   ~ reg.VIN", "vin.v   ~ reg.VNI")
    _, diags = build(src)
    (d,) = [d for d in diags if d.code == "R003"]
    assert "VIN" in (d.help or "")


def test_unknown_base_in_connect():
    _, diags = build("module M:\n    a = X.P1 ~ b\n" if False else "component C:\n    pin P: role=passive\nmodule M:\n    c = C()\n    c.P ~ ghost.P\n")
    assert "R002" in codes(diags)


def test_duplicate_binding():
    _, diags = build("component C:\n    pin P: role=passive\nmodule M:\n    a = C()\n    a = C()\n")
    assert "R004" in codes(diags)


def test_io_requires_interface():
    _, diags = build("component C:\n    pin P: role=passive\nmodule M:\n    p = io(C)\n")
    assert "R005" in codes(diags)


def test_pin_missing_role():
    _, diags = build("component C:\n    pin P: pad=1\n")
    assert "R006" in codes(diags)


def test_invalid_role():
    _, diags = build("component C:\n    pin P: role=outputt\n")
    (d,) = [d for d in diags if d.code == "R007"]
    assert "output" in (d.help or "")


def test_require_unknown_member():
    _, diags = build("interface X:\n    a: Signal\n    require pair(a, b)\n")
    assert "R008" in codes(diags)


def test_poison_suppresses_cascade():
    _, diags = build("module M:\n    u = Ghost()\n    u.P1 ~ u.P2\n")
    assert codes(diags) == ["R002"]  # one error, no cascade from u.P1/u.P2
