"""P1 gate: canonical program parses to expected AST; >=10 syntax fixtures
(including malformed -> clean error with line/col) pass."""

from decimal import Decimal
from pathlib import Path

from ohm import ast_nodes as A
from ohm.parse import parse

CANONICAL = (Path(__file__).parent / "fixtures" / "canonical.ohm").read_text()


def ok(src):
    prog, diags = parse(src)
    assert diags == [], [d.machine() for d in diags]
    return prog


def bad(src):
    prog, diags = parse(src)
    assert diags, "expected syntax error"
    return prog, diags


# ---------- the canonical program (manual 3.8) ----------
def test_canonical_shape():
    prog = ok(CANONICAL)
    i2c, power, ncp, supply = prog.decls
    assert isinstance(i2c, A.InterfaceDecl) and i2c.name == "I2C"
    assert [m.name for m in i2c.members] == ["scl", "sda"]
    assert i2c.members[0].attrs[0].name == "role"
    assert i2c.members[0].attrs[0].value.dotted() == "bidirectional"
    assert isinstance(power, A.InterfaceDecl) and power.name == "Power"
    assert isinstance(ncp, A.ComponentDecl) and ncp.name == "NCP1529"
    assert isinstance(supply, A.ModuleDecl) and supply.name == "Supply"


def test_canonical_component():
    ncp = ok(CANONICAL).decls[2]
    attrs = {a.name: a.value for a in ncp.attrs}
    assert attrs["package"].value == "TSOT-23-5"
    assert attrs["mpn"].value == "NCP1529ASNT1G"
    assert [p.name for p in ncp.pins] == ["EN", "GND", "SW", "VIN", "FB"]
    vin = {a.name: a.value for a in ncp.pins[3].attrs}
    assert vin["role"].dotted() == "power_in"
    assert vin["vmax"] == A.QuantityLit(Decimal("6.0"), "V", None, vin["vmax"].span)


def test_canonical_module():
    supply = ok(CANONICAL).decls[3]
    bindings = [i for i in supply.items if isinstance(i, A.Binding)]
    connects = [i for i in supply.items if isinstance(i, A.Connect)]
    asserts = [i for i in supply.items if isinstance(i, A.AssertStmt)]
    obligations = [i for i in supply.items if isinstance(i, A.ObligationStmt)]
    assert [b.name for b in bindings] == ["vin", "vout", "reg"]
    assert bindings[0].call.callee.dotted() == "io"
    assert bindings[2].call.callee.dotted() == "NCP1529"
    assert [(c.left.dotted(), c.right.dotted()) for c in connects] == [
        ("vin.v", "reg.VIN"),
        ("vin.gnd", "reg.GND"),
    ]
    (a,) = asserts
    assert isinstance(a.expr, A.BinOp) and a.expr.op == "<="
    assert a.expr.left.dotted() == "vin.v.voltage"
    (o,) = obligations
    assert o.call.callee.dotted() == "ripple" and o.op == "<"
    assert o.bound == A.QuantityLit(Decimal("50"), "mV", None, o.bound.span)


def test_canonical_spans():
    prog = ok(CANONICAL)
    assert prog.decls[0].span.line == 1
    assert prog.decls[2].pins[0].span.line == 12  # pin EN


# ---------- good syntax fixtures ----------
def test_tolerance_percent():
    prog = ok("component R:\n    package = \"R_0402\"\n    value = 10kohm +/- 1%\n")
    v = {a.name: a.value for a in prog.decls[0].attrs}["value"]
    assert v.unit == "kohm" and v.tol == A.Tolerance(Decimal("1"), None)


def test_tolerance_absolute():
    prog = ok("component R:\n    value = 10kohm +/- 500ohm\n")
    v = prog.decls[0].attrs[0].value
    assert v.tol == A.Tolerance(Decimal("500"), "ohm")


def test_generate_package():
    prog = ok("component U:\n    package = generate(SOIC, pins=8, pitch=1.27mm)\n")
    pkg = prog.decls[0].attrs[0].value
    assert isinstance(pkg, A.Call) and pkg.callee.dotted() == "generate"
    assert pkg.args[0].dotted() == "SOIC"
    assert dict(pkg.kwargs)["pins"].value == Decimal("8")


def test_require_relation():
    prog = ok("interface USB:\n    dp: Signal, role=bidirectional\n    dm: Signal, role=bidirectional\n    require pair(dp, dm)\n")
    (req,) = prog.decls[0].requires
    assert req.relation.callee.dotted() == "pair"
    assert [a.dotted() for a in req.relation.args] == ["dp", "dm"]


def test_forward_reference_order():
    # module may reference a component declared later (R8)
    prog = ok("module M:\n    u = Later()\ncomponent Later:\n    package = \"X\"\n")
    assert isinstance(prog.decls[0], A.ModuleDecl)
    assert isinstance(prog.decls[1], A.ComponentDecl)


def test_arithmetic_assert():
    prog = ok("module M:\n    assert (3.3V - 1.2V) / 2 <= 1.1V\n")
    (a,) = prog.decls[0].items
    assert a.expr.op == "<=" and a.expr.left.op == "/"


def test_config_default():
    prog = ok("module M:\n    vout = config(Voltage, default=3.3V)\n")
    call = prog.decls[0].items[0].call
    assert call.callee.dotted() == "config"
    assert dict(call.kwargs)["default"].unit == "V"


def test_raw_pin_connect_and_comments():
    prog = ok("# top comment\nmodule M:  # trailing\n    # indented comment\n    a = X()\n    a.P1 ~ a.P2  # raw escape hatch\n")
    items = prog.decls[0].items
    assert isinstance(items[1], A.Connect)


def test_blank_lines_and_no_final_newline():
    prog = ok("\n\nmodule M:\n    a = X()")
    assert prog.decls[0].name == "M"


# ---------- malformed fixtures (clean error with line/col) ----------
def test_missing_colon():
    _, diags = bad("interface I2C\n    scl: Signal\n")
    assert diags[0].code == "P001" and diags[0].span.line == 1


def test_bad_indent():
    _, diags = bad("module M:\nx = Y()\n")
    assert diags[0].severity == "error"


def test_garbage_line_reports_line():
    _, diags = bad("module M:\n    a = X()\n    ~~~ what ~~~\n")
    assert diags[0].span.line == 3


def test_unclosed_paren():
    _, diags = bad("module M:\n    a = X(\n")
    assert diags[0].code == "P001"


def test_bad_tolerance():
    _, diags = bad("component R:\n    value = 10kohm +/-\n")
    assert diags[0].span.line == 2


def test_multiple_errors_and_partial_recovery():
    src = (
        "interface Good:\n    s: Signal\n"
        "component Bad1\n    x = 1\n"
        "module Good2:\n    a = X()\n"
        "module Bad2:\n    a ==== b\n"
    )
    prog, diags = parse(src)
    # both bad blocks reported, both good blocks recovered
    assert len(diags) == 2
    assert sorted(d.span.line for d in diags) == [3, 8]
    assert [d.name for d in prog.decls] == ["Good", "Good2"]


def test_machine_and_human_rendering():
    _, diags = parse("interface I2C\n    scl: Signal\n", path="bad.ohm")
    line = diags[0].machine()
    assert line.startswith("bad.ohm:1:") and "error[P001]" in line
    human = diags[0].human("interface I2C\n    scl: Signal\n")
    assert "^" in human
