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
from lark.exceptions import LarkError, UnexpectedToken
from lark.indenter import Indenter

from ohm import ast_nodes as A
from ohm.diagnostics import Diagnostic, Span

_GRAMMAR = (Path(__file__).parent / "ohm.lark").read_text()


class _Conforms:
    """Transformer marker for a `(A, B)` conformance/parent list on kind/component."""

    __slots__ = ("names",)

    def __init__(self, names: tuple[str, ...]):
        self.names = names


class _Index:
    """Transformer marker for a `[n]` / `[i]` ref index (ADR-010)."""

    __slots__ = ("value",)

    def __init__(self, value: str):
        self.value = value

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


def _span(meta, children=None) -> Span:
    # meta of rules that swallow a trailing _NL (or _DEDENT) bleeds into the next
    # line; when children are given, take the end from the last positioned child
    # (AST node or token) so statements/decls end where their source text ends.
    end_line, end_col = meta.end_line, meta.end_column
    if children is not None:
        for c in reversed(children):
            if isinstance(c, Token):
                end_line, end_col = c.end_line, c.end_column
                break
            span = getattr(c, "span", None)
            if isinstance(span, Span):
                end_line, end_col = span.end_line, span.end_col
                break
    return Span(meta.line, meta.column, end_line, end_col)


def _tok_span(tok: Token) -> Span:
    return Span(tok.line or 1, tok.column or 1, tok.end_line or 1, tok.end_column or 1)


@v_args(meta=True)
class _ToAst(Transformer):
    # ---------- expressions ----------
    def index(self, meta, children):
        return _Index(str(children[0]))  # literal number or loop-var name

    def ref(self, meta, children):
        # fuse each `index` onto its preceding NAME: leds [3] .A -> ("leds[3]", "A")
        parts: list[str] = []
        for child in children:
            if isinstance(child, _Index):
                parts[-1] += f"[{child.value}]"
            else:
                parts.append(str(child))
        return A.Ref(tuple(parts), _span(meta))

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
        return A.Member(str(name), str(sigtype), tuple(attrs), _span(meta, children))

    def require_decl(self, meta, children):
        return A.Require(children[0], _span(meta, children))

    def interface_decl(self, meta, children):
        name, *items = children
        members = tuple(i for i in items if isinstance(i, A.Member))
        requires = tuple(i for i in items if isinstance(i, A.Require))
        expects = tuple(i for i in items if isinstance(i, A.Expectation))
        return A.InterfaceDecl(str(name), members, requires, _span(meta, children), expects)

    # ---------- component ----------
    def pin_decl(self, meta, children):
        name, *attrs = children
        return A.PinDecl(str(name), tuple(attrs), _span(meta, children))

    def attr_decl(self, meta, children):
        name, value = children
        return A.Attr(str(name), value, _span(meta, children))

    def conforms(self, meta, children):
        return _Conforms(tuple(r.dotted() for r in children))  # kind / parent names

    def component_decl(self, meta, children):
        name, *rest = children
        kinds = next((r.names for r in rest if isinstance(r, _Conforms)), ())
        items = [i for i in rest if not isinstance(i, _Conforms)]
        attrs = tuple(i for i in items if isinstance(i, A.Attr))
        pins = tuple(i for i in items if isinstance(i, A.PinDecl))
        return A.ComponentDecl(str(name), attrs, pins, _span(meta, children), kinds)

    # ---------- kind (ADR-008) ----------
    def req_pin(self, meta, children):
        return ("req", A.KindMember(str(children[0]), str(children[1]), _span(meta, children)))

    def opt_pin(self, meta, children):
        return ("opt", A.KindMember(str(children[0]), str(children[1]), _span(meta, children)))

    def kind_param(self, meta, children):
        return ("param", A.KindParam(str(children[0]), str(children[1]), _span(meta, children)))

    def kind_decl(self, meta, children):
        name, *rest = children
        parents = next((r.names for r in rest if isinstance(r, _Conforms)), ())
        items = [i for i in rest if isinstance(i, tuple)]
        requires = tuple(n for k, n in items if k == "req")
        optional = tuple(n for k, n in items if k == "opt")
        params = tuple(n for k, n in items if k == "param")
        expects = tuple(i for i in rest if isinstance(i, A.Expectation))
        return A.KindDecl(str(name), parents, requires, optional, params,
                          _span(meta, children), expects)

    # ---------- expects (ADR-009) ----------
    def expects_args(self, meta, children):
        return ("eargs", children[0])  # the arguments list [(kind, payload), ...]

    def expects_on(self, meta, children):
        return ("eon", tuple(str(t) for t in children))

    def expects_when(self, meta, children):
        return ("ewhen", str(children[0]))

    def expects_item(self, meta, children):
        pattern, *rest = children
        args: tuple = ()
        members: tuple[str, ...] = ()
        when = None
        for item in rest:
            if not isinstance(item, tuple):
                continue
            tag, payload = item
            if tag == "eargs":
                args = tuple(p for k, p in payload if k == "pos")  # positional args only
            elif tag == "eon":
                members = payload
            elif tag == "ewhen":
                when = payload
        return A.Expectation(str(pattern), args, members, when, _span(meta, children))

    # ---------- module ----------
    def binding(self, meta, children):
        name, call = children
        return A.Binding(str(name), call, _span(meta, children))

    def array_binding(self, meta, children):
        name, count, call = children
        return A.ArrayBinding(str(name), Decimal(str(count)), call, _span(meta, children))

    def for_stmt(self, meta, children):
        var, lo, hi, *items = children
        return A.ForStmt(str(var), Decimal(str(lo)), Decimal(str(hi)),
                         tuple(items), _span(meta, children))

    def loop_item(self, meta, children):
        return children[0]

    def connect(self, meta, children):
        return A.Connect(children[0], children[1], _span(meta, children))

    def assert_stmt(self, meta, children):
        return A.AssertStmt(children[0], _span(meta, children))

    def obligation_stmt(self, meta, children):
        call, op, bound = children
        return A.ObligationStmt(call, op, bound, _span(meta, children))

    def constraint_stmt(self, meta, children):
        return A.ConstraintStmt(children[0], _span(meta, children))

    def module_decl(self, meta, children):
        name, *items = children
        return A.ModuleDecl(str(name), tuple(items), _span(meta, children))

    def use_stmt(self, meta, children):
        (target,) = children
        if isinstance(target, A.Ref):
            return A.UseDecl(target.dotted(), False, _span(meta, children))
        return A.UseDecl(str(target)[1:-1], True, _span(meta, children))  # STRING token

    # ---------- top ----------
    def top_decl(self, meta, children):
        return children[0]

    def interface_item(self, meta, children):
        return children[0]  # unwrap the expects_item alternative

    def kind_item(self, meta, children):
        return children[0]

    def module_item(self, meta, children):
        return children[0]  # unwrap the for_stmt alternative

    def start(self, meta, children):
        return A.Program(tuple(children))


