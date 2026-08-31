"""KiCad emitters — valid .kicad_sch / .kicad_pcb, format pinned to KiCad 7.

The schematic is a DRAWING, not a netlist rendered as boxes (ADR-017): one sheet
partitioned into a dashed, titled block per module; a passive that serves one pin
of an IC placed against that pin and WIRED to it; rails as power symbols; and net
labels only where a human uses them — IC fanout, and signals crossing a module.
KiCad extracts the same net graph back out of it (`tests/test_schematic_roundtrip.py`
makes KiCad itself the oracle). `emit_kicad_sheets()` gives the ADR-016 hierarchy
(one sheet per module) instead, for a design too big for one page.
The board carries real footprints (Milestone F Part D):

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
  Auto-placed parts never overlap (disjoint cells); a `fixed` part sits where
  the SOURCE says, which can collide — S011 checks that, over this same
  placement, so the check and the board cannot disagree;
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
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from ohm import ir, units
from ohm.formats import _PCB_VERSION, _SCH_VERSION

_NS = uuid.UUID("00000000-0000-0000-0000-00000000006f")  # fixed Ohm namespace
# THE VERSION POLICY, in the one place the stamps live. Ohm writes the KiCad 7
# format for every `--target-kicad` and never bumps these, because KiCad reads
# FORWARD and that is the direction it guarantees: a newer stamp would buy
# nothing and cost every older KiCad the ability to open the file. It follows
# that Ohm's own READERS (signoff, import-symbol, provenance) must be structural
# rather than layout-anchored — they face the other way, at files the user's
# KiCad wrote, and a line-anchored parser has already been broken twice by
# nothing more than a pretty-printer change (KiCad 9 and again KiCad 10).
# Gated by .github/workflows/ci.yml's `kicad` job at 8.0.9, 9.0.9 and 10.0.5.
# The two constants themselves live in `ohm/formats.py` now, alongside every
# other external-format stamp Ohm writes; imported here under their original
# names so this file's diff — and every f-string below — stays unchanged.

_D = Decimal
_STUB_W = _D(5)  # default courtyard for string-footprint stubs, mm
_STUB_H = _D(5)
_CELL_GAP = _D(2)  # placement grid quantum + inter-part clearance, mm
_GROUP_GAP = _D(4)  # extra gap between module groups, mm
_ORIGIN_X = _D(10)
_ORIGIN_Y = _D(20)

_PARAM_RE = re.compile(r"^(-?\d+(?:\.\d+)?)([A-Za-z]*)$")


_PWR_REF_RE = re.compile(r'\(property "Reference" "(#PWR\d+)"')


def _pwr_ref(seq: int) -> str:
    """KiCad's own spelling for the nth power symbol: #PWR01, #PWR02, ..."""
    return f"#PWR{seq:02d}"


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


_PCB_QUANTUM = _D("0.01")  # mm; KiCad stores nm, fabs work to 10um


def _mm(v: Decimal) -> Decimal:
    """Round a BOARD coordinate to 0.01mm.

    Placement used `_snap`, which rounds up to the 1.27mm schematic grid — a
    0.05in imperial pitch applied to a metric board. Every `near(x, 2mm)` came
    out at up to 3.27mm because of it, so the one number the author wrote was
    never the number that reached the board.
    """
    return v.quantize(_PCB_QUANTUM)


def _ceil_grid(d: Decimal) -> Decimal:
    return (d / _CELL_GAP).quantize(_D(1), rounding=ROUND_CEILING) * _CELL_GAP


def _group_of(ref: str) -> str:
    return ref.split(".", 1)[0] if "." in ref else "root"


def placement(low: ir.IRLow) -> dict[str, tuple[Decimal, Decimal, Decimal, str, bool]]:
    """part ref -> (x, y, rot, side, locked). Deterministic, Decimal-only.

    Fixed parts sit at their declared pose (locked). Everything else FLOWS: one
    block per top-level module group, parts wrapped into rows, blocks stacked
    down the board. Then every `near` follower is moved beside the part it was
    declared near, at the distance it asked for, on the first side that is free.

    Three things this gets right that the row-cursor version did not:

    - It advances y. The old placer only moved x, so 116 parts landed on a
      single 500mm line — a placement no board holds and no router uses.
    - `near` is honoured against a FIXED target. `near(c_vdd1, u, 2mm)` with the
      MCU pinned at (30, 25) is the most load-bearing near on a board, and it
      was dropped outright because the target was not in the flow.
    - A chain is a LINE, not a star. `a near b near c` places each part beside
      its OWN target; collapsing the chain to its root (which is right for the
      schematic, where satellites ring one host) put a connector on the far side
      of the anchor and off the board at x = -8.89.

    Distances are edge-to-edge, because that is what `near` means. Placing on
    the 2mm cell grid instead turned a declared 2mm into 2.9mm.
    """
    fixed = fixed_poses(low)
    out: dict[str, tuple[Decimal, Decimal, Decimal, str, bool]] = {}
    auto = [p for p in low.parts if p.ref not in fixed]
    auto_refs = {p.ref for p in auto}
    for ref, (x, y, rot, side) in fixed.items():
        out[ref] = (x, y, rot, side, True)

    pkg_of = {p.ref: p.package for p in low.parts}

    def half(ref: str) -> tuple[Decimal, Decimal]:
        w, h, _m = _courtyard(pkg_of.get(ref))
        return w / 2, h / 2

    def cell_of(ref: str) -> tuple[Decimal, Decimal]:
        w, h, _m = _courtyard(pkg_of.get(ref))
        return _ceil_grid(w + _CELL_GAP), _ceil_grid(h + _CELL_GAP)

    # near(follower, target, distance): first declaration wins
    follows: dict[str, str] = {}
    want_of: dict[str, Decimal] = {}
    for c in low.constraints:
        if (c.kind == "near" and len(c.nets) >= 2 and c.nets[0] in auto_refs
                and c.nets[0] != c.nets[1]
                and (c.nets[1] in auto_refs or c.nets[1] in fixed)
                and c.nets[0] not in follows):
            follows[c.nets[0]] = c.nets[1]
            want_of[c.nets[0]] = next(
                (d for p in c.params if (d := parse_mm(p)) is not None), _CELL_GAP)

    # depth from a non-follower root, so a target is always placed before the
    # part that has to sit beside it. A cycle keeps its first-seen depth and is
    # placed in the flow like anything else.
    depth_of: dict[str, int] = {}
    for follower in sorted(follows):
        seen, cur, hops = {follower}, follows[follower], 1
        while cur in follows and cur not in seen:
            seen.add(cur)
            cur = follows[cur]
            hops += 1
        depth_of[follower] = hops

    groups: dict[str, list[str]] = {}
    for p in sorted(auto, key=lambda p: p.ref):
        if p.ref not in follows:
            groups.setdefault(_group_of(p.ref), []).append(p.ref)

    # Occupancy, so nothing lands on anything: fixed parts first, then each
    # part as it is placed. A mounting hole has a screw through it.
    taken: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []

    def rect(ref: str, x: Decimal, y: Decimal):
        hw, hh = half(ref)
        pad = _CELL_GAP / 2
        return (x - hw - pad, y - hh - pad, x + hw + pad, y + hh + pad)

    def free(r) -> bool:
        # Compared with a quantum of slack. `rect` pads each part by half a cell
        # gap as a courtesy margin, so a follower placed at exactly the distance
        # it asked for has pads that just touch — and rounding the position to
        # 0.01mm tipped that touch 0.005mm into an overlap, which rejected every
        # side of the MCU and flowed its bypass caps 134mm away. A sub-quantum
        # touch of a margin is not a collision.
        e = _PCB_QUANTUM
        return r[0] >= 0 and r[1] >= 0 and not any(
            r[0] + e < t[2] and r[2] - e > t[0]
            and r[1] + e < t[3] and r[3] - e > t[1] for t in taken)

    for ref, (x, y, _r, _s) in fixed.items():
        taken.append(rect(ref, x, y))

    followers_of: dict[str, list[str]] = {}
    for f in sorted(follows):
        followers_of.setdefault(follows[f], []).append(f)

    def seat(ref: str) -> None:
        """Put every follower of `ref` beside it, then their followers.

        Called the moment `ref` itself lands. Placing all the flow first and the
        followers afterwards left nothing free beside the targets, and three
        parts fell through to the fallback and were flowed 150mm from the pin
        they were declared 2mm from — worse than the row they replaced.
        """
        tx, ty, _tr, _ts, _tl = out[ref]
        thw, thh = half(ref)
        for f in followers_of.get(ref, ()):
            if f in out:
                continue
            rhw, rhh = half(f)
            gap = want_of.get(f, _CELL_GAP)
            # Round TOWARD the target, so the quantum can only ever shorten the
            # gap. Rounding away made a declared 2mm land at 2.005mm — 5um, far
            # under any fab tolerance, but the declared number is the one thing
            # here that is not an approximation, and it costs nothing to honour.
            for x, y, rx, ry in (
                (tx + thw + gap + rhw, ty, ROUND_FLOOR, ROUND_FLOOR),
                (tx - thw - gap - rhw, ty, ROUND_CEILING, ROUND_FLOOR),
                (tx, ty + thh + gap + rhh, ROUND_FLOOR, ROUND_FLOOR),
                (tx, ty - thh - gap - rhh, ROUND_FLOOR, ROUND_CEILING),
            ):
                px = x.quantize(_PCB_QUANTUM, rounding=rx)
                py = y.quantize(_PCB_QUANTUM, rounding=ry)
                r = rect(f, px, py)
                if free(r):
                    out[f] = (px, py, _D(0), "top", False)
                    taken.append(r)
                    seat(f)
                    break

    # followers of a FIXED part get their space before anything flows into it
    for ref in sorted(fixed):
        seat(ref)

    # width of the board the design implies, via the parts pinned to it
    if fixed:
        target_w = max(t[2] for t in taken) - min(t[0] for t in taken)
    else:
        area = sum((cw * ch for cw, ch in map(cell_of, sorted(auto_refs))), _D(0))
        target_w = _ceil_grid(area.sqrt())
    target_w = max(target_w, _D(20))

    # ---- flow every part that is not a near-follower ----
    cursor_x, cursor_y, row_h = _ORIGIN_X, _ORIGIN_Y, _D(0)
    for gname in sorted(groups):
        for ref in groups[gname]:
            cw, ch = cell_of(ref)
            if cursor_x > _ORIGIN_X and cursor_x + cw > _ORIGIN_X + target_w:
                cursor_x, cursor_y, row_h = _ORIGIN_X, _mm(cursor_y + row_h), _D(0)
            # step past anything already there; bounded by the occupancy count
            for _ in range(len(taken) + 1):
                r = rect(ref, cursor_x + cw / 2, cursor_y + ch / 2)
                if free(r):
                    break
                blocking = [t for t in taken
                            if r[0] < t[2] and r[2] > t[0] and r[1] < t[3] and r[3] > t[1]]
                cursor_x = _mm(max(t[2] for t in blocking))
                if cursor_x + cw > _ORIGIN_X + target_w:
                    cursor_x, cursor_y, row_h = _ORIGIN_X, _mm(cursor_y + row_h), _D(0)
            x, y = _mm(cursor_x + cw / 2), _mm(cursor_y + ch / 2)
            out[ref] = (x, y, _D(0), "top", False)
            taken.append(rect(ref, x, y))
            seat(ref)
            cursor_x = _mm(cursor_x + cw)
            row_h = max(row_h, ch)
        cursor_x, cursor_y, row_h = _ORIGIN_X, _mm(cursor_y + row_h + _GROUP_GAP), _D(0)

    # ---- anything seat() could not fit: flow it, do not stack it ----
    # Only parts still unplaced. An earlier draft re-ran this over EVERY
    # follower and overwrote the positions seat() had already found, which is
    # how a bypass cap ended up 134mm from a pin it was sitting 2mm from.
    for ref in sorted(follows, key=lambda r: (depth_of[r], r)):
        if ref in out:
            continue
        target = follows[ref]
        if target not in out:  # a cycle: nothing to sit beside
            continue
        tx, ty, _tr, _ts, _tl = out[target]
        thw, thh = half(target)
        rhw, rhh = half(ref)
        gap = want_of.get(ref, _CELL_GAP)
        for x, y in ((tx + thw + gap + rhw, ty), (tx - thw - gap - rhw, ty),
                     (tx, ty + thh + gap + rhh), (tx, ty - thh - gap - rhh)):
            if free(rect(ref, _mm(x), _mm(y))):
                out[ref] = (_mm(x), _mm(y), _D(0), "top", False)
                taken.append(rect(ref, _mm(x), _mm(y)))
                break
        else:  # every side taken — flow it rather than stack it on a neighbour
            cw, ch = cell_of(ref)
            x, y = _mm(cursor_x + cw / 2), _mm(cursor_y + ch / 2)
            while not free(rect(ref, x, y)):
                x = _mm(x + cw)
                if x > _ORIGIN_X + target_w:
                    x, cursor_y = _mm(_ORIGIN_X + cw / 2), _mm(cursor_y + ch)
                    y = _mm(cursor_y + ch / 2)
            out[ref] = (x, y, _D(0), "top", False)
            taken.append(rect(ref, x, y))
            cursor_x = _mm(x + cw / 2)
    return out


