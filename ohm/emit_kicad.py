"""KiCad emitters — valid .kicad_sch / .kicad_pcb, format pinned to KiCad 7.

The schematic carries the design as global labels + text notes; the board now
carries real footprints (Milestone F Part D):

* generated packages (``package = generate(...)``) embed the full P11 footprint
  geometry with pads bound to board nets;
* string-footprint parts embed a pad-less STUB footprint (correct LibId,
  position, ``ohm_path`` property, reference/value texts). Users hydrate stubs
  inside KiCad via Tools -> "Update Footprints from Library..." — position and
  grouping survive the update;
* placement: parts with a ``fixed`` constraint sit at their declared pose and
  are locked. Everything else is clustered by top-level module (part-ref prefix
  before the first dot, "root" otherwise), groups laid out left-to-right in
  name-sorted order, parts within a group in a name-sorted row of 2 mm-gridded
  cells advanced by courtyard size (default 5x5 mm cell for stubs). A ``near``
  constraint pulls the near-part immediately after its target in the same row.
  No courtyard overlaps by construction (disjoint cells);
* ``keepout`` constraints carrying 4 numeric params (x, y, w, h in mm) become
  rule-area zones; geometry-less keepouts stay JSON-only (constraints.json);
* nets of a ``diffpair`` constraint are renamed ``<base>_P``/``<base>_N``
  (first ref -> _P; base = the constraint's string param, else the first net's
  canonical name) in the .kicad_sch/.kicad_pcb net tables ONLY — the netlist
  and constraints JSON keep canonical names.

Deterministic: uuid5 from a fixed namespace + stable path strings, Decimal
arithmetic on a fixed grid — no clock, no floats, no randomness (hermeticity
N1: nothing outside the IR is ever read at emit time).
"""

from __future__ import annotations

import re
import uuid
from decimal import ROUND_CEILING, Decimal

from ohm import ir, units

_NS = uuid.UUID("00000000-0000-0000-0000-00000000006f")  # fixed Ohm namespace
_SCH_VERSION = 20230121  # KiCad 7 schematic format
_PCB_VERSION = 20221018  # KiCad 7 board format

_D = Decimal
_STUB_W = _D(5)  # default courtyard for string-footprint stubs, mm
_STUB_H = _D(5)
_CELL_GAP = _D(2)  # placement grid quantum + inter-part clearance, mm
_GROUP_GAP = _D(4)  # extra gap between module groups, mm
_ORIGIN_X = _D(10)
_ORIGIN_Y = _D(20)

_PARAM_RE = re.compile(r"^(-?\d+(?:\.\d+)?)([A-Za-z]*)$")


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "/".join(parts)))


