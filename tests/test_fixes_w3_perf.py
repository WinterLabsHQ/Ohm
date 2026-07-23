"""W3 perf refactor net: single-lower driver == per-checker-lower path (diags +
netlist), determinism holds, and a 200-instance design compiles fast."""

import dataclasses
import time
from pathlib import Path

import pytest

from ohm.check_units import annotate_units
from ohm.diagnostics import has_errors
from ohm.driver import CHECKERS, compile_source
from ohm.emit_netlist import emit_netlist
from ohm.holes import collect_holes
from ohm.lower import lower
from ohm.parse import parse
from ohm.resolve import resolve

TESTS = Path(__file__).parent
DESIGNS = [TESTS / "fixtures" / "canonical.ohm"] + sorted((TESTS / "golden").glob("*.ohm"))


def old_style_compile(source: str, path: str):
    """The pre-refactor pipeline: every checker lowers for itself (low=None)."""
    program, diags = parse(source, path)
    diags = list(diags)
    design, rdiags = resolve(program, path)
    diags += rdiags
    design, udiags = annotate_units(design, path)
    diags += udiags
    for checker in CHECKERS:
        diags += checker(design, path)  # check_intent/check_quant re-lower internally
    low = None
    if not has_errors(diags):
        lowres, ldiags = lower(design, path)
        low = dataclasses.replace(lowres, holes=collect_holes(design))
        diags += ldiags
    return low, diags


@pytest.mark.parametrize("design", DESIGNS, ids=lambda p: p.stem)
def test_single_lower_matches_per_checker_lower(design):
    source, path = design.read_text(), str(design)
    result = compile_source(source, path)
    old_low, old_diags = old_style_compile(source, path)
    assert [d.machine() for d in result.diagnostics] == [d.machine() for d in old_diags]
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert emit_netlist(result.low, path) == emit_netlist(old_low, path)


@pytest.mark.parametrize("design", DESIGNS, ids=lambda p: p.stem)
def test_double_compile_byte_identical(design):
    source, path = design.read_text(), str(design)
    a = compile_source(source, path)
    b = compile_source(source, path)
    assert emit_netlist(a.low, path).encode() == emit_netlist(b.low, path).encode()
    assert [d.machine() for d in a.diagnostics] == [d.machine() for d in b.diagnostics]


def test_scale_200_instances_under_5s():
    lines = [
        "component R0402:\n"
        '    package = "R_0402_1005Metric"\n'
        '    designator_prefix = "R"\n'
        "    pin P1: role=passive, pad=1\n"
        "    pin P2: role=passive, pad=2\n"
        "\n"
        "module Big:\n"
    ]
    lines += [f"    r{i} = R0402(value=10kohm +/- 1%)\n" for i in range(200)]
    lines += [f"    r{i}.P2 ~ r{(i + 1) % 200}.P1\n" for i in range(200)]
    source = "".join(lines)
    t0 = time.perf_counter()
    result = compile_source(source, "synthetic.ohm")
    elapsed = time.perf_counter() - t0
    assert result.ok, [d.machine() for d in result.diagnostics]
    assert len(result.low.parts) == 200
    assert elapsed < 5.0, f"200-instance compile took {elapsed:.2f}s"
