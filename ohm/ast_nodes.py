"""Typed AST — faithful tree with spans (compiler design P-AST output)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ohm.diagnostics import Span


# ---------- expressions ----------
@dataclass(frozen=True)
class Ref:
    parts: tuple[str, ...]  # e.g. ("reg", "VIN", "vmax")
    span: Span

    def dotted(self) -> str:
        return ".".join(self.parts)


@dataclass(frozen=True)
class Num:
    value: Decimal
    span: Span


@dataclass(frozen=True)
class Str:
    value: str
    span: Span


@dataclass(frozen=True)
class Tolerance:
    value: Decimal
    unit: str | None  # None => percent


@dataclass(frozen=True)
class QuantityLit:
    value: Decimal
    unit: str  # raw unit text; validated/normalized by units.py (P3)
    tol: Tolerance | None
    span: Span


@dataclass(frozen=True)
class BinOp:
    op: str  # + - * / <= >= < > == !=
    left: "Expr"
    right: "Expr"
    span: Span


@dataclass(frozen=True)
class Call:
    callee: Ref
    args: tuple["Expr", ...]
    kwargs: tuple[tuple[str, "Expr"], ...]
    span: Span


Expr = Ref | Num | Str | QuantityLit | BinOp | Call


# ---------- declarations ----------
@dataclass(frozen=True)
class Attr:
    name: str
    value: Expr
    span: Span


@dataclass(frozen=True)
class Member:
    name: str
    signal_type: str
    attrs: tuple[Attr, ...]  # e.g. role=bidirectional
    span: Span


@dataclass(frozen=True)
class Require:
    relation: Call
    span: Span


@dataclass(frozen=True)
class InterfaceDecl:
    name: str
    members: tuple[Member, ...]
    requires: tuple[Require, ...]
    span: Span


@dataclass(frozen=True)
class PinDecl:
    name: str
    attrs: tuple[Attr, ...]  # role/pad/vmax/imax/pmax
    span: Span


@dataclass(frozen=True)
class ComponentDecl:
    name: str
    attrs: tuple[Attr, ...]  # package/mpn/...
    pins: tuple[PinDecl, ...]
    span: Span


@dataclass(frozen=True)
class Binding:
    name: str
    call: Call  # io(...), config(...), or Component/Module instantiation
    span: Span


@dataclass(frozen=True)
class Connect:
    left: Ref
    right: Ref
    span: Span


@dataclass(frozen=True)
class AssertStmt:
    expr: Expr
    span: Span


@dataclass(frozen=True)
class ObligationStmt:
    call: Call
    op: str
    bound: Expr
    span: Span


ModuleItem = Binding | Connect | AssertStmt | ObligationStmt


@dataclass(frozen=True)
class ModuleDecl:
    name: str
    items: tuple[ModuleItem, ...]
    span: Span


Decl = InterfaceDecl | ComponentDecl | ModuleDecl


@dataclass(frozen=True)
class Program:
    decls: tuple[Decl, ...] = field(default=())
