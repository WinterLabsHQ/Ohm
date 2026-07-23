"""Regression tests for W1 audit fixes (closedform arity, imp_zen net binding)."""

from ohm.driver import compile_source
from ohm.imp_zen import import_zen


def nets_of(low):
    return {n.name: {(nd.part_ref, nd.pin) for nd in n.nodes} for n in low.nets}


# ---------- finding 1: wrong-arity closed-form call must not crash ----------
def test_wrong_arity_assert_is_C003_not_crash():
    src = (
        "module M:\n"
        "    vcc = config(Voltage, default=3V)\n"
        "    assert divider(vcc) <= 1V\n"
    )
    result = compile_source(src, "arity.ohm")  # must not raise TypeError
    infos = [d for d in result.diagnostics if d.code == "C003"]
    assert len(infos) == 1 and infos[0].severity == "info"
    assert result.ok


# ---------- finding 2: io-attr bundle members land on distinct nets ----------
def test_io_attr_members_distinct_nets():
    src = (
        "spi = io(Spi)\n"
        'Component(name="U1", footprint="X", pins={"MISO": spi.MISO, "MOSI": spi.MOSI})\n'
    )
    low, diags = import_zen(src, "bundle.zen")
    assert [d for d in diags if d.severity == "error"] == []
    nets = nets_of(low)
    assert nets["spi.MISO"] == {("U1", "MISO")}
    assert nets["spi.MOSI"] == {("U1", "MOSI")}


def test_x_dot_net_resolves_to_base_net():
    src = (
        "VCC = io(Power)\n"
        'Component(name="U1", footprint="X", pins={"VIN": VCC.NET, "VIN2": VCC})\n'
    )
    low, diags = import_zen(src, "zener.zen")
    assert nets_of(low) == {"VCC": {("U1", "VIN"), ("U1", "VIN2")}}


# ---------- finding 3: NotConnected-bound var creates no net, drops pins ----------
def test_notconnected_var_drops_pins():
    src = (
        "nc = NotConnected()\n"
        'a = Net("A")\n'
        'Component(name="U1", footprint="X", pins={"P1": a, "P2": nc})\n'
    )
    low, diags = import_zen(src, "nc.zen")
    assert nets_of(low) == {"A": {("U1", "P1")}}  # no "NC"/"nc" net, P2 dropped
    assert not any(d.code == "Z002" for d in diags)


# ---------- finding 4: assignment-form Component / generic call imports the part ----------
def test_assignment_form_parts_imported():
    src = (
        'Resistor = Module("@stdlib/generics/Resistor.zen")\n'
        'vcc = Net("VCC")\n'
        "gnd = Ground()\n"
        'led = Component(name="D1", footprint="LED", pins={"A": vcc, "K": gnd})\n'
        'r1 = Resistor(name="R1", package="0603", P1=vcc, P2=gnd)\n'
        'Component(name="U2", footprint="X", pins={"P": led})\n'
    )
    low, diags = import_zen(src, "assign.zen")
    assert {(p.ref, p.component) for p in low.parts} == {
        ("D1", "Component"), ("R1", "Resistor"), ("U2", "Component")}
    nets = nets_of(low)
    assert nets["VCC"] == {("D1", "A"), ("R1", "P1")}
    assert nets["GND"] == {("D1", "K"), ("R1", "P2")}
    # part-bound var used as a pin is dropped silently, not warned as unknown net
    assert not any(d.code in ("Z002", "Z003") for d in diags)
