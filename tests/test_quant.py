"""P9 gate: over-power/over-current/over-voltage fixtures error
deterministically; ratings come through ratings_iface; builds stay hermetic
(ratings frozen into the design)."""

from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.ratings_iface import DeclaredRatings, Ratings

KNOWN_BAD = Path(__file__).parent / "known_bad"
GOLDEN = Path(__file__).parent / "golden"


def run(name):
    path = KNOWN_BAD / name
    return compile_source(path.read_text(), str(path))


CASES = [
    ("q001_overvoltage.ohm", "Q001", 9),   # the vmax pin declaration
    ("q002_overcurrent.ohm", "Q002", 4),   # the imax driver pin
    ("q003_overpower.ohm", "Q003", 11),    # the pmax pin
    ("q004_conflicting_rails.ohm", "Q004", 13),  # the offending ~
]


@pytest.mark.parametrize("fixture,code,line", CASES, ids=lambda c: str(c))
def test_known_bad_fixture(fixture, code, line):
    result = run(fixture)
    hits = [d for d in result.diagnostics if d.code == code]
    assert hits, f"expected {code}, got {[d.code for d in result.diagnostics]}"
    assert hits[0].severity == "error" and not result.ok
    assert hits[0].span.line == line


def test_q_checks_deterministic():
    a = [d.machine() for d in run("q003_overpower.ohm").diagnostics]
    b = [d.machine() for d in run("q003_overpower.ohm").diagnostics]
    assert a == b


def test_q001_message_has_values():
    (d,) = [d for d in run("q001_overvoltage.ohm").diagnostics if d.code == "Q001"]
    assert "5.0V" in d.message and "3.6V" in d.message


def test_q004_includes_ground_short():
    src = (
        "component Reg5:\n"
        '    package = "SOT-23"\n'
        "    pin OUT: role=power_out, voltage=5.0V, pad=1\n"
        "component C:\n"
        '    package = "X"\n'
        "    pin G: role=ground, pad=1\n"
        "module M:\n"
        "    a = Reg5()\n"
        "    c = C()\n"
        "    a.OUT ~ c.G\n"
    )
    result = compile_source(src, "short.ohm")
    assert any(d.code == "Q004" for d in result.diagnostics)


def test_undeclared_values_are_unchecked():
    # same topology as q001 but without a declared rail voltage: nothing to
    # compare, nothing reported (D3 — detectability bounded by declared intent)
    src = (KNOWN_BAD / "q001_overvoltage.ohm").read_text().replace(", voltage=5.0V", "")
    result = compile_source(src, "undeclared.ohm")
    assert result.ok
    assert not [d for d in result.diagnostics if d.code.startswith("Q")]


def test_ratings_flow_through_provider():
    class NoRatings(DeclaredRatings):
        def ratings(self, component, pin):
            return Ratings(None, None, None)  # provider seam: erase all ratings

    from ohm.check_quant import check_quant
    from ohm.check_units import annotate_units
    from ohm.parse import parse
    from ohm.resolve import resolve

    src = (KNOWN_BAD / "q001_overvoltage.ohm").read_text()
    design, _ = resolve(parse(src)[0])
    design, _ = annotate_units(design)
    assert any(d.code == "Q001" for d in check_quant(design))  # default provider
    assert not check_quant(design, provider=NoRatings())  # seam respected


def test_tolerance_worst_case_fires():
    src = (KNOWN_BAD / "q001_overvoltage.ohm").read_text().replace(
        "voltage=5.0V", "voltage=3.5V +/- 5%")  # hi = 3.675V > 3.6V
    result = compile_source(src, "tol.ohm")
    assert any(d.code == "Q001" for d in result.diagnostics)


@pytest.mark.parametrize("design", sorted(GOLDEN.glob("*.ohm")), ids=lambda p: p.stem)
def test_golden_designs_quant_clean(design):
    result = compile_source(design.read_text(), str(design))
    assert result.ok
    assert not [d for d in result.diagnostics if d.code.startswith("Q")]
