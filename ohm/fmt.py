"""P10: `ohm fmt` — canonical reprint from the AST (deterministic).

Comments are re-attached by source line: full-line comments print before the
nearest following declaration/item, trailing comments stay on their line.
Sources with syntax errors are returned unchanged (never reformat broken code).
"""

from __future__ import annotations

from ohm import ast_nodes as A
from ohm.diagnostics import Diagnostic, has_errors
from ohm.parse import parse

_INDENT = "    "


def _comments(source: str) -> tuple[dict[int, str], dict[int, str]]:
    """(full_line, trailing) comments keyed by 1-based line number."""
    full, trailing = {}, {}
    for i, line in enumerate(source.splitlines(), start=1):
        if "#" not in line:
            continue
        before, _, comment = line.partition("#")
        if '"' in before and before.count('"') % 2 == 1:
            continue  # '#' inside a string literal
        if before.strip():
            trailing[i] = "#" + comment.rstrip()
        else:
            full[i] = "#" + comment.rstrip()
    return full, trailing


def _attr(a: A.Attr) -> str:
    return f"{a.name}={A.to_src(a.value)}"


def _item_line(item: A.ModuleItem) -> str:
    match item:
        case A.Binding():
            return f"{item.name} = {A.to_src(item.call)}"
        case A.Connect():
            return f"{item.left.dotted()} ~ {item.right.dotted()}"
        case A.AssertStmt():
            return f"assert {A.to_src(item.expr)}"
        case A.ObligationStmt():
            return f"obligation {A.to_src(item.call)} {item.op} {A.to_src(item.bound)}"
        case A.ConstraintStmt():
            return f"constraint {A.to_src(item.call)}"


def _decl_lines(decl: A.Decl) -> list[tuple[int, str]]:
    """[(source_line, formatted_text)] for a declaration."""
    out: list[tuple[int, str]] = []
    if isinstance(decl, A.InterfaceDecl):
        out.append((decl.span.line, f"interface {decl.name}:"))
        for m in decl.members:
            attrs = "".join(f", {_attr(a)}" for a in m.attrs)
            out.append((m.span.line, f"{_INDENT}{m.name}: {m.signal_type}{attrs}"))
        for r in decl.requires:
            out.append((r.span.line, f"{_INDENT}require {A.to_src(r.relation)}"))
    elif isinstance(decl, A.ComponentDecl):
        out.append((decl.span.line, f"component {decl.name}:"))
        for a in decl.attrs:
            out.append((a.span.line, f"{_INDENT}{a.name} = {A.to_src(a.value)}"))
        for p in decl.pins:
            attrs = ", ".join(_attr(a) for a in p.attrs)
            out.append((p.span.line, f"{_INDENT}pin {p.name}: {attrs}"))
    else:
        out.append((decl.span.line, f"module {decl.name}:"))
        for item in decl.items:
            out.append((item.span.line, f"{_INDENT}{_item_line(item)}"))
    return out


def format_source(source: str, path: str = "<string>") -> tuple[str, list[Diagnostic]]:
    program, diags = parse(source, path)
    if has_errors(diags):
        return source, diags
    full, trailing = _comments(source)
    lines: list[tuple[int, str]] = []
    for decl in program.decls:
        if lines:
            lines.append((lines[-1][0], ""))  # one blank line between decls
        lines.extend(_decl_lines(decl))
    out: list[str] = []
    pending = sorted(full)
    for src_line, text in lines:
        while pending and pending[0] <= src_line:
            out.append(full[pending.pop(0)])
        if src_line in trailing and text:
            text = f"{text}  {trailing[src_line]}"
        out.append(text)
    out.extend(full[line] for line in pending)  # comments after the last item
    return "\n".join(out) + "\n", diags
