"""Std-library `expects` declarations (ADR-009 bring-up, wave F0 follow-up).

The std interfaces/kinds now carry bring-up expectations (I2C pullups, RS485/CAN
endpoint termination, MOSFET gate tie, supply decoupling, gated LDO caps,
gated crystal load caps). These tests pin the false-positive discipline: bare
instantiations and correct designs stay quiet; seeded mistakes fire.
"""

from pathlib import Path

from ohm.driver import compile_source

STD = Path(__file__).parent.parent / "ohm" / "std"
EXAMPLES = Path(__file__).parent / "examples"

POWER_R_C = (
    "use std.interfaces\nuse std.passives\n"
)


def diags_of(src, name="f.ohm"):
    return compile_source(src, name).diagnostics


def codes(src):
    return [d.code for d in diags_of(src)]


# ---------- std library itself stays clean ----------
def test_every_std_file_compiles_clean_standalone():
    # CRITICAL: no bare-instantiation B-codes, no R020 from the new expects
    for f in sorted(STD.glob("*.ohm")):
        result = compile_source(f.read_text(), str(f))
        assert not result.diagnostics, (f.name, [d.machine() for d in result.diagnostics])


def test_examples_no_new_error_severity_b_codes():
    for f in sorted(EXAMPLES.glob("*.ohm")):
        result = compile_source(f.read_text(), str(f))
        bad = [d.machine() for d in result.diagnostics
               if d.code.startswith("B") and d.severity == "error"]
        assert not bad, (f.name, bad)


def test_examples_known_b_warnings_audited():
    # B005 originally caught two examples missing their decoupling caps — the
    # check was right (dogfooding) and the examples were fixed. Every example
    # must now be free of B-warnings; new entries here require the same triage.
    expected: dict[str, list[str]] = {}
    for f in sorted(EXAMPLES.glob("*.ohm")):
        result = compile_source(f.read_text(), str(f))
        warns = sorted(d.code for d in result.diagnostics
                       if d.code.startswith("B") and d.severity == "warning")
        assert warns == expected.get(f.name, []), (f.name, warns)


# ---------- I2C pullups via the STD interface ----------
MCU = ('component Mcu:\n    package = "QFN"\n'
       "    pin SDA: role=bidirectional, pad=1\n    pin SCL: role=bidirectional, pad=2\n")


def test_std_i2c_without_pullups_fires_b001():
    src = (POWER_R_C + MCU +
           "module M:\n    bus = io(I2C)\n    u = Mcu()\n"
           "    bus.sda ~ u.SDA\n    bus.scl ~ u.SCL\n")
    assert codes(src).count("B001") == 2


def test_std_i2c_with_pullups_clean():
    src = (POWER_R_C + MCU +
           "module M:\n    bus = io(I2C)\n    pwr = io(Power)\n    u = Mcu()\n"
           "    r1 = Resistor(value=4.7kohm)\n    r2 = Resistor(value=4.7kohm)\n"
           "    bus.sda ~ u.SDA\n    bus.scl ~ u.SCL\n"
           "    r1.P1 ~ pwr.v\n    r1.P2 ~ bus.sda\n"
           "    r2.P1 ~ pwr.v\n    r2.P2 ~ bus.scl\n")
    assert "B001" not in codes(src)


# ---------- RS485/CAN endpoint termination via the STD interface ----------
NODE = ("use std.interfaces\nuse std.passives\nuse std.connectivity\n"
        "module Node:\n    bus = io(RS485)\n    u = RS485Transceiver_SOIC8()\n"
        "    bus.a ~ u.A\n    bus.b ~ u.B\n")


def test_std_rs485_endpoint_fires_midbus_quiet():
    src = (NODE + "module Top:\n    n1 = Node(endpoint=1)\n    n2 = Node()\n"
           "    n1.bus.a ~ n2.bus.a\n    n1.bus.b ~ n2.bus.b\n")
    assert codes(src).count("B007") == 1  # only the declared endpoint


def test_std_rs485_endpoint_terminated_clean():
    src = (NODE + "module Top:\n    n1 = Node(endpoint=1)\n"
           "    rt = Resistor(value=120ohm)\n"
           "    n1.bus.a ~ rt.P1\n    n1.bus.b ~ rt.P2\n")
    assert "B007" not in codes(src)


# ---------- MOSFET gate tie via std.discrete ----------
def test_std_mosfet_floating_gate_fires_b002():
    src = ("use std.interfaces\nuse std.discrete\n"
           "module M:\n    pwr = io(Power)\n    q = NMOS()\n"
           "    pwr.v ~ q.D\n    q.S ~ pwr.gnd\n")
    assert "B002" in codes(src)


def test_std_mosfet_gate_resistor_clean():
    src = (POWER_R_C + "use std.discrete\n"
           "module M:\n    pwr = io(Power)\n    q = NMOS()\n"
           "    rg = Resistor(value=100kohm)\n"
           "    pwr.v ~ q.D\n    q.S ~ pwr.gnd\n"
           "    rg.P1 ~ q.G\n    rg.P2 ~ pwr.gnd\n")
    assert "B002" not in codes(src)


