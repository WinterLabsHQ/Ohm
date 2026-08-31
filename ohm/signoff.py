"""ADR-011: `ohm signoff` — deterministic verification of a ROUTED board
against the Ohm source (the missing leg of the LLM→compile→route loop).

Research anchor: PCBWorld (arXiv 2607.05915) — open-loop LLM agents leave nets
unconnected, emit floating traces "unrelated to net connectivity", and drift
from the netlist. A router (Meridian, KiCad) is trusted to add copper; signoff
verifies the result against the *source*, which no board-local DRC can do.

Pure: (IRLow, kicad_pcb text) -> diagnostics. No I/O, no subprocess (N1).
Geometric rule checking (clearance/overlap shorts) is impure — it needs KiCad's
own engine — so it lives in ohm/drc.py and the `ohm signoff` COMMAND runs both,
reporting geometry as V007. This module checks source-fidelity and
point-topology connectivity:

  V001  board ≠ source: footprint missing/foreign, pad bound to the wrong net
  V002  incomplete net: a net's copper does not join all its pads (islands)
  V003  floating/foreign copper: segments on no known net, or islands with no pad
  V004  short: one quantized point claimed by copper of two different nets
  V005  info: out-of-scope notice (zones, pad-less stub footprints, geom DRC)
  V006  a `constraint fixed(...)` part moved or rotated by the router
  V008  the board could not be READ: a shape signoff depends on was not found

Connectivity model (documented limits): copper items connect where quantized
coordinates coincide (0.001 mm) — segment endpoints chain, vias join layers at
a point, a segment endpoint lands on a pad if it falls within the pad's
axis-aligned half-size + 0.05 mm slop on a shared layer. Zones and arbitrary
mid-segment T-junctions are not modeled (KiCad DRC covers them); routers that
emit endpoint-chained tracks (KiCad, freerouting, Meridian) are in-model.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from ohm import ir
from ohm.diagnostics import Diagnostic, Span
from ohm.emit_kicad import diffpair_renames, fixed_poses

_Q = Decimal("0.001")  # coordinate quantum, mm
_SLOP = Decimal("0.05")  # pad-catch slop, mm
_SPAN = Span(1, 1, 1, 1)

Sexpr = list  # nested lists of str


# ---------- minimal S-expression reader ----------
_TOK = re.compile(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+')


def _read(text: str) -> tuple[Sexpr, list[str]]:
    """Read s-expressions, and say whether the text was WELL-FORMED.

    The reader never raises. `ohm signoff` is the last gate before fabrication
    and its input is a file some OTHER program wrote (whichever KiCad the user
    installed), so damaged input has to come back as a finding rather than as a
    traceback — and, just as importantly, not as a smaller board. A truncated
    file read as "a board with fewer footprints" is reported as parts missing
    from the layout (V001), which blames the user's board for a file Ohm could
    not read. Those are opposite verdicts, so bracket damage is named.
    """
    stack: list[list] = [[]]
    problems: list[str] = []
    stray = 0
    for m in _TOK.finditer(text):
        t = m.group(0)
        if t == "(":
            stack.append([])
        elif t == ")":
            if len(stack) == 1:  # a `)` closing nothing: damaged or foreign text
                stray += 1
                continue
            done = stack.pop()
            stack[-1].append(done)
        else:
            if t.startswith('"'):
                t = t[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            stack[-1].append(t)
    if stray:
        problems.append(f"{stray} `)` closing nothing — the text is not a balanced "
                        "S-expression")
    if len(stack) > 1:
        problems.append(f"the text ends inside {len(stack) - 1} unclosed `(` form(s) "
                        "— a truncated or partially written file")
        while len(stack) > 1:  # keep what WAS read; the finding says it is partial
            done = stack.pop()
            stack[-1].append(done)
    return stack[0], problems


def parse_sexpr(text: str) -> Sexpr:
    return _read(text)[0]


def _kids(node: Sexpr, head: str) -> list[Sexpr]:
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def _kid(node: Sexpr, head: str) -> Sexpr | None:
    got = _kids(node, head)
    return got[0] if got else None


def _num(tok: object) -> Decimal | None:
    """One positional token as a number, or None if it is not one.

    Every geometric form Ohm reads — `(at x y [rot])`, `(size w h)`, `(start x
    y)` — is POSITIONAL, and a position is exactly the kind of thing a format
    revision moves. Asking "is there a number here" instead of indexing blind is
    what turns such a change into a named finding rather than a traceback (or,
    worse, a Decimal built from the word `unlocked`).
    """
    if not isinstance(tok, str):
        return None
    try:
        return Decimal(tok)
    except (InvalidOperation, ValueError):
        return None


def _xy(node: Sexpr | None) -> tuple[Decimal, Decimal] | None:
    """`(at x y …)` / `(size w h)` / `(start x y)` -> the first two numbers."""
    if node is None or len(node) < 3:
        return None
    x, y = _num(node[1]), _num(node[2])
    return None if x is None or y is None else (x, y)


def _rot(node: Sexpr) -> Decimal:
    """The optional third field of `(at x y [rot])`.

    Deliberately tolerant: rotation is absent on every unrotated part, and KiCad
    has also printed a trailing keyword in that slot (`unlocked`). An absent or
    unreadable angle means "not rotated", which is true of the overwhelming
    majority of parts and is locally recoverable — unlike a missing position,
    which is not.
    """
    return (_num(node[3]) if len(node) > 3 else None) or Decimal(0)


# ---------- board model ----------
@dataclass
class _Pad:
    fp_path: str
    pad_id: str
    x: Decimal  # absolute, quantized
    y: Decimal
    half_w: Decimal
    half_h: Decimal
    layers: frozenset[str]
    net: str  # board net name ("" = unbound)


@dataclass
class _Board:
    footprints: dict[str, tuple[Decimal, Decimal, Decimal]] = field(default_factory=dict)
    stub_paths: set[str] = field(default_factory=set)  # footprints without pads
    pads: list[_Pad] = field(default_factory=list)
    segs: list[tuple[Decimal, Decimal, Decimal, Decimal, str, str]] = field(default_factory=list)
    vias: list[tuple[Decimal, Decimal, str]] = field(default_factory=list)
    has_zones: bool = False
    # Shapes this reader depends on and did NOT find. Empty on every board Ohm
    # and KiCad 7-10 write; each entry becomes a V008 (see `read_board`).
    unreadable: list[str] = field(default_factory=list)


def _rot_rel(px: Decimal, py: Decimal, rot: Decimal) -> tuple[Decimal, Decimal]:
    """Rotate a footprint-relative offset by the footprint angle (KiCad: CCW
    positive with y-down => screen-clockwise). Exact for right angles."""
    r = float(rot) % 360.0
    c, s = math.cos(math.radians(r)), math.sin(math.radians(r))
    fx, fy = float(px), float(py)
    return (Decimal(fx * c + fy * s).quantize(_Q), Decimal(-fx * s + fy * c).quantize(_Q))


# KiCad prints a pad's NAME as the first positional field — `(pad "2" smd ...)`
# — and omits it on an unnamed mechanical pad: `(pad smd circle ...)`. Taking
# p[1] blind then yields a pad called "smd", which matches no source pad and
# reads as board/source drift. The pad TYPE vocabulary is small and closed, so
# the shape says which form this is without anyone asking the version.
_PAD_TYPES = frozenset({"thru_hole", "smd", "connect", "np_thru_hole"})


def read_board(text: str) -> _Board:
    """Parse a .kicad_pcb into the model signoff checks against.

    Structural, never line-anchored (ADR-021): every field is reached by walking
    to a named form rather than by matching printed layout, because the file was
    written by whichever KiCad the user installed — 7.0 through 10.0 — and the
    printer is the part that changes.

    Where a shape this reader DEPENDS on is absent, the board records it in
    `unreadable` instead of quietly returning less, and `signoff` turns each
    entry into a V008. That distinction is the whole point: a board carrying no
    `ohm_path` is not a board whose parts are all missing, and a truncated file
    is not an empty layout — but both look identical to a reader that only
    counts what it managed to find.
    """
    b = _Board()
    root, problems = _read(text)
    b.unreadable.extend(problems)
    pcb = next((n for n in root if isinstance(n, list) and n and n[0] == "kicad_pcb"), None)
    if pcb is None:
        b.unreadable.append("no `(kicad_pcb …)` form: this is not a KiCad board file "
                            "(or it was truncated before one was written)")
        return b
    # Resolve every net slot to a NAME here, once, so nothing downstream has to
    # know which KiCad wrote the file (see _net_slot). Empty on KiCad 10, which
    # names its nets inline and has no table.
    ids = _net_ids(pcb)
    fp_seen = fp_named = fp_no_at = 0
    pad_seen = pad_bad = pad_cu = copper_bad = 0
    for node in pcb:
        if not isinstance(node, list) or not node:
            continue
        if node[0] == "zone":
            b.has_zones = True
        elif node[0] == "segment":
            st, en = _xy(_kid(node, "start")), _xy(_kid(node, "end"))
            layer, net = _kid(node, "layer"), _kid(node, "net")
            if layer is None or len(layer) < 2 or net is None:
                continue
            if st is None or en is None:
                copper_bad += 1  # copper that exists but cannot be placed
                continue
            b.segs.append((st[0].quantize(_Q), st[1].quantize(_Q),
                           en[0].quantize(_Q), en[1].quantize(_Q),
                           str(layer[1]), _net_name(net, ids)))
        elif node[0] == "via":
            at, net = _xy(_kid(node, "at")), _kid(node, "net")
            if net is None:
                continue
            if at is None:
                copper_bad += 1
                continue
            b.vias.append((at[0].quantize(_Q), at[1].quantize(_Q), _net_name(net, ids)))
        elif node[0] == "footprint":
            fp_seen += 1
            # ADR-021 / AGENTS.md item 5: signoff identifies every part by this
            # one custom property, taking its VALUE from element 2 of the form.
            # KiCad 8 already rewrote footprint properties into full field forms
            # carrying (at …)/(layer …)/(uuid …)/(effects …) children and left
            # the value in place, so the index holds today — but a release that
            # moved or dropped it would stop identifying parts in the last check
            # before fabrication while the reader went on answering confidently.
            # So the value is REQUIRED to be a non-empty bare string, and a board
            # where NO footprint yields one is reported as unreadable below
            # rather than as a board full of missing parts.
            path_p = next((p for p in _kids(node, "property")
                           if len(p) >= 3 and p[1] == "ohm_path"
                           and isinstance(p[2], str) and p[2] != ""), None)
            if path_p is None:
                continue
            fp_named += 1
            at_n = _kid(node, "at")
            pos = _xy(at_n)
            if at_n is None or pos is None:
                fp_no_at += 1
                continue
            path = str(path_p[2])
            fx, fy = pos[0].quantize(_Q), pos[1].quantize(_Q)
            frot = _rot(at_n)
            b.footprints[path] = (fx, fy, frot)
            pads = _kids(node, "pad")
            if not pads:
                b.stub_paths.add(path)
            for p in pads:
                pad_seen += 1
                rel, size = _xy(_kid(p, "at")), _xy(_kid(p, "size"))
                if rel is None or size is None:
                    pad_bad += 1  # a pad that exists but cannot be placed
                    continue
                dx, dy = _rot_rel(rel[0], rel[1], frot)
                pnet = _kid(p, "net")
                layers_n = _kid(p, "layers") or []
                pad_id = str(p[1]) if len(p) > 1 and isinstance(p[1], str) else ""
                if pad_id in _PAD_TYPES:
                    pad_id = ""  # an unnamed pad, not a pad called "smd"
                cu = frozenset(s for s in layers_n[1:] if isinstance(s, str)
                               and (s.endswith(".Cu") or s == "*.Cu"))
                pad_cu += bool(cu)
                b.pads.append(_Pad(
                    path, pad_id, (fx + dx).quantize(_Q), (fy + dy).quantize(_Q),
                    size[0] / 2, size[1] / 2, cu, _net_name(pnet, ids)))

    # ---- structural sanity: the shapes this reader stands on, checked ONCE ----
    #
    # One "I could not read this file" signal per missing shape, rather than a
    # guard on every index: the user needs to know the reader failed, not which
    # of four hundred pads it failed on. Every check here is silent on the boards
    # Ohm and KiCad 7-10 actually write, and speaks only when an assumption this
    # reader is built on has stopped holding.
    if fp_seen and not fp_named:
        b.unreadable.append(
            f'{fp_seen} footprints, none carrying `(property "ohm_path" <value>)` — '
            "signoff identifies every part by that property, so this board cannot "
            "be matched to the source at all")
    if fp_no_at:
        b.unreadable.append(
            f"{fp_no_at} footprint(s) whose `(at x y [rot])` carries no readable "
            "coordinates — their placement and pad positions are unknown")
    if pad_bad:
        b.unreadable.append(
            f"{pad_bad} pad(s) whose `(at x y)` or `(size w h)` carries no readable "
            "numbers — those pads cannot be placed, so copper reaching them looks "
            "like a net nobody routed")
    if copper_bad:
        b.unreadable.append(
            f"{copper_bad} track segment(s)/via(s) whose `(start …)`, `(end …)` or "
            "`(at …)` carries no readable coordinates — that copper is invisible "
            "here, so the nets it routes look incomplete")
    if pad_seen and not pad_cu:
        b.unreadable.append(
            f"{pad_seen} pads, none naming a copper layer in `(layers …)` (a `*.Cu` "
            "or `<side>.Cu` entry) — no track can be matched to any pad, so every "
            "routed net looks incomplete")
    return b


# ---------- the net slot: a NUMBER through KiCad 9, a NAME from KiCad 10 ----------
#
# KiCad 7-9 wrote a top-level net table and referenced it by index everywhere:
#
#     (net 3 "R1_P2")                     <- top-level declaration
#     (pad "2" smd ... (net 3 "R1_P2"))   <- number AND name
#     (segment ... (net 3))               <- number only, resolved via the table
#
# KiCad 10 (format 20260206) drops the table entirely and references nets by
# NAME alone:
#
#     (pad "2" smd ... (net "R1_P2"))
#     (segment ... (net "R1_P2"))
#
# Reading only the old shape made every pad and every track come back bound to
# "", so `ohm signoff` reported V001 on EVERY pad of any board KiCad 10 had
# saved — and V001 says "the board does not match the source", which reads as
# the user's routing being wrong rather than as Ohm failing to parse. This is
# the last check before a board is fabricated, so a parse failure that presents
# as a design failure is the worst possible way for it to break.
#
# Both shapes are accepted, and neither is guessed at: a net slot's payload is a
# number in the old form and a quoted string in the new one.
def _net_ids(pcb: Sexpr) -> dict[str, str]:
    """Board net table: id -> name (top-level `(net N "name")` entries).

    Empty on KiCad 10, which has no such table. Callers must therefore treat a
    miss as "this board names its nets inline", not as "unknown net". Takes the
    already-parsed board rather than the text: reading the file twice was two
    chances to disagree about what is in it.
    """
    out = {}
    for n in _kids(pcb, "net"):
        if len(n) >= 3:
            out[str(n[1])] = str(n[2])
    return out


def _net_slot(node: Sexpr | None) -> tuple[str, str]:
    """A `(net ...)` form -> (id_or_empty, name_or_empty).

    `(net 3 "R1_P2")` -> ("3", "R1_P2");  `(net 3)` -> ("3", "");
    `(net "R1_P2")`   -> ("", "R1_P2").
    """
    if not node or len(node) < 2:
        return "", ""
    first = str(node[1])
    if len(node) >= 3:                     # (net N "name") — KiCad 7-9
        return first, str(node[2])
    if first.lstrip("-").isdigit():        # (net N) — a number with no name
        return first, ""
    return "", first                       # (net "name") — KiCad 10


def _net_name(node: Sexpr | None, ids: dict[str, str]) -> str:
    """The net a pad/segment/via is on, whichever shape the board uses."""
    nid, name = _net_slot(node)
    return name or ids.get(nid, "")


class _UF:
    def __init__(self) -> None:
        self.p: dict[object, object] = {}

    def find(self, a: object) -> object:
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: object, b: object) -> None:
        self.p[self.find(a)] = self.find(b)


def _lay_match(seg_layer: str, pad_layers: frozenset[str]) -> bool:
    return "*.Cu" in pad_layers or seg_layer in pad_layers


def signoff(low: ir.IRLow, board_text: str, board_file: str) -> list[Diagnostic]:
    diags: list[Diagnostic] = []

    def d(sev: str, code: str, msg: str, help_: str | None = None) -> None:
        diags.append(Diagnostic(sev, code, msg, _SPAN, board_file, help_))

    board = read_board(board_text)
    # ---- V008: shapes the reader depends on that this file does not have ----
    #
    # First, and before any finding computed FROM the board, because every one of
    # those is only as good as what could be read. A board Ohm cannot read is not
    # a board that matches nothing: those are opposite verdicts, and this is the
    # last gate before fabrication.
    for note in board.unreadable:
        d("error", "V008", f"this board could not be read: {note}",
          "signoff walks the board's S-expression tree structurally, so this is a "
          "shape it expected and did not find — likely a KiCad version writing "
          "something new. Treat every other finding below as unreliable, and "
          "report the KiCad version that wrote this file.")

    renames = diffpair_renames(low)
    # expected board-net name per source net (diffpair _P/_N renames apply)
    exp_net = {n.name: renames.get(n.name, n.name) for n in low.nets}
    exp_names = set(exp_net.values())
    src_parts = {p.ref for p in low.parts}

    # ---- V001: footprint set + pad-net bindings match the source ----
    for ref in sorted(src_parts - set(board.footprints)):
        d("error", "V001", f"part '{ref}' is missing from the routed board")
    for ref in sorted(set(board.footprints) - src_parts):
        d("error", "V001", f"board footprint '{ref}' does not exist in the source")
    pad_by_key = {(p.fp_path, p.pad_id): p for p in board.pads}
    src_pad_net: dict[tuple[str, str], str] = {}
    for net in low.nets:
        for node in net.nodes:
            src_pad_net[(node.part_ref, node.pad or node.pin)] = exp_net[net.name]
    for key, want in sorted(src_pad_net.items()):
        got = pad_by_key.get(key)
        if got is None:
            continue  # stub/missing footprints are reported elsewhere
        if got.net != want:
            d("error", "V001",
              f"pad {key[0]}.{key[1]} is bound to net '{got.net}' on the board, "
              f"source says '{want}'")

    # ---- point-topology connectivity ----
    uf = _UF()
    by_xyl: dict[tuple[Decimal, Decimal, str], set[str]] = {}  # segment endpoints
    via_xy: dict[tuple[Decimal, Decimal], set[str]] = {}
    seg_net_name: list[str] = []
    for x1, y1, x2, y2, layer, name in board.segs:
        seg_net_name.append(name)
        uf.union((x1, y1, layer), (x2, y2, layer))
        for pt in ((x1, y1, layer), (x2, y2, layer)):
            by_xyl.setdefault(pt, set()).add(name)
    for x, y, via_net in board.vias:
        anchor = (x, y, "*via*")
        uf.find(anchor)
        for pt in [p for p in by_xyl if p[0] == x and p[1] == y]:
            uf.union(pt, anchor)
        via_xy.setdefault((x, y), set()).add(via_net)
    for i, pad in enumerate(board.pads):
        uf.find(("pad", i))  # ensure singleton pads exist in the forest
        if not pad.net:
            continue
        for pt in list(by_xyl) + [(x, y, "*via*") for (x, y) in via_xy]:
            if pt[2] != "*via*" and not _lay_match(pt[2], pad.layers):
                continue
            if (abs(pt[0] - pad.x) <= pad.half_w + _SLOP
                    and abs(pt[1] - pad.y) <= pad.half_h + _SLOP):
                uf.union(("pad", i), pt)
                here = (via_xy.get((pt[0], pt[1]), set()) if pt[2] == "*via*"
                        else by_xyl.get(pt, set()))
                for n in sorted(here):
                    if n and n != pad.net:
                        d("error", "V004",
                          f"copper of net '{n}' lands on pad {pad.fp_path}.{pad.pad_id} "
                          f"of net '{pad.net}' at ({pt[0]}, {pt[1]}) — short")

    # ---- V004: one point, two nets ----
    for pt in sorted(by_xyl, key=str):
        real = {n for n in by_xyl[pt] if n}
        if len(real) > 1:
            d("error", "V004",
              f"copper of nets {sorted(real)} meets at ({pt[0]}, {pt[1]}) — short")
    for (x, y) in sorted(via_xy, key=str):
        stack = {n for n in via_xy[(x, y)] if n}
        for pt, names in by_xyl.items():
            if pt[0] == x and pt[1] == y:
                stack |= {n for n in names if n}
        if len(stack) > 1:
            d("error", "V004",
              f"via at ({x}, {y}) joins copper of nets {sorted(stack)} — short")

    # ---- V002: every source net's pads share one copper island ----
    unverifiable: list[str] = []
    for net in low.nets:
        pad_idx = [i for i, p in enumerate(board.pads)
                   if src_pad_net.get((p.fp_path, p.pad_id)) == exp_net[net.name]
                   and p.net == exp_net[net.name]]
        want_pads = [k for k, v in src_pad_net.items() if v == exp_net[net.name]]
        stubbed = [k for k in want_pads if k[0] in board.stub_paths]
        if stubbed:
            unverifiable.append(exp_net[net.name])
            continue
        if len(pad_idx) < 2:
            continue  # 0/1-pad nets have nothing to route
        roots = {uf.find(("pad", i)) for i in pad_idx}
        if len(roots) > 1:
            d("error", "V002",
              f"net '{exp_net[net.name]}' is incomplete: {len(pad_idx)} pads in "
              f"{len(roots)} disconnected islands",
              "route the net (or fix endpoint coordinates so copper actually meets)")

    # ---- V003: foreign copper + trace islands that touch no pad ----
    if any(not n for n in seg_net_name):
        d("warning", "V003", "board has track segments bound to no net (net 0) — "
          "dead copper the source never asked for")
    for name in sorted({n for n in seg_net_name if n and n not in exp_names}):
        d("error", "V003", f"board copper on net '{name}' which the source does not define")
    pad_roots = {uf.find(("pad", i)) for i, p in enumerate(board.pads) if p.net}
    floating: set[str] = set()
    for (x1, y1, x2, y2, layer, nid), name in zip(board.segs, seg_net_name):
        if name and name in exp_names and uf.find((x1, y1, layer)) not in pad_roots:
            floating.add(name)
    for name in sorted(floating):
        d("warning", "V003", f"floating trace(s) on net '{name}': copper island "
          "touches no pad of the net")

    # ---- V006: `constraint fixed(...)` parts must not move ----
    for ref, (x, y, rot, _side) in sorted(fixed_poses(low).items()):
        pose = board.footprints.get(ref)
        if pose is None:
            continue  # V001 already reported
        if ((pose[0], pose[1]) != (x.quantize(_Q), y.quantize(_Q))
                or pose[2] % 360 != rot % 360):
            d("error", "V006",
              f"fixed part '{ref}' moved: board has ({pose[0]}, {pose[1]}, rot {pose[2]}), "
              f"source fixes ({x}, {y}, rot {rot})")

    # ---- V005: honest scope notices ----
    if board.has_zones:
        d("info", "V005", "board has copper zones; zone connectivity is not modeled "
          "here — the V007 geometry gate covers them")
    if unverifiable:
        d("info", "V005", "nets with pad-less stub footprints cannot be traced: "
          + ", ".join(sorted(set(unverifiable))))
    d("info", "V005", "geometric rules (clearance, overlap shorts) are not checked "
      "here — `ohm signoff` runs them as V007 via KiCad's DRC engine")
    return diags