# ---------- schematic ----------
def schematic_value(part: ir.PartLow) -> str:
    """What the Value field shows: the value AND what qualifies it.

    A schematic that says `100nF` has not specified a capacitor. Voltage rating
    decides whether the part survives the rail, and dielectric decides how much
    capacitance survives the bias — X7R vs X5R is a real difference on a 5V
    input. Professional practice prints them, so Ohm prints whatever the source
    declared: `100nF 16V X7R`. Tolerance rides along inside the Quantity, so
    `10kohm +/- 1%` renders itself.

    Falls back to the component name when no `value` is declared, which is what
    a part named for its value (`C100n`) wants anyway.
    """
    from ohm.emit_netlist import _value_text

    attrs = dict(part.attrs)
    value = attrs.get("value")
    text = _value_text(value) if value is not None else part.component
    for key in ("vmax", "dielectric"):
        if (extra := attrs.get(key)) is not None:
            text += " " + _value_text(extra)
    return text


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
_A3_W, _A3_H = _D(420), _D(297)  # KiCad A3 landscape, mm — the target sheet size
# Standard landscape sheets, smallest first: the page snaps up to the first one
# the drawing fits on. KiCad knows these by name and plots them with its own
# frame and title block.
_PAPERS = (("A4", _D(297), _D(210)), ("A3", _A3_W, _A3_H),
           ("A2", _D(594), _D(420)), ("A1", _D(841), _D(594)),
           ("A0", _D(1189), _D(841)))
# KiCad draws its title block inside the frame at the bottom right, and it is
# not part of the drawing area. Reserving its height keeps a module frame from
# running straight through "Title: ... / Rev: ...", which is the one part of the
# sheet a reviewer reads first. Height only: reserving the width as well would
# push most sheets up a paper size for a strip that is usually empty anyway.
_TITLE_H = _D(32)
_SCH_TALL_FACTOR = _D("3")  # symbols this much taller than median get their own row
# A rail touching this many pins is drawn as a power symbol at every pin instead of
# a repeated text label. That is how a human draws a supply: on this board GND.gnd
# alone appears on 96 pins, and 96 identical labels read as noise where 96 ground
# symbols read as a rail. Smaller nets keep labels — a power symbol on a two-pin
# local net would be misleading.
_PWR_MIN_PINS = 4
# Above this fan-out a net is a rail, not a local connection, so it never
# makes one part the "satellite" of another.
_SAT_MAX_FANOUT = 4
# A passive of at most this many pins can RIDE the pin it serves — be placed
# against it and wired to it — provided the part it serves has at least
# _HOST_MIN_PINS. That is the difference between a decoupling cap drawn as a
# floating symbol carrying a label and one drawn wired to the pin it decouples.
_RIDER_MAX_PINS = 3
_HOST_MIN_PINS = 4
_RIDER_GAP = _D("2.54")  # wire length from host pin to the rider's near pin
# Two riders closer together than this on the same side would overlap: a 2-pin
# symbol is 5.08mm tall and host pins are on a 2.54mm pitch. The loser stays in
# the flow layout with labels — never a wire drawn through another symbol.
_RIDER_MIN_DY = _D("5.08")
# an upright rider spans its body, its stub and its rail symbol
_RIDER_MIN_DY_V = _D("15.24")
# Width:height a module block aims for. Landscape, because symbols are wider than
# they are tall and a sheet is landscape too.
_BLOCK_ASPECT = _D("1.6")
# Page width the blocks pack to before wrapping: A2 landscape less its margins.
# A block wider than this simply gets its own row rather than being squeezed.
_PAGE_TARGET_W = _D(594) - _D("25.4") * 2
# Sheet sizes a human actually reviews. A2 and up are plotter sizes: they get
# split into per-module A3 sheets instead (ADR-016 / ADR-018).
_REVIEWABLE_RE = re.compile(r'\(paper "(A4|A3)"\)')
_SCH_CHAR_W = _D("1.4")   # label text advance per glyph at font size 1.27, mm
_PWR_SYM_W = _D("3.81")   # a power port's graphic plus its name


def title_block(design: ir.Design | None, source: str, page: int = 1,
                pages: int = 1, sheet_note: str | None = None) -> list[str]:
    """KiCad `(title_block ...)` from the design's `board` declaration.

    A blank title block is the mark of a draft: a document with no title, no
    revision and no owner cannot be cited in an ECO, a review or a fab package.
    Ohm had no way to state any of it, so the space stayed empty and KiCad's own
    generator string sat where a company name belongs. Sheet "n of m" is
    computed from what is actually emitted rather than declared — the only
    honest source for it.
    """
    board = design.board if design is not None else None
    title = board.get("title") if board else ""
    out = ["  (title_block",
           f'    (title "{_q(title or Path(source).stem)}")']
    if board is not None:
        for key, tag in (("date", "date"), ("rev", "rev"), ("company", "company")):
            if value := board.get(key):
                out.append(f'    ({tag} "{_q(value)}")')
        # KiCad's title block has four free comment lines; use them for the
        # fields it has no slot for rather than dropping them on the floor.
        for i, key in enumerate(("designer", "license", "comment"), start=1):
            if value := board.get(key):
                label = {"designer": "Drawn by", "license": "License"}.get(key)
                text = f"{label}: {value}" if label else value
                out.append(f'    (comment {i} "{_q(text)}")')
    where = sheet_note or f"Sheet {page} of {pages}"
    out.append(f'    (comment 4 "{_q(where)} — generated by Ohm '
               f'from {_q(Path(source).name)}")')
    out.append("  )")
    return out


def _snap(v: Decimal) -> Decimal:
    return (v / _SCH_GRID).quantize(_D(1), rounding=ROUND_CEILING) * _SCH_GRID


# Symbol coords are Y-up and the sheet is Y-down, so an unrotated pin at (lx, ly)
# sits at (X+lx, Y-ly). A 180-degree instance negates both. These two are the only
# rotations emitted; the round-trip oracle is what proves the transform, since a
# wire that misses its pin shows up as a different circuit rather than as a
# slightly wrong-looking drawing.
def _delta(lx: Decimal, ly: Decimal, rot: int) -> tuple[Decimal, Decimal]:
    """Pin offset from the symbol origin, in SHEET coordinates.

    At rot 0 a pin at (lx, ly) sits at (+lx, -ly). KiCad turns a symbol
    counter-clockwise on screen, so the offset rotates with it — 180 negates
    both, and 90/270 swap the axes. 180 was verified by the round-trip oracle
    (a wire that misses its pin splits the net); 90/270 are verified the same
    way, plus a render, since the sign convention is the easy thing to get
    backwards.
    """
    dx, dy = lx, -ly
    if rot == 90:
        return (dy, -dx)
    if rot == 180:
        return (-dx, -dy)
    if rot == 270:
        return (-dy, dx)
    return (dx, dy)


def _pin_abs(px: Decimal, py: Decimal, lx: Decimal, ly: Decimal,
             rot: int) -> tuple[Decimal, Decimal]:
    """Where a pin lands, for a symbol whose origin is at (px, py)."""
    dx, dy = _delta(lx, ly, rot)
    return (_snap(px + dx), _snap(py + dy))


def _pin_origin(ax: Decimal, ay: Decimal, lx: Decimal, ly: Decimal,
                rot: int) -> tuple[Decimal, Decimal]:
    """The inverse: where to put the symbol so that pin lands at (ax, ay)."""
    dx, dy = _delta(lx, ly, rot)
    return (_snap(ax - dx), _snap(ay - dy))


def _stub_dir(side: str, rot: int) -> tuple[Decimal, Decimal]:
    """Unit direction a pin's wire stub runs, outward from the symbol."""
    base = (_D(-1), _D(0)) if side == "left" else (_D(1), _D(0))
    return _delta(base[0], base[1], rot)


def _rail_nets(low: ir.IRLow, comps: dict) -> tuple[set[str], set[str]]:
    """(ground nets, supply nets) drawn as power symbols rather than labels.

    A rail is worth a power symbol when it is ground or a declared supply AND
    wide enough that repeating the name at every pin is noise, not information.
    Both become power ports: ERC can then flag an undriven or single-pin rail,
    and a mistyped label stops being a silent open circuit. Computed over the
    whole design — see emit_kicad_sch on why this must not be per-sheet.
    """
    ground_nets: set[str] = set()
    power_nets: set[str] = set()
    part_component = {p.ref: p.component for p in low.parts}
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
    return ground_nets, power_nets


