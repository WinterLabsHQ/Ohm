"""P8: P-CLOSEDFORM — pure closed-form physics + interval expression evaluation
and assert folding (D2, N2).

Everything here is deterministic and terminates in a fixed number of steps:
algebraic formulas over Decimal intervals, plus float transcendentals
(microstrip) rounded to fixed precision before they re-enter Decimal world.
NEVER iterative/convergent — that is a Hole (holes.py), delegated to the
external solver layer and not evaluated in this compiler.

Assert folding is worst-case over tolerance intervals: an assert passes only if
it holds across the whole interval; certain violation and possible violation
are both build errors (C001/C002); an assert the build cannot evaluate is
surfaced honestly as info C003 (D3: we check exactly what is declared).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from ohm import ast_nodes as A
from ohm import ir, units
from ohm.diagnostics import Diagnostic
from ohm.units import DIMENSIONLESS, Interval

_Q4 = Decimal("0.0001")


@dataclass(frozen=True)
class Qty:
    """An interval value in the BASE unit of its dimension."""

    iv: Interval
    dim: str


def quantity_qty(q: ir.Quantity) -> Qty | None:
    d = units.dim(q.unit)
    if d is None:
        return None
    factor = units.UNITS[q.unit][1]
    iv = units.interval_of(q)
    return Qty(Interval(iv.lo * factor, iv.hi * factor), d)


def value_qty(v: ir.Value | None) -> Qty | None:
    if isinstance(v, ir.Quantity):
        return quantity_qty(v)
    if isinstance(v, Decimal):
        return Qty(Interval(v, v), DIMENSIONLESS)
    return None


# ---------- pure closed-form functions (the catalog) ----------
def _mid(q: Qty) -> float:
    return float((q.iv.lo + q.iv.hi) / 2)


def divider(vin: Qty, r_top: Qty, r_bottom: Qty) -> Qty | None:
    """Voltage divider: vout = vin * r_bottom / (r_top + r_bottom).

    Exact for independent inputs via monotonicity (vout rises with r_bottom,
    falls with r_top) — naive interval arithmetic would decorrelate r_bottom
    between numerator and denominator and overestimate the range.
    """
    if (vin.dim, r_top.dim, r_bottom.dim) != ("voltage", "resistance", "resistance"):
        return None
    if r_top.iv.lo <= 0 or r_bottom.iv.lo <= 0:
        return None
    if vin.iv.lo < 0:  # ponytail: negative rails fall back to conservative intervals
        return Qty(vin.iv * r_bottom.iv / (r_top.iv + r_bottom.iv), "voltage")
    lo = vin.iv.lo * r_bottom.iv.lo / (r_top.iv.hi + r_bottom.iv.lo)
    hi = vin.iv.hi * r_bottom.iv.hi / (r_top.iv.lo + r_bottom.iv.hi)
    return Qty(Interval(lo, hi), "voltage")


def series(*rs: Qty) -> Qty | None:
    if not rs or any(r.dim != rs[0].dim for r in rs):
        return None
    total = rs[0].iv
    for r in rs[1:]:
        total = total + r.iv
    return Qty(total, rs[0].dim)


def parallel(*rs: Qty) -> Qty | None:
    if not rs or any(r.dim != "resistance" for r in rs) or any(r.iv.lo <= 0 for r in rs):
        return None
    one = Interval(Decimal(1), Decimal(1))
    inv_sum = one / rs[0].iv
    for r in rs[1:]:
        inv_sum = inv_sum + one / r.iv
    return Qty(one / inv_sum, "resistance")


def p_vi(v: Qty, i: Qty) -> Qty | None:
    if (v.dim, i.dim) != ("voltage", "current"):
        return None
    return Qty(v.iv * i.iv, "power")


def p_i2r(i: Qty, r: Qty) -> Qty | None:
    if (i.dim, r.dim) != ("current", "resistance"):
        return None
    return Qty(i.iv * i.iv * r.iv, "power")


def p_v2r(v: Qty, r: Qty) -> Qty | None:
    if (v.dim, r.dim) != ("voltage", "resistance") or r.iv.lo <= 0:
        return None
    return Qty(v.iv * v.iv / r.iv, "power")


def rc_tau(r: Qty, c: Qty) -> Qty | None:
    if (r.dim, c.dim) != ("resistance", "capacitance"):
        return None
    return Qty(r.iv * c.iv, "time")


def thermal_rise(p: Qty, theta_ja: Qty) -> Qty | None:
    """Coarse thermal rise: ΔT = P × θJA (θJA supplied as a plain number, degC/W)."""
    if p.dim != "power" or theta_ja.dim != DIMENSIONLESS:
        return None
    return Qty(p.iv * theta_ja.iv, "temperature")


def microstrip_z0(w: Qty, h: Qty, er: Qty) -> Qty | None:
    """Hammerstad microstrip impedance. Float transcendentals, then rounded to
    4 decimal places so the value is identical across machines (design §6.4)."""
    if w.dim != "length" or h.dim != "length" or er.dim != DIMENSIONLESS:
        return None
    u = _mid(w) / _mid(h)
    e = _mid(er)
    if u <= 0 or e < 1:
        return None
    eff = (e + 1) / 2 + (e - 1) / 2 * ((1 + 12 / u) ** -0.5 + (0.04 * (1 - u) ** 2 if u <= 1 else 0.0))
    if u <= 1:
        z0 = 60 / math.sqrt(eff) * math.log(8 / u + u / 4)
    else:
        z0 = 376.730313668 / (math.sqrt(eff) * (u + 1.393 + 0.667 * math.log(u + 1.444)))
    z = Decimal(repr(z0)).quantize(_Q4)
    return Qty(Interval(z, z), "resistance")


FUNCTIONS = {
    "divider": divider,
    "series": series,
    "parallel": parallel,
    "p_vi": p_vi,
    "p_i2r": p_i2r,
    "p_v2r": p_v2r,
    "rc_tau": rc_tau,
    "thermal_rise": thermal_rise,
    "microstrip_z0": microstrip_z0,
}


# ---------- expression evaluation over the design ----------
_PIN_PROPS = ("vmax", "imax", "pmax", "voltage", "current")


class Env:
    """Resolves refs inside one module instantiation context. Read-only."""

    def __init__(self, design: ir.Design, module: ir.Module, configs: dict[str, Qty | None]):
        self.comps = {c.name: c for c in design.components}
        self.instances = {i.name: i for i in module.instances}
        self.configs = configs

    def resolve(self, ref: A.Ref) -> Qty | None:
        parts = ref.parts
        if len(parts) == 1:
            return self.configs.get(parts[0])
        inst = self.instances.get(parts[0])
        comp = self.comps.get(inst.def_ref) if inst is not None else None
        if comp is None:
            return None
        if len(parts) == 2:  # inst.attr — override wins, else component attr
            for k, v in inst.overrides:
                if k == parts[1]:
                    return value_qty(v)
            return value_qty(dict(comp.attrs).get(parts[1]))
        if len(parts) == 3 and parts[2] in _PIN_PROPS:  # inst.PIN.rating
            pin = next((p for p in comp.pins if p.name == parts[1]), None)
            q = getattr(pin, parts[2], None) if pin is not None else None
            return quantity_qty(q) if q is not None else None
        return None


def eval_expr(expr: A.Expr, env: Env) -> Qty | None:
    match expr:
        case A.QuantityLit():
            tol = (expr.tol.value, expr.tol.unit) if expr.tol else None
            q, err = units.normalize(ir.Quantity(expr.value, expr.unit, tol_raw=tol))
            return quantity_qty(q) if err is None else None
        case A.Num():
            return Qty(Interval(expr.value, expr.value), DIMENSIONLESS)
        case A.Ref():
            return env.resolve(expr)
        case A.Call():
            fn = FUNCTIONS.get(expr.callee.dotted())
            if fn is None or expr.kwargs:
                return None
            args = [eval_expr(a, env) for a in expr.args]
            if any(a is None for a in args):
                return None
            try:
                return fn(*args)
            except TypeError:  # wrong arity → unevaluable, surfaces as C003
                return None
        case A.BinOp() if expr.op in ("+", "-", "*", "/"):
            left, right = eval_expr(expr.left, env), eval_expr(expr.right, env)
            if left is None or right is None:
                return None
            if expr.op in ("+", "-"):
                if left.dim != right.dim:
                    return None  # Tier-U reports the mismatch
                iv = left.iv + right.iv if expr.op == "+" else left.iv - right.iv
                return Qty(iv, left.dim)
            dim = units.mul_dim(left.dim, right.dim) if expr.op == "*" else units.div_dim(left.dim, right.dim)
            if dim is None:
                return None
            try:
                iv = left.iv * right.iv if expr.op == "*" else left.iv / right.iv
            except ZeroDivisionError:
                return None
            return Qty(iv, dim)
        case _:
            return None


def fold_compare(op: str, a: Interval, b: Interval) -> str:
    """'holds' | 'fails' | 'may' — worst-case interval comparison."""
    table = {
        "<=": (a.hi <= b.lo, a.lo > b.hi),
        "<": (a.hi < b.lo, a.lo >= b.hi),
        ">=": (a.lo >= b.hi, a.hi < b.lo),
        ">": (a.lo > b.hi, a.hi <= b.lo),
        "==": (a.lo == a.hi == b.lo == b.hi, a.hi < b.lo or a.lo > b.hi),
        "!=": (a.hi < b.lo or a.lo > b.hi, a.lo == a.hi == b.lo == b.hi),
    }
    certain_true, certain_false = table[op]
    return "holds" if certain_true else ("fails" if certain_false else "may")


def _rng(q: Qty) -> str:
    if q.iv.lo == q.iv.hi:
        return f"{q.iv.lo} {q.dim}"
    return f"[{q.iv.lo}, {q.iv.hi}] {q.dim}"


def fold_asserts(design: ir.Design, path: str = "<string>") -> list[Diagnostic]:
    """Fold every assert in every reachable instantiation context (worst case).

    Modules not reachable from the top are folded once with config defaults.
    """
    diags: list[Diagnostic] = []
    mods = {m.name: m for m in design.modules}
    folded_at_least_once: set[str] = set()

    def config_bindings(module: ir.Module, overrides: dict[str, Qty | None]) -> dict[str, Qty | None]:
        return {
            cfg.name: overrides.get(cfg.name, value_qty(cfg.default) if cfg.default is not None else None)
            for cfg in module.configs
        }

    def walk(module: ir.Module, overrides: dict[str, Qty | None], label: str, stack: frozenset[str]) -> None:
        folded_at_least_once.add(module.name)
        env = Env(design, module, config_bindings(module, overrides))
        where = f" (in {label.rstrip('.')})" if label else ""
        for a in module.asserts:
            if not isinstance(a.expr, A.BinOp) or a.expr.op in ("+", "-", "*", "/"):
                diags.append(Diagnostic(
                    "error", "C004",
                    f"assert must be a comparison (<, <=, >, >=, ==, !=){where}: {A.to_src(a.expr)}",
                    a.span, file=path))
                continue
            left, right = eval_expr(a.expr.left, env), eval_expr(a.expr.right, env)
            if left is None or right is None or left.dim != right.dim:
                diags.append(Diagnostic(
                    "info", "C003",
                    f"assert not evaluable at build{where}: {A.to_src(a.expr)}",
                    a.span, file=path,
                    help="declare the referenced values (config default, component attr, or rating) to make this checkable"))
                continue
            verdict = fold_compare(a.expr.op, left.iv, right.iv)
            if verdict == "fails":
                diags.append(Diagnostic(
                    "error", "C001",
                    f"assert is false{where}: {A.to_src(a.expr)} — left is {_rng(left)}, right is {_rng(right)}",
                    a.span, file=path))
            elif verdict == "may":
                diags.append(Diagnostic(
                    "error", "C002",
                    f"assert may fail over tolerances{where}: {A.to_src(a.expr)} — left is {_rng(left)}, right is {_rng(right)}",
                    a.span, file=path,
                    help="worst-case interval overlaps the bound; tighten tolerances or the bound"))
        for inst in module.instances:
            sub = mods.get(inst.def_ref)
            if sub is not None and sub.name not in stack:  # cycle guard
                ov = {k: value_qty(v) for k, v in inst.overrides}
                walk(sub, ov, f"{label}{inst.name}.", stack | {sub.name})

    top = mods.get(design.top) if design.top else None
    if top is not None:
        walk(top, {}, "", frozenset({top.name}))
    for m in design.modules:
        if m.name not in folded_at_least_once:
            walk(m, {}, "", frozenset({m.name}))
    return diags
