"""Milestone F, wave F0: the `expects` mechanism + bring-up checks (ADR-009).
Each B-code fires on its seeded-mistake fixture and clears on the corrected
design; waivers suppress-with-note; no false positives on existing designs."""

from pathlib import Path

from ohm.driver import compile_source

GOLDEN = Path(__file__).parent / "golden"
EXAMPLES = Path(__file__).parent / "examples"

I2C_IFACE = (
    "interface I2C:\n    scl: Signal, role=bidirectional\n"
    "    sda: Signal, role=bidirectional\n    expects pullup on sda, scl\n"
)
POWER_IFACE = (
    "interface Power:\n    v: Signal, role=power_in\n    gnd: Signal, role=ground\n"
)
PULLUP_R = (
    'component R:\n    package = "R_0402"\n    value = 4.7kohm\n'
    "    pin P1: role=passive, pad=1\n    pin P2: role=passive, pad=2\n"
)
MCU = (
    'component Mcu:\n    package = "QFN"\n'
    "    pin SDA: role=bidirectional, pad=1\n    pin SCL: role=bidirectional, pad=2\n"
)


def diags_of(src):
    return compile_source(src, "f.ohm").diagnostics


def codes(src):
    return [d.code for d in diags_of(src)]


# ---------- B001 pull-ups (interface expects + automatic open-drain) ----------
def test_b001_i2c_without_pullups():
    src = (I2C_IFACE + MCU + "module M:\n    bus = io(I2C)\n    u = Mcu()\n"
           "    bus.sda ~ u.SDA\n    bus.scl ~ u.SCL\n")
    assert codes(src).count("B001") == 2  # one per member


def test_b001_clears_with_pullups():
    src = (I2C_IFACE + POWER_IFACE + MCU + PULLUP_R +
           "module M:\n    bus = io(I2C)\n    pwr = io(Power)\n    u = Mcu()\n"
           "    r1 = R()\n    r2 = R()\n"
           "    bus.sda ~ u.SDA\n    bus.scl ~ u.SCL\n"
           "    r1.P1 ~ pwr.v\n    r1.P2 ~ bus.sda\n"
           "    r2.P1 ~ pwr.v\n    r2.P2 ~ bus.scl\n")
    assert "B001" not in codes(src)


def test_b001_automatic_open_drain():
    src = ('component Cmp:\n    package = "X"\n'
           "    pin OUT: role=open_collector, pad=1\n"
           'component Load:\n    package = "X"\n    pin IN: role=input, pad=1\n'
           "module M:\n    c = Cmp()\n    ld = Load()\n    c.OUT ~ ld.IN\n")
    assert "B001" in codes(src)


# ---------- B002 tie ----------
GATE_KIND = (
    "kind MOSFET:\n    requires gate: input\n    requires drain: passive\n"
    "    requires source: passive\n    expects tie on gate\n"
    'component NMOS(MOSFET):\n    package = "SOT-23"\n'
    "    pin G: role=input, pad=1, is=gate\n    pin S: role=passive, pad=2, is=source\n"
    "    pin D: role=passive, pad=3, is=drain\n"
)


def test_b002_floating_gate():
    src = (GATE_KIND + POWER_IFACE + "module M:\n    pwr = io(Power)\n    q = NMOS()\n"
           "    pwr.v ~ q.D\n    q.S ~ pwr.gnd\n")
    assert "B002" in codes(src)


def test_b002_clears_with_pulldown():
    src = (GATE_KIND + POWER_IFACE + PULLUP_R.replace("component R", "component Rg") +
           "module M:\n    pwr = io(Power)\n    q = NMOS()\n    rg = Rg()\n"
           "    pwr.v ~ q.D\n    q.S ~ pwr.gnd\n"
           "    rg.P1 ~ q.G\n    rg.P2 ~ pwr.gnd\n")
    assert "B002" not in codes(src)


def test_waiver_suppresses_to_b010():
    src = (GATE_KIND + POWER_IFACE +
           'module M:\n    pwr = io(Power)\n    q = NMOS(waive="tie")\n'
           "    pwr.v ~ q.D\n    q.S ~ pwr.gnd\n")
    cs = codes(src)
    assert "B002" not in cs and "B010" in cs
    result = compile_source(src, "f.ohm")
    (note,) = [d for d in result.diagnostics if d.code == "B010"]
    assert note.severity == "info"


