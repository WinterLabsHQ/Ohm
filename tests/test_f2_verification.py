"""Milestone F, wave F2 — verification.

Half 1 (seeded mistakes): realistic mini-boards each containing exactly one
classic respin mistake; the compiler must report exactly the intended finding.

Half 2 (adversarial FP-hunt): the research's named CORRECT real-world designs —
single-supply op-amp, mid-bus RS485 node, crystal without declared CL
(integrated-cap MCU), LM317-style no-GND regulator, open-drain bus with
off-board pulls, pre-BOM board without fiducials/MPNs — must produce ZERO
false errors and ZERO false warnings. The value of every check dies the day
it cries wolf on a correct design.
"""

from ohm.driver import compile_source


POWER = ("interface Power:\n    v: Signal, role=power_in\n"
         "    gnd: Signal, role=ground\n")


def diags(src):
    return compile_source(src, "f2.ohm").diagnostics


def codes(src, sev=None):
    return [d.code for d in diags(src) if sev is None or d.severity == sev]


# =========================== seeded mistakes ===========================

def test_seeded_i2c_board_missing_pullups():
    src = (
        "use std.interfaces\nuse std.passives\n"
        'component Sensor:\n    package = generate(SOT23, pins=5)\n'
        "    pin SDA: role=bidirectional, pad=1\n    pin SCL: role=bidirectional, pad=2\n"
        "    pin VDD: role=power_in, pad=3\n    pin GND: role=ground, pad=4\n"
        "    pin NC1: role=nc, pad=5\n"
        "module Board:\n    pwr = io(Power)\n    bus = io(I2C)\n    s = Sensor()\n"
        "    c1 = Capacitor(value=100nF)\n"
        "    bus.sda ~ s.SDA\n    bus.scl ~ s.SCL\n"
        "    pwr.v ~ s.VDD\n    pwr.gnd ~ s.GND\n"
        "    c1.P1 ~ pwr.v\n    c1.P2 ~ pwr.gnd\n"
    )
    got = codes(src, "warning")
    assert got.count("B001") == 2 and set(got) == {"B001"}


def test_seeded_5v_into_3v3_input():
    src = (
        'component Reg5:\n    package = "X"\n'
        "    pin OUT: role=power_out, voltage=5.0V, pad=1\n    pin GND: role=ground, pad=2\n"
        'component Mcu:\n    package = "X"\n'
        "    pin IO: role=input, tolerant_to=3.6V, pad=1\n    pin GND: role=ground, pad=2\n"
        "module Board:\n    r = Reg5()\n    u = Mcu()\n    r.OUT ~ u.IO\n    r.GND ~ u.GND\n"
    )
    errs = codes(src, "error")
    assert errs == ["B004"]


def test_seeded_rs485_endpoint_unterminated():
    src = (
        "use std.interfaces\nuse std.connectivity\nuse std.passives\n"
        "module Node:\n    pwr = io(Power)\n    bus = io(RS485)\n"
        "    u = RS485Transceiver_SOIC8()\n    c1 = Capacitor(value=100nF)\n"
        "    bus.a ~ u.A\n    bus.b ~ u.B\n    pwr.v ~ u.VCC\n    pwr.gnd ~ u.GND\n"
        "    c1.P1 ~ pwr.v\n    c1.P2 ~ pwr.gnd\n"
        "module Line:\n    n1 = Node(endpoint=1)\n    n2 = Node(endpoint=1)\n"
        "    n1.bus.a ~ n2.bus.a\n    n1.bus.b ~ n2.bus.b\n"
    )
    got = codes(src, "warning")
    assert got.count("B007") == 2 and set(got) == {"B007"}  # both declared ends bare


def test_seeded_bom_stage_missing_mpn_and_testpoint():
    # declaring one mpn activates BOM-stage lints: missing MPNs + unprobeable rail
    src = (
        "use std.interfaces\nuse std.passives\n"
        'component Reg:\n    package = "X"\n    mpn = "REG-123"\n'
        "    pin OUT: role=power_out, voltage=3.3V, pad=1\n    pin GND: role=ground, pad=2\n"
        "module Board:\n    r1 = Resistor(value=10kohm)\n    reg = Reg()\n"
        "    reg.OUT ~ r1.P1\n    reg.GND ~ r1.P2\n"
    )
    got = codes(src, "warning")
    assert "M001" in got and "M004" in got  # r1 lacks mpn; 3.3V rail unprobeable


# =========================== adversarial FP-hunt ===========================

def _assert_totally_clean(src, label):
    all_diags = [d for d in diags(src) if d.severity in ("error", "warning")]
    assert not all_diags, (label, [d.machine() for d in all_diags])


