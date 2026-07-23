"""W1 audit fixes in ohm/resolve.py: R010 (shadowing), R011 (bad connect endpoints),
depth-3 submodule-port member validation (R003)."""

from pathlib import Path

from ohm.parse import parse
from ohm.resolve import resolve

_T = Path(__file__).parent

_I2C = (
    "interface I2C:\n"
    "    scl: Signal, role=bidirectional\n"
    "    sda: Signal, role=bidirectional\n"
)
_C = "component C:\n    pin P: role=passive\n"
_SUB = _I2C + _C + (
    "module Sub:\n"
    "    bus = io(I2C)\n"
    "    c = C()\n"
    "    bus.sda ~ c.P\n"
)


def build(src):
    prog, pdiags = parse(src)
    assert pdiags == []
    return resolve(prog)


def codes(diags):
    return [d.code for d in diags]


# ---------- positive: existing good designs stay clean ----------
def test_canonical_still_resolves_clean():
    _, diags = build((_T / "fixtures" / "canonical.ohm").read_text())
    assert diags == [], [d.machine() for d in diags]


def test_all_golden_designs_still_resolve_clean():
    for f in sorted((_T / "golden").glob("*.ohm")):
        _, diags = build(f.read_text())
        assert diags == [], (f.name, [d.machine() for d in diags])


def test_whole_submodule_port_connect_still_legal():
    # depth-2 inst.port ~ inst.port (i2c_bus.ohm pattern) must stay legal
    src = _SUB + "module Top:\n    a = Sub()\n    b = Sub()\n    a.bus ~ b.bus\n"
    _, diags = build(src)
    assert diags == [], [d.machine() for d in diags]


def test_valid_submodule_port_member_connect_legal():
    src = _SUB + (
        "module Top:\n"
        "    bus = io(I2C)\n"
        "    s = Sub()\n"
        "    bus.sda ~ s.bus.sda\n"
    )
    _, diags = build(src)
    assert diags == [], [d.machine() for d in diags]


# ---------- finding 1: depth-1 instance connect endpoints -> R011 ----------
def test_connect_component_instance_rejected():
    src = _C + "module M:\n    a = C()\n    b = C()\n    a ~ b\n"
    _, diags = build(src)
    assert codes(diags) == ["R011", "R011"]
    assert "cannot connect 'a'" in diags[0].message


def test_connect_module_instance_rejected():
    src = _SUB + "module Top:\n    s = Sub()\n    t = Sub()\n    s ~ t\n"
    _, diags = build(src)
    assert codes(diags) == ["R011", "R011"]


def test_whole_io_port_connect_still_legal():
    # depth-1 on an io() port stays legal
    src = _I2C + "module M:\n    a = io(I2C)\n    b = io(I2C)\n    a ~ b\n"
    _, diags = build(src)
    assert diags == [], [d.machine() for d in diags]


# ---------- finding 2: submodule-port member validated (R003) ----------
def test_unknown_submodule_port_member_rejected():
    src = _SUB + (
        "module Top:\n"
        "    bus = io(I2C)\n"
        "    s = Sub()\n"
        "    bus.sda ~ s.bus.sdaa\n"
    )
    _, diags = build(src)
    (d,) = [d for d in diags if d.code == "R003"]
    assert "sdaa" in d.message and "sda" in (d.help or "")


# ---------- finding 3: shadowing a global -> R010 ----------
def test_binding_shadows_global_component():
    src = "component Reg:\n    pin P: role=passive\nmodule M:\n    Reg = Reg()\n"
    _, diags = build(src)
    (d,) = [d for d in diags if d.code == "R010"]
    assert "shadows the global component 'Reg'" in d.message


def test_shadowed_binding_does_not_cascade():
    src = (
        "component Reg:\n    pin P: role=passive\n"
        "module M:\n    Reg = Reg()\n    Reg.P ~ Reg.P\n"
    )
    _, diags = build(src)
    assert codes(diags) == ["R010"]  # no R002/R011 cascade from later refs


# ---------- finding 4: connecting a config binding -> R011 ----------
def test_connect_config_rejected():
    src = _C + (
        "module M:\n"
        "    vcc = config(Voltage, default=3.3V)\n"
        "    c = C()\n"
        "    vcc ~ c.P\n"
    )
    _, diags = build(src)
    (d,) = [d for d in diags if d.code == "R011"]
    assert "cannot connect config 'vcc'" in d.message


def test_config_in_assert_still_legal():
    src = _C + (
        "module M:\n"
        "    vcc = config(Voltage, default=3.3V)\n"
        "    c = C()\n"
        "    assert vcc <= 5V\n"
    )
    _, diags = build(src)
    assert diags == [], [d.machine() for d in diags]


def test_connect_submodule_internal_instance_rejected():
    # verification-loop catch: `s.inner ~ c.P` must not silently vanish
    src = (
        "component C:\n    package = \"X\"\n    pin P: role=passive, pad=1\n"
        "module Sub:\n    inner = C()\n"
        "module Top:\n    s = Sub()\n    c = C()\n    s.inner ~ c.P\n"
    )
    from ohm.driver import compile_source

    result = compile_source(src, "internal.ohm")
    hits = [d for d in result.diagnostics if d.code == "R003" and "internal" in d.message]
    assert hits and not result.ok
