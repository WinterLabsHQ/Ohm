"""The IR — the crown asset (manual Part 4). Typed, serializable, deterministic.

IR-HIGH is produced only by the resolver; checkers READ it and emit diagnostics;
emitters READ it and never mutate. Quantities keep a raw tolerance at IR-HIGH;
P-UNITS (P3) normalizes tol_lo/tol_hi in the value's own unit.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from decimal import Decimal

from ohm import ast_nodes as A
from ohm.diagnostics import Span


@dataclass(frozen=True)
class Quantity:
    value: Decimal
    unit: str
    tol_lo: Decimal | None = None  # absolute bound, same unit as value (set by P-UNITS)
    tol_hi: Decimal | None = None
    tol_raw: tuple[Decimal, str | None] | None = None  # (value, unit|None=percent)


@dataclass(frozen=True)
class Pin:
    name: str
    role: str
    pad: str | None = None
    vmax: Quantity | None = None
    imax: Quantity | None = None
    pmax: Quantity | None = None
    voltage: Quantity | None = None  # declared operating point (ADR-004)
    current: Quantity | None = None  # declared draw (ADR-004)
    satisfies: str | None = None  # kind-member this pin binds via `is=` (ADR-008)
    span: Span | None = None
    must_connect: bool = False  # pin may not be left netless (ADR-009)
    tolerant_to: Quantity | None = None  # abs-max input for domain checks (ADR-009)


@dataclass(frozen=True)
class GenerateSpec:
    """`package = generate(...)` frozen as data; elaborated to geometry in P11."""

    package_type: str
    kwargs: tuple[tuple[str, "Value"], ...]


Value = str | Decimal | Quantity | GenerateSpec


@dataclass(frozen=True)
class ComponentDef:
    name: str
    package: Value | None
    pins: tuple[Pin, ...]
    attrs: tuple[tuple[str, Value], ...]
    padmap: tuple[tuple[str, str], ...]  # pin name -> pad id (Tier-S loop)
    kinds: tuple[str, ...] = ()  # declared component-type conformance (ADR-008)
    span: Span | None = None


@dataclass(frozen=True)
class Relation:
    kind: str  # e.g. "pair"
    members: tuple[str, ...]


@dataclass(frozen=True)
class ExpectationDef:
    """Bring-up requirement predicate (ADR-009): recorded on kinds/interfaces,
    verified by check_bringup over the flat nets."""

    pattern: str  # closed v1 set: pullup, decoupling, loadcaps, cap, termination, tie
    args: tuple["Value", ...]
    members: tuple[str, ...]
    when: str | None  # instance-attr gate (e.g. "endpoint")
    span: Span | None = None


@dataclass(frozen=True)
class InterfaceDef:
    name: str
    members: tuple[tuple[str, str, str | None], ...]  # (name, signal_type, role|None)
    requires: tuple[Relation, ...]
    span: Span | None = None
    expects: tuple[ExpectationDef, ...] = ()  # ADR-009


@dataclass(frozen=True)
class Port:
    name: str
    interface: str
    span: Span | None = None


@dataclass(frozen=True)
class Config:
    name: str
    type_name: str
    default: Value | None
    span: Span | None = None


@dataclass(frozen=True)
class Instance:
    name: str
    def_ref: str  # component or module name
    overrides: tuple[tuple[str, Value], ...]
    span: Span | None = None
    waivers: tuple[str, ...] = ()  # suppressed expectation codes/patterns (ADR-009)


@dataclass(frozen=True)
class Connection:
    left: tuple[str, ...]
    right: tuple[str, ...]
    span: Span | None = None


@dataclass(frozen=True)
class AssertRec:
    expr: A.Expr  # closed-form; folded in P8
    span: Span | None = None


@dataclass(frozen=True)
class ObligationRec:
    kind: str  # e.g. "ripple" — becomes a Hole (P8); NEVER evaluated in-compiler
    args: tuple[tuple[str, ...], ...]
    op: str
    bound: A.Expr
    span: Span | None = None


@dataclass(frozen=True)
class Constraint:
    """R7 constraint (ADR-005): recorded for the Meridian/solver seam, never solved."""

    kind: str
    ref_args: tuple[tuple[str, ...], ...]  # dotted refs (net endpoints)
    params: tuple[str, ...]  # canonical source strings of value args
    span: Span | None = None


@dataclass(frozen=True)
class Module:
    name: str
    configs: tuple[Config, ...]
    ports: tuple[Port, ...]
    instances: tuple[Instance, ...]
    connections: tuple[Connection, ...]
    asserts: tuple[AssertRec, ...]
    obligations: tuple[ObligationRec, ...]
    constraints: tuple[Constraint, ...] = ()
    span: Span | None = None


@dataclass(frozen=True)
class KindDef:
    """Component-type contract (ADR-008): required/optional pins-by-role + typed params."""

    name: str
    parents: tuple[str, ...]
    required: tuple[tuple[str, str], ...]  # (member name, role)
    optional: tuple[tuple[str, str], ...]
    params: tuple[tuple[str, str], ...]  # (param name, dimension)
    span: Span | None = None
    expects: tuple[ExpectationDef, ...] = ()  # ADR-009


@dataclass(frozen=True)
class FnDef:
    """A pure compile-time function (ADR-012 E0) — body kept as AST, folded by
    the closed-form engine wherever expressions fold."""

    name: str
    params: tuple[str, ...]
    expr: A.Expr  # folded like AssertRec.expr
    span: Span | None = None


@dataclass(frozen=True)
class Design:
    interfaces: tuple[InterfaceDef, ...]
    components: tuple[ComponentDef, ...]
    modules: tuple[Module, ...]
    top: str | None  # ponytail: top = last-declared module; --top flag when needed
    kinds: tuple[KindDef, ...] = ()  # component-type taxonomy (ADR-008)
    fns: tuple[FnDef, ...] = ()  # pure compile-time functions (ADR-012 E0)


# ---------- IR-LOW (flat, emitter-ready; produced by P-LOWER) ----------
@dataclass(frozen=True)
class PartLow:
    ref: str  # hierarchical path, e.g. "reg" or "sensor.u1" (deterministic uniquification)
    designator: str  # e.g. "U1"
    component: str
    package: Value | None
    attrs: tuple[tuple[str, Value], ...]


@dataclass(frozen=True)
class NetNode:
    part_ref: str
    designator: str
    pin: str
    pad: str | None  # netlist pin number = pad if mapped, else pin name


@dataclass(frozen=True)
class NetLow:
    name: str
    nodes: tuple[NetNode, ...]
    ports: tuple[str, ...]  # external port-member labels on this net
    conn_spans: tuple[Span, ...] = ()  # source `~` connections that formed this net


@dataclass(frozen=True)
class Hole:
    kind: str
    args: tuple[tuple[str, ...], ...]
    op: str
    bound_repr: str


@dataclass(frozen=True)
class ConstraintLow:
    kind: str
    nets: tuple[str, ...]  # flat net names
    params: tuple[str, ...]


@dataclass(frozen=True)
class IRLow:
    parts: tuple[PartLow, ...]
    nets: tuple[NetLow, ...]
    constraints: tuple[ConstraintLow, ...] = ()  # R7 entries (P10)
    holes: tuple[Hole, ...] = ()  # obligations → solver seam (P8)


def _jsonable(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        d = {"__type__": type(obj).__name__}
        for f in dataclasses.fields(obj):
            d[f.name] = _jsonable(getattr(obj, f.name))
        return d
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (tuple, list)):
        return [_jsonable(x) for x in obj]
    return obj


def to_json(node: Design | IRLow) -> str:
    """Deterministic serialization: same input ⇒ byte-identical JSON."""
    return json.dumps(_jsonable(node), sort_keys=True, indent=1)
