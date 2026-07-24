"""The single seam between the open compiler and the proprietary platform
(manual Part 8). Emitted by Ohm (`emit_constraints.py`); consumed by Meridian
and the physics solver layer. Pure data — no computation on either side of the
boundary lives here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

SCHEMA_VERSION = "1"

# R7 constraint kinds (extensible; unknown kinds warn at compile, R009)
CONSTRAINT_KINDS = {"impedance", "length", "diffpair", "netclass", "keepout",
                    "fixed", "near", "edge"}

# kinds whose refs are component INSTANCES, not net endpoints; lowered
# ConstraintLow.nets holds flat part refs for these
PLACEMENT_KINDS = {"fixed", "near", "edge"}


@dataclass(frozen=True)
class ConstraintEntry:
    kind: str
    nets: tuple[str, ...]  # flat net names (IR-LOW); part refs for PLACEMENT_KINDS
    params: tuple[str, ...]  # canonical source strings, e.g. "50ohm"


@dataclass(frozen=True)
class PlacementEntry:
    """One placed part — the DSN-shaped triple routers expect."""

    ref: str  # flat part ref
    x_mm: str  # canonical decimal strings, mm / degrees (pure data, no floats)
    y_mm: str
    rot: str
    side: str  # "top" | "bottom"
    fixed: bool


@dataclass(frozen=True)
class GroupEntry:
    name: str  # module path ("root" for top-level parts)
    members: tuple[str, ...]  # flat part refs


@dataclass(frozen=True)
class HoleEntry:
    """A convergence obligation — recorded by Ohm, solved downstream, never in
    the compiler (D2/N2)."""

    kind: str
    args: tuple[str, ...]  # dotted instance paths
    op: str
    bound: str  # canonical source string, verbatim


@dataclass(frozen=True)
class ConstraintSet:
    version: str
    constraints: tuple[ConstraintEntry, ...]
    holes: tuple[HoleEntry, ...]
    placement: tuple[PlacementEntry, ...] = ()
    groups: tuple[GroupEntry, ...] = ()


def to_json(cs: ConstraintSet) -> str:
    return json.dumps(asdict(cs), sort_keys=True, indent=1)
