"""W2 audit-fix gate: emitter value/footprint/escaping + lower net-naming and
no-create constraint expansion (I007)."""

from pathlib import Path

from ohm import ir
from ohm.driver import compile_source
from ohm.emit_kicad import emit_kicad_sch
from ohm.emit_netlist import emit_netlist

GOLDEN = Path(__file__).parent / "golden"
FIXTURES = Path(__file__).parent / "fixtures"


def compile_ok(src: str, path: str = "<w2>"):
    result = compile_source(src, path)
    assert result.ok, [d.machine() for d in result.diagnostics]
    return result


# ---------- finding 1: value precedence (value attr > mpn > component name) ----------
def test_divider_value_line_is_quantity_text():
    path = GOLDEN / "divider.ohm"
    result = compile_ok(path.read_text(), str(path))
    emitted = emit_netlist(result.low, f"tests/golden/{path.name}")
    assert (
        '    (comp (ref "R1") (value "10kohm +/- 1%") (footprint "R_0402_1005Metric")'
        ' (fields (field (name "ohm_ref") "r1")))' in emitted.splitlines()
    )


# ---------- finding 2: generated package -> ohm:<component> footprint ----------
def test_generated_package_footprint():
    path = FIXTURES / "agent_mcp9808.ohm"
    result = compile_ok(path.read_text(), str(path))
    net = emit_netlist(result.low, path.name)
    assert '(footprint "ohm:TSSOP-8")' in net
    assert '(footprint "")' not in net
    sch = emit_kicad_sch(result.low, path.name, result.design)
    # the schematic now carries real symbols, so the footprint id rides on the
    # Footprint property rather than in a text note
    assert '(property "Footprint" "ohm:TSSOP-8"' in sch


# ---------- finding 3: backslash escaped before quotes ----------
def test_backslash_mpn_escaped():
    low = ir.IRLow(
        parts=(ir.PartLow("u", "U1", "C", None, (("mpn", 'trail\\'),)),),
        nets=(),
    )
    net = emit_netlist(low, "x")
    assert '(value "trail\\\\")' in net  # netlist text: "trail\\" — not a bare trailing \


# ---------- finding 4: net name prefers shallowest port label ----------
DEEP = """\
interface Power:
    v:   Signal, role=power_in
    gnd: Signal, role=ground

component R0402:
    package = "R_0402_1005Metric"
    designator_prefix = "R"
    pin P1: role=passive, pad=1
    pin P2: role=passive, pad=2

module Inner:
    pwr = io(Power)
    r = R0402()
    pwr.v ~ r.P1
    pwr.gnd ~ r.P2

module Top:
    vin = io(Power)
    a = Inner()
    vin ~ a.pwr
"""


def test_deep_port_net_named_from_top_level():
    result = compile_ok(DEEP)
    names = {n.name for n in result.low.nets}
    assert "vin.v" in names and "vin.gnd" in names
    assert "a.pwr.v" not in names  # deep inner label must not win over vin.v


# ---------- finding 5: constraint on unconnected pin -> I007, no phantom net ----------
UNCONNECTED = """\
component Conn:
    package = "USB_C"
    pin DP: role=bidirectional, pad=3
    pin DM: role=bidirectional, pad=2
    pin NC: role=passive, pad=1

module Board:
    j = Conn()
    j.DP ~ j.DM
    constraint impedance(j.NC, 45ohm)
"""


def test_constraint_unconnected_pin_warns_no_phantom_net():
    result = compile_source(UNCONNECTED, "<w2>")
    assert result.ok  # I007 is a warning, not an error
    i007 = [d for d in result.diagnostics if d.code == "I007"]
    assert len(i007) == 1  # once via driver's lower; checkers must not re-surface it
    assert i007[0].severity == "warning"
    assert "j.NC" in i007[0].message
    assert [n.name for n in result.low.nets] == ["j.DM"]  # no phantom j.NC net
    assert "j.NC" not in emit_netlist(result.low, "x")


# ---------- goldens byte-stable after regeneration (double-build) ----------
def test_goldens_byte_stable():
    for design in sorted(GOLDEN.glob("*.ohm")):
        src = design.read_text()
        a = compile_ok(src, str(design))
        b = compile_ok(src, str(design))
        emitted = emit_netlist(a.low, f"tests/golden/{design.name}")
        assert emitted == emit_netlist(b.low, f"tests/golden/{design.name}")
        assert emitted == (GOLDEN / f"{design.stem}.net").read_text()
