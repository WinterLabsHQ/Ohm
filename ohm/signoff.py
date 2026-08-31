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
from decimal import Decimal

from ohm import ir
from ohm.diagnostics import Diagnostic, Span
from ohm.emit_kicad import diffpair_renames, fixed_poses

_Q = Decimal("0.001")  # coordinate quantum, mm
_SLOP = Decimal("0.05")  # pad-catch slop, mm
_SPAN = Span(1, 1, 1, 1)

Sexpr = list  # nested lists of str


# ---------- minimal S-expression reader ----------
_TOK = re.compile(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+')


def parse_sexpr(text: str) -> Sexpr:
    stack: list[list] = [[]]
    for m in _TOK.finditer(text):
        t = m.group(0)
        if t == "(":
            stack.append([])
        elif t == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            if t.startswith('"'):
                t = t[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            stack[-1].append(t)
    return stack[0]


def _kids(node: Sexpr, head: str) -> list[Sexpr]:
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def _kid(node: Sexpr, head: str) -> Sexpr | None:
    got = _kids(node, head)
    return got[0] if got else None


def _q(v: str) -> Decimal:
    return Decimal(v).quantize(_Q)


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


def _rot_rel(px: Decimal, py: Decimal, rot: Decimal) -> tuple[Decimal, Decimal]:
    """Rotate a footprint-relative offset by the footprint angle (KiCad: CCW
    positive with y-down => screen-clockwise). Exact for right angles."""
    r = float(rot) % 360.0
    c, s = math.cos(math.radians(r)), math.sin(math.radians(r))
    fx, fy = float(px), float(py)
    return (Decimal(fx * c + fy * s).quantize(_Q), Decimal(-fx * s + fy * c).quantize(_Q))


def read_board(text: str) -> _Board:
    b = _Board()
    root = parse_sexpr(text)
    pcb = next((n for n in root if isinstance(n, list) and n and n[0] == "kicad_pcb"), [])
    # Resolve every net slot to a NAME here, once, so nothing downstream has to
    # know which KiCad wrote the file (see _net_slot). Empty on KiCad 10, which
    # names its nets inline and has no table.
    ids = _net_ids(text)
    for node in pcb:
        if not isinstance(node, list) or not node:
            continue
        if node[0] == "zone":
            b.has_zones = True
        elif node[0] == "segment":
            st, en = _kid(node, "start"), _kid(node, "end")
            layer, net = _kid(node, "layer"), _kid(node, "net")
            if st and en and layer and net:
                b.segs.append((_q(st[1]), _q(st[2]), _q(en[1]), _q(en[2]),
                               str(layer[1]), _net_name(net, ids)))
        elif node[0] == "via":
            at, net = _kid(node, "at"), _kid(node, "net")
            if at and net:
                b.vias.append((_q(at[1]), _q(at[2]), _net_name(net, ids)))
        elif node[0] == "footprint":
            path_p = next((p for p in _kids(node, "property")
                           if len(p) >= 3 and p[1] == "ohm_path"), None)
            at = _kid(node, "at")
            if path_p is None or at is None:
                continue
            path = str(path_p[2])
            fx, fy = _q(at[1]), _q(at[2])
            frot = Decimal(at[3]) if len(at) > 3 else Decimal(0)
            b.footprints[path] = (fx, fy, frot)
            pads = _kids(node, "pad")
            if not pads:
                b.stub_paths.add(path)
            for p in pads:
                pat, size = _kid(p, "at"), _kid(p, "size")
                if pat is None or size is None:
                    continue
                dx, dy = _rot_rel(Decimal(pat[1]), Decimal(pat[2]), frot)
                pnet = _kid(p, "net")
                layers_n = _kid(p, "layers") or []
                b.pads.append(_Pad(
                    path, str(p[1]), (fx + dx).quantize(_Q), (fy + dy).quantize(_Q),
                    Decimal(size[1]) / 2, Decimal(size[2]) / 2,
                    frozenset(s for s in layers_n[1:] if s.endswith(".Cu") or s == "*.Cu"),
                    _net_name(pnet, ids)))
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
def _net_ids(text: str) -> dict[str, str]:
    """Board net table: id -> name (top-level `(net N "name")` entries).

    Empty on KiCad 10, which has no such table. Callers must therefore treat a
    miss as "this board names its nets inline", not as "unknown net".
    """
    root = parse_sexpr(text)
    pcb = next((n for n in root if isinstance(n, list) and n and n[0] == "kicad_pcb"), [])
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
