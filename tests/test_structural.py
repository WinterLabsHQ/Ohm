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


def test_grid_unmapped_pads_do_not_crash():
    """BGA/LGA pad ids are alphanumeric — the unmapped-pad report used to sort
    them with int() and raise ValueError, taking the compiler down."""
    result = run("s003_grid_unmapped.ohm")
    unmapped = [d.message for d in result.diagnostics
                if d.code == "S003" and "no pin mapped" in d.message]
    assert len(unmapped) == 1
    assert "B1" in unmapped[0]  # 16 balls, 2 mapped, 1 bogus -> 14 reported
    # S002 must describe an alphanumeric pad set, not claim "pads 1..16"
    s002 = [d.message for d in result.diagnostics if d.code == "S002"]
    assert s002 and "Z9" in s002[0] and "1..16" not in s002[0]
    assert "16 pads" in s002[0]


def test_unmapped_pad_list_is_truncated():
    """A large grid must not emit a multi-KB diagnostic."""
    src = ("component C:\n"
           "    package = generate(BGA, rows=16, cols=16, pitch=0.8mm, ball=0.4mm)\n"
           "    pin VDD: role=power_in, pad=A1\n")
    result = compile_source(src, "<t>")
    msg = next(d.message for d in result.diagnostics
               if d.code == "S003" and "no pin mapped" in d.message)
    assert "more)" in msg and len(msg) < 300


def test_warning_does_not_block_emit():
    result = run("s004_partial_padmap.ohm")
    assert result.ok and result.low is not None


@pytest.mark.parametrize("design", sorted(GOLDEN.glob("*.ohm")), ids=lambda p: p.stem)
def test_good_designs_still_emit(design):
    result = compile_source(design.read_text(), str(design))
    assert result.ok and result.low is not None
    s_diags = [d for d in result.diagnostics if d.code.startswith("S")]
    assert not [d for d in s_diags if d.severity == "error"]
    if design.stem == "supply":  # NCP1529 declares no pads at all -> S004 warning
        assert [d.code for d in s_diags] == ["S004"]
    else:
        assert s_diags == []
