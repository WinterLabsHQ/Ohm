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


def _comment_start(line: str) -> int:
    """Index of the first '#' outside a string literal, or -1.

    Ohm strings have no escapes (STRING: /"[^"\\n]*"/), so an even count of
    preceding '"' means the '#' is outside any string.
    """
    idx = line.find("#")
    while idx != -1:
        if line[:idx].count('"') % 2 == 0:
            return idx
        idx = line.find("#", idx + 1)
    return -1


def _comments(source: str) -> tuple[dict[int, str], dict[int, str]]:
    """(full_line, trailing) comments keyed by 1-based line number."""
    full, trailing = {}, {}
    for i, line in enumerate(source.splitlines(), start=1):
        idx = _comment_start(line)
        if idx == -1:
            continue
        comment = line[idx:].rstrip()
        if line[:idx].strip():
            trailing[i] = comment
        else:
            full[i] = comment
    return full, trailing


def _attr(a: A.Attr) -> str:
    return f"{a.name}={A.to_src(a.value)}"


def _expects_line(ex: A.Expectation) -> str:
    parts = [f"expects {ex.pattern}"]
    if ex.args:
        parts[0] += f"({', '.join(A.to_src(a) for a in ex.args)})"
    if ex.members:
        parts.append(f"on {', '.join(ex.members)}")
    if ex.when:
        parts.append(f"when {ex.when}")
    return " ".join(parts)


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


def _decl_lines(decl: A.Decl) -> list[tuple[int, int, str]]:
    """[(source_line, source_end_line, formatted_text)] for a declaration."""
    out: list[tuple[int, int, str]] = []
    if isinstance(decl, A.UseDecl):
        target = f'"{decl.target}"' if decl.is_path else decl.target
        return [(decl.span.line, decl.span.end_line, f"use {target}")]
    if isinstance(decl, A.KindDecl):
        head = decl.name + (f"({', '.join(decl.parents)})" if decl.parents else "")
        if not (decl.requires or decl.optional or decl.params or decl.expects):
            return [(decl.span.line, decl.span.line, f"kind {head}")]  # bodyless refinement
        out.append((decl.span.line, decl.span.line, f"kind {head}:"))
        for km in decl.requires:
            out.append((km.span.line, km.span.end_line, f"{_INDENT}requires {km.name}: {km.role}"))
        for km in decl.optional:
            out.append((km.span.line, km.span.end_line, f"{_INDENT}optional {km.name}: {km.role}"))
        for kp in decl.params:
            out.append((kp.span.line, kp.span.end_line, f"{_INDENT}param {kp.name}: {kp.dimension}"))
        for ex in decl.expects:
            out.append((ex.span.line, ex.span.end_line, f"{_INDENT}{_expects_line(ex)}"))
        return out[:1] + sorted(out[1:], key=lambda t: t[0])
    if isinstance(decl, A.InterfaceDecl):
        out.append((decl.span.line, decl.span.line, f"interface {decl.name}:"))
        for m in decl.members:
            attrs = "".join(f", {_attr(a)}" for a in m.attrs)
            out.append((m.span.line, m.span.end_line, f"{_INDENT}{m.name}: {m.signal_type}{attrs}"))
        for r in decl.requires:
            out.append((r.span.line, r.span.end_line, f"{_INDENT}require {A.to_src(r.relation)}"))
        for ex in decl.expects:
            out.append((ex.span.line, ex.span.end_line, f"{_INDENT}{_expects_line(ex)}"))
    elif isinstance(decl, A.ComponentDecl):
        head = decl.name + (f"({', '.join(decl.kinds)})" if decl.kinds else "")
        out.append((decl.span.line, decl.span.line, f"component {head}:"))
        for a in decl.attrs:
            out.append((a.span.line, a.span.end_line, f"{_INDENT}{a.name} = {A.to_src(a.value)}"))
        for p in decl.pins:
            attrs = ", ".join(_attr(a) for a in p.attrs)
            out.append((p.span.line, p.span.end_line, f"{_INDENT}pin {p.name}: {attrs}"))
    else:
        out.append((decl.span.line, decl.span.line, f"module {decl.name}:"))
        for item in decl.items:
            out.append((item.span.line, item.span.end_line, f"{_INDENT}{_item_line(item)}"))
    return out[:1] + sorted(out[1:], key=lambda t: t[0])  # body in source order


def format_source(source: str, path: str = "<string>") -> tuple[str, list[Diagnostic]]:
    program, diags = parse(source, path)
    if has_errors(diags):
        return source, diags
    full, trailing = _comments(source)
    lines: list[tuple[int, int, str]] = []
    prev_use = False
    for decl in program.decls:
        is_use = isinstance(decl, A.UseDecl)
        if lines and not (is_use and prev_use):  # consecutive use lines stay adjacent
            lines.append((lines[-1][0], lines[-1][0], ""))  # one blank line between decls
        lines.extend(_decl_lines(decl))
        prev_use = is_use
    out: list[str] = []
    pending = sorted(full)
    for src_line, end_line, text in lines:
        indent = text[: len(text) - len(text.lstrip())]
        while pending and pending[0] <= src_line:
            out.append(indent + full[pending.pop(0)])  # full-line comments keep the item's indent
        tr = [ln for ln in range(src_line, end_line + 1) if ln in trailing]
        if tr and text:  # merge trailing comments from every line of a multiline item
            text = f"{text}  {' '.join(trailing.pop(ln) for ln in tr)}"
        out.append(text)
    out.extend(full[line] for line in pending)  # comments after the last item
    return "\n".join(out) + "\n", diags
