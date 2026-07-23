"""P8: P-HOLES — collect `obligation` declarations into Hole records (D2, N2).

Holes are NEVER evaluated in this compiler. They flow to IR-LOW and out through
the constraints seam to the external solver layer. This module only records.
"""

from __future__ import annotations

from ohm import ast_nodes as A
from ohm import ir


def collect_holes(design: ir.Design) -> tuple[ir.Hole, ...]:
    """Obligations of every module instantiation reachable from top, with
    instance-path-prefixed args. Deterministic walk order."""
    mods = {m.name: m for m in design.modules}
    holes: list[ir.Hole] = []

    def walk(module: ir.Module, prefix: tuple[str, ...], stack: frozenset[str]) -> None:
        for ob in module.obligations:
            holes.append(ir.Hole(
                ob.kind,
                tuple(prefix + args for args in ob.args),
                ob.op,
                A.to_src(ob.bound),  # source text, verbatim — never evaluated
            ))
        for inst in module.instances:
            sub = mods.get(inst.def_ref)
            if sub is not None and sub.name not in stack:
                walk(sub, prefix + (inst.name,), stack | {sub.name})

    top = mods.get(design.top) if design.top else None
    if top is not None:
        walk(top, (), frozenset({top.name}))
    return tuple(holes)
