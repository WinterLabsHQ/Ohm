"""P-LEX/PARSE + P-AST: .ohm source → Lark CST → typed AST.

Error recovery (compiler design §5): if the whole-file parse fails, re-parse each
top-level block independently (newline-padded so spans stay correct), collecting
one syntax diagnostic per bad block while still returning the good declarations.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from lark import Lark, Token, Transformer, v_args
from lark.exceptions import UnexpectedInput, UnexpectedToken
from lark.indenter import Indenter

from ohm import ast_nodes as A
from ohm.diagnostics import Diagnostic, Span

_GRAMMAR = (Path(__file__).parent / "ohm.lark").read_text()

_QUANTITY_RE = re.compile(r"(\d+(?:\.\d+)?)([A-Za-z]+)")


class _OhmIndenter(Indenter):
    NL_type = "_NL"
    OPEN_PAREN_types = ["LPAR"]
    CLOSE_PAREN_types = ["RPAR"]
    INDENT_type = "_INDENT"
    DEDENT_type = "_DEDENT"
    tab_len = 8


def _parser() -> Lark:
    global _CACHED
    if _CACHED is None:
        _CACHED = Lark(
            _GRAMMAR,
            parser="earley",
            lexer="basic",
            postlex=_OhmIndenter(),
            propagate_positions=True,
            maybe_placeholders=True,
        )
    return _CACHED


_CACHED: Lark | None = None


def _span(meta) -> Span:
    return Span(meta.line, meta.column, meta.end_line, meta.end_column)


def _tok_span(tok: Token) -> Span:
    return Span(tok.line, tok.column, tok.end_line, tok.end_column)


@v_args(meta=True)
class _ToAst(Transformer):
    # ---------- expressions ----------
    def ref(self, meta, children):
        return A.Ref(tuple(str(t) for t in children), _span(meta))

    def number(self, meta, children):
        return A.Num(Decimal(str(children[0])), _span(meta))

    def string(self, meta, children):
        return A.Str(str(children[0])[1:-1], _span(meta))

    def quantity(self, meta, children):
        tok = children[0]
        m = _QUANTITY_RE.fullmatch(str(tok))
        tol = children[1] if len(children) > 1 else None
        return A.QuantityLit(Decimal(m.group(1)), m.group(2), tol, _span(meta))

    def tol_abs(self, meta, children):
        m = _QUANTITY_RE.fullmatch(str(children[1]))
        return A.Tolerance(Decimal(m.group(1)), m.group(2))

    def tol_pct(self, meta, children):
        return A.Tolerance(Decimal(str(children[1])), None)

    def cmp_op(self, meta, children):
        return str(children[0])

    def comparison(self, meta, children):
        left, op, right = children
        return A.BinOp(op, left, right, _span(meta))

    def _binop(self, op, meta, children):
        return A.BinOp(op, children[0], children[1], _span(meta))

    def add(self, meta, children):
        return self._binop("+", meta, children)

    def sub(self, meta, children):
        return self._binop("-", meta, children)

    def mul(self, meta, children):
        return self._binop("*", meta, children)

    def div(self, meta, children):
        return self._binop("/", meta, children)

    def call(self, meta, children):
        callee = children[0]
        args, kwargs = [], []
        if len(children) > 1 and children[1] is not None:
            for kind, payload in children[1]:
                if kind == "pos":
                    args.append(payload)
                else:
                    kwargs.append(payload)
        return A.Call(callee, tuple(args), tuple(kwargs), _span(meta))

    def arguments(self, meta, children):
        return list(children)

    def posarg(self, meta, children):
        return ("pos", children[0])

    def kwarg(self, meta, children):
        return ("kw", (str(children[0]), children[1]))

    # ---------- shared ----------
    def attr(self, meta, children):
        name, value = children
        return A.Attr(str(name), value, _span(meta))

    # ---------- interface ----------
    def member_decl(self, meta, children):
        name, sigtype, *attrs = children
        return A.Member(str(name), str(sigtype), tuple(attrs), _span(meta))

    def require_decl(self, meta, children):
        return A.Require(children[0], _span(meta))

    def interface_decl(self, meta, children):
        name, *items = children
        members = tuple(i for i in items if isinstance(i, A.Member))
        requires = tuple(i for i in items if isinstance(i, A.Require))
        return A.InterfaceDecl(str(name), members, requires, _span(meta))

    # ---------- component ----------
    def pin_decl(self, meta, children):
        name, *attrs = children
        return A.PinDecl(str(name), tuple(attrs), _span(meta))

    def attr_decl(self, meta, children):
        name, value = children
        return A.Attr(str(name), value, _span(meta))

    def component_decl(self, meta, children):
        name, *items = children
        attrs = tuple(i for i in items if isinstance(i, A.Attr))
        pins = tuple(i for i in items if isinstance(i, A.PinDecl))
        return A.ComponentDecl(str(name), attrs, pins, _span(meta))

    # ---------- module ----------
    def binding(self, meta, children):
        name, call = children
        return A.Binding(str(name), call, _span(meta))

    def connect(self, meta, children):
        return A.Connect(children[0], children[1], _span(meta))

    def assert_stmt(self, meta, children):
        return A.AssertStmt(children[0], _span(meta))

    def obligation_stmt(self, meta, children):
        call, op, bound = children
        return A.ObligationStmt(call, op, bound, _span(meta))

    def module_decl(self, meta, children):
        name, *items = children
        return A.ModuleDecl(str(name), tuple(items), _span(meta))

    # ---------- top ----------
    def top_decl(self, meta, children):
        return children[0]

    def start(self, meta, children):
        return A.Program(tuple(children))


def _syntax_diag(exc: UnexpectedInput, path: str) -> Diagnostic:
    line = getattr(exc, "line", 1) or 1
    col = getattr(exc, "column", 1) or 1
    if isinstance(exc, UnexpectedToken):
        tok = exc.token
        shown = "end of file" if tok.type == "$END" else repr(str(tok))
        msg = f"unexpected {shown}"
        expected = sorted(t for t in exc.expected if not t.startswith("__"))
        help_ = f"expected one of: {', '.join(expected[:6])}" if expected else None
    else:
        msg = "invalid syntax"
        help_ = None
    return Diagnostic("error", "P001", msg, Span(line, col, line, col), file=path, help=help_)


def _blocks(source: str) -> list[tuple[int, str]]:
    """Split into (start_line_0based, text) top-level blocks.

    A block starts at a non-blank, non-comment line with no leading whitespace.
    """
    blocks: list[tuple[int, list[str]]] = []
    for i, line in enumerate(source.splitlines(keepends=True)):
        stripped = line.strip()
        starts_new = line[:1] not in (" ", "\t") and stripped and not stripped.startswith("#")
        if starts_new or not blocks:
            blocks.append((i, [line]))
        else:
            blocks[-1][1].append(line)
    return [(start, "".join(lines)) for start, lines in blocks]


def parse(source: str, path: str = "<string>") -> tuple[A.Program, list[Diagnostic]]:
    """Parse source → (Program, diagnostics). Partial Program on syntax errors."""
    if not source.endswith("\n"):
        source += "\n"
    try:
        return _ToAst().transform(_parser().parse(source)), []
    except UnexpectedInput:
        pass
    # recovery: parse each top-level block on its own, newline-padded for true spans
    decls: list[A.Decl] = []
    diags: list[Diagnostic] = []
    for start_line, text in _blocks(source):
        padded = "\n" * start_line + text
        try:
            prog = _ToAst().transform(_parser().parse(padded))
            decls.extend(prog.decls)
        except UnexpectedInput as exc:
            diags.append(_syntax_diag(exc, path))
    return A.Program(tuple(decls)), diags


def parse_file(path: str) -> tuple[A.Program, list[Diagnostic]]:
    return parse(Path(path).read_text(), path)