def _q(s: str) -> str:
    """Escape for interpolation inside a double-quoted s-expression string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ---------- constraint-param parsing (canonical source strings -> Decimal) ----------
def parse_mm(param: str) -> Decimal | None:
    """'10mm' / '250mil' / bare '10' (= mm) -> Decimal mm; None if not a length."""
    m = _PARAM_RE.match(param)
    if not m:
        return None
    value, unit = _D(m.group(1)), m.group(2)
    if unit in ("", "mm"):
        return value
    return units.convert(value, unit, "mm")


def parse_rot(param: str) -> Decimal | None:
    m = _PARAM_RE.match(param)
    if not m or m.group(2) not in ("", "deg"):
        return None
    return _D(m.group(1))


def _str_param(param: str) -> str | None:
    if len(param) >= 2 and param[0] == '"' and param[-1] == '"':
        return param[1:-1]
    return None


def fixed_poses(low: ir.IRLow) -> dict[str, tuple[Decimal, Decimal, Decimal, str]]:
    """part ref -> (x_mm, y_mm, rot_deg, side) from `fixed` constraints."""
    poses: dict[str, tuple[Decimal, Decimal, Decimal, str]] = {}
    for c in low.constraints:
        if c.kind != "fixed" or not c.nets:
            continue
        nums = [v for p in c.params if (v := parse_mm(p)) is not None]
        side = next((s for p in c.params if (s := _str_param(p)) in ("top", "bottom")), "top")
        x = nums[0] if len(nums) > 0 else _D(0)
        y = nums[1] if len(nums) > 1 else _D(0)
        rot = parse_rot(c.params[2]) if len(c.params) > 2 else None
        poses.setdefault(c.nets[0], (x, y, rot or _D(0), side or "top"))
    return poses


def diffpair_renames(low: ir.IRLow) -> dict[str, str]:
    """canonical net name -> renamed _P/_N name, for kicad_sch/pcb/pro ONLY."""
    renames: dict[str, str] = {}
    for c in low.constraints:
        if c.kind != "diffpair" or len(c.nets) < 2:
            continue
        base = next((s for p in c.params if (s := _str_param(p))), None) or c.nets[0]
        renames.setdefault(c.nets[0], f"{base}_P")
        renames.setdefault(c.nets[1], f"{base}_N")
    return renames


# ---------- placement ----------
def _courtyard(pkg: ir.Value | None):
    """(cell basis w, h, PackageModel|None) — Decimal mm."""
    if isinstance(pkg, ir.GenerateSpec):
        from ohm.pkggen.packages import PkgError, build_from_spec, spec_name

        try:
            model = build_from_spec(pkg, spec_name(pkg))
            return model.courtyard_w, model.courtyard_h, model
        except PkgError:  # S006-gated before emit; belt and braces
            pass
    return _STUB_W, _STUB_H, None


def _ceil_grid(d: Decimal) -> Decimal:
    return (d / _CELL_GAP).quantize(_D(1), rounding=ROUND_CEILING) * _CELL_GAP


def _group_of(ref: str) -> str:
    return ref.split(".", 1)[0] if "." in ref else "root"


def placement(low: ir.IRLow) -> dict[str, tuple[Decimal, Decimal, Decimal, str, bool]]:
    """part ref -> (x, y, rot, side, locked). Deterministic, Decimal-only.

    Rules: fixed parts at their declared pose (locked). Others: one row per
    top-level module group, groups left-to-right name-sorted, parts name-sorted
    within the group, each in a 2mm-gridded cell sized ceil2(courtyard + 2mm);
    near-parts are inserted immediately after their target (same row).
    """
    fixed = fixed_poses(low)
    out: dict[str, tuple[Decimal, Decimal, Decimal, str, bool]] = {}
    auto = [p for p in low.parts if p.ref not in fixed]
    auto_refs = {p.ref for p in auto}
    for ref, (x, y, rot, side) in fixed.items():
        out[ref] = (x, y, rot, side, True)

    # near(follower, target): follower rides right after target, first wins
    follows: dict[str, str] = {}
    for c in low.constraints:
        if (c.kind == "near" and len(c.nets) >= 2 and c.nets[0] in auto_refs
                and c.nets[1] in auto_refs and c.nets[0] != c.nets[1]):
            follows.setdefault(c.nets[0], c.nets[1])
    followers_of: dict[str, list[str]] = {}
    for follower in sorted(follows):
        followers_of.setdefault(follows[follower], []).append(follower)

    groups: dict[str, list[str]] = {}
    for p in sorted(auto, key=lambda p: p.ref):
        if p.ref not in follows:  # followers are placed via their target
            groups.setdefault(_group_of(p.ref), []).append(p.ref)

    court = {p.ref: _courtyard(p.package) for p in auto}
    cursor = _ORIGIN_X
    placed: set[str] = set()

    def place(ref: str) -> None:
        nonlocal cursor
        if ref in placed:
            return
        placed.add(ref)
        w, _h, _m = court[ref]
        cell = _ceil_grid(w + _CELL_GAP)
        out[ref] = (cursor + cell / 2, _ORIGIN_Y, _D(0), "top", False)
        cursor += cell
        for follower in followers_of.get(ref, ()):
            place(follower)

    for gname in sorted(groups):
        for ref in groups[gname]:
            place(ref)
        cursor += _GROUP_GAP
    for ref in sorted(follows):  # follower whose target chain never placed (cycle)
        place(ref)
    return out


# ---------- schematic ----------
def emit_kicad_sch(low: ir.IRLow, source: str = "<string>") -> str:
    renames = diffpair_renames(low)
    out = [
        "(kicad_sch",
        f"  (version {_SCH_VERSION})",
        '  (generator "ohm")',
        f'  (uuid "{_uid(source, "sch")}")',
        '  (paper "A4")',
        "  (lib_symbols)",
    ]
    y = 20
    for p in low.parts:
        if isinstance(p.package, str):
            fp = p.package
        elif isinstance(p.package, ir.GenerateSpec):
            from ohm.pkggen.packages import spec_name

            fp = f"ohm:{spec_name(p.package)}"  # sibling generated .kicad_mod (P11)
        else:
            fp = ""
        out.append(
            f'  (text "{_q(p.designator)}: {_q(p.component)} ({_q(fp)}) [{_q(p.ref)}]"'
            f' (at 20 {y} 0) (effects (font (size 2 2)))'
            f' (uuid "{_uid(source, "part", p.ref)}"))'
        )
        y += 10
    for net in low.nets:
        name = renames.get(net.name, net.name)
        out.append(
            f'  (global_label "{_q(name)}" (shape passive) (at 150 {y} 0)'
            f' (effects (font (size 1.27 1.27)))'
            f' (uuid "{_uid(source, "net", net.name)}"))'
        )
        y += 10
    out.append('  (sheet_instances (path "/" (page "1")))')
    out.append(")")
    return "\n".join(out) + "\n"


# ---------- board ----------
def _fp_texts(part: ir.PartLow, half_h: Decimal, layer_pfx: str) -> list[str]:
    ref_y = half_h + _D(1)
    font = "(effects (font (size 1 1) (thickness 0.15)))"
    return [
        f'    (property "ohm_path" "{_q(part.ref)}")',
        f'    (fp_text reference "{_q(part.designator)}" (at 0 -{ref_y})'
        f' (layer "{layer_pfx}.SilkS") {font})',
        f'    (fp_text value "{_q(part.component)}" (at 0 {ref_y})'
        f' (layer "{layer_pfx}.Fab") {font})',
    ]


def _footprint(part: ir.PartLow, pose, pad_net, source: str) -> list[str]:
    x, y, rot, side, locked = pose
    pfx = "B" if side == "bottom" else "F"
    w, h, model = _courtyard(part.package)
    half_w, half_h = w / 2, h / 2
    at = f"(at {x} {y}{f' {rot}' if rot else ''})"
    if model is not None:
        name = f"ohm:{model.name}"
    elif isinstance(part.package, str):
        name = part.package
    else:
        name = part.component
    out = [
        f'  (footprint "{_q(name)}"{" locked" if locked else ""} (layer "{pfx}.Cu")',
        f'    (tstamp "{_uid(source, "fp", part.ref)}")',
        f"    {at}",
        "    (attr smd)",
    ]
    out += _fp_texts(part, half_h, pfx)
    out.append(
        f"    (fp_rect (start -{half_w} -{half_h}) (end {half_w} {half_h})"
        f' (stroke (width 0.05) (type solid)) (fill none) (layer "{pfx}.CrtYd"))'
    )
    if model is None:
        out.append("  )")  # string-footprint stub: no pads (hydrate in KiCad)
        return out
    # full generated geometry — same shapes as ohm/pkggen/footprint.py, board-embedded
    silk_w, silk_h = half_w - _D("0.25"), half_h - _D("0.25")
    out.append(
        f"    (fp_rect (start -{silk_w} -{silk_h}) (end {silk_w} {silk_h})"
        f' (stroke (width 0.12) (type solid)) (fill none) (layer "{pfx}.SilkS"))'
    )
    p1 = model.pads[0]
    cx = -half_w if p1.x < 0 else half_w
    cy = -half_h if p1.y < 0 else half_h
    out.append(
        f"    (fp_circle (center {cx} {cy}) (end {cx + _D('0.1')} {cy})"
        f' (stroke (width 0.12) (type solid)) (fill none) (layer "{pfx}.SilkS"))'
    )
    pad_rot = f" {rot}" if rot else ""
    for pad in model.pads:
        net = pad_net.get((part.ref, pad.id))
        net_s = f' (net {net[0]} "{_q(net[1])}")' if net else ""
        out.append(
            f'    (pad "{pad.id}" smd roundrect (at {pad.x} {pad.y}{pad_rot})'
            f' (size {pad.w} {pad.h})'
            f' (layers "{pfx}.Cu" "{pfx}.Paste" "{pfx}.Mask")'
            f" (roundrect_rratio 0.25){net_s})"
        )
    out.append("  )")
    return out


def emit_kicad_pcb(low: ir.IRLow, source: str = "<string>") -> str:
    renames = diffpair_renames(low)
    out = [
        "(kicad_pcb",
        f"  (version {_PCB_VERSION})",
        '  (generator "ohm")',
        "  (general (thickness 1.6))",
        '  (paper "A4")',
        "  (layers",
        '    (0 "F.Cu" signal)',
        '    (31 "B.Cu" signal)',
        '    (34 "B.Paste" user)',
        '    (35 "F.Paste" user)',
        '    (36 "B.SilkS" user "B.Silkscreen")',
        '    (37 "F.SilkS" user "F.Silkscreen")',
        '    (38 "B.Mask" user)',
        '    (39 "F.Mask" user)',
        '    (44 "Edge.Cuts" user)',
        '    (46 "B.CrtYd" user "B.Courtyard")',
        '    (47 "F.CrtYd" user "F.Courtyard")',
        '    (48 "B.Fab" user)',
        '    (49 "F.Fab" user)',
        "  )",
        '  (net 0 "")',
    ]
    pad_net: dict[tuple[str, str], tuple[int, str]] = {}
    for code, net in enumerate(low.nets, start=1):
        name = renames.get(net.name, net.name)
        out.append(f'  (net {code} "{_q(name)}")')
        for node in net.nodes:
            pad_net[(node.part_ref, node.pad or node.pin)] = (code, name)

    poses = placement(low)
    for part in low.parts:
        out += _footprint(part, poses[part.ref], pad_net, source)

    # groups per top-level module prefix (root-level parts stay ungrouped)
    grouped: dict[str, list[str]] = {}
    for part in low.parts:
        g = _group_of(part.ref)
        if g != "root":
            grouped.setdefault(g, []).append(_uid(source, "fp", part.ref))
    for gname in sorted(grouped):
        members = " ".join(f'"{m}"' for m in grouped[gname])
        out.append(f'  (group "{_q(gname)}" (id "{_uid(source, "group", gname)}")'
                   f" (members {members}))")

    # keepout zones — only constraints carrying 4 numeric params (x, y, w, h mm);
    # geometry-less keepouts remain JSON-only (see module docstring)
    for i, c in enumerate(low.constraints):
        if c.kind != "keepout":
            continue
        nums = [v for p in c.params if (v := parse_mm(p)) is not None]
        if len(nums) != 4:
            continue
        x, y, w, h = nums
        x2, y2 = x + w, y + h
        out.append(
            f'  (zone (net 0) (net_name "") (layers "F.Cu" "B.Cu")'
            f' (tstamp "{_uid(source, "keepout", str(i))}")'
            " (hatch edge 0.5)"
            " (connect_pads (clearance 0)) (min_thickness 0.25)"
            " (keepout (tracks not_allowed) (vias not_allowed) (pads allowed)"
            " (copperpour not_allowed) (footprints allowed))"
            " (fill (thermal_gap 0.5) (thermal_bridge_width 0.5))"
            f" (polygon (pts (xy {x} {y}) (xy {x2} {y}) (xy {x2} {y2}) (xy {x} {y2}))))"
        )
    out.append(")")
    return "\n".join(out) + "\n"
