"""P6 gate ★: swapped SDA/SCL and D+/D- fixtures error; output↔output errors;
correct designs pass. This is the feature that beats standard ERC."""

from pathlib import Path

import pytest

from ohm.driver import compile_source

KNOWN_BAD = Path(__file__).parent / "known_bad"
GOLDEN = Path(__file__).parent / "golden"


def run(name):
    path = KNOWN_BAD / name
    return compile_source(path.read_text(), str(path))


CASES = [
    ("i001_iface_type_mismatch.ohm", "I001", "error", 28),
    ("i002_iface_to_pin.ohm", "I002", "error", 13),
    ("i003_two_outputs.ohm", "I003", "error", 10),
    ("i005_swapped_sda_scl.ohm", "I005", "error", 32),
    ("i005_swapped_dp_dm.ohm", "I005", "error", 16),
    ("i006_undriven_input.ohm", "I006", "warning", 10),
]


@pytest.mark.parametrize("fixture,code,severity,line", CASES, ids=lambda c: str(c))
def test_known_bad_fixture(fixture, code, severity, line):
    result = run(fixture)
    hits = [d for d in result.diagnostics if d.code == code]
    assert hits, f"expected {code}, got {[d.code for d in result.diagnostics]}"
    assert hits[0].severity == severity
    assert hits[0].span.line == line


def test_swapped_sda_scl_reports_both_crossings():
    result = run("i005_swapped_sda_scl.ohm")
    assert len([d for d in result.diagnostics if d.code == "I005"]) == 2
    (first,) = [d for d in result.diagnostics if d.code == "I005" and d.span.line == 32]
    assert "sda" in first.message and "scl" in first.message and "require pair" in first.message


def test_errored_design_does_not_emit():
    result = run("i005_swapped_sda_scl.ohm")
    assert not result.ok and result.low is None


def test_undriven_warning_still_emits():
    result = run("i006_undriven_input.ohm")
    assert result.ok and result.low is not None


def test_correct_member_wiring_passes():
    # explicit member-wise wiring in the CORRECT order must be clean
    src = (KNOWN_BAD / "i005_swapped_sda_scl.ohm").read_text().replace(
        "mcu.bus.sda ~ sens.bus.scl", "mcu.bus.sda ~ sens.bus.sda").replace(
        "mcu.bus.scl ~ sens.bus.sda", "mcu.bus.scl ~ sens.bus.scl")
    result = compile_source(src, "fixed.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert not [d for d in result.diagnostics if d.code.startswith("I")]


@pytest.mark.parametrize("design", sorted(GOLDEN.glob("*.ohm")), ids=lambda p: p.stem)
def test_golden_designs_intent_clean(design):
    result = compile_source(design.read_text(), str(design))
    assert result.ok
    assert not [d for d in result.diagnostics if d.code.startswith("I")], \
        [d.machine() for d in result.diagnostics]
