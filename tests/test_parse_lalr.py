"""The parser is LALR(1), and it parses exactly as the Earley parser it replaced.

`parse._parser` used Earley, which is general but super-linear and dominated the
compile of a large design (a 500-component board spent ~2.3 s of a ~3.5 s
`ohm check` in the Earley parse). The grammar turns out to be LALR(1) as written
-- no shift/reduce and no reduce/reduce conflict -- so it is unambiguous, and an
LALR parser over the same grammar and the same "basic" lexer builds the same
tree. These tests hold that claim against an Earley parser built here from the
same `ohm.lark`:

* the grammar stays conflict-free (a conflict lark would silently resolve as a
  shift is a change of language, so it fails here instead);
* every .ohm file in the repository parses to an identical AST and identical
  diagnostics;
* malformed inputs -- tokens deleted, duplicated, swapped or replaced, lines
  dropped or re-indented, text truncated -- give identical P001 diagnostics:
  code, span, message and the "expected one of" help. That last one needs
  `parse._viable_terminals`, because LALR's own `expected` set on an error is
  taken after reductions the full context would not allow.
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path

import pytest
from lark import Lark, logger as lark_logger

from ohm import parse as P

ROOT = Path(__file__).resolve().parent.parent
OHM_FILES = sorted(p for p in ROOT.rglob("*.ohm") if ".git" not in p.parts)


@pytest.fixture(scope="module")
def earley() -> Lark:
    return Lark(P._GRAMMAR, parser="earley", lexer="basic", postlex=P._OhmIndenter(),
                propagate_positions=True, maybe_placeholders=True)


def _dump(source: str, path: str) -> str:
    prog, diags = P.parse(source, path)
    return repr(prog) + "\n" + "\n".join(repr(d) for d in diags)


def _earley_dump(earley: Lark, source: str, path: str) -> str:
    saved = P._CACHED, P._viable_terminals
    P._CACHED, P._viable_terminals = earley, (lambda _source: None)
    try:
        return _dump(source, path)
    finally:
        P._CACHED, P._viable_terminals = saved


def test_the_parser_is_lalr():
    assert P._parser().options.parser == "lalr"
    assert P._parser().options.lexer == "basic"


def test_the_grammar_has_no_lalr_conflict():
    """lark resolves a shift/reduce conflict as a shift and only logs it."""
    records: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler, level = _Keep(), lark_logger.level
    lark_logger.addHandler(handler)
    lark_logger.setLevel(logging.DEBUG)
    try:
        Lark(P._GRAMMAR, parser="lalr", lexer="basic", postlex=P._OhmIndenter(),
             propagate_positions=True, maybe_placeholders=True, debug=True)
    finally:
        lark_logger.removeHandler(handler)
        lark_logger.setLevel(level)
    conflicts = [r.getMessage() for r in records if "conflict" in r.getMessage().lower()]
    assert conflicts == []


def test_the_corpus_is_not_empty():
    assert len(OHM_FILES) >= 40  # std (17), examples, fixtures, golden, known_bad


@pytest.mark.parametrize("path", OHM_FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_every_ohm_file_parses_as_earley_did(earley, path):
    source = path.read_text(encoding="utf-8-sig")
    rel = path.relative_to(ROOT).as_posix()
    assert _dump(source, rel) == _earley_dump(earley, source, rel)


# ---------- malformed input ----------

_TOKEN = re.compile(r'"[^"\n]*"|\+/-|\.\.|[<>=!]=|\d+(?:\.\d+)?[A-Za-z]*|[A-Za-z_]\w*'
                    r'|\n[ \t]*|#[^\n]*|[ \t]+|.', re.S)
_VOCAB = ["(", ")", ":", ",", "=", "~", ".", "..", "[", "]", "+", "-", "*", "/", "%",
          "+/-", "<=", "==", "<", "\n", "\n    ", "    ", "\t", "module", "component",
          "interface", "kind", "pin", "net", "use", "for", "in", "if", "else", "assert",
          "fn", "board", "expects", "requires", "on", "when", "note", "obligation",
          "constraint", "x", "10k", "3.3V", "5", '"s"', "#c", "@", "\r\n"]


def _mutant(rng: random.Random, source: str) -> str:
    toks = _TOKEN.findall(source)
    i = rng.randrange(len(toks))
    op = rng.choice(["del", "dup", "swap", "insert", "replace", "trunc_tok", "trunc_char",
                     "del_line", "dup_line", "indent_line", "dedent_line"])
    if op == "del":
        return "".join(toks[:i] + toks[i + 1:])
    if op == "dup":
        return "".join(toks[:i + 1] + toks[i:])
    if op == "swap" and i + 1 < len(toks):
        return "".join(toks[:i] + [toks[i + 1], toks[i]] + toks[i + 2:])
    if op == "insert":
        return "".join(toks[:i] + [rng.choice(_VOCAB)] + toks[i:])
    if op == "replace":
        return "".join(toks[:i] + [rng.choice(_VOCAB)] + toks[i + 1:])
    if op == "trunc_tok":
        return "".join(toks[:i])
    if op == "trunc_char":
        return source[:rng.randrange(len(source) + 1)]
    lines = source.splitlines(keepends=True)
    j = rng.randrange(len(lines))
    if op == "del_line":
        del lines[j]
    elif op == "dup_line":
        lines.insert(j, lines[j])
    elif op == "indent_line":
        lines[j] = rng.choice([" ", "  ", "    ", "\t"]) + lines[j]
    elif op == "dedent_line":
        lines[j] = lines[j].lstrip(" \t")
    return "".join(lines)


_SMALL = [p for p in OHM_FILES if p.stat().st_size < 12_000]


@pytest.mark.parametrize("seed", range(6))
def test_malformed_input_reports_as_earley_did(earley, seed):
    rng = random.Random(seed)
    errors = 0
    for _ in range(50):
        path = rng.choice(_SMALL)
        source = _mutant(rng, path.read_text(encoding="utf-8-sig"))
        new, old = _dump(source, "m.ohm"), _earley_dump(earley, source, "m.ohm")
        assert new == old, source
        errors += "P001" in new
    assert errors > 0  # the mutants do reach the error path


def test_running_out_of_input_is_still_an_end_of_file(earley):
    """LALR reports the end of input as a `$END` token, Earley as UnexpectedEOF;
    both must give P001 at the last non-empty line with no help. (Where the
    indenter closes a block first, the bad token is a `_DEDENT` instead, in both.)"""
    for source in ["module M:\n", "module M:\n    a = R(\n", "component C:\n    pin a: role=",
                   "kind K(", "fn f(a, b) = (a +"]:
        assert _dump(source, "e.ohm") == _earley_dump(earley, source, "e.ohm"), source
    for source in ["module M:\n", "kind K(", "fn f(a, b) = (a +"]:
        assert "unexpected end of file" in _dump(source, "e.ohm"), source


def test_hypothesis_truncations_and_edits(earley):
    hypothesis = pytest.importorskip("hypothesis")
    st = pytest.importorskip("hypothesis.strategies")
    canonical = (ROOT / "tests" / "fixtures" / "canonical.ohm").read_text(encoding="utf-8")

    @hypothesis.settings(max_examples=100, deadline=None, derandomize=True, database=None)
    @hypothesis.given(st.integers(0, len(canonical)), st.integers(0, len(canonical)),
                      st.sampled_from(["", *_VOCAB]))
    def check(a, b, insert):
        lo, hi = min(a, b), max(a, b)
        source = canonical[:lo] + insert + canonical[hi:]
        assert _dump(source, "h.ohm") == _earley_dump(earley, source, "h.ohm")

    check()
