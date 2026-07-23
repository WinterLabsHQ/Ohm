"""ADR-008 kind retrofit of the std blocks in electromech / connectivity /
connectors / opto. Each file must compile clean standalone, every retrofitted
block must instantiate clean, and the declared kinds + un-kinded exceptions
(role mismatches we refuse to bend a block's role to fix) must be exactly what
the retrofit intends."""

from ohm.driver import compile_source


def design(module):
    r = compile_source(f"use {module}\n", "m.ohm")
    assert r.ok, [d.machine() for d in r.diagnostics]
    # zero error diagnostics: the whole point — conformance must pass clean
    assert not [d for d in r.diagnostics if d.severity == "error"], \
        [d.machine() for d in r.diagnostics]
    return {c.name: c for c in r.design.components}


def kinds_of(module):
    return {name: c.kinds for name, c in design(module).items()}


# ---------- each file compiles clean standalone (zero error diagnostics) ----------
def test_electromech_compiles_clean():
    design("std.electromech")


def test_connectivity_compiles_clean():
    design("std.connectivity")


def test_connectors_compiles_clean():
    design("std.connectors")


def test_opto_compiles_clean():
    design("std.opto")


# ---------- every retrofitted block instantiates clean ----------
def test_all_blocks_instantiate_clean():
    for module in ("std.electromech", "std.connectivity", "std.connectors", "std.opto"):
        comps = design(module)
        lines = "".join(f"    x{i} = {n}()\n" for i, n in enumerate(comps))
        r = compile_source(f"use {module}\nmodule M:\n{lines}", "m.ohm")
        assert r.ok, [d.machine() for d in r.diagnostics]
        assert not [d for d in r.diagnostics if d.severity == "error"], \
            [d.machine() for d in r.diagnostics]


# ---------- declared kinds are exactly as intended ----------
def test_electromech_kinds():
    k = kinds_of("std.electromech")
    for h in ("Header_1x02", "Header_1x03", "Header_1x04", "Header_1x05",
              "Header_1x06", "Header_1x08", "Header_1x10", "Header_2x03",
              "Header_2x05"):
        assert k[h] == ("Connector",), h
    assert k["TactSwitch4"] == ("Switch",)
    assert k["SlideSwitch"] == ("Switch",)
    assert k["Buzzer"] == ("Buzzer",)
    # un-kinded (role mismatch / no clean kind)
    assert k["Relay_SPDT"] == ("Relay",)  # coil pins are passive (taxonomy fixed)
    assert k["Jumper"] == ()            # solder bridge, not a switch device
    assert k["DIPSwitch_4"] == ()       # 4-pole bank, no single kind
    assert k["RotaryEncoder_EC11"] == ()  # quadrature + switch, no kind


def test_connectivity_kinds():
    k = kinds_of("std.connectivity")
    assert k["CANTransceiver_SOIC8"] == ("Transceiver",)
    assert k["RS485Transceiver_SOIC8"] == ("Transceiver",)
    assert k["USBConnector_MicroB"] == ("Connector",)
    assert k["USBConnector_C16"] == ("Connector",)


def test_connectors_kinds():
    k = kinds_of("std.connectors")
    for c in ("TerminalBlock_2", "TerminalBlock_3", "DCBarrelJack", "JST_XH_2",
              "JST_XH_3", "RJ45", "MicroSD", "AudioJack_3p5", "SMAConnector",
              "UFL", "ICSocket_DIP8"):
        assert k[c] == ("Connector",), c
    assert k["CoinCellHolder"] == ("Battery",)
    assert k["Battery2"] == ("Battery",)
    assert kinds_of("std.connectors")["RJ45"] == ("Connector",)


def test_opto_kinds():
    k = kinds_of("std.opto")
    assert k["PhotoDiode"] == ("Diode",)
    assert k["IREmitter"] == ("LED",)
    assert k["IRReceiver"] == ("Sensor",)
    assert k["SevenSegment"] == ("Display",)
    assert k["SevenSegment_CA"] == ("Display",)
    # un-kinded: LED cathode is role=input here but kind requires passive -> K003
    assert k["Optocoupler_4"] == ("Optocoupler",)  # led side both input (taxonomy fixed)
    assert k["Optocoupler_6"] == ("Optocoupler",)
    # un-kinded: no clean single kind
    assert k["PhotoTransistor"] == ()
    assert k["AddressableLED"] == ()
    assert k["RGBLED_CommonAnode"] == ()
    assert k["RGBLED_CommonCathode"] == ()


# ---------- is= bindings actually landed on the required members ----------
def _binds(comp):
    return {p.satisfies for p in comp.pins if p.satisfies}


def test_required_members_bound():
    assert _binds(design("std.electromech")["Buzzer"]) == {"a", "b"}
    assert _binds(design("std.electromech")["TactSwitch4"]) == {"a", "b"}
    assert _binds(design("std.connectivity")["CANTransceiver_SOIC8"]) == {"vcc", "gnd"}
    assert _binds(design("std.connectors")["CoinCellHolder"]) == {"pos", "neg"}
    assert _binds(design("std.connectors")["RJ45"]) == {"shield"}
    assert _binds(design("std.opto")["PhotoDiode"]) == {"anode", "cathode"}
    assert _binds(design("std.opto")["IRReceiver"]) == {"signal"}
