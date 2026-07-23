"""P7 gate: a real-shaped .zen file imports to IR and re-exports; round-trips
structurally. Interop only — a test also proves `ohm build` never touches it."""

from pathlib import Path

from ohm.driver import compile_source
from ohm.emit_zen import emit_zen
from ohm.imp_zen import import_zen

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"
ZEN_SRC = (FIXTURES / "led_indicator.zen").read_text()


def structure(low):
    """Structural fingerprint: parts + net partition of (ref, pin) endpoints."""
    parts = {(p.ref, p.package if isinstance(p.package, str) else "") for p in low.parts}
    nets = {frozenset((n.part_ref, n.pin) for n in net.nodes) for net in low.nets if net.nodes}
    return parts, nets


# ---------- gate: import a real-shaped .zen ----------
def test_import_led_indicator():
    low, diags = import_zen(ZEN_SRC, "led_indicator.zen")
    assert [d for d in diags if d.severity == "error"] == []
    assert {(p.ref, p.component) for p in low.parts} == {
        ("R1", "Resistor"), ("D1", "Led"), ("U1", "Component")}
    by_name = {n.name: n for n in low.nets}
    assert {(n.part_ref, n.pin) for n in by_name["LED_ANODE"].nodes} == {("R1", "P2"), ("D1", "A")}
    assert {(n.part_ref, n.pin) for n in by_name["VCC"].nodes} == {("R1", "P1"), ("U1", "VCC")}
    assert {(n.part_ref, n.pin) for n in by_name["GND"].nodes} == {("D1", "K"), ("U1", "GND")}
    assert {(n.part_ref, n.pin) for n in by_name["EN"].nodes} == {("U1", "EN")}
    # NotConnected pin dropped; config vars produced no spurious warnings
    assert not any(d.code == "Z002" for d in diags)


# ---------- gate: round-trip structurally ----------
def test_zen_round_trip_from_import():
    low1, _ = import_zen(ZEN_SRC, "led_indicator.zen")
    low2, diags = import_zen(emit_zen(low1, "led_indicator.zen"), "reexport.zen")
    assert [d for d in diags if d.severity == "error"] == []
    assert structure(low1) == structure(low2)


def test_zen_round_trip_from_ohm_designs():
    for design in sorted(GOLDEN.glob("*.ohm")):
        result = compile_source(design.read_text(), str(design))
        assert result.ok
        exported = emit_zen(result.low, design.name)
        reimported, diags = import_zen(exported, design.name + ".zen")
        assert [d for d in diags if d.severity == "error"] == []
        assert structure(result.low)[1] == structure(reimported)[1], design.name
        assert {p.ref for p in result.low.parts} == {p.ref for p in reimported.parts}


def test_export_is_deterministic():
    result = compile_source((GOLDEN / "i2c_bus.ohm").read_text(), "i2c_bus.ohm")
    assert emit_zen(result.low, "x").encode() == emit_zen(result.low, "x").encode()


def test_zen_syntax_error_diagnostic():
    low, diags = import_zen("Component(name=\n", "bad.zen")
    assert diags[0].code == "Z001" and diags[0].severity == "error"
    assert low.parts == ()


def test_build_path_never_imports_zen_interop():
    """.zen stays interop-only (N5/ADR-002): ohm build must not touch it."""
    import ohm.driver as driver_mod
    import ohm.lower as lower_mod

    for mod in (driver_mod, lower_mod):
        src = Path(mod.__file__).read_text()
        assert "imp_zen" not in src and "emit_zen" not in src
