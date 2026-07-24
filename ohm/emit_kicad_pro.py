"""Minimal valid KiCad 7 project (.kicad_pro) — net classes from R7 constraints.

`netclass` constraints become one class each (name = the constraint's string
param) with their nets as membership patterns; `diffpair` constraints share a
"DiffPair" class (400um trace/gap defaults) matching the renamed ``<base>_P`` /
``<base>_N`` nets (see ohm.emit_kicad.diffpair_renames). Deterministic:
json.dumps(sort_keys=True), class list name-sorted after "Default".
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from ohm import ir
from ohm.emit_kicad import diffpair_renames

_DEFAULTS = {
    "bus_width": 12,
    "clearance": 0.2,
    "diff_pair_gap": 0.25,
    "diff_pair_via_gap": 0.25,
    "diff_pair_width": 0.2,
    "line_style": 0,
    "microvia_diameter": 0.3,
    "microvia_drill": 0.1,
    "pcb_color": "rgba(0, 0, 0, 0.000)",
    "schematic_color": "rgba(0, 0, 0, 0.000)",
    "track_width": 0.25,
    "via_diameter": 0.6,
    "via_drill": 0.3,
    "wire_width": 6,
}


def _class(name: str, **over) -> dict:
    return {"name": name, **_DEFAULTS, **over}


def emit_kicad_pro(low: ir.IRLow, source: str = "<string>") -> str:
    classes: dict[str, dict] = {}
    patterns: list[tuple[str, str]] = []  # (class, net pattern)
    for c in low.constraints:
        if c.kind != "netclass":
            continue
        name = next((p[1:-1] for p in c.params
                     if len(p) >= 2 and p[0] == '"' and p[-1] == '"'), None)
        if not name:
            continue
        classes.setdefault(name, _class(name))
        patterns += [(name, net) for net in c.nets]
    renames = diffpair_renames(low)
    if renames:
        classes.setdefault("DiffPair", _class(
            "DiffPair", track_width=0.4, diff_pair_width=0.4, diff_pair_gap=0.4))
        patterns += [("DiffPair", renamed) for renamed in sorted(renames.values())]

    doc = {
        "board": {"3dviewports": [], "design_settings": {}, "layer_presets": []},
        "boards": [],
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{PurePosixPath(source).stem}.kicad_pro", "version": 1},
        "net_settings": {
            "classes": [_class("Default")]
            + [classes[k] for k in sorted(classes)],
            "meta": {"version": 3},
            "net_colors": None,
            "netclass_assignments": None,
            "netclass_patterns": [
                {"netclass": cls, "pattern": pat} for cls, pat in patterns
            ],
        },
        "pcbnew": {"page_layout_descr_file": ""},
        "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
        "sheets": [],
        "text_variables": {},
    }
    return json.dumps(doc, sort_keys=True, indent=2) + "\n"