def _syntax_diag(exc: LarkError, path: str, source: str = "") -> Diagnostic:
    raw_line = getattr(exc, "line", None)
    if raw_line is not None and raw_line <= 0:
        # unexpected EOF (lark reports -1:-1): point at the last non-empty line
        src_lines = source.splitlines()
        last = next((i for i in range(len(src_lines), 0, -1) if src_lines[i - 1].strip()), 1)
        end_col = len(src_lines[last - 1]) + 1 if last <= len(src_lines) else 1
        return Diagnostic("error", "P001", "unexpected end of file",
                          Span(last, 1, last, end_col), file=path)
    line = max(raw_line or 1, 1)
    col = max(getattr(exc, "column", 1) or 1, 1)
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

    A block starts at a non-blank, non-comment line with no leading whitespace,
    unless a paren is still open (a multiline call continuation may sit at col 0).
    """
    blocks: list[tuple[int, list[str]]] = []
    depth = 0
    for i, line in enumerate(source.splitlines(keepends=True)):
        stripped = line.strip()
        starts_new = (depth == 0 and line[:1] not in (" ", "\t")
                      and stripped and not stripped.startswith("#"))
        code = re.sub(r'"[^"\n]*"', "", line).split("#", 1)[0]
        depth = max(depth + code.count("(") - code.count(")"), 0)
        if starts_new or not blocks:
            blocks.append((i, [line]))
        else:
            blocks[-1][1].append(line)
    return [(start, "".join(lines)) for start, lines in blocks]


def _skip_top_indent(source: str, path: str) -> tuple[str, Diagnostic | None]:
    """If the first code line is indented: P001 + blank out that leading run."""
    lines = source.splitlines(keepends=True)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line[:1] not in (" ", "\t"):
            return source, None
        indent = len(line) - len(line.lstrip(" \t"))
        diag = Diagnostic("error", "P001", "unexpected indentation at top level",
                          Span(i + 1, 1, i + 1, indent + 1), file=path)
        # blank the indented run (line count preserved) up to the next top-level line
        j = i
        while j < len(lines):
            s = lines[j].strip()
            if s and not s.startswith("#") and lines[j][:1] not in (" ", "\t"):
                break
            lines[j] = "\n"
            j += 1
        return "".join(lines), diag
    return source, None


def parse(source: str, path: str = "<string>") -> tuple[A.Program, list[Diagnostic]]:
    """Parse source → (Program, diagnostics). Partial Program on syntax errors."""
    source = source.lstrip("﻿")  # UTF-8 BOM would otherwise be a P001 at 1:1
    if not source.endswith("\n"):
        source += "\n"
    diags: list[Diagnostic] = []
    source, indent_diag = _skip_top_indent(source, path)
    if indent_diag is not None:
        diags.append(indent_diag)
    try:
        return _ToAst().transform(_parser().parse(source)), diags
    except LarkError:  # incl. DedentError, which is not UnexpectedInput
        pass
    # recovery: parse each top-level block on its own, newline-padded for true spans
    decls: list[A.Decl] = []
    for start_line, text in _blocks(source):
        padded = "\n" * start_line + text
        try:
            prog = _ToAst().transform(_parser().parse(padded))
            decls.extend(prog.decls)
        except LarkError as exc:
            diags.append(_syntax_diag(exc, path, padded))
    return A.Program(tuple(decls)), diags


def parse_file(path: str) -> tuple[A.Program, list[Diagnostic]]:
    return parse(Path(path).read_text(), path)
