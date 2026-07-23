"""P10: constraint-schema emitter — IR-LOW → shared ConstraintSet JSON.

The one seam to Meridian and the solver layer (manual Part 8): R7 constraints
plus the recorded Holes. Ohm records; it never routes and never solves.
"""

from __future__ import annotations

from ohm import ir
from shared.constraints_schema import (SCHEMA_VERSION, ConstraintEntry,
                                       ConstraintSet, HoleEntry, to_json)


def emit_constraints(low: ir.IRLow, source: str = "<string>") -> str:
    constraints = tuple(
        ConstraintEntry(c.kind, c.nets, c.params) for c in low.constraints
    )
    holes = tuple(
        HoleEntry(h.kind, tuple(".".join(a) for a in h.args), h.op, h.bound_repr)
        for h in low.holes
    )
    return to_json(ConstraintSet(SCHEMA_VERSION, constraints, holes))
