"""P5 gate: every known_bad S-fixture errors with the right code; good designs
still emit."""

from pathlib import Path

import pytest

from ohm.driver import compile_source

KNOWN_BAD = Path(__file__).parent / "known_bad"
GOLDEN = Path(__file__).parent / "golden"


def run(name):
    path = KNOWN_BAD / name
    return compile_source(path.read_text(), str(path))


CASES = [
    ("s001_duplicate_pad.ohm", "S001", "error", 5),
    ("s002_pad_out_of_range.ohm", "S002", "error", 11),
    ("s003_unmapped.ohm", "S003", "error", 7),
    ("s004_partial_padmap.ohm", "S004", "warning", 2),
    ("s005_no_package.ohm", "S005", "error", 2),
]


@pytest.mark.parametrize("fixture,code,severity,line", CASES, ids=lambda c: str(c))
def test_known_bad_fixture(fixture, code, severity, line):
    result = run(fixture)
    hits = [d for d in result.diagnostics if d.code == code]
    assert hits, f"expected {code}, got {[d.code for d in result.diagnostics]}"
    assert hits[0].severity == severity
    assert hits[0].span.line == line


def test_s003_reports_both_directions():
    result = run("s003_unmapped.ohm")
    msgs = [d.message for d in result.diagnostics if d.code == "S003"]
    assert any("no pad" in m for m in msgs)  # pin V unmapped
    assert any("no pin mapped" in m for m in msgs)  # pad 4 unmapped


def test_warning_does_not_block_emit():
    result = run("s004_partial_padmap.ohm")
    assert result.ok and result.low is not None


@pytest.mark.parametrize("design", sorted(GOLDEN.glob("*.ohm")), ids=lambda p: p.stem)
def test_good_designs_still_emit(design):
    result = compile_source(design.read_text(), str(design))
    assert result.ok and result.low is not None
    assert not [d for d in result.diagnostics if d.code.startswith("S")]
