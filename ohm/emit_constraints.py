"""P10: constraint-schema emitter — IR-LOW → shared ConstraintSet JSON.

The one seam to Meridian and the solver layer (manual Part 8): R7 constraints
plus the recorded Holes, plus (Milestone F) placement poses from `fixed`
constraints and module-prefix part groups — the DSN-shaped triple routers
expect. Net names stay canonical here (diffpair renaming is a KiCad-file-only
concern). `near`/`edge` pairs ride in the plain constraints list.
"""

from __future__ import annotations

from ohm import ir
from ohm.emit_kicad import _group_of, fixed_poses
from shared.constraints_schema import (SCHEMA_VERSION, ConstraintEntry,
                                       ConstraintSet, GroupEntry, HoleEntry,
                                       PlacementEntry, to_json)


def emit_constraints(low: ir.IRLow, source: str = "<string>") -> str:
    constraints = tuple(
        ConstraintEntry(c.kind, c.nets, c.params) for c in low.constraints
    )
    holes = tuple(
        HoleEntry(h.kind, tuple(".".join(a) for a in h.args), h.op, h.bound_repr)
        for h in low.holes
    )
    placement = tuple(
        PlacementEntry(ref, str(x), str(y), str(rot), side, True)
        for ref, (x, y, rot, side) in sorted(fixed_poses(low).items())
    )
    by_group: dict[str, list[str]] = {}
    for p in low.parts:
        by_group.setdefault(_group_of(p.ref), []).append(p.ref)
    groups = tuple(
        GroupEntry(name, tuple(sorted(by_group[name]))) for name in sorted(by_group)
    )
    return to_json(ConstraintSet(SCHEMA_VERSION, constraints, holes, placement, groups))
