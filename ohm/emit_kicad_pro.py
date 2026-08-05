"""Minimal valid KiCad 7 project (.kicad_pro) — net classes from R7 constraints.

`netclass` constraints become one class each (name = the constraint's string
param) with their nets as membership patterns; `diffpair` constraints share a
"DiffPair" class (400um trace/gap defaults) matching the renamed ``<base>_P`` /
``<base>_N`` nets (see ohm.emit_kicad.diffpair_renames). Deterministic:
json.dumps(sort_keys=True), class list name-sorted after "Default".
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ohm import ir
from ohm.emit_kicad import _uid, diffpair_renames, sheet_groups
from ohm.trace_width import net_currents, trace_width_mm

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


def emit_kicad_pro(low: ir.IRLow, source: str = "<string>",
                   design: ir.Design | None = None) -> str:
    classes: dict[str, dict] = {}
    patterns: list[tuple[str, str]] = []  # (class, net pattern)
    # A class carries the current of the WORST net in it: one rule governs every
    # net it matches, so it has to hold for the heaviest.
    currents = net_currents(low, design)
    class_amps: dict[str, Decimal] = {}
    for c in low.constraints:
        if c.kind != "netclass":
            continue
        name = next((p[1:-1] for p in c.params
                     if len(p) >= 2 and p[0] == '"' and p[-1] == '"'), None)
        if not name:
            continue
        classes.setdefault(name, _class(name))
        patterns += [(name, net) for net in c.nets]
        for net in c.nets:
            if (amps := currents.get(net)) is not None:
                class_amps[name] = max(class_amps.get(name, Decimal(0)), amps)
    # Size the copper. Only ever WIDER than the default — sizing may not quietly
    # relax a rule — and only where the design actually declared a draw, because
    # a width computed from no current is a default wearing a standard's name.
    for name, amps in class_amps.items():
        classes[name]["track_width"] = float(
            max(Decimal(str(classes[name]["track_width"])), trace_width_mm(amps)))
    renames = diffpair_renames(low)
    if renames:
        classes.setdefault("DiffPair", _class(
            "DiffPair", track_width=0.4, diff_pair_width=0.4, diff_pair_gap=0.4))
        patterns += [("DiffPair", renamed) for renamed in sorted(renames.values())]

    # KiCad will not open a hierarchy it cannot find in the project: root first,
    # then one (uuid, name) per module sheet. Empty for a flat single sheet.
    groups = sheet_groups(low)
    sheets = ([[_uid(source, "sch"), ""]]
              + [[_uid(source, "sheetsym", g), g] for g in groups]
              if len(groups) > 1 else [])

    doc = {
        "board": {"3dviewports": [], "design_settings": {}, "layer_presets": []},
        "boards": [],
        # KiCad 7 has no headless ERC (`kicad-cli sch` only exports), so ERC is
        # a GUI run — but it is only worth running against the right severities.
        # The two that matter for a LABEL-connected schematic are raised to
        # error: `similar_labels` catches a typo'd label silently forking a net,
        # and `pin_not_connected` catches a pin nobody accounted for (deliberate
        # opens carry a no-connect marker from source intent, ADR-015).
        "erc": {
            "erc_exclusions": [],
            "meta": {"version": 0},
            "rule_severities": {
                "endpoint_off_grid": "warning",
                "label_dangling": "error",
                "no_connect_connected": "error",
                "no_connect_dangling": "warning",
                "pin_not_connected": "error",
                "pin_not_driven": "error",
                "power_pin_not_driven": "error",
                "similar_labels": "error",
                "wire_dangling": "error",
            },
        },
        "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
        "meta": {"filename": f"{Path(source).stem}.kicad_pro", "version": 1},
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
        "sheets": sheets,
        "text_variables": {},
    }
    return json.dumps(doc, sort_keys=True, indent=2) + "\n"