def emit_kicad_sch(low: ir.IRLow, source: str = "<string>",
                   design: ir.Design | None = None, *,
                   group: str | None = None,
                   hier_nets: frozenset[str] = frozenset(),
                   sheet_path: str | None = None,
                   page: int = 1, pages: int = 1,
                   pwr_start: int = 0,
                   sheet_note: str | None = None) -> str:
    """A real, reviewable schematic: symbol bodies, pins, wires and net labels.

    With ``group`` set this emits ONE sheet of a hierarchy — only the parts of
    that top-level module — and nets in ``hier_nets`` get hierarchical labels
    (matching a pin on the parent's sheet symbol) instead of sheet-local ones.
    ``sheet_path`` is the instance path this sheet lives at, ``/root/sheetsym``.
    Rail classification is deliberately computed over the WHOLE design, never
    the sheet: a net drawn as a power symbol on one sheet and a plain label on
    another would be two disconnected nets.

    Connectivity is expressed as a short wire stub at every pin carrying a net
    LABEL, rather than routed point-to-point wires. That is a deliberate choice,
    not a shortcut: for a board with hundreds of pins, orthogonal net routing in
    2-D produces a diagram no one can read, while labels give exactly the same
    electrical graph, survive re-generation, and are what a human draws for dense
    MCU/FPGA sheets anyway. KiCad's net highlighting makes them navigable.
    """
    from ohm.emit_netlist import BOM_FIELDS, _value_text
    from ohm.pkggen.pindata_iface import DeclaredPinData
    from ohm.pkggen.symbol import emit_symbol, glyph_for, symbol_layout

    renames = diffpair_renames(low)
    comps = {c.name: c for c in design.components} if design is not None else {}
    pin_tables: dict[str, list[tuple[str, str, str]]] = {}
    glyphs: dict[str, str | None] = {}
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

    ground_nets, power_nets = _rail_nets(low, comps)
    rail_nets = ground_nets | power_nets

    sheet_parts = [p for p in low.parts
                   if group is None or _group_of(p.ref) == group]

    for part in sheet_parts:
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
        # a two-terminal passive is drawn as its conventional glyph, not a box
        comp_kinds = comp.kinds if comp is not None else ()
        glyph = glyph_for(comp_kinds, pins)
        pin_tables[part.component] = pins
        glyphs[part.component] = glyph
        layouts[part.component] = symbol_layout(pins, glyph)
        body = emit_symbol(part.component, pins, footprint=_footprint_id(part.package),
                           glyph=glyph)
        inner = body.split("\n")[3:-2]  # strip the kicad_symbol_lib wrapper
        inner[0] = inner[0].replace(f'(symbol "{part.component}"',
                                    f'(symbol "ohm:{part.component}"', 1)
        lib_defs.extend("  " + line for line in inner)

    def _pwr_id(net: str) -> str:
        return "PWR_" + re.sub(r"\W", "_", net)

    # Every power symbol used to be emitted as reference "#PWR", so a board with
    # 191 of them had 191 symbols sharing one designator. KiCad numbers them
    # #PWR01, #PWR02, ... precisely because a reference identifies ONE symbol;
    # KiCad 9 tolerated the collision silently, KiCad 10 refuses the schematic as
    # having "annotation errors" on every netlist export. Numbered in emission
    # order, which is deterministic, so the file stays byte-stable.
    #
    # `pwr_start` offsets the run so a HIERARCHY stays unique too: a reference
    # identifies one symbol in the whole schematic, not one per sheet, and
    # restarting at #PWR01 on every sheet just moves the collision up a level.
    pwr_seq = pwr_start

    for rail_name in sorted(rail_nets):
        # KiCad takes a power symbol's NET from its pin NAME, so there is one
        # definition per rail rather than one shared symbol.
        is_gnd = rail_name in ground_nets
        graphic = ("(polyline (pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) "
                   "(xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27)) "
                   "(stroke (width 0) (type default)) (fill (type none)))"
                   if is_gnd else
                   "(polyline (pts (xy 0 0) (xy 0 1.27)) "
                   "(stroke (width 0) (type default)) (fill (type none)))"
                   " (polyline (pts (xy -1.27 1.27) (xy 1.27 1.27)) "
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
            f'        (pin power_in line (at 0 0 {"270" if is_gnd else "90"}) (length 0) hide'
            f' (name "{_q(rail_name)}" (effects (font (size 1.27 1.27))))'
            ' (number "1" (effects (font (size 1.27 1.27)))))',
            "      )",
            "    )",
        ])

    # ---- layout: one partition per module, passives riding the pin they serve ----
    _group_index: dict[str, int] = {}
    for part in sheet_parts:  # groups keep first-appearance order
        _group_index.setdefault(_group_of(part.ref), len(_group_index))
    placed: list[tuple[ir.PartLow, Decimal, Decimal, int]] = []
    group_titles: list[tuple[str, Decimal, Decimal]] = []
    # Each IC is followed immediately by ITS OWN satellites — the two-pin parts
    # that share a net with it and nothing else of substance. That is the
    # convention every reference schematic uses: the decoupling cap sits beside
    # the pin it bypasses, not in a bank of capacitors three rows down, because
    # the pairing is what a reviewer and the layout engineer need to see.
    # Ordering is deterministic: ICs by (descending height, ref), satellites by ref.
    pin_count = {p.ref: len(pin_tables[p.component]) for p in sheet_parts}
    nets_of: dict[str, list[ir.NetLow]] = {}
    for net in low.nets:
        for node in net.nodes:
            nets_of.setdefault(node.part_ref, []).append(net)

    # A local net is DRAWN as a wire between two of its parts. Everything else
    # about the net still works: if it has further nodes (a crystal loop also
    # reaches the MCU), those keep their labels and ONE label rides the wire, so
    # the cluster has shape on the page and a single signal leaves it. That is
    # what a reference schematic does — the crystal, its two load caps and the
    # oscillator pins read as one recognisable circuit, not five islands.
    #
    # Requiring a net to have exactly TWO nodes is what kept every cluster on
    # this board unwired: a load cap sits on cap+crystal+MCU, three nodes.
    on_sheet = {p.ref for p in sheet_parts}
    part_by_ref = {p.ref: p for p in sheet_parts}
    # A net drawn as a bare wire is ANONYMOUS — KiCad names it Net-(C2-P1). That
    # is right for a local RC node and wrong for a net anything refers to by
    # name: `.kicad_pro` matches netclasses (and the diffpair track/gap rules) by
    # net-name pattern, so anonymising one would silently drop its trace rule the
    # next time the board is updated from the schematic. Those keep their label.
    constrained = {n for c in low.constraints for n in c.nets} | set(renames)
    # net -> [(part, pin)] for every endpoint of a block-local, non-rail net
    drawable: dict[str, list[tuple[str, str]]] = {}
    for net in low.nets:
        if net.name in rail_nets or net.name in constrained or len(net.nodes) < 2:
            continue
        # Above a small fan-out a net is a BUS, not a local connection, and
        # picking one of its members to ride is arbitrary. Eight LED resistors
        # sharing a supply node each rode the next, telescoping into a chain
        # that overlapped itself and shorted four separate LED nets together.
        # Same threshold the satellite rule already uses.
        if len(net.nodes) > _SAT_MAX_FANOUT:
            continue
        refs = {n.part_ref for n in net.nodes}
        if len(refs) < 2 or not refs <= on_sheet:
            continue
        if len({_group_of(r) for r in refs}) != 1:
            continue
        drawable[renames.get(net.name, net.name)] = [
            (n.part_ref, n.pad or n.pin) for n in net.nodes]

    edges: dict[str, list[tuple[str, str, str, str]]] = {}  # ref -> (net, mine, other, its)
    for nname, ends in sorted(drawable.items()):
        for ref, pin in ends:
            for other, opin in ends:
                if other != ref:
                    edges.setdefault(ref, []).append((nname, pin, other, opin))

    # rider ref -> (net, host ref, host pin, rider pin).
    #
    # The host is the SMALLEST neighbour that is at least as big as the rider,
    # not the richest. A crystal load cap belongs against its crystal, not
    # dragged across the block to the 100-pin MCU that the same net also
    # reaches; a sensor's pull-up belongs with the sensor. Ties break on ref, so
    # exactly one of a pair of equals rides the other and the pairing is stable.
    riders: dict[str, tuple[str, str, str, str]] = {}
    for part in sorted(sheet_parts, key=lambda p: (pin_count[p.ref], p.ref)):
        if pin_count[part.ref] > _RIDER_MAX_PINS:
            continue
        seen_nets = set()
        cand = []
        for nname, mine, other, opin in edges.get(part.ref, ()):
            rank = (pin_count.get(other, 0), other)
            if rank < (pin_count[part.ref], part.ref):
                continue  # the other one rides US, or is smaller
            if other in riders:
                continue  # a rider cannot host a rider — that is a chain, not a pin
            seen_nets.add(nname)
            cand.append((rank, nname, other, opin, mine))
        # exactly one net linking it to a host: a part bridging two pins of the
        # same host (a crystal across OSC_IN/OSC_OUT) can ride neither
        if len(seen_nets) == 1 and cand:
            _rank, nname, other, opin, mine = min(cand)
            riders[part.ref] = (nname, other, opin, mine)

    # `near(cap, ic, 2mm)` is the design SAYING which part this cap serves. A
    # bypass cap sits on two rails and nothing else, so connectivity alone can
    # never recover that pairing — every bypass cap on the board looks identical
    # to every other. Without the hint they can only be banked; with it they go
    # where the author said they belong, on the sheet as well as on the board.
    near_host: dict[str, str] = {}
    for c in low.constraints:
        if c.kind != "near" or len(c.nets) < 2 or c.nets[0] == c.nets[1]:
            continue
        follower, host_ref = c.nets[0], c.nets[1]
        # Only ever a SMALLER part riding a BIGGER one: the reverse would drag a
        # 100-pin MCU into a capacitor's slot. Ties are not pairings.
        if (follower in on_sheet and host_ref in on_sheet
                and pin_count.get(follower, 0) < pin_count.get(host_ref, 0)):
            near_host.setdefault(follower, host_ref)

    # Collapse chains to their ROOT host. `near` constraints chain freely — the
    # FMU's four port connectors are each declared near the previous one — but a
    # satellite is only ever expanded ONE level beside its host. A two-deep chain
    # therefore placed the head, placed its satellite, and silently dropped the
    # satellite's own satellite: J9 vanished from the drawing while staying in
    # the netlist and the BOM, which is the worst way for a part to go missing.
    for follower in list(near_host):
        seen, root = {follower}, near_host[follower]
        while root in near_host and root not in seen:
            seen.add(root)
            root = near_host[root]
        near_host[follower] = root  # a cycle stops at its first repeat


    # The decoupling bank: parts whose every pin lands on a rail. They have no
    # local net to be wired by, so they can only ever be a row of rail-connected
    # symbols — which is the bank convention, and correct for a monolithic MCU
    # symbol whose bypass caps cannot travel with a unit. Framing them as one
    # block is what turns that row from "scattered caps" into "the bank".
    # A part the design placed by `near` is not bank material: it has a home.
    bank = {p.ref for p in sheet_parts
            if pin_count[p.ref] <= _RIDER_MAX_PINS
            and p.ref not in near_host
            and not edges.get(p.ref)
            and all(net_at.get((p.ref, num)) in rail_nets
                    for num, *_r in layouts[p.component][1]
                    if net_at.get((p.ref, num)) is not None)
            and any(net_at.get((p.ref, num)) in rail_nets
                    for num, *_r in layouts[p.component][1])}

    # CHAINS. A rider may itself ride another rider — a ferrite from the rail
    # into a filter cap, a resistor into the LED it feeds — and that is a series
    # circuit, which is exactly what should be drawn end to end. What must not
    # happen is placing a rider before its host has a position: an earlier
    # version did, and the dependent part was silently dropped from the sheet.
    # So chains are kept and DEPTH is computed here; placement then walks in
    # depth order, and only a cycle (which has no valid order) is rejected.
    depth: dict[str, int] = {}
    for ref in sorted(riders):
        seen, cursor, hops = {ref}, ref, 0
        while cursor in riders:
            cursor = riders[cursor][1]
            hops += 1
            if cursor in seen:
                hops = -1  # a ring of parts riding each other: no valid order
                break
            seen.add(cursor)
        depth[ref] = hops
    for ref in [r for r, d in depth.items() if d < 0]:
        del riders[ref]

    # Drop riders that cannot be drawn: two on one side of a host would overlap.
    # Decided here, from pin GEOMETRY alone, because a dropped rider has to go
    # back into the flow layout — which is built below.
    def _pin_geom(ref: str) -> dict[str, tuple[Decimal, Decimal, str]]:
        _h, geom = layouts[part_by_ref[ref].component]
        return {num: (lx, ly, side) for num, _n, _r, lx, ly, side in geom}

    def _rider_far_rail(ref: str, nearpin: str) -> str | None:
        """"ground" / "supply" if the rider's OTHER pin lands on a rail."""
        for number, _n, _r, _lx, _ly, _s in layouts[part_by_ref[ref].component][1]:
            if number == nearpin:
                continue
            net = net_at.get((ref, number))
            if net in ground_nets:
                return "ground"
            if net in power_nets:
                return "supply"
        return None

    riders_of: dict[str, list[str]] = {}
    taken: dict[tuple[str, str], list[tuple[Decimal, bool]]] = {}
    # shallow first, so a rider is only ever accepted after its host is
    for ref in sorted(riders, key=lambda r: (depth[r], r)):
        _n, host, hostpin, mypin = riders[ref]
        if host in riders and host not in riders_of.get(riders[host][1], ()):
            continue  # its host lost its own lane, so there is nothing to ride
        hg, rg = _pin_geom(host).get(hostpin), _pin_geom(ref).get(mypin)
        if hg is None or rg is None:
            continue
        _hx, hy, hside = hg
        # a rider standing up needs its whole body plus its rail symbol clear of
        # the next one, not just the 5.08mm two flat ones need
        upright = _rider_far_rail(ref, mypin) is not None
        lane = taken.setdefault((host, hside), [])
        if any(abs(hy - y) < (_RIDER_MIN_DY_V if upright or was_up else _RIDER_MIN_DY)
               for y, was_up in lane):
            continue
        lane.append((hy, upright))
        riders_of.setdefault(host, []).append(ref)
    riders = {r: v for r, v in riders.items()
              if r in {x for refs in riders_of.values() for x in refs}}

    host_of: dict[str, str] = {}
    # A declared `near` outranks the inferred neighbour: it is the one statement
    # about this pairing that came from the engineer rather than from the graph.
    for follower, host_ref in sorted(near_host.items()):
        if follower not in riders and host_ref not in riders:
            host_of[follower] = host_ref
    for part in sheet_parts:
        if pin_count[part.ref] != 2 or part.ref in riders or part.ref in host_of:
            continue
        hosts: list[tuple[int, str]] = []
        for net in nets_of.get(part.ref, ()):
            if len(net.nodes) > _SAT_MAX_FANOUT:
                continue  # a rail is not a host; it touches everything
            for node in net.nodes:
                # Same subsystem only. A sensor's CS pull-up also touches the MCU,
                # and the MCU has more pins, so an unconstrained "richest neighbour"
                # rule drags the pull-up across the sheet into the FMU block and
                # fragments both groups. The part belongs with its sensor.
                if (node.part_ref != part.ref
                        and pin_count.get(node.part_ref, 0) >= 4
                        and _group_of(node.part_ref) == _group_of(part.ref)):
                    hosts.append((-pin_count[node.part_ref], node.part_ref))
        if hosts:  # richest-pinned neighbour wins, ties by ref
            host_of[part.ref] = sorted(hosts)[0][1]

    satellites: dict[str, list[ir.PartLow]] = {}
    for part in sheet_parts:
        if (h := host_of.get(part.ref)) is not None:
            satellites.setdefault(h, []).append(part)

    # Bank parts sort LAST inside their block. They are only ever a row of
    # rail-connected symbols, and while they were interleaved with the real
    # clusters their bounding box swallowed the connector and the crystal — a
    # frame that encloses three other frames says nothing.
    ordered: list[ir.PartLow] = []
    for part in sorted(sheet_parts, key=lambda p: (_group_index[_group_of(p.ref)],
                                                   p.ref in bank,
                                                   -layouts[p.component][0], p.ref)):
        if part.ref in host_of or part.ref in riders:
            continue  # placed with its host — beside it, or wired onto its pin
        ordered.append(part)
        ordered.extend(sorted(satellites.get(part.ref, ()), key=lambda p: p.ref))
    # A 100-pin MCU symbol is ~15x taller than a 0402 capacitor, and a row is as
    # tall as its tallest member, so any row mixing the two wastes that whole
    # difference across its full width. Oversized symbols get a row to themselves.
    heights = sorted(layouts[p.component][0] for p in sheet_parts)
    median_h = heights[len(heights) // 2] if heights else _D(1)
    tall_cut = median_h * _SCH_TALL_FACTOR
    # Room a pin actually needs outboard of the symbol. Reserving a flat 30mm for
    # every pin is what made the sheet metres wide: most labels are far shorter,
    # a rail is a small graphic, and a wired pin needs nothing at all.
    wired = {n for n, _h, _hp, _mp in riders.values()}

    def _pin_space(ref: str, want: str) -> Decimal:
        need = _D(0)
        for number, _n, _r, _lx, _ly, side in layouts[part_by_ref[ref].component][1]:
            if side != want:
                continue
            net = net_at.get((ref, number))
            if net is None or net in wired:
                continue
            # a GROUND port is just its triangle; a SUPPLY also prints its name
            # horizontally outward, which is as wide as any label
            if net in ground_nets:
                room = _SCH_STUB + _PWR_SYM_W
            else:
                room = _SCH_STUB + _D(len(net)) * _SCH_CHAR_W
                if net in power_nets:
                    room += _PWR_SYM_W
            need = max(need, room)
        return need

    def _stand_off(ref: str) -> Decimal:
        """How far the outermost pin stands from the symbol origin."""
        return max((abs(lx) for _n, _nm, _r, lx, _ly, _s
                    in layouts[part_by_ref[ref].component][1]), default=_D(0))

    def _upright(ref: str, nearpin: str) -> bool:
        return _rider_far_rail(ref, nearpin) is not None

    def _rail_name_width(ref: str, nearpin: str) -> Decimal:
        """How much horizontal room the rail name at a rider's far pin needs.

        A ground port hides its name (the triangle says it), so it needs none.
        """
        for number, _n, _r, _lx, _ly, _s in layouts[part_by_ref[ref].component][1]:
            if number == nearpin:
                continue
            net = net_at.get((ref, number))
            if net in power_nets:
                return _SCH_STUB + _D(len(net)) * _SCH_CHAR_W + _PWR_SYM_W
        return _D(0)

    def _rider_offset(host: str, hside: str, upright: bool) -> Decimal:
        """How far outboard of the host pin the rider's near pin sits.

        An upright rider's BODY is vertical, so it would cross the wire stubs of
        the host's other pins if it stood in the label column — a wire endpoint
        touching another wire is a junction in KiCad, i.e. a short. It stands
        clear of that column instead.
        """
        if not upright:
            return _RIDER_GAP
        return _snap(_SCH_STUB + _pin_space(host, hside) + _SCH_STUB)

    def _chain_span(host: str, rider: str, hside: str) -> Decimal:
        """Width a rider claims outboard of its host pin, INCLUDING its own.

        A chain (rail -> ferrite -> filter cap) extends outward one part at a
        time, so reserving only the first rider's width leaves the rest of the
        chain overrunning into the next cell and drawing over its symbols. The
        recursion is finite because cycles were rejected when depth was computed.
        """
        mypin = riders[rider][3]
        rside = _pin_geom(rider)[mypin][2]
        up = _upright(rider, mypin)
        offset = _rider_offset(host, hside, up)
        if up:
            # It stands vertical, so its BODY claims no outward width — but the
            # rail it terminates on still prints its name HORIZONTALLY, and that
            # name is as wide as any label. Reserving a token 2.54mm for it is
            # what let a buzzer connector's VDD_5V_PERIPH print straight through
            # the MOSFET beside it.
            return offset + max(_D("2.54"), _rail_name_width(rider, mypin))
        far = "left" if rside == "right" else "right"
        own = _stand_off(rider) * 2 + _pin_space(rider, far if rside != hside else rside)
        # whatever rides the rider extends further out on the same side
        beyond = max((_chain_span(rider, nxt, hside)
                      for nxt in riders_of.get(rider, ())), default=_D(0))
        return offset + max(own, _stand_off(rider) + beyond)

    def _cell(part: ir.PartLow) -> tuple[Decimal, Decimal, Decimal]:
        """(width, height, x of the symbol origin from the cell's left edge)."""
        box_h, _ = layouts[part.component]
        need = {"left": _pin_space(part.ref, "left"),
                "right": _pin_space(part.ref, "right")}
        for rref in riders_of.get(part.ref, ()):
            hostpin = riders[rref][2]
            hside = _pin_geom(part.ref)[hostpin][2]
            need[hside] = max(need[hside], _chain_span(part.ref, rref, hside))
        return (_snap(_stand_off(part.ref) * 2 + need["left"] + need["right"]
                      + _SCH_COL_GAP),
                _snap(box_h * 2 + _SCH_ROW_GAP),
                _snap(need["left"] + _stand_off(part.ref)))

    # Each module wraps into a BLOCK roughly _BLOCK_ASPECT wide for its area,
    # instead of running off in one row. A subsystem drawn as a compact rectangle
    # is what a reader recognises as "the buck converter"; the same parts in a
    # 1.2m strip are just a queue.
    block_w: dict[str, Decimal] = {}
    for part in ordered:
        cw, ch, _o = _cell(part)
        g_ = _group_of(part.ref)
        block_w[g_] = block_w.get(g_, _D(0)) + cw * ch  # area, folded to width below
    widest: dict[str, Decimal] = {}
    for part in ordered:
        g_ = _group_of(part.ref)
        widest[g_] = max(widest.get(g_, _D(0)), _cell(part)[0])
    for g_, area in block_w.items():
        target = (area * _BLOCK_ASPECT).sqrt()
        block_w[g_] = max(widest[g_], _snap(target))

    # ---- pass 1: lay each module out inside its own block, block-relative ----
    rel_of: dict[str, list[tuple[ir.PartLow, Decimal, Decimal]]] = {}
    size_of: dict[str, tuple[Decimal, Decimal]] = {}
    for part in ordered:
        rel_of.setdefault(_group_of(part.ref), []).append((part, _D(0), _D(0)))
    for gname, entries in rel_of.items():
        bx, by, brow, bw = _D(0), _D(0), _D(0), _D(0)
        rel = []
        started_bank = False
        for part, _z1, _z2 in entries:
            box_h, _ = layouts[part.component]
            cell_w, cell_h, off_x = _cell(part)
            # The bank starts its own row. Sorting it last is not enough: the
            # first bypass cap would land in whatever row the debug connector
            # left half-empty, and the bank's frame would then reach up and
            # enclose that connector's frame.
            first_bank = part.ref in bank and not started_bank
            if first_bank:
                started_bank = True
            # a host keeps its riders alongside it, so it is not given a solo row
            solo = box_h > tall_cut and not satellites.get(part.ref)
            if (first_bank and bx > 0) or (solo and bx > 0) \
                    or bx + cell_w > block_w[gname]:
                bx, by, brow = _D(0), _snap(by + brow + _SCH_ROW_GAP), _D(0)
            rel.append((part, _snap(bx + off_x), _snap(by + cell_h / 2)))
            brow = max(brow, cell_h)
            bw = max(bw, bx + cell_w)
            if solo:  # close the row behind it so nothing shares the empty space
                bx, by, brow = _D(0), _snap(by + cell_h + _SCH_ROW_GAP), _D(0)
            else:
                bx = _snap(bx + cell_w)
        rel_of[gname] = rel
        size_of[gname] = (bw, _snap(by + brow))

    # ---- pass 2: pack the blocks across the page ----
    # Blocks flow left to right and wrap, so a board of twelve small subsystems
    # fills a page instead of running down a 2.4m column. The wrap width is
    # chosen by trying each standard sheet from A4 up and keeping the first the
    # drawing actually fits on — the page follows the circuit, and a reviewer
    # gets "A2" rather than "User 513.08 x 944.88".
    # Blocks are laid out by ZONE, then by declaration order inside a zone:
    # power, compute, peripherals, interface. Signal flows outward from the
    # middle of the drawing and power flows inward, which is the arrangement a
    # reviewer reads without being told.
    order = sorted(rel_of, key=lambda g: (zone_of(g, low, comps), _group_index[g]))
    widest_block = max([w for w, _h in size_of.values()], default=_D(0))

    def _pack(target: Decimal):
        bx, by, brow, right = _SCH_MARGIN, _SCH_MARGIN, _D(0), _D(0)
        out_pos, out_box = [], {}
        for gname in order:
            bw, bh = size_of[gname]
            if bx > _SCH_MARGIN and bx + bw > target:
                bx, by, brow = _SCH_MARGIN, _snap(by + brow + _SCH_ROW_GAP * 2), _D(0)
            out_pos.append((gname, bx, by))
            out_box[gname] = (bx, by, _snap(bx + bw), _snap(by + bh))
            right = max(right, bx + bw)
            brow = max(brow, bh)
            bx = _snap(bx + bw + _SCH_COL_GAP * 2)
        return out_pos, out_box, _snap(right + _SCH_MARGIN), _snap(by + brow + _SCH_MARGIN)

    packed, blocks, page_w, page_h = _pack(max(_PAGE_TARGET_W, widest_block))
    paper_fit: str | None = None
    for sheet_name, sheet_w, sheet_h in _PAPERS:
        cand = _pack(sheet_w - _SCH_MARGIN * 2)
        if cand[2] <= sheet_w and cand[3] <= sheet_h - _TITLE_H:
            packed, blocks, page_w, page_h = cand
            paper_fit = sheet_name
            break

    # Centre the drawing on the sheet it landed on. The page snaps UP to a
    # standard size, so a small module on an A4 would otherwise sit in the
    # top-left corner with three quarters of the sheet blank — which reads as an
    # unfinished page rather than a small circuit.
    shift_x, shift_y = _D(0), _D(0)
    if paper_fit is not None:
        sheet_w, sheet_h = next((w, h) for n, w, h in _PAPERS if n == paper_fit)
        shift_x = _snap(max(_D(0), (sheet_w - page_w) / 2))
        # centred in the drawing area, which stops above the title block
        shift_y = _snap(max(_D(0), (sheet_h - _TITLE_H - page_h) / 2))
        blocks = {g: (x1 + shift_x, y1 + shift_y, x2 + shift_x, y2 + shift_y)
                  for g, (x1, y1, x2, y2) in blocks.items()}

    for gname, bx, by in packed:
        bx, by = _snap(bx + shift_x), _snap(by + shift_y)
        for part, rx, ry in rel_of[gname]:
            placed.append((part, _snap(bx + rx), _snap(by + ry), 0))
        group_titles.append((gname, bx, by))

    # ---- riders: onto the pin they serve, and the wire that joins them ----
    # A rider whose far end is a rail STANDS UP: the wire leaves the host pin,
    # the part hangs off it, and the rail symbol sits at the bottom (ground) or
    # top (supply). That is how a decoupling cap or a pull-up is drawn, and it
    # is why the reference reads as a circuit. Everything else stays in line
    # with the pin, where a straight wire is enough.
    pos = {p.ref: (px, py) for p, px, py, _r in placed}
    # A chained rider's host is itself a rider and may be TURNED, so the host
    # pin has to be transformed by the host's own rotation — assuming 0 would
    # aim the wire at where the pin would have been unrotated.
    rot_of: dict[str, int] = {p.ref: r for p, _px, _py, r in placed}
    wires: list[tuple[Decimal, Decimal, Decimal, Decimal, str]] = []
    wire_labels: list[tuple[Decimal, Decimal, Decimal, str]] = []
    rail_ends: list[tuple[Decimal, Decimal, str]] = []
    drawn_nets: set[str] = set()
    wired_pins: set[tuple[str, str]] = set()
    for ref in sorted(riders, key=lambda r: (depth[r], r)):
        nname, host, hostpin, mypin = riders[ref]
        if ref not in riders_of.get(host, ()):
            continue  # lost its lane
        if host not in pos:
            continue  # host never placed; the flow layout owns this part
        hx, hy = pos[host]
        hgeom = _pin_geom(host)
        if True:
            hlx, hly, hside = hgeom[hostpin]
            rlx, rly, rside = _pin_geom(ref)[mypin]
            host_rot = rot_of.get(host, 0)
            ax, ay = _pin_abs(hx, hy, hlx, hly, host_rot)
            sdx, _sdy = _stub_dir(hside, host_rot)
            hside = "right" if sdx > 0 else "left"
            far = _rider_far_rail(ref, mypin)
            off = _rider_offset(host, hside, far is not None)
            bx = _snap(ax + off if hside == "right" else ax - off)
            if far == "ground":
                # near pin up, body hanging down to the ground symbol
                rot = 270 if rside == "left" else 90
            elif far is not None:
                rot = 90 if rside == "left" else 270  # supply above
            else:
                # Turn the rider round when its connecting pin points away from
                # the host — the cap whose P2 does the decoupling, not its P1.
                rot = 180 if rside == hside else 0
            cx_, cy_ = _pin_origin(bx, ay, rlx, rly, rot)
            placed.append((part_by_ref[ref], cx_, cy_, rot))
            pos[ref] = (cx_, cy_)
            rot_of[ref] = rot
            wires.append((ax, ay, bx, ay, nname))
            # Only a net whose EVERY endpoint is now joined by copper can lose
            # its labels. A net that also reaches elsewhere (the crystal loop
            # that runs on to the MCU) keeps them, and gets one label on this
            # wire so the two halves are one net rather than two — the drawing
            # would look right either way, which is exactly why this is checked
            # by the round-trip oracle and not by eye.
            if len(drawable.get(nname, ())) == 2:
                drawn_nets.add(nname)
            else:
                wire_labels.append((ax, ay, bx, nname))
            wired_pins |= {(host, hostpin), (ref, mypin)}

    # ---- parallel groups: ONE ladder, not N isolated symbols ----
    # Eleven bypass caps across the same rail are eleven copies of the same
    # two-terminal drawing, each with its own rail label and its own ground
    # symbol. A human draws one rail wire along the top, the parts hanging off
    # it, one return along the bottom — which is what says "these are in
    # parallel" at a glance. The parts are already placed in rows by the flow
    # layout, so a ladder is every run of them that shares a row AND the same
    # pair of nets; a run of one is just a part.
    junctions: list[tuple[Decimal, Decimal]] = []
    pairs: dict[str, tuple[str, ...]] = {}
    for part in sheet_parts:
        if part.ref in riders or pin_count[part.ref] != 2:
            continue
        nets = {net_at.get((part.ref, num))
                for num, *_r in layouts[part.component][1]}
        nets.discard(None)
        if len(nets) == 2:
            pairs[part.ref] = tuple(sorted(nets))  # type: ignore[arg-type]

    by_row: dict[tuple[Decimal, tuple[str, ...]], list[tuple[Decimal, int, str]]] = {}
    for index, (part, px, py, _rot) in enumerate(placed):
        if part.ref in pairs:
            by_row.setdefault((py, pairs[part.ref]), []).append((px, index, part.ref))

    for (py, pair), members in sorted(by_row.items()):
        if len(members) < 2:
            continue
        members.sort()
        # ground goes at the bottom of a ladder; if neither net is ground the
        # pair is ordered, so the choice is still deterministic
        top, bottom = pair
        if top in ground_nets:
            top, bottom = bottom, top
        # Decide the whole ladder BEFORE changing anything. Rotating a part and
        # suppressing its pin stubs are commitments: doing them as we go and then
        # bailing on a member that cannot form a rung left the earlier ones
        # turned, silenced and connected to nothing — one diode floating off both
        # its nets, which is exactly the kind of break that looks fine on paper.
        plan: list[tuple[Decimal, str, int, int, dict, Decimal]] = []
        for px, index, ref in members:
            geom = _pin_geom(ref)
            top_pin = next((num for num, (_lx, _ly, _s) in geom.items()
                            if net_at.get((ref, num)) == top), None)
            if top_pin is None or len(geom) != 2:
                plan = []
                break
            # rot 270 puts the LEFT pin above the origin, rot 90 the right one
            plan.append((px, ref, index, 270 if geom[top_pin][0] < 0 else 90, geom,
                         abs(geom[top_pin][0])))
        # Every rung must reach the same two rail lines, so they must all stand
        # the same distance from their origin. A glyph does (3.81mm); a part
        # drawn as a box does not, and mixing them ran the rail straight past a
        # diode's pins — a part on the drawing, touching nothing.
        offsets = {off for *_rest, off in plan}
        if len(plan) < 2 or len(offsets) != 1:
            continue
        stand = offsets.pop()
        # Only the pins on the SHARED rail are taken over by the ladder. Each
        # rung's other end keeps its own port symbol, drawn directly beneath it
        # by the ordinary per-pin path — which is both the convention and
        # shorter: a common return wire running the width of the bank stretches
        # the block for no information, since a ground symbol already says
        # "ground" wherever it stands.
        for px, ref, index, rot, geom, _off in plan:
            placed[index] = (placed[index][0], px, py, rot)
            top_pin = next(num for num in geom
                           if net_at.get((ref, num)) == top)
            wired_pins.add((ref, top_pin))
        rungs = [(px, ref) for px, ref, _i, _r, _g, _o in plan]
        y_top = _snap(py - stand)
        for rung_y, rail in ((y_top, top),):
            for (x1, _r1), (x2, _r2) in zip(rungs, rungs[1:]):
                wires.append((x1, rung_y, x2, rung_y, f"{rail}/{x1}"))
            junctions += [(x, rung_y) for x, _r in rungs[1:-1]]
            # the rail arrives once, at the head of the ladder
            lead = _snap(rungs[0][0] - _SCH_STUB * 2)
            wires.append((lead, rung_y, rungs[0][0], rung_y, f"{rail}/lead{rung_y}"))
            rail_ends.append((lead, rung_y, rail))

    # Oversize keeps a User page rather than clipping content off the sheet.
    paper = (f'(paper "{paper_fit}")' if paper_fit
             else f'(paper "User" {page_w} {page_h})')

    sheet_uuid = _uid(source, "sch", group) if group else _uid(source, "sch")
    out = [
        "(kicad_sch",
        f"  (version {_SCH_VERSION})",
        '  (generator "ohm")',
        f'  (uuid "{sheet_uuid}")',
        f"  {paper}",
        *title_block(design, source, page, pages, sheet_note),
        "  (lib_symbols",
        *lib_defs,
        "  )",
    ]

    # ---- partition per module: a dashed box round its parts, titled ----
    # Purely graphical, no electrical meaning — but it is what turns a page of
    # symbols into "here is the buck converter, here is the IMU". KiCad 7 draws
    # sheet-level graphics as polylines; a closed 5-point one is the rectangle.
    inst_path = sheet_path or f"/{sheet_uuid}"
    # Every piece of text Ohm places, so the one text it may freely MOVE — a
    # frame title — can be put where nothing else already is. A net label is not
    # movable: it is bound to the wire it names, and sliding it off to tidy the
    # page would disconnect the net.
    taken_text: list[tuple[Decimal, Decimal, Decimal, Decimal]] = []
    # Symbol bodies are obstacles too: a rail name is drawn outward from its
    # port, and on a crowded pin that ran straight through the neighbouring
    # IC's own pin names. Seeded first so the very first name already avoids
    # them.
    for part, px, py, rot in placed:
        half = max(_stand_off(part.ref), layouts[part.component][0])
        taken_text.append((px - half, py - half, px + half, py + half))
    for jx, jy in sorted(set(junctions)):
        out.append(f'  (junction (at {jx} {jy}) (diameter 0.9144) (color 0 0 0 0)'
                   f' (uuid "{_uid(source, "jct", str(jx), str(jy))}"))')
    for ex, ey, rail_net in sorted(set(rail_ends), key=lambda t: (t[2], t[0], t[1])):
        # one rail symbol per ladder, at its head — the whole point of drawing a
        # ladder is that the rail arrives once instead of eleven times
        if rail_net in rail_nets:
            pwr_seq += 1
            sid = _pwr_id(rail_net)
            out.append(f'  (symbol (lib_id "ohm:{sid}") (at {ex} {ey} 0)'
                       " (unit 1) (in_bom no) (on_board no) (dnp no)")
            out.append(f'    (uuid "{_uid(source, "ldpwr", rail_net, str(ex), str(ey))}")')
            out.append(f'    (property "Reference" "{_pwr_ref(pwr_seq)}"'
                       f" (at {ex} {ey} 0)"
                       " (effects (font (size 1.27 1.27)) hide))")
            hide = " hide" if rail_net in ground_nets else ""
            ty = ey if rail_net in ground_nets else _snap(ey - _D("2.54"))
            out.append(f'    (property "Value" "{_q(rail_net)}" (at {ex} {ty} 0)'
                       f" (effects (font (size 1.27 1.27)){hide}))")
            out.append('    (pin "1" (uuid "'
                       f'{_uid(source, "ldpin", rail_net, str(ex), str(ey))}"))')
            out.append(f'    (instances (project "ohm" (path "{inst_path}"'
                       f' (reference "{_pwr_ref(pwr_seq)}") (unit 1))))')
            out.append("  )")
        else:
            out.append(f'  (label "{_q(rail_net)}" (at {ex} {ey} 180)'
                       " (effects (font (size 1.27 1.27)) (justify right bottom))"
                       f' (uuid "{_uid(source, "ldlbl", rail_net, str(ex), str(ey))}"))')

    # A label at a PIN cannot move — it sits on that pin's stub end and shifting
    # it would name a different net. A label riding a WIRE is not pinned like
    # that: every point along the wire is the same net, so the label may slide.
    # It was always dropped at the midpoint, which is where the crystal sits in
    # a crystal/load-cap/MCU cluster — that one blind midpoint accounted for
    # every "text prints over a symbol" on the MCU sheet. Slide it instead.
    # Measured with the CHECKER's own function against the CHECKER's own input
    # (the library block this file is about to carry), so "clear of that symbol"
    # means the same thing on both sides. A private estimate here — pin extents,
    # layout height — silently disagreed with D002 and reported every candidate
    # position as free.
    from ohm.check_drawing import _body_half

    _libtext = "\n".join(lib_defs)
    sym_boxes = [
        (px - hw, py - hh, px + hw, py + hh)
        for part, px, py, rot in placed
        for hw, hh in (_body_half(_libtext, part.component, rot),)
    ]
    for ax, ay, bx, nname in wire_labels:
        lo, hi = (ax, bx) if ax <= bx else (bx, ax)
        span = hi - lo
        # midpoint first (it reads as "this wire's name"), then progressively
        # further along it, then the ends — all still on the wire.
        cands = [(_snap(lo + span * _D(f)), ay, "left")
                 for f in ("0.5", "0.35", "0.65", "0.2", "0.8", "0.05", "0.95")]
        lx, ly, just = _free_spot(cands, nname, _D("1.27"), sym_boxes)
        out.append(f'  (label "{_q(nname)}" (at {lx} {ly} 0)'
                   f" (effects (font (size 1.27 1.27)) (justify {just} bottom))"
                   f' (uuid "{_uid(source, "wlbl", nname, str(lx), str(ly))}"))')

    hier_drawn: set[str] = set()
    for part, px, py, rot in placed:
        box_h, pin_geom = layouts[part.component]
        # KiCad turns a symbol's property text with the symbol, so an upright
        # part would carry sideways text through its neighbours. Compensate the
        # angle and stack ref/value beside the body instead of above and below.
        #
        # Modulo 180, not 360: a drawing has exactly two legal text angles, 0 and
        # 90 (read from the bottom edge or the right edge). Compensating into the
        # full circle put R5's value at 180 — printed upside down — and every
        # 90-degree part's text at 270, reading top-to-bottom. Both are the same
        # text either way, so the half-turn is free to drop.
        tang = (360 - rot) % 180
        if rot in (90, 270):
            tx = _snap(px + box_h + _SCH_GRID)
            ref_pos = f"{tx} {_snap(py - _SCH_GRID)} {tang}"
            val_pos = f"{tx} {_snap(py + _SCH_GRID)} {tang}"
            just = " (justify left)"
        else:
            ref_pos = f"{px} {_snap(py - box_h - _SCH_GRID * 2)} {tang}"
            val_pos = f"{px} {_snap(py + box_h + _SCH_GRID * 2)} {tang}"
            just = ""
        out.append(f'  (symbol (lib_id "ohm:{_q(part.component)}") (at {px} {py} {rot})'
                   " (unit 1) (in_bom yes) (on_board yes) (dnp no)")
        out.append(f'    (uuid "{_uid(source, "sym", part.ref)}")')
        out.append(f'    (property "Reference" "{_q(part.designator)}" (at {ref_pos})'
                   f" (effects (font (size 1.27 1.27)){just}))")
        out.append(f'    (property "Value" "{_q(schematic_value(part))}" (at {val_pos})'
                   f" (effects (font (size 1.27 1.27)){just}))")
        out.append(f'    (property "Footprint" "{_q(_footprint_id(part.package))}"'
                   " (at 0 0 0) (effects (font (size 1.27 1.27)) hide))")
        out.append(f'    (property "Ohm" "{_q(part.ref)}" (at 0 0 0)'
                   " (effects (font (size 1.27 1.27)) hide))")
        # Sourcing attrs ride the symbol so KiCad's own BOM export can see them.
        # HIDDEN on purpose: a part number is what you order by, not what you
        # read the circuit by, and printing GRM155R71C104KA88D beside all 47
        # bypass caps buries the values that carry the design intent.
        part_attrs = dict(part.attrs)
        for _k, _field in BOM_FIELDS.items():
            if (_v := part_attrs.get(_k)) is not None:
                out.append(f'    (property "{_field}" "{_q(_value_text(_v))}"'
                           " (at 0 0 0) (effects (font (size 1.27 1.27)) hide))")
        for number, *_rest in pin_geom:
            out.append(f'    (pin "{_q(number)}" (uuid "'
                       f'{_uid(source, "pin", part.ref, number)}"))')
        out.append(f'    (instances (project "ohm" (path "{inst_path}"'
                   f' (reference "{_q(part.designator)}") (unit 1))))')
        out.append("  )")

        # wire stub + net label at every connected pin
        comp_def = comps.get(part.component)
        pin_by_pad = {p.pad: p for p in comp_def.pins} if comp_def else {}
        # `unused_pins = open`: one line on a 100-pin MCU instead of 145
        # must_connect=false lines. Every pin left unwired on this part is a
        # deliberate open, so it gets a no-connect marker and ERC stays clean —
        # and a pin nobody accounted for is then visible because it has none.
        open_pins = str(dict(part.attrs).get("unused_pins", "")) == "open"
        # A component that declares NO pins at all — a fiducial, a mounting
        # hole — still gets one synthesized anchor pin from the pin-table
        # fallback above, so KiCad has something to place. That pin exists for
        # the emitter's benefit, not the design's, and it must carry a
        # no-connect: otherwise ERC reports an unconnected pin the source never
        # declared, and `unused_pins = open` cannot silence it because there is
        # no pin in the source to mark.
        anchor_only = comp_def is not None and not comp_def.pins
        for number, _name, _role, lx, ly, pside in pin_geom:
            # a turned symbol takes its pins' stubs with it
            sdx, sdy = _stub_dir(pside, rot)
            side = "left" if sdx < 0 else ("right" if sdx > 0 else pside)
            net_name = net_at.get((part.ref, number))
            if net_name is None:
                # An unconnected pin that the SOURCE says is deliberately open
                # (role=nc, or must_connect=false under ADR-015) becomes a KiCad
                # no-connect marker. Without this ERC flags every unused pin and
                # the findings that matter are invisible; with it, anything left
                # unflagged is a pin nobody has accounted for.
                pin_def = pin_by_pad.get(number)
                if anchor_only or (pin_def is not None
                                   and (pin_def.role == "nc"
                                        or pin_def.must_connect is False
                                        or open_pins)):
                    nx, ny = _pin_abs(px, py, lx, ly, rot)
                    out.append(f"  (no_connect (at {nx} {ny})"
                               f' (uuid "{_uid(source, "nc", part.ref, number)}"))')
                continue
            if net_name in drawn_nets or (part.ref, number) in wired_pins:
                continue  # joined by a wire, so no stub and no second label
            ax, ay = _pin_abs(px, py, lx, ly, rot)
            bx, by = _snap(ax + sdx * _SCH_STUB), _snap(ay + sdy * _SCH_STUB)
            out.append(f'  (wire (pts (xy {ax} {ay}) (xy {bx} {by}))'
                       " (stroke (width 0) (type default))"
                       f' (uuid "{_uid(source, "w", part.ref, number)}"))')
            if net_name in rail_nets:
                sid = _pwr_id(net_name)
                # graphic hangs below the pin for ground, above for a supply;
                # the pin sits AT the symbol origin, so it meets the wire end
                # whatever the rotation
                out.append(f'  (symbol (lib_id "ohm:{sid}") (at {bx} {by} 0)'
                           " (unit 1) (in_bom no) (on_board no) (dnp no)")
                out.append(f'    (uuid "{_uid(source, "pwr", part.ref, number)}")')
                pwr_seq += 1
                out.append(f'    (property "Reference" "{_pwr_ref(pwr_seq)}"'
                           f" (at {bx} {by} 0)"
                           " (effects (font (size 1.27 1.27)) hide))")
                if net_name in ground_nets:  # the triangle needs no caption
                    out.append(f'    (property "Value" "{_q(net_name)}" (at {bx} {by} 0)'
                               " (effects (font (size 1.27 1.27)) hide))")
                else:
                    ty = _snap(by - _D("2.54")) if sdy else by
                    out_x = bx - _D("1.27") if side == "left" else bx + _D("1.27")
                    out_j = "right" if side == "left" else "left"
                    # For a HORIZONTAL stub the name runs outward only: flipping
                    # it back over the part it belongs to trades one overlap for
                    # a worse one, landing on that part's own pin names. For a
                    # VERTICAL stub — an upright part, its port above or below —
                    # neither horizontal side is "back over" anything, so both
                    # are offered and the obstacle test picks the clear one.
                    # That is the case that printed a buzzer connector's
                    # VDD_5V_PERIPH straight through the MOSFET beside it.
                    flip_x = bx + _D("1.27") if side == "left" else bx - _D("1.27")
                    flip_j = "left" if side == "left" else "right"
                    # Outward is PREFERRED, not required. A rail name is a
                    # property — free to move, unlike a label bound to its wire —
                    # so when outward is blocked it may go the other way or step
                    # off the pin's row. Symbol bodies are among the obstacles,
                    # which is what stops the flipped position landing on the
                    # part's own pin names.
                    spots = [(out_x, ty, out_j)]
                    for dy in (_D("2.54"), _D("-2.54"), _D("5.08"), _D("-5.08")):
                        spots.append((out_x, _snap(ty + dy), out_j))
                    spots.append((flip_x, ty, flip_j))
                    for dy in (_D("2.54"), _D("-2.54")):
                        spots.append((flip_x, _snap(ty + dy), flip_j))
                    tx, ty, tj = _free_spot(spots, net_name, _D("1.27"), taken_text)
                    out.append(f'    (property "Value" "{_q(net_name)}" (at {tx} {ty} 0)'
                               " (effects (font (size 1.27 1.27))"
                               f" (justify {tj})))")
                    taken_text.append(_text_box(tx, ty, net_name, _D("1.27"), tj))
                out.append('    (pin "1" (uuid "'
                           f'{_uid(source, "pwrpin", part.ref, number)}"))')
                out.append(f'    (instances (project "ohm" (path "{inst_path}"'
                           f' (reference "{_pwr_ref(pwr_seq)}") (unit 1))))')
                out.append("  )")
                continue
            just = "right" if side == "left" else "left"
            lrot = 180 if side == "left" else 0  # text rotation, NOT the symbol's
            if net_name in hier_nets and net_name not in hier_drawn:
                # Leaves this sheet: ONE hierarchical label, matched by a pin of
                # the same name on this sheet's symbol in the root. The net's other
                # pins on this sheet keep plain labels, which join it locally —
                # that is how a human draws it, and it keeps the sheet symbol's
                # pin list one entry per signal.
                hier_drawn.add(net_name)
                out.append(f'  (hierarchical_label "{_q(net_name)}"'
                           f" (shape bidirectional) (at {bx} {by} {lrot})"
                           f" (effects (font (size 1.27 1.27)) (justify {just}))"
                           f' (uuid "{_uid(source, "hlbl", part.ref, number)}"))')
                continue
            out.append(f'  (label "{_q(net_name)}" (at {bx} {by} {lrot})'
                       f" (effects (font (size 1.27 1.27)) (justify {just} bottom))"
                       f' (uuid "{_uid(source, "lbl", part.ref, number)}"))')
            taken_text.append(_text_box(bx, by, net_name, _D("1.27"), just))

    # The module frame is the packed rectangle EXPANDED to whatever actually
    # landed inside it. The packing sizes a block from its cells, but a rider
    # placed outboard of an edge cell — or the arrows on a rotated LED — can
    # reach past that, and the frame was then drawn straight through the symbol.
    for part, px, py, _r in placed:
        grp = _group_of(part.ref)
        if grp not in blocks:
            continue
        half = max(_stand_off(part.ref), layouts[part.component][0]) + _SCH_GRID * 3
        x1, y1, x2, y2 = blocks[grp]
        blocks[grp] = (min(x1, _snap(px - half)), min(y1, _snap(py - half)),
                       max(x2, _snap(px + half)), max(y2, _snap(py + half)))

    for title, _tx, _ty in group_titles:
        if title not in blocks:
            continue
        # the block's own rectangle, so two partitions can never overlap
        x1, y1, x2, y2 = blocks[title]
        out.append(f"  (polyline (pts (xy {x1} {y1}) (xy {x2} {y1}) (xy {x2} {y2})"
                   f" (xy {x1} {y2}) (xy {x1} {y1}))"
                   " (stroke (width 0.2) (type dash))"
                   f' (uuid "{_uid(source, "box", title)}"))')
        tx, ty, tj = _free_spot(
            [(x1, _snap(y1 - _SCH_GRID), "left"),
             (x2, _snap(y1 - _SCH_GRID), "right"),
             (x1, _snap(y1 - _SCH_GRID * 3), "left")], title, _D(3), taken_text)
        out.append(f'  (text "{_q(title)}" (at {tx} {ty} 0)'
                   f' (effects (font (size 3 3) bold) (justify {tj} bottom))'
                   f' (uuid "{_uid(source, "grp", title)}"))')
        taken_text.append(_text_box(tx, ty, title, _D(3), tj))

    # ---- sub-blocks: a lighter box round each cluster INSIDE the module ----
    # A module box on a sheet that holds one module frames the whole page and
    # says nothing. The structure a reader wants is one level down: the crystal
    # and its load caps, the debug connector and its cap, the bank of bypass
    # caps. Those clusters already exist — a host and the riders wired onto its
    # pins — they were simply never drawn. Titled by KIND where the component
    # declares one ("Crystal", "Connector"), because that is the functional name
    # a reader is looking for; by designator otherwise.
    for title, x1, y1, x2, y2 in sub_blocks(pos, layouts, part_by_ref, riders_of,
                                            bank, comps, _stand_off):
        out.append(f"  (polyline (pts (xy {x1} {y1}) (xy {x2} {y1}) (xy {x2} {y2})"
                   f" (xy {x1} {y2}) (xy {x1} {y1}))"
                   " (stroke (width 0.12) (type dot))"
                   f' (uuid "{_uid(source, "subbox", title, str(x1), str(y1))}"))')
        tx, ty, tj = _free_spot(
            [(_snap(x1 + _SCH_GRID), _snap(y1 + _SCH_GRID * 2), "left"),
             (_snap(x2 - _SCH_GRID), _snap(y1 + _SCH_GRID * 2), "right"),
             (_snap(x1 + _SCH_GRID), _snap(y2 - _SCH_GRID), "left")],
            title, _D("1.6"), taken_text)
        out.append(f'  (text "{_q(title)}" (at {tx} {ty} 0)'
                   f' (effects (font (size 1.6 1.6) bold) (justify {tj} bottom))'
                   f' (uuid "{_uid(source, "subttl", title, str(x1), str(y1))}"))')
        taken_text.append(_text_box(tx, ty, title, _D("1.6"), tj))

    for ax, ay, bx, by, nname in wires:
        out.append(f'  (wire (pts (xy {ax} {ay}) (xy {bx} {by}))'
                   " (stroke (width 0) (type default))"
                   f' (uuid "{_uid(source, "netw", nname, str(ax), str(ay))}"))')

    notes = sheet_notes(low, design) if group is None else []
    if notes:
        ny = _snap(page_h - _SCH_MARGIN - _SCH_ROW_GAP * (len(notes) + 1))
        out.append(f'  (text "NOTES" (at {_SCH_MARGIN} {ny} 0)'
                   ' (effects (font (size 2 2) bold) (justify left bottom))'
                   f' (uuid "{_uid(source, "noteshdr")}"))')
        for i, text in enumerate(notes, start=1):
            ty = _snap(ny + _SCH_ROW_GAP * i)
            out.append(f'  (text "{i}. {_q(text)}" (at {_SCH_MARGIN} {ty} 0)'
                       ' (effects (font (size 1.5 1.5)) (justify left bottom))'
                       f' (uuid "{_uid(source, "note", str(i))}"))')

    if group is None:  # a child sheet carries no sheet_instances; the root does
        out.append('  (sheet_instances (path "/" (page "1")))')
    out.append(")")
    return "\n".join(out) + "\n"


# ---------- hierarchy ----------
_SH_PIN_PITCH = _D("2.54")
_SH_CHAR_W = _D("1.1")  # conservative advance per char at font size 1.27, mm


def sheet_groups(low: ir.IRLow) -> list[str]:
    """Top-level modules, one sheet each. Fewer than two means no hierarchy."""
    return sorted({_group_of(p.ref) for p in low.parts})


_BANK_MIN = 3  # fewer than this is a few caps, not a bank worth framing
# Kinds that name a PART rather than a function. An LED with its series resistor
# is an indicator, not a resistor — so when a cluster mixes a generic passive
# with something specific, the specific one names the box, whichever of them
# happened to host the other.
_GENERIC_KINDS = frozenset({"TwoTerminal", "Resistor", "Capacitor", "Inductor",
                            "Fuse"})

# Functional zones, in reading order. A reviewer expects a board laid out the way
# it works: power generation first, the thing that computes next, what it talks
# to after that, and the outside world last. Ohm can tell these apart because
# the design DECLARED them — the kind taxonomy (ADR-008) already says which part
# is a regulator and which is a connector, so no heuristic on names is needed.
_ZONE_KINDS = (
    # deliberately NOT Fuse/TVS: protection parts sit in every block, so they
    # would drag a USB port into the power zone
    ("power", {"LDO", "SwitchingConverter", "VoltageReference"}),
    ("compute", {"Mcu", "Fpga", "Processor"}),
    ("peripheral", {"SpiSensor", "I2cSensor", "MemoryDevice", "Sensor",
                    "Transceiver", "Oscillator", "Crystal"}),
    ("interface", {"Connector"}),
)


def zone_of(group: str, low: ir.IRLow, comps: dict) -> int:
    """Which functional zone a module block belongs to (lower sorts earlier).

    The EARLIEST zone any member matches wins, because zones are a precedence,
    not a vote: a block holding a regulator is the power block whatever else it
    contains. Ranking by pin count instead put `pwr` in the interface zone, its
    6-pin input connector outranking the two regulators the block exists for.
    Unclassified blocks sort where a peripheral would.
    """
    best = 2
    for part in low.parts:
        if _group_of(part.ref) != group:
            continue
        comp = comps.get(part.component)
        if comp is None:
            continue
        for index, (_name, kinds) in enumerate(_ZONE_KINDS):
            if kinds & set(comp.kinds):
                best = min(best, index)
    return best


def _text_box(x: Decimal, y: Decimal, text: str, size: Decimal,
              justify: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """Axis-aligned extent of a piece of sheet text, for collision testing."""
    width = _D(len(text)) * size * _D("0.8")
    if justify == "right":
        x1 = x - width
    elif justify == "center":
        x1 = x - width / 2
    else:
        x1 = x
    return (x1, y - size, x1 + width, y + size / 2)


def _free_spot(candidates, text: str, size: Decimal,
               taken: list[tuple[Decimal, Decimal, Decimal, Decimal]]):
    """First candidate (x, y, justify) whose text does not overlap `taken`.

    Titles are the only sheet text Ohm may move freely — a net label is bound to
    the wire it names, and shifting it off would disconnect the net rather than
    tidy it. So the collision is resolved by moving the label's *neighbour*.
    Falls back to the first candidate, because a title in a busy corner still
    beats no title.
    """
    for x, y, justify in candidates:
        box = _text_box(x, y, text, size, justify)
        if not any(box[0] < t[2] and box[2] > t[0]
                   and box[1] < t[3] and box[3] > t[1] for t in taken):
            return x, y, justify
    return candidates[0]


def _cluster_title(members, part_by_ref, comps) -> str:
    """The functional name for a cluster box: its least generic member kind."""
    best = None
    for rank, ref in enumerate(members):  # host first, so it wins ties
        comp = comps.get(part_by_ref[ref].component)
        for kind in reversed(comp.kinds if comp else ()):
            score = 1 if kind in _GENERIC_KINDS else 0
            if best is None or (score, rank) < best[:2]:
                best = (score, rank, kind)
            break
    if best is not None:
        return best[2]
    return part_by_ref[members[0]].designator


def sub_blocks(pos, layouts, part_by_ref, riders_of, bank, comps, stand_off):
    """[(title, x1, y1, x2, y2)] — one frame per cluster inside a module block.

    A cluster is a host with riders wired onto its pins, or the bank of parts
    that touch nothing but rails (the bypass caps). Both are already computed by
    placement; this only draws them. Boxes are dotted and thinner than the module
    frame so the nesting reads as nesting.
    """
    out = []

    def _bbox(refs):
        xs, ys = [], []
        for ref in refs:
            if ref not in pos:
                return None
            px, py = pos[ref]
            box_h, _ = layouts[part_by_ref[ref].component]
            # a TURNED symbol swaps its axes, and a glyph draws past its pins
            # (an LED's arrows). A square envelope of the larger dimension is
            # rotation-independent and cannot under-reach either way.
            half = max(stand_off(ref), box_h) + _SCH_GRID
            xs += [px - half, px + half]
            ys += [py - half, py + half]
        pad = _SCH_GRID * 2
        # extra headroom at the top: the title is drawn INSIDE the frame, where
        # nothing outside it can collide with it. Drawn above the frame it
        # landed exactly where a rail name arriving at the block sits, and the
        # two overprinted. Extra at the bottom for the value text a part carries.
        return (_snap(min(xs) - pad), _snap(min(ys) - pad - _SCH_GRID * 2),
                _snap(max(xs) + pad), _snap(max(ys) + pad + _SCH_GRID * 2))

    for host in sorted(riders_of):
        members = [host, *riders_of[host]]
        if len(members) < 2:
            continue
        box = _bbox(members)
        if box is None:
            continue
        out.append((_cluster_title(members, part_by_ref, comps), *box))

    # Only rows that are ENTIRELY bank get framed. One bypass cap sharing a row
    # with the debug connector was enough to stretch the bank's bounding box
    # across two other clusters and enclose them — a frame around three other
    # frames tells a reader nothing.
    #
    # "Entirely" has to be TESTED, not assumed from the sort order. Bank parts
    # sort last within a block, but a part that is not bank can still land on a
    # bank row, and then the bbox over all bank members quietly swallows it: the
    # frame said "Decoupling" over a 3.3V regulator, two diodes and four test
    # points. So rows are built from the placed positions and a row is kept only
    # if nothing outside the bank sits on it.
    # Rows have to be RECOVERED, not read off y: every part in a row is centred
    # in its own cell, so a tall part and a 0402 on the same row differ in y by
    # half their height difference. Split on the gaps instead — a real row break
    # is at least _SCH_ROW_GAP, which no within-row offset reaches.
    rows: list[list[str]] = []
    for ref in sorted(pos, key=lambda r: (pos[r][1], pos[r][0])):
        y = pos[ref][1]
        if rows and y - max(pos[m][1] for m in rows[-1]) < _SCH_ROW_GAP:
            rows[-1].append(ref)
        else:
            rows.append([ref])
    # ...and only a CONTIGUOUS run of pure rows can be framed. Two pure rows
    # with the regulator's row between them still bound a rectangle that
    # contains the regulator; the frame has to stop at the first row that
    # is not all bank.
    best: list[str] = []
    run: list[str] = []
    for row in rows + [["\0"]]:  # sentinel closes the final run
        if all(m in bank for m in row):
            run.extend(row)
        else:
            best, run = max((best, run), key=len), []
    if len(best) >= _BANK_MIN and (box := _bbox(best)) is not None:
        out.append(("Decoupling", *box))
    return out


def sheet_notes(low: ir.IRLow, design: ir.Design | None) -> list[str]:
    """Engineering notes for the drawing: declared, plus the ones Ohm can derive.

    A `near(a, b, 2mm)` constraint already says "these must be close"; layout
    reads it from constraints.json, but the person reviewing the PDF cannot. It
    is the same fact, and it belongs on the sheet in words.
    """
    notes = [n for m in (design.modules if design else ()) for n in m.notes]
    for c in low.constraints:
        if c.kind != "near" or len(c.nets) < 2:
            continue
        dist = next((p for p in c.params if parse_mm(p) is not None), None)
        within = f" within {dist}" if dist else ""
        notes.append(f"PLACEMENT: {c.nets[0]} must sit{within} of {c.nets[1]}")
    return notes


def _crossing(low: ir.IRLow, design: ir.Design | None) -> dict[str, list[str]]:
    """group -> the net names that leave it, sorted. Rails are excluded.

    "Leaves the group" is decided by the NODES, not by ``net.ports``: a net whose
    pins all sit in one module is fully drawn on that sheet whatever the source
    called it, so a sheet-local label is complete and correct for it. Rails are
    power symbols, which connect across sheets on their own.
    """
    comps = {c.name: c for c in design.components} if design is not None else {}
    g, p = _rail_nets(low, comps)
    rails = g | p
    renames = diffpair_renames(low)
    out: dict[str, set[str]] = {}
    for net in low.nets:
        if net.name in rails:
            continue
        groups = {_group_of(node.part_ref) for node in net.nodes}
        if len(groups) < 2:
            continue
        for grp in groups:
            out.setdefault(grp, set()).add(renames.get(net.name, net.name))
    return {grp: sorted(names) for grp, names in out.items()}


def _emit_root_sheet(low: ir.IRLow, source: str, stem: str,
                     crossing: dict[str, list[str]],
                     design: ir.Design | None = None) -> str:
    """The top sheet: one box per module, wired to its neighbours by name.

    Deliberately carries no parts of its own — every part lives on its module's
    sheet, so this page is the block diagram and nothing else.
    """
    groups = sheet_groups(low)
    root_uuid = _uid(source, "sch")
    boxes, wires = [], []
    cx, cy, row_h = _SCH_MARGIN, _SCH_MARGIN, _D(0)
    for page, grp in enumerate(groups, start=2):
        pins = crossing.get(grp, [])
        longest = max((len(n) for n in pins), default=len(grp))
        box_w = _snap(max(_D(25), _D(longest) * _SH_CHAR_W + _SCH_STUB * 2))
        box_h = _snap(_SH_PIN_PITCH * (len(pins) + 2))
        label_w = _snap(_D(longest) * _SH_CHAR_W + _SCH_STUB * 2)
        cell_w = label_w + box_w + _SCH_COL_GAP
        if cx + cell_w > _A3_W - _SCH_MARGIN and cx > _SCH_MARGIN:
            cx, cy, row_h = _SCH_MARGIN, _snap(cy + row_h + _SCH_ROW_GAP), _D(0)
        bx, by = cx + label_w, cy
        suid = _uid(source, "sheetsym", grp)
        boxes.append(f"  (sheet (at {bx} {by}) (size {box_w} {box_h})")
        boxes.append("    (stroke (width 0) (type solid))"
                     " (fill (color 0 0 0 0.0000))")
        boxes.append(f'    (uuid "{suid}")')
        boxes.append(f'    (property "Sheetname" "{_q(grp)}" (at {bx} {by - _D("0.76")} 0)'
                     " (effects (font (size 1.524 1.524)) (justify left bottom)))")
        boxes.append(f'    (property "Sheetfile" "{_q(stem)}_{_q(grp)}.kicad_sch"'
                     f' (at {bx} {by + box_h + _D("0.76")} 0)'
                     " (effects (font (size 1.524 1.524)) (justify left top)))")
        for i, name in enumerate(pins, start=1):
            py = _snap(by + _SH_PIN_PITCH * i)
            boxes.append(f'    (pin "{_q(name)}" bidirectional (at {bx} {py} 180)'
                         " (effects (font (size 1.27 1.27)) (justify left))"
                         f' (uuid "{_uid(source, "shpin", grp, name)}"))')
            # stub + plain label: same-named labels on THIS sheet are what joins
            # one module's pin to another's, so the boxes are actually wired.
            ex = _snap(bx - _SCH_STUB)
            wires.append(f'  (wire (pts (xy {bx} {py}) (xy {ex} {py}))'
                         " (stroke (width 0) (type default))"
                         f' (uuid "{_uid(source, "shw", grp, name)}"))')
            wires.append(f'  (label "{_q(name)}" (at {ex} {py} 180)'
                         " (effects (font (size 1.27 1.27)) (justify right bottom))"
                         f' (uuid "{_uid(source, "shlbl", grp, name)}"))')
        boxes.append(f'    (instances (project "ohm" (path "/{root_uuid}"'
                     f' (page "{page}"))))')
        boxes.append("  )")
        cx = _snap(cx + cell_w)
        row_h = max(row_h, box_h + _SCH_ROW_GAP)

    notes = []
    declared = sheet_notes(low, design)
    if declared:
        ny = _snap(cy + row_h + _SCH_ROW_GAP)
        notes.append(f'  (text "NOTES" (at {_SCH_MARGIN} {ny} 0)'
                     ' (effects (font (size 2 2) bold) (justify left bottom))'
                     f' (uuid "{_uid(source, "noteshdr")}"))')
        for i, text in enumerate(declared, start=1):
            notes.append(
                f'  (text "{i}. {_q(text)}" '
                f'(at {_SCH_MARGIN} {_snap(ny + _SCH_ROW_GAP * i)} 0)'
                ' (effects (font (size 1.5 1.5)) (justify left bottom))'
                f' (uuid "{_uid(source, "note", str(i))}"))')
        cy = _snap(ny + _SCH_ROW_GAP * (len(declared) + 1))
        row_h = _D(0)

    page_h = _snap(cy + row_h + _SCH_MARGIN)
    paper = ('(paper "A3")' if page_h <= _A3_H
             else f'(paper "User" {_A3_W} {page_h})')
    return "\n".join([
        "(kicad_sch",
        f"  (version {_SCH_VERSION})",
        '  (generator "ohm")',
        f'  (uuid "{root_uuid}")',
        f"  {paper}",
        *title_block(design, source, 1, len(groups) + 1),
        "  (lib_symbols",
        "  )",
        *boxes,
        *wires,
        *notes,
        '  (sheet_instances (path "/" (page "1")))',
        ")",
    ]) + "\n"


def emit_kicad_sheets(low: ir.IRLow, source: str = "<string>",
                      design: ir.Design | None = None, *,
                      force: bool = False) -> dict[str, str]:
    """{filename: content} — the drawing, on as many sheets as it needs.

    Sheet count is decided by FIT, not by preference. A drawing that fits one
    reviewable page stays on one page: a hierarchy of one is indirection, and so
    is a hierarchy of three blocks that would have fitted an A3. Past that, one
    A3 sheet per top-level module (ADR-016) — because nobody reviews an A0. A0 is
    a plotter size; reviews happen on printed or paged PDFs, and a reviewer needs
    to hold the power section and the MCU section side by side.
    """
    stem = Path(source).stem
    groups = sheet_groups(low)
    if len(groups) < 2:
        return {f"{stem}.kicad_sch": emit_kicad_sch(low, source, design)}
    flat = emit_kicad_sch(low, source, design)
    if not force and _REVIEWABLE_RE.search(flat):
        return {f"{stem}.kicad_sch": flat}
    flat = emit_kicad_sch(low, source, design,
                          sheet_note="OVERVIEW — every block on one sheet, "
                                     "not part of the hierarchy")
    crossing = _crossing(low, design)
    root_uuid = _uid(source, "sch")
    out = {f"{stem}.kicad_sch": _emit_root_sheet(low, source, stem, crossing, design)}
    pwr_used = 0
    for page, grp in enumerate(groups, start=2):
        sheet = emit_kicad_sch(
            low, source, design, group=grp,
            hier_nets=frozenset(crossing.get(grp, [])),
            sheet_path=f"/{root_uuid}/{_uid(source, 'sheetsym', grp)}",
            page=page, pages=len(groups) + 1, pwr_start=pwr_used)
        # count what this sheet consumed so the next one starts past it
        pwr_used += len(_PWR_REF_RE.findall(sheet))
        out[f"{stem}_{grp}.kicad_sch"] = sheet
    # The same netlist, rendered twice. Hierarchical sheets are what you EDIT —
    # each module on a reviewable page. The overview is what you REVIEW: every
    # block on one sheet, so the system view is never lost while paging. Two
    # renderings from one source costs nothing when you own the source format,
    # and no schematic tool offers it because none of them do.
    out[f"{stem}_overview.kicad_sch"] = flat
    return out


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
    from ohm.pkggen.footprint import silk_pin1, silk_segments

    for sx1, sy1, sx2, sy2 in silk_segments(model):
        out.append(
            f"    (fp_line (start {sx1} {sy1}) (end {sx2} {sy2})"
            f' (stroke (width 0.12) (type solid)) (layer "{pfx}.SilkS"))'
        )
    if (corner := silk_pin1(model)) is not None:
        cx, cy = corner
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
