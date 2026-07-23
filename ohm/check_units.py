"""P3: P-UNITS annotation pass + Tier-U dimensional checker.

annotate_units: pure (Design) -> (Design, diagnostics) — normalizes every
Quantity's tolerance into absolute bounds; never mutates its input.
check_units: dimensional consistency of assert expressions (U codes).
"""

from __future__ import annotations

import dataclasses

from ohm import ast_nodes as A
from ohm import ir, units
from ohm.diagnostics import Diagnostic, Span

_NO_SPAN = Span(1, 1, 1, 1)

# known quantity-valued property names on refs (heuristic; richer typing in P9)
_PROP_DIMS = {
    "voltage": "voltage",
    "current": "current",
    "vmax": "voltage",
    "imax": "current",
    "pmax": "power",
}


def annotate_units(design: ir.Design, path: str = "<string>") -> tuple[ir.Design, list[Diagnostic]]:
    diags: list[Diagnostic] = []

    def rebuild(obj, span: Span):
        if isinstance(obj, ir.Quantity):
            q, err = units.normalize(obj)
            if err:
                diags.append(Diagnostic("error", "U003" if "unknown" in err else "U004",
                                        err, span, file=path))
            return q
        if isinstance(obj, A.Expr):
            return obj  # AST expr payloads (asserts/bounds) handled by check_units, not here
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            own_span = getattr(obj, "span", None) or span
            changes = {f.name: rebuild(getattr(obj, f.name), own_span)
                       for f in dataclasses.fields(obj) if f.name != "span"}
            return dataclasses.replace(obj, **changes)
        if isinstance(obj, tuple):
            return tuple(rebuild(x, span) for x in obj)
        return obj

    return rebuild(design, _NO_SPAN), diags


def _expr_dim(expr: A.Expr, diags: list[Diagnostic], path: str) -> str | None:
    match expr:
        case A.QuantityLit():
            d = units.dim(expr.unit)
            if d is None:
                diags.append(Diagnostic("error", "U003", f"unknown unit '{expr.unit}'",
                                        expr.span, file=path))
            return d
        case A.Num():
            return units.DIMENSIONLESS
        case A.Ref():
            return _PROP_DIMS.get(expr.parts[-1])
        case A.BinOp():
            left = _expr_dim(expr.left, diags, path)
            right = _expr_dim(expr.right, diags, path)
            if expr.op in ("+", "-"):
                if left and right and left != right:
                    diags.append(Diagnostic(
                        "error", "U001", f"cannot {'add' if expr.op == '+' else 'subtract'} "
                        f"{left} and {right}", expr.span, file=path))
                    return None
                return left or right
            if expr.op == "*":
                return units.mul_dim(left, right)
            if expr.op == "/":
                return units.div_dim(left, right)
            # comparison
            if left and right and left != right:
                diags.append(Diagnostic(
                    "error", "U002", f"cannot compare {left} with {right}", expr.span, file=path))
            return "bool"
        case _:
            return None  # Call/Str: typed later (P8 closed-form signatures)


def check_units(design: ir.Design, path: str = "<string>") -> list[Diagnostic]:
    diags: list[Diagnostic] = []
    for module in design.modules:
        for a in module.asserts:
            _expr_dim(a.expr, diags, path)
        for o in module.obligations:
            _expr_dim(o.bound, diags, path)
    return diags
