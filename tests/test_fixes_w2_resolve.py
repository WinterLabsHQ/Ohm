"""W2 audit fixes: R012 (unknown instance overrides), R002 (non-literal config
default), R003 (component attr as connect endpoint), C004 (non-comparison
assert), driver best-effort analysis after parse errors, debug_passes."""

from pathlib import Path

from ohm import driver as drv
from ohm.driver import compile_source
from ohm.parse import parse
from ohm.resolve import resolve

_T = Path(__file__).parent

_PWR = (
    "interface Power:\n"
    "    v:   Signal, role=power_in\n"
    "    gnd: Signal, role=ground\n"
)
_R_WITH_VALUE = (
    "component R:\n"
    "    value = 1kohm\n"
    "    pin P1: role=passive\n"
    "    pin P2: role=passive\n"
)
_R_BARE = (
    "component R:\n"
    "    pin P1: role=passive\n"
    "    pin P2: role=passive\n"
)


def build(src):
    prog, pdiags = parse(src)
    assert pdiags == []
    return resolve(prog)


def codes(diags):
    return [d.code for d in diags]


# ---------- positive: existing good designs stay clean end to end ----------
def test_canonical_and_golden_still_compile_clean():
    for f in [_T / "fixtures" / "canonical.ohm", *sorted((_T / "golden").glob("*.ohm"))]:
        res = compile_source(f.read_text(), str(f))
        assert res.ok, (f.name, [d.machine() for d in res.diagnostics])


def test_divider_value_override_stays_legal():
    # component kwargs may introduce NEW attrs (no close declared name)
    src = _T.joinpath("golden", "divider.ohm").read_text()
    _, diags = build(src)
    assert diags == [], [d.machine() for d in diags]


# ---------- finding 1: R012 unknown instance overrides ----------
def test_module_unknown_config_override_is_r012():
    src = (
        "module Sub:\n    vin = config(Voltage, default=3.3V)\n"
        "module Top:\n    s = Sub(vinn=10V)\n"
    )
    _, diags = build(src)
    assert codes(diags) == ["R012"]
    assert "did you mean 'vin'" in (diags[0].help or "")


def test_module_declared_config_override_is_legal():
    src = (
        "module Sub:\n    vin = config(Voltage, default=3.3V)\n"
        "module Top:\n    s = Sub(vin=10V)\n"
    )
    _, diags = build(src)
    assert diags == []


def test_component_override_typo_of_declared_attr_is_r012():
    src = _R_WITH_VALUE + "module M:\n    r = R(valu=10ohm)\n"
    _, diags = build(src)
    assert codes(diags) == ["R012"]
    assert "did you mean 'value'" in (diags[0].help or "")


def test_component_override_typo_of_pin_name_is_r012():
    src = _R_BARE + "module M:\n    r = R(p1=10ohm)\n"
    _, diags = build(src)
    assert codes(diags) == ["R012"]


def test_component_new_attr_override_is_legal():
    # 'value' is not within edit-distance 2 of P1/P2 -> new attr, allowed
    src = _R_BARE + "module M:\n    r = R(value=10kohm)\n"
    _, diags = build(src)
    assert diags == []


# ---------- finding 2: config default must be a literal ----------
def test_config_default_ref_is_r002():
    src = "module M:\n    vcc = config(Voltage, default=nosuchthing)\n"
    _, diags = build(src)
    assert codes(diags) == ["R002"]
    assert "literal" in diags[0].message


def test_config_default_other_config_is_r002():
    src = (
        "module M:\n"
        "    a = config(Voltage, default=3.3V)\n"
        "    b = config(Voltage, default=a)\n"
    )
    _, diags = build(src)
    assert codes(diags) == ["R002"]


def test_config_literal_defaults_stay_legal():
    src = (
        "module M:\n"
        "    a = config(Voltage, default=3.3V)\n"
        "    b = config(Number, default=42)\n"
        '    c = config(String, default="x")\n'
    )
    _, diags = build(src)
    assert diags == []


# ---------- finding 3: component attr as connect endpoint ----------
def test_connect_declared_attr_endpoint_is_r003():
    src = _PWR + _R_WITH_VALUE + (
        "module M:\n    p = io(Power)\n    r = R()\n    r.value ~ p.v\n"
    )
    _, diags = build(src)
    assert codes(diags) == ["R003"]
    assert "not a pin" in diags[0].message


def test_connect_override_attr_endpoint_is_r003():
    src = _PWR + _R_BARE + (
        "module M:\n    p = io(Power)\n    r = R(value=1kohm)\n    r.value ~ p.v\n"
    )
    _, diags = build(src)
    assert codes(diags) == ["R003"]


def test_attr_in_assert_still_legal():
    src = _PWR + _R_WITH_VALUE + (
        "module M:\n    p = io(Power)\n    r = R()\n"
        "    p.v ~ r.P1\n    assert r.value <= 2kohm\n"
    )
    _, diags = build(src)
    assert diags == []


# ---------- finding 4: C004 non-comparison assert ----------
def test_bare_quantity_assert_is_c004():
    src = "module M:\n    vcc = config(Voltage, default=3.3V)\n    assert 3.3V\n"
    res = compile_source(src, emit=False)
    assert "C004" in codes(res.diagnostics)
    assert not res.ok


def test_bare_call_assert_is_c004():
    src = (
        "module M:\n    vcc = config(Voltage, default=3.3V)\n"
        "    assert series(vcc, vcc)\n"
    )
    res = compile_source(src, emit=False)
    assert "C004" in codes(res.diagnostics)


def test_comparison_assert_no_c004():
    src = "module M:\n    vcc = config(Voltage, default=3.3V)\n    assert vcc <= 5V\n"
    res = compile_source(src, emit=False)
    assert "C004" not in codes(res.diagnostics)


# ---------- finding 5: best-effort analysis after parse errors ----------
def test_parse_error_still_reports_semantic_errors():
    src = _T.joinpath("known_bad", "i005_swapped_sda_scl.ohm").read_text()
    src += "\nmodule Broken(:\n    junk junk\n"
    res = compile_source(src)
    cs = codes(res.diagnostics)
    assert "P001" in cs and "I005" in cs, cs
    assert res.low is None  # never lower/emit on errors


# ---------- finding 6: debug_passes no-mutation mode ----------
def test_debug_passes_clean_on_good_design():
    src = _T.joinpath("fixtures", "canonical.ohm").read_text()
    res = compile_source(src, debug_passes=True)
    assert res.ok


def test_debug_passes_catches_mutating_checker(monkeypatch):
    def mutator(design, path):
        object.__setattr__(design, "top", "MUTATED")
        return []

    monkeypatch.setattr(drv, "CHECKERS", list(drv.CHECKERS) + [mutator])
    src = _T.joinpath("fixtures", "canonical.ohm").read_text()
    try:
        compile_source(src, debug_passes=True)
        raise SystemExit("expected AssertionError")
    except AssertionError as exc:
        assert "mutator" in str(exc)