def test_fp_single_supply_opamp():
    # vneg omitted entirely (single-supply part) + decoupled — must be clean
    src = (
        "use std.kinds\nuse std.interfaces\nuse std.passives\n"
        "component Amp(OpAmp):\n    package = generate(SOT23, pins=5)\n"
        "    pin OUT: role=output, pad=1, is=out\n"
        "    pin GND: role=ground, pad=2\n"
        "    pin INP: role=input, pad=3, is=inp\n"
        "    pin INN: role=input, pad=4, is=inn\n"
        "    pin VDD: role=power_in, pad=5, is=vpos\n"
        "module Board:\n    pwr = io(Power)\n    u = Amp()\n    c = Capacitor(value=100nF)\n"
        "    pwr.v ~ u.VDD\n    pwr.gnd ~ u.GND\n"
        "    c.P1 ~ pwr.v\n    c.P2 ~ pwr.gnd\n"
        "    u.OUT ~ u.INN\n"  # unity follower
    )
    _assert_totally_clean(src, "single-supply opamp")


def test_fp_midbus_rs485_node_no_termination():
    src = (
        "use std.interfaces\nuse std.connectivity\nuse std.passives\n"
        "module Node:\n    pwr = io(Power)\n    bus = io(RS485)\n"
        "    u = RS485Transceiver_SOIC8()\n    c1 = Capacitor(value=100nF)\n"
        "    bus.a ~ u.A\n    bus.b ~ u.B\n    pwr.v ~ u.VCC\n    pwr.gnd ~ u.GND\n"
        "    c1.P1 ~ pwr.v\n    c1.P2 ~ pwr.gnd\n"
        "module Line:\n    mid = Node()\n    _o = Node()\n"  # neither declares endpoint
        "    mid.bus.a ~ _o.bus.a\n    mid.bus.b ~ _o.bus.b\n"
    )
    _assert_totally_clean(src, "mid-bus rs485")


def test_fp_crystal_without_declared_cl():
    # MCU with integrated load caps: author declares no cl -> loadcaps gated off.
    # NOTE the modeling convention (555 precedent, documented in STYLE.md): XI is
    # a high-impedance analog sense pin fed through the crystal by design →
    # role=passive, not input (the undriven-input check is for digital logic).
    src = (
        "use std.kinds\nuse std.passives\n"
        'component Mcu:\n    package = "QFN"\n'
        "    pin XI: role=passive, pad=1\n    pin XO: role=output, pad=2\n"
        "module Board:\n    y = Crystal2(freq=8MHz)\n    u = Mcu()\n"
        "    y.P1 ~ u.XI\n    y.P2 ~ u.XO\n"
    )
    _assert_totally_clean(src, "integrated-cap crystal")


def test_fp_lm317_no_gnd_ldo():
    src = (
        "use std.kinds\n" + POWER +
        "component LM317(LDO):\n    package = generate(SOT23, pins=3)\n"
        "    pin ADJ: role=passive, pad=1\n"  # analog sense pin, divider-fed by design
        "    pin OUT: role=power_out, pad=2, is=vout\n"
        "    pin IN: role=power_in, pad=3, is=vin\n"
        'component Rdiv:\n    package = "R"\n    value = 240ohm\n'
        "    pin P1: role=passive, pad=1\n    pin P2: role=passive, pad=2\n"
        "module Board:\n    pwr = io(Power)\n    u = LM317()\n    r1 = Rdiv()\n"
        "    pwr.v ~ u.IN\n    u.OUT ~ r1.P1\n    r1.P2 ~ u.ADJ\n"
    )
    _assert_totally_clean(src, "LM317 floating regulator")


def test_fp_open_drain_with_offboard_pulls():
    # open-drain net that reaches a port: pulls may live off-board — no B001
    src = (
        "interface Alert:\n    alrt: Signal, role=bidirectional\n"
        'component Sensor:\n    package = "X"\n'
        "    pin NALERT: role=open_collector, pad=1\n"
        "module Board:\n    a = io(Alert)\n    s = Sensor()\n    a.alrt ~ s.NALERT\n"
    )
    _assert_totally_clean(src, "off-board pulled open-drain")


def test_fp_pre_bom_board_stays_quiet():
    # no mpn anywhere -> M-lints silent even though no fiducial/testpoints exist
    src = (
        "use std.interfaces\nuse std.passives\n"
        "module Board:\n    pwr = io(Power)\n    r1 = Resistor(value=1kohm)\n"
        "    pwr.v ~ r1.P1\n    pwr.gnd ~ r1.P2\n"
    )
    assert not [c for c in codes(src) if c.startswith("M")]


def test_fp_waived_board_passes_strict(tmp_path):
    from ohm.cli import main

    src = (
        "use std.kinds\n" + POWER +
        "component Q(MOSFET):\n    package = generate(SOT23, pins=3)\n"
        "    pin G: role=input, pad=1, is=gate\n"
        "    pin S: role=passive, pad=2, is=source\n    pin D: role=passive, pad=3, is=drain\n"
        'module Board:\n    pwr = io(Power)\n    q = Q(waive="tie")\n'
        "    pwr.v ~ q.D\n    q.S ~ pwr.gnd\n"
    )
    f = tmp_path / "b.ohm"
    f.write_text(src)
    assert main(["check", str(f), "--strict"]) == 0  # waived → info only → strict passes
