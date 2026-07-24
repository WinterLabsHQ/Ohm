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
class Expectation:
    """`expects <pattern>[(args)] [on members] [when attr]` — a bring-up
    requirement predicate over the bound members' nets (ADR-009)."""

    pattern: str
    args: tuple[Expr, ...]
    members: tuple[str, ...]
    when: str | None
    span: Span


@dataclass(frozen=True)
class InterfaceDecl:
    name: str
    members: tuple[Member, ...]
    requires: tuple[Require, ...]
    span: Span
    expects: tuple[Expectation, ...] = ()  # bring-up predicates (ADR-009)
    file: str | None = None  # stamped by the loader (ADR-007)


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
    kinds: tuple[str, ...] = ()  # declared component-type conformance (ADR-008)
    file: str | None = None


@dataclass(frozen=True)
class Binding:
    name: str
    call: Call  # io(...), config(...), or Component/Module instantiation
    span: Span


@dataclass(frozen=True)
class ArrayBinding:
    """`leds = 8 * LED(...)` — unrolled to leds[0..N-1] at elaborate (ADR-010)."""

    name: str
    count: Decimal
    call: Call
    span: Span


@dataclass(frozen=True)
class ForStmt:
    """`for i in 0..7:` — bounded, unrolled at elaborate (ADR-010)."""

    var: str
    lo: Decimal
    hi: Decimal
    items: tuple["ModuleItem", ...]  # connects/asserts/constraints only
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


@dataclass(frozen=True)
class ConstraintStmt:
    call: Call  # e.g. impedance(mcu.bus.scl, 50ohm) — recorded, never solved
    span: Span


ModuleItem = Binding | ArrayBinding | ForStmt | Connect | AssertStmt | ObligationStmt | ConstraintStmt


@dataclass(frozen=True)
class ModuleDecl:
    name: str
    items: tuple[ModuleItem, ...]
    span: Span
    file: str | None = None


@dataclass(frozen=True)
class UseDecl:
    """`use std.passives` / `use "./mylib.ohm"` — resolved by the loader (ADR-007)."""

    target: str
    is_path: bool  # True for the quoted-string form
    span: Span
    file: str | None = None


@dataclass(frozen=True)
class KindMember:
    name: str
    role: str
    span: Span


@dataclass(frozen=True)
class KindParam:
    name: str
    dimension: str
    span: Span


@dataclass(frozen=True)
class KindDecl:
    """A component-type contract (ADR-008): required/optional pins-by-role + params."""

    name: str
    parents: tuple[str, ...]
    requires: tuple[KindMember, ...]
    optional: tuple[KindMember, ...]
    params: tuple[KindParam, ...]
    span: Span
    expects: tuple[Expectation, ...] = ()  # bring-up predicates (ADR-009)
    file: str | None = None


Decl = InterfaceDecl | ComponentDecl | ModuleDecl | UseDecl | KindDecl


def to_src(e: Expr) -> str:
    """Canonical source rendering of an expression (deterministic)."""
    match e:
        case Ref():
            return e.dotted()
        case Num():
            return str(e.value)
        case Str():
            return f'"{e.value}"'
        case QuantityLit():
            tol = ""
            if e.tol is not None:
                tol = f" +/- {e.tol.value}{e.tol.unit or '%'}"
            return f"{e.value}{e.unit}{tol}"
        case BinOp():
            left = f"({to_src(e.left)})" if isinstance(e.left, BinOp) else to_src(e.left)
            right = f"({to_src(e.right)})" if isinstance(e.right, BinOp) else to_src(e.right)
            return f"{left} {e.op} {right}"
        case Call():
            args = [to_src(a) for a in e.args] + [f"{k}={to_src(v)}" for k, v in e.kwargs]
            return f"{e.callee.dotted()}({', '.join(args)})"


@dataclass(frozen=True)
class Program:
    decls: tuple[Decl, ...] = field(default=())
