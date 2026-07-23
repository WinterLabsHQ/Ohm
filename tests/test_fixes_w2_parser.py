"""W2 audit-fix gate: statement/decl spans end on their own line (no _NL bleed),
error recovery survives column-0 call continuations, unexpected-EOF diagnostics
point at real source, --debug-passes is accepted, U-code fixtures + golden IR."""

from pathlib import Path

import pytest

from ohm import ir
from ohm.cli import main
from ohm.driver import compile_source
from ohm.parse import parse

FIXTURES = Path(__file__).parent / "fixtures"
KNOWN_BAD = Path(__file__).parent / "known_bad"
GOLDEN = Path(__file__).parent / "golden"
CANONICAL_PATH = FIXTURES / "canonical.ohm"
CANONICAL = CANONICAL_PATH.read_text()


# ---------- finding 1: spans no longer bleed through the trailing _NL ----------
def test_statement_spans_end_on_their_own_line():
    prog, diags = parse(CANONICAL)
    assert diags == []
    pin_en = prog.decls[2].pins[0]  # `    pin EN:  role=input` on line 12
    assert (pin_en.span.line, pin_en.span.end_line) == (12, 12)
    assert pin_en.span.end_col > pin_en.span.col  # real width, not collapsed
    binding = prog.decls[3].items[0]  # `    vin  = io(Power)` on line 19
    assert (binding.span.line, binding.span.end_line) == (19, 19)


def test_decl_spans_end_on_last_content_line():
    prog, _ = parse(CANONICAL)
    ncp = prog.decls[2]  # component NCP1529: lines 9..16 (last pin on 16)
    assert ncp.span.line == 9
    assert ncp.span.end_line == 16  # not 18:1 (bled into `module Supply:`)
    supply = prog.decls[3]
    assert supply.span.end_line == 25  # last obligation line, not EOF


def test_human_caret_regains_width():
    src = "module M:\n    assert 3.3V <= 10ohm\n"
    result = compile_source(src, "w.ohm", emit=False)
    (d,) = [d for d in result.diagnostics if d.code == "U002"]
    assert d.span.line == d.span.end_line == 2
    caret_line = d.human(src).splitlines()[2]
    assert caret_line.count("^") == d.span.end_col - d.span.col > 1


# ---------- finding 2: recovery keeps blocks with open parens together ----------
def test_recovery_keeps_column0_call_continuation():
    src = (
        "module Good:\n"
        "    a = X(\n"
        "1, 2)\n"          # continuation at column 0 — legal inside parens
        "module Bad:\n"
        "    a ==== b\n"
    )
    prog, diags = parse(src)
    assert [d.span.line for d in diags] == [5]  # only the real error, no spurious P001s
    assert [d.name for d in prog.decls] == ["Good"]  # Good survived intact
    assert prog.decls[0].items[0].call.args  # the multiline call kept its args


# ---------- finding 3: unexpected EOF points at the last non-empty line ----------
def test_unexpected_eof_points_at_last_line():
    _, diags = parse("module M:\n")
    (d,) = diags
    assert d.code == "P001" and d.message == "unexpected end of file"
    assert d.span.line == 1 and d.span.end_col == len("module M:") + 1


def test_unexpected_eof_in_later_block():
    src = "module Good:\n    a = X()\n\ncomponent Trunc:\n"
    prog, diags = parse(src)
    assert [d.name for d in prog.decls] == ["Good"]
    (d,) = diags
    assert d.message == "unexpected end of file" and d.span.line == 4


# ---------- finding 4: --debug-passes accepted on check and build ----------
def test_debug_passes_flag_check():
    assert main(["check", str(CANONICAL_PATH), "--debug-passes"]) == 0


def test_debug_passes_flag_build(tmp_path):
    assert main(["build", str(CANONICAL_PATH), "-o", str(tmp_path),
                 "--debug-passes"]) == 0


# ---------- finding 7: U-code fixtures ----------
U_CASES = [
    ("u001_add_mismatch.ohm", "U001", 2),
    ("u002_compare_mismatch.ohm", "U002", 2),
    ("u003_unknown_unit.ohm", "U003", 2),
    ("u004_tolerance_mismatch.ohm", "U004", 1),  # annotate_units reports the decl
]


@pytest.mark.parametrize("fixture,code,line", U_CASES, ids=lambda c: str(c))
def test_u_known_bad_fixture(fixture, code, line):
    path = KNOWN_BAD / fixture
    result = compile_source(path.read_text(), str(path), emit=False)
    hits = [d for d in result.diagnostics if d.code == code]
    assert hits, f"expected {code}, got {[d.code for d in result.diagnostics]}"
    assert hits[0].severity == "error" and not result.ok
    assert hits[0].span.line == line


# ---------- finding 7: golden annotated-IR snapshot ----------
def test_golden_canonical_ir_snapshot():
    result = compile_source(CANONICAL, str(CANONICAL_PATH))
    assert result.ok, [d.machine() for d in result.diagnostics]
    got = ir.to_json(result.design)
    want = (GOLDEN / "canonical.ir.json").read_text()
    assert got == want, (
        "annotated IR for canonical.ohm changed. If intentional, regenerate:\n"
        "  .venv/bin/python -c \"from pathlib import Path; from ohm import ir; "
        "from ohm.driver import compile_source; "
        "p = Path('tests/fixtures/canonical.ohm'); "
        "r = compile_source(p.read_text(), str(p)); "
        "Path('tests/golden/canonical.ir.json').write_text(ir.to_json(r.design))\""
    )