def test_std_mosfet_pushpull_driven_gate_clean():
    # normal GPIO-driven gate: a push-pull driver satisfies tie
    src = ("use std.interfaces\nuse std.discrete\n"
           'component Drv:\n    package = "X"\n    pin OUT: role=output, pad=1\n'
           "module M:\n    pwr = io(Power)\n    q = NMOS()\n    d = Drv()\n"
           "    pwr.v ~ q.D\n    q.S ~ pwr.gnd\n    d.OUT ~ q.G\n")
    assert "B002" not in codes(src)


# ---------- OpAmp decoupling via std.analog ----------
OPAMP = ("use std.interfaces\nuse std.passives\nuse std.analog\n"
         "module M:\n    pwr = io(Power)\n    u = OpAmp_SOT23_5()\n"
         "    pwr.v ~ u.VDD\n    pwr.gnd ~ u.VSS\n    u.INP ~ u.OUT\n")


def test_std_opamp_without_decoupling_fires_b005():
    assert "B005" in codes(OPAMP)


def test_std_opamp_with_decoupling_clean():
    src = (OPAMP + "    c = Capacitor(value=100nF)\n"
           "    c.P1 ~ pwr.v\n    c.P2 ~ pwr.gnd\n")
    assert "B005" not in codes(src)


def test_waiver_suppresses_decoupling_to_b010():
    src = OPAMP.replace("OpAmp_SOT23_5()", 'OpAmp_SOT23_5(waive="decoupling")')
    cs = codes(src)
    assert "B005" not in cs and "B010" in cs


# ---------- gated expectations stay quiet by default ----------
def test_std_ldo_without_gate_attr_quiet():
    # std LDO wired with no caps and no cin_min/cout_min declared: no B006
    src = ("use std.interfaces\nuse std.power\n"
           "module M:\n    pwr = io(Power)\n    reg = LDO_SOT23_5()\n"
           "    pwr.v ~ reg.IN\n    pwr.gnd ~ reg.GND\n    pwr.v ~ reg.EN\n")
    assert "B006" not in codes(src)


def test_std_ldo_with_gate_attr_fires_and_clears():
    ldo = ('component MyLdo(LDO):\n    package = "X"\n'
           "    cout_min = 1uF\n    cin_min = 100nF\n"
           "    pin IN: role=power_in, pad=1, is=vin\n"
           "    pin OUT: role=power_out, voltage=3.3V, pad=2, is=vout\n"
           "    pin GND: role=ground, pad=3, is=gnd\n")
    # NOTE: vout must be on a net to be checked — cap expectations on
    # unconnected members do not fire (bare-instantiation discipline)
    base = ("use std.kinds\nuse std.interfaces\nuse std.passives\n" + ldo +
            "module M:\n    pwr = io(Power)\n    out = io(PowerOut)\n    reg = MyLdo()\n"
            "    pwr.v ~ reg.IN\n    pwr.gnd ~ reg.GND\n"
            "    out.v ~ reg.OUT\n    out.gnd ~ reg.GND\n")
    assert codes(base).count("B006") == 2  # vin and vout both uncapped
    withcaps = (base +
                "    cin = Capacitor(value=1uF)\n    cout = Capacitor(value=10uF)\n"
                "    cin.P1 ~ pwr.v\n    cin.P2 ~ pwr.gnd\n"
                "    cout.P1 ~ reg.OUT\n    cout.P2 ~ pwr.gnd\n")
    assert "B006" not in codes(withcaps)


def test_std_crystal_without_cl_quiet():
    # integrated-cap MCU case: no cl declared, wired crystal stays quiet
    mcu = ('component Mcu:\n    package = "X"\n'
           "    pin XI: role=input, pad=1\n    pin XO: role=output, pad=2\n")
    src = ("use std.passives\n" + mcu +
           "module M:\n    y = Crystal2(freq=8MHz)\n    u = Mcu()\n"
           "    y.P1 ~ u.XI\n    y.P2 ~ u.XO\n")
    assert "B008" not in codes(src)


def test_std_crystal_with_cl_fires_and_clears():
    xtal = ('component MyXtal(Crystal):\n    package = "X"\n'
            "    freq = 8MHz\n    cl = 18pF\n"
            "    pin P1: role=passive, pad=1, is=a\n    pin P2: role=passive, pad=2, is=b\n")
    mcu = ('component Mcu:\n    package = "X"\n'
           "    pin XI: role=input, pad=1\n    pin XO: role=output, pad=2\n"
           "    pin GND: role=ground, pad=3\n")
    base = ("use std.kinds\nuse std.passives\n" + xtal + mcu +
            "module M:\n    y = MyXtal()\n    u = Mcu()\n"
            "    y.P1 ~ u.XI\n    y.P2 ~ u.XO\n")
    assert codes(base).count("B008") == 2
    withcaps = (base +
                "    c1 = Capacitor(value=18pF)\n    c2 = Capacitor(value=18pF)\n"
                "    c1.P1 ~ y.P1\n    c1.P2 ~ u.GND\n"
                "    c2.P1 ~ y.P2\n    c2.P2 ~ u.GND\n")
    assert "B008" not in codes(withcaps)