# ---------- B003 unconnected pins ----------
def test_b003_unwired_power_in_in_wired_module():
    src = ('component Chip:\n    package = "X"\n    pin VDD: role=power_in, pad=1\n'
           "    pin IO: role=output, pad=2\n"
           'component Ld:\n    package = "X"\n    pin IN: role=input, pad=1\n'
           "module M:\n    u = Chip()\n    l1 = Ld()\n    u.IO ~ l1.IN\n")
    assert "B003" in codes(src)


def test_b003_quiet_in_unwired_module():
    # bare part-catalog instantiation (zero connections) stays silent
    src = ('component Chip:\n    package = "X"\n    pin VDD: role=power_in, pad=1\n'
           "module M:\n    u = Chip()\n")
    assert "B003" not in codes(src)


def test_b003_must_connect_always_fires():
    src = ('component Chip:\n    package = "X"\n'
           "    pin EP: role=passive, must_connect=true, pad=1\n"
           "module M:\n    u = Chip()\n")
    assert "B003" in codes(src)


# ---------- B004 domain damage ----------
def test_b004_intolerant_input():
    src = ('component Reg5:\n    package = "X"\n'
           "    pin OUT: role=power_out, voltage=5.0V, pad=1\n"
           'component Mcu:\n    package = "X"\n'
           "    pin IO: role=input, tolerant_to=3.6V, pad=1\n"
           "module M:\n    r = Reg5()\n    u = Mcu()\n    r.OUT ~ u.IO\n")
    result = compile_source(src, "f.ohm")
    (d,) = [d for d in result.diagnostics if d.code == "B004"]
    assert d.severity == "error" and "series resistor" in d.message


# ---------- B005/B006/B008 caps ----------
CAP = ('component Cap:\n    package = "C_0402"\n    value = 100nF\n'
       "    pin P1: role=passive, pad=1\n    pin P2: role=passive, pad=2\n")


def test_b005_missing_decoupling_and_clear():
    kind = ("kind Chip:\n    requires vdd: power_in\n"
            "    expects decoupling(10nF, 1uF) on vdd\n")
    comp = ('component Mcu(Chip):\n    package = "QFN"\n'
            "    pin VDD: role=power_in, pad=1, is=vdd\n    pin GND: role=ground, pad=2\n")
    bare = (kind + POWER_IFACE + comp + "module M:\n    pwr = io(Power)\n    u = Mcu()\n"
            "    pwr.v ~ u.VDD\n    pwr.gnd ~ u.GND\n")
    assert "B005" in codes(bare)
    withcap = (kind + POWER_IFACE + comp + CAP +
               "module M:\n    pwr = io(Power)\n    u = Mcu()\n    c = Cap()\n"
               "    pwr.v ~ u.VDD\n    pwr.gnd ~ u.GND\n"
               "    c.P1 ~ pwr.v\n    c.P2 ~ pwr.gnd\n")
    assert "B005" not in codes(withcap)


def test_b006_regulator_headroom():
    src = ("kind LDO:\n    requires vin: power_in\n    requires vout: power_out\n"
           'component Ldo(LDO):\n    package = "X"\n    v_dropout = 1.3V\n'
           "    pin IN: role=power_in, pad=1, is=vin\n"
           "    pin OUT: role=power_out, voltage=3.3V, pad=2, is=vout\n"
           "    pin GND: role=ground, pad=3\n"
           'component Src:\n    package = "X"\n'
           "    pin OUT: role=power_out, voltage=4.0V, pad=1\n"  # 4.0 < 3.3+1.3
           'component Ld:\n    package = "X"\n    pin VIN: role=power_in, pad=1\n'
           "module M:\n    s = Src()\n    u = Ldo()\n    ld = Ld()\n"
           "    s.OUT ~ u.IN\n    u.OUT ~ ld.VIN\n")
    assert "B006" in codes(src)


def test_b008_crystal_loadcaps():
    kind = ("kind Xtal:\n    requires a: passive\n    requires b: passive\n"
            "    expects loadcaps(5pF, 30pF) on a, b\n")
    comp = ('component Y1(Xtal):\n    package = "X"\n'
            "    pin P1: role=passive, pad=1, is=a\n    pin P2: role=passive, pad=2, is=b\n")
    mcu = ('component Mcu:\n    package = "X"\n'
           "    pin XI: role=input, pad=1\n    pin XO: role=output, pad=2\n")
    src = (kind + comp + mcu + "module M:\n    y = Y1()\n    u = Mcu()\n"
           "    y.P1 ~ u.XI\n    y.P2 ~ u.XO\n")
    assert codes(src).count("B008") == 2  # both terminals missing caps


