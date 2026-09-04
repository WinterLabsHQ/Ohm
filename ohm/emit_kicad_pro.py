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
from ohm.emit_common import _source_id
from ohm.emit_kicad import _uid, diffpair_renames, sheet_plan
from ohm.formats import (_PRO_ERC_VERSION, _PRO_NET_SETTINGS_VERSION,
                         _PRO_PROJECT_VERSION)
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
                   design: ir.Design | None = None,
                   groups: list[str] | None = None) -> str:
    # Normalise BEFORE anything derives from it. Two things here did: every
    # `_uid(source, ...)` in the `sheets` list below, and `meta.filename`, which
    # takes `Path(source).stem` — and `Path("D:/repo/x.ohm").stem` on POSIX is
    # the whole string, so the project's own declared filename was
    # path-dependent too. `_source_id` is idempotent, so this is safe to apply
    # here as well as in the emitters that already do it.
    source = _source_id(source)
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
    #
    # `groups` comes from the caller when the caller knows what it wrote (the
    # CLI honours --sheets flat/hier/auto), and otherwise from `sheet_plan` —
    # the SAME predicate `emit_kicad_sheets` writes files from. Deciding it
    # again here is what listed three sheets for a design that was emitted as
    # one, naming two .kicad_sch files that do not exist.
    if groups is None:
        groups = sheet_plan(low, source, design)
    sheets = ([[_uid(source, "sch"), ""]]
              + [[_uid(source, "sheetsym", g), g] for g in groups]
              if groups else [])

    doc = {
        "board": {"3dviewports": [], "design_settings": {}, "layer_presets": []},
        "boards": [],
        # KiCad 7 has no headless ERC (`kicad-cli sch` only exports), so on the
        # oldest supported KiCad an ERC is a GUI run; from KiCad 8 `kicad-cli
        # sch erc` exists and Ohm does not yet drive it. Either way the run is
        # only worth doing against the right severities, which is what this sets.
        # The two that matter for a LABEL-connected schematic are raised to
        # error: `similar_labels` catches a typo'd label silently forking a net,
        # and `pin_not_connected` catches a pin nobody accounted for (deliberate
        # opens carry a no-connect marker from source intent, ADR-015).
        "erc": {
            "erc_exclusions": [],
            "meta": {"version": _PRO_ERC_VERSION},
            "rule_severities": {
                "endpoint_off_grid": "warning",
                # One rule, two spellings, both written on purpose: KiCad 10
                # renamed `label_dangling` to `isolated_pin_label`. A .kicad_pro
                # carries no KiCad version and Ohm serves 7 through 10 from ONE
                # set of bytes (ohm.cli.SUPPORTED_KICAD), so the only way a
                # single file can hold this severity on all four is to name the
                # rule the way each of them spells it. Writing only
                # `label_dangling`, which is what Ohm did until now, meant that
                # on KiCad 10 the rule silently reverted to KiCad's default
                # severity: the promise above held on 7/8/9 and quietly did not
                # on the version the README certifies.
                #
                # Believed safe because KiCad's settings loader reads the
                # severities it KNOWS out of this object rather than
                # enumerating the object's members, so a key belonging to
                # another release is not rejected, it is simply never looked at
                # — the same forward/backward tolerance Ohm already leans on to
                # write KiCad 7 files that KiCad 10 reads. NOT verified against
                # a running KiCad: no gate in this repo puts a .kicad_pro in
                # front of kicad-cli (the roundtrip and plot gates hand it a
                # bare .kicad_sch/.kicad_pcb), and no KiCad was installed on the
                # machine this was written on. If some KiCad is ever found to
                # reject an unknown ERC key, this is the line to look at — and
                # the fix is to emit per-target severities, not to drop one.
                "isolated_pin_label": "error",   # KiCad 10 spelling
                "label_dangling": "error",       # KiCad 7/8/9 spelling
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
        "meta": {"filename": f"{Path(source).stem}.kicad_pro",
                 "version": _PRO_PROJECT_VERSION},
        "net_settings": {
            "classes": [_class("Default")]
            + [classes[k] for k in sorted(classes)],
            "meta": {"version": _PRO_NET_SETTINGS_VERSION},
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
