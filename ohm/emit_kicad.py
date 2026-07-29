"""KiCad emitters — valid .kicad_sch / .kicad_pcb, format pinned to KiCad 7.

The schematic carries real symbol bodies, pins, wire stubs and net labels — a
diagram a human can review and KiCad can extract the same net graph from
(`tests/test_schematic_roundtrip.py` makes KiCad itself the oracle). Connectivity
is expressed as a labelled stub per pin rather than routed point-to-point wires:
identical electrically, readable at hundreds of pins, and stable across
re-generation. The board carries real footprints (Milestone F Part D):

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
def _footprint_id(package: ir.Value | None) -> str:
    if isinstance(package, str):
        return package
    if isinstance(package, ir.GenerateSpec):
        from ohm.pkggen.packages import spec_name

        return f"ohm:{spec_name(package)}"  # sibling generated .kicad_mod (P11)
    return ""


# Schematic layout constants. Everything is a multiple of 1.27mm so symbols, wire
# stubs and labels all land on KiCad's default grid — off-grid pins are the
# classic way a generated schematic *looks* wired and is not.
_SCH_GRID = _D("1.27")
_SCH_STUB = _D("2.54")  # wire stub from pin to its label
_SCH_LABEL_W = _D("30.48")  # room reserved for a net label either side
_SCH_COL_GAP = _D("10.16")
_SCH_ROW_GAP = _D("12.7")
_SCH_MARGIN = _D("25.4")
_SCH_MAX_W = _D("1200")  # wrap the flow layout past this, mm
_SCH_TALL_FACTOR = _D("3")  # symbols this much taller than median get their own row
# A rail touching this many pins is drawn as a power symbol at every pin instead of
# a repeated text label. That is how a human draws a supply: on this board GND.gnd
# alone appears on 96 pins, and 96 identical labels read as noise where 96 ground
# symbols read as a rail. Smaller nets keep labels — a power symbol on a two-pin
# local net would be misleading.
_PWR_MIN_PINS = 4


def _snap(v: Decimal) -> Decimal:
    return (v / _SCH_GRID).quantize(_D(1), rounding=ROUND_CEILING) * _SCH_GRID


def emit_kicad_sch(low: ir.IRLow, source: str = "<string>",
                   design: ir.Design | None = None) -> str:
    """A real, reviewable schematic: symbol bodies, pins, wires and net labels.

    Connectivity is expressed as a short wire stub at every pin carrying a net
    LABEL, rather than routed point-to-point wires. That is a deliberate choice,
    not a shortcut: for a board with hundreds of pins, orthogonal net routing in
    2-D produces a diagram no one can read, while labels give exactly the same
    electrical graph, survive re-generation, and are what a human draws for dense
    MCU/FPGA sheets anyway. KiCad's net highlighting makes them navigable.
    """
    from ohm.pkggen.pindata_iface import DeclaredPinData
    from ohm.pkggen.symbol import BOX_HALF_W, PIN_LEN, emit_symbol, symbol_layout

    renames = diffpair_renames(low)
    comps = {c.name: c for c in design.components} if design is not None else {}
    pin_tables: dict[str, list[tuple[str, str, str]]] = {}
    layouts: dict[str, tuple[Decimal, list]] = {}
    lib_defs: list[str] = []

    # Net lookup keyed by the SYMBOL PIN NUMBER, which is the pad wherever one is
    # mapped. Keying by pin name as well would let a pin *named* "C1" shadow the
    # pad *called* "C1" on the same part (real case: LSM303D pin C1 sits on pad
    # E1 while pad C1 carries INT2) and hang a label on an unconnected pin.
    net_at: dict[tuple[str, str], str] = {}
    part_component = {p.ref: p.component for p in low.parts}
    # fallback pin table when no design is supplied: every pin the netlist touches
    seen_pins: dict[str, dict[str, str]] = {}
    for net in low.nets:
        shown = renames.get(net.name, net.name)
        for node in net.nodes:
            net_at[(node.part_ref, node.pad or node.pin)] = shown

    for net in low.nets:
        for node in net.nodes:
            cname = part_component.get(node.part_ref)
            if cname is not None:
                seen_pins.setdefault(cname, {}).setdefault(node.pad or node.pin, node.pin)

    # Rail nets worth a power symbol: ground, or a declared supply, and wide enough
    # that repeating the name at every pin is noise rather than information.
    ground_nets, power_nets = set(), set()
    for net in low.nets:
        if len(net.nodes) < _PWR_MIN_PINS:
            continue
        roles = set()
        for node in net.nodes:
            comp = comps.get(part_component.get(node.part_ref, ""))
            if comp is None:
                continue
            pin = next((p for p in comp.pins if p.name == node.pin), None)
            if pin is not None:
                roles.add(pin.role)
        if "ground" in roles:
            ground_nets.add(net.name)
        elif roles & {"power_in", "power_out"}:
            power_nets.add(net.name)
    # Grounds become symbols; supplies stay labels. The triangle is unambiguous
    # and carries no text, so it survives the 2.54mm pin pitch. A supply symbol
    # must show its name to distinguish 3V3 from 5V, and that text collides with
    # the neighbouring pin rows — a label in the margin reads better.
    rail_nets = ground_nets

    for part in low.parts:
        if part.component in pin_tables:
            continue
        comp = comps.get(part.component)
        if comp is not None:
            pins = DeclaredPinData().pin_table(comp)
        else:  # degraded: reconstruct from the netlist, roles unknown
            pins = [(pad, name, "passive") for pad, name
                    in sorted(seen_pins.get(part.component, {}).items(),
                              key=lambda kv: (len(kv[0]), kv[0]))]
        if not pins:  # no declared pin table: a single anchor pin keeps it placeable
            pins = [("1", part.component, "passive")]
        pin_tables[part.component] = pins
        layouts[part.component] = symbol_layout(pins)
        body = emit_symbol(part.component, pins, footprint=_footprint_id(part.package))
        inner = body.split("\n")[3:-2]  # strip the kicad_symbol_lib wrapper
        inner[0] = inner[0].replace(f'(symbol "{part.component}"',
                                    f'(symbol "ohm:{part.component}"', 1)
        lib_defs.extend("  " + line for line in inner)

    def _pwr_id(net: str) -> str:
        return "PWR_" + re.sub(r"\W", "_", net)

    for rail_name in sorted(rail_nets):
        # KiCad takes a power symbol's NET from its pin NAME, so there is one
        # definition per rail rather than one shared symbol.
        graphic = ("(polyline (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) "
                   "(xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27)) "
                   "(stroke (width 0) (type default)) (fill (type none)))")
        sid = _pwr_id(rail_name)
        lib_defs.extend([
            f'    (symbol "ohm:{sid}" (power) (pin_names (offset 0))'
            " (in_bom no) (on_board no)",
            '      (property "Reference" "#PWR" (at 0 -6.35 0)'
            " (effects (font (size 1.27 1.27)) hide))",
            f'      (property "Value" "{_q(rail_name)}" (at 0 -3.81 0)'
            " (effects (font (size 1.27 1.27))))",
            f'      (symbol "{sid}_0_1" {graphic})',
            f'      (symbol "{sid}_1_1"',
            f'        (pin power_in line (at 0 0 270) (length 0) hide'
            f' (name "{_q(rail_name)}" (effects (font (size 1.27 1.27))))'
            ' (number "1" (effects (font (size 1.27 1.27)))))',
            "      )",
            "    )",
        ])

    # ---- flow layout, grouped by module, on grid ----
    stand_off = BOX_HALF_W + PIN_LEN
    _group_index: dict[str, int] = {}
    for part in low.parts:  # groups keep first-appearance order
        _group_index.setdefault(_group_of(part.ref), len(_group_index))
    placed: list[tuple[ir.PartLow, Decimal, Decimal]] = []
    group_titles: list[tuple[str, Decimal, Decimal]] = []
    cx, cy, row_h = _SCH_MARGIN, _SCH_MARGIN, _D(0)
    last_group = None
    # Within a group, tallest first. A row is as tall as its tallest symbol, so
    # mixing a 100-pin MCU with 0402 capacitors leaves most of that row empty;
    # grouping like heights together packs the sheet without moving anything
    # between groups. Ties break on ref, so placement stays deterministic.
    ordered = sorted(low.parts,
                     key=lambda p: (_group_index[_group_of(p.ref)],
                                    -layouts[p.component][0], p.ref))
    # A 100-pin MCU symbol is ~15x taller than a 0402 capacitor, and a row is as
    # tall as its tallest member, so any row mixing the two wastes that whole
    # difference across its full width. Oversized symbols get a row to themselves.
    heights = sorted(layouts[p.component][0] for p in low.parts)
    median_h = heights[len(heights) // 2] if heights else _D(1)
    tall_cut = median_h * _SCH_TALL_FACTOR

    for part in ordered:
        box_h, _ = layouts[part.component]
        cell_w = _snap(stand_off * 2 + _SCH_LABEL_W * 2 + _SCH_COL_GAP)
        cell_h = _snap(box_h * 2 + _SCH_ROW_GAP)
        group = _group_of(part.ref)
        solo = box_h > tall_cut
        if solo and cx > _SCH_MARGIN and group == last_group:
            cx, cy = _SCH_MARGIN, _snap(cy + row_h + _SCH_ROW_GAP)
            row_h = _D(0)
        if group != last_group or cx + cell_w > _SCH_MAX_W:
            if last_group is not None:
                cx, cy = _SCH_MARGIN, _snap(cy + row_h + _SCH_ROW_GAP)
                row_h = _D(0)
            if group != last_group:
                group_titles.append((group, _SCH_MARGIN, cy))
                cy = _snap(cy + _SCH_ROW_GAP)
                last_group = group
        placed.append((part, _snap(cx + cell_w / 2), _snap(cy + cell_h / 2)))
        row_h = max(row_h, cell_h)
        if solo:  # close the row behind it so nothing shares the empty space
            cx, cy, row_h = _SCH_MARGIN, _snap(cy + cell_h + _SCH_ROW_GAP), _D(0)
        else:
            cx = _snap(cx + cell_w)

    page_w = _snap(_SCH_MAX_W + _SCH_MARGIN * 2)
    page_h = _snap(cy + row_h + _SCH_MARGIN * 2)

    out = [
        "(kicad_sch",
        f"  (version {_SCH_VERSION})",
        '  (generator "ohm")',
        f'  (uuid "{_uid(source, "sch")}")',
        f'  (paper "User" {page_w} {page_h})',
        "  (lib_symbols",
        *lib_defs,
        "  )",
    ]

    for title, tx, ty in group_titles:
        out.append(f'  (text "{_q(title)}" (at {tx} {ty} 0)'
                   ' (effects (font (size 3 3) bold) (justify left bottom))'
                   f' (uuid "{_uid(source, "grp", title)}"))')

    sheet_uuid = _uid(source, "sch")
    for part, px, py in placed:
        _, pin_geom = layouts[part.component]
        ref_y, val_y = _snap(py - layouts[part.component][0] - _SCH_GRID * 2), \
            _snap(py + layouts[part.component][0] + _SCH_GRID * 2)
        out.append(f'  (symbol (lib_id "ohm:{_q(part.component)}") (at {px} {py} 0)'
                   " (unit 1) (in_bom yes) (on_board yes) (dnp no)")
        out.append(f'    (uuid "{_uid(source, "sym", part.ref)}")')
        out.append(f'    (property "Reference" "{_q(part.designator)}" (at {px} {ref_y} 0)'
                   " (effects (font (size 1.27 1.27))))")
        out.append(f'    (property "Value" "{_q(part.component)}" (at {px} {val_y} 0)'
                   " (effects (font (size 1.27 1.27))))")
        out.append(f'    (property "Footprint" "{_q(_footprint_id(part.package))}"'
                   " (at 0 0 0) (effects (font (size 1.27 1.27)) hide))")
        out.append(f'    (property "Ohm" "{_q(part.ref)}" (at 0 0 0)'
                   " (effects (font (size 1.27 1.27)) hide))")
        for number, *_rest in pin_geom:
            out.append(f'    (pin "{_q(number)}" (uuid "'
                       f'{_uid(source, "pin", part.ref, number)}"))')
        out.append(f'    (instances (project "ohm" (path "/{sheet_uuid}"'
                   f' (reference "{_q(part.designator)}") (unit 1))))')
        out.append("  )")

        # wire stub + net label at every connected pin
        for number, _name, _role, lx, ly, side in pin_geom:
            net_name = net_at.get((part.ref, number))
            if net_name is None:
                continue
            ax, ay = _snap(px + lx), _snap(py - ly)  # symbol coords are Y-up
            bx = ax - _SCH_STUB if side == "left" else ax + _SCH_STUB
            out.append(f'  (wire (pts (xy {ax} {ay}) (xy {bx} {ay}))'
                       " (stroke (width 0) (type default))"
                       f' (uuid "{_uid(source, "w", part.ref, number)}"))')
            if net_name in rail_nets:
                sid = _pwr_id(net_name)
                # graphic hangs below the pin for ground, above for a supply;
                # the pin sits AT the symbol origin, so it meets the wire end
                # whatever the rotation
                out.append(f'  (symbol (lib_id "ohm:{sid}") (at {bx} {ay} 0)'
                           " (unit 1) (in_bom no) (on_board no) (dnp no)")
                out.append(f'    (uuid "{_uid(source, "pwr", part.ref, number)}")')
                out.append(f'    (property "Reference" "#PWR" (at {bx} {ay} 0)'
                           " (effects (font (size 1.27 1.27)) hide))")
                out.append(f'    (property "Value" "{_q(net_name)}" (at {bx} {ay} 0)'
                           " (effects (font (size 1.27 1.27)) hide))")
                out.append('    (pin "1" (uuid "'
                           f'{_uid(source, "pwrpin", part.ref, number)}"))')
                out.append(f'    (instances (project "ohm" (path "/{sheet_uuid}"'
                           ' (reference "#PWR") (unit 1))))')
                out.append("  )")
                continue
            just = "right" if side == "left" else "left"
            rot = 180 if side == "left" else 0
            out.append(f'  (label "{_q(net_name)}" (at {bx} {ay} {rot})'
                       f" (effects (font (size 1.27 1.27)) (justify {just} bottom))"
                       f' (uuid "{_uid(source, "lbl", part.ref, number)}"))')

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