# ---------- B007 termination with `when` gate ----------
RS485 = ("interface RS485:\n    a: Signal, role=bidirectional\n"
         "    b: Signal, role=bidirectional\n"
         "    expects termination(100ohm, 130ohm) on a, b when endpoint\n"
         'component Xcvr:\n    package = "SOIC"\n'
         "    pin A: role=bidirectional, pad=6\n    pin B: role=bidirectional, pad=7\n"
         "module Node:\n    bus = io(RS485)\n    u = Xcvr()\n"
         "    bus.a ~ u.A\n    bus.b ~ u.B\n")


def test_b007_endpoint_fires_midbus_does_not():
    src = (RS485 + "module Top:\n    n1 = Node(endpoint=1)\n    n2 = Node()\n"
           "    n1.bus.a ~ n2.bus.a\n    n1.bus.b ~ n2.bus.b\n")
    assert codes(src).count("B007") == 1  # only the declared endpoint


def test_b007_clears_with_termination():
    term = ('component Rt:\n    package = "R_0402"\n    value = 120ohm\n'
            "    pin P1: role=passive, pad=1\n    pin P2: role=passive, pad=2\n")
    src = (RS485 + term + "module Top:\n    n1 = Node(endpoint=1)\n    rt = Rt()\n"
           "    n1.bus.a ~ rt.P1\n    n1.bus.b ~ rt.P2\n")
    assert "B007" not in codes(src)


# ---------- B009 polarized bias ----------
def test_b009_reverse_biased_polarized_cap(tmp_path):
    src = (
        "use std.kinds\n"
        'component PCap(Capacitor, Polarized):\n    package = "CP"\n    value = 10uF\n'
        "    pin A: role=passive, pad=1, is=a\n    pin K: role=passive, pad=2, is=b\n"
        'component Reg5:\n    package = "X"\n'
        "    pin OUT: role=power_out, voltage=5.0V, pad=1\n    pin GND: role=ground, pad=2\n"
        "module M:\n    r = Reg5()\n    c = PCap()\n"
        "    c.A ~ r.GND\n    c.K ~ r.OUT\n"  # backwards!
    )
    f = tmp_path / "p.ohm"
    f.write_text(src)
    result = compile_source(src, str(f))
    assert any(d.code == "B009" for d in result.diagnostics)
    # correct orientation is clean
    good = src.replace("c.A ~ r.GND\n    c.K ~ r.OUT", "c.A ~ r.OUT\n    c.K ~ r.GND")
    f.write_text(good)
    assert not any(d.code == "B009" for d in compile_source(good, str(f)).diagnostics)


# ---------- resolver-level ----------
def test_r020_unknown_pattern():
    assert "R020" in codes("kind K:\n    requires x: input\n    expects wiggle on x\n")


def test_r020_unknown_member():
    assert "R020" in codes("kind K:\n    requires x: input\n    expects tie on y\n")


# ---------- no false positives on the whole existing corpus ----------
def test_no_b_codes_on_goldens_and_examples():
    for f in sorted(GOLDEN.glob("*.ohm")) + sorted(EXAMPLES.glob("*.ohm")):
        result = compile_source(f.read_text(), str(f))
        bad = [d.machine() for d in result.diagnostics
               if d.code.startswith("B") and d.severity == "error"]
        assert not bad, (f.name, bad)


# ---------- fmt ----------
def test_fmt_round_trips_expects():
    from ohm.fmt import format_source

    src = ("interface I2C:\n    sda: Signal, role=bidirectional\n"
           "    expects pullup on sda\n"
           "kind K:\n    requires g: input\n"
           "    expects tie on g when strict\n"
           "    expects decoupling(10nF, 1uF) on g\n")
    once, diags = format_source(src)
    assert not diags
    assert "expects pullup on sda" in once
    assert "expects tie on g when strict" in once
    assert "expects decoupling(10nF, 1uF) on g" in once
    twice, _ = format_source(once)
    assert once == twice
