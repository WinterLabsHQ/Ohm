"""Milestone F Part D gate: placement intent + routing-friendly KiCad handoff.

fixed pose + locked; deterministic non-overlapping auto-placement; groups;
ohm_path properties; pads with net bindings; diffpair rename in kicad files
only; .kicad_pro net classes; kicad-cli parse; byte-identity; near adjacency.
"""

import json
import re
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from ohm.driver import compile_source
from ohm.emit_constraints import emit_constraints
from ohm.emit_kicad import _CELL_GAP, emit_kicad_pcb, emit_kicad_sch, parse_mm
from ohm.emit_kicad_pro import emit_kicad_pro
from ohm.emit_netlist import emit_netlist
from ohm.pkggen.packages import build

from tests.conftest import KICAD_CLI, requires_kicad_cli

SRC = (
    "interface Power:\n"
    "    v: Signal\n"
    "    gnd: Signal\n"
    "component R:\n"
    "    package = generate(CHIP0603)\n"
    '    designator_prefix = "R"\n'
    "    pin P1: role=passive, pad=1\n"
    "    pin P2: role=passive, pad=2\n"
    "component Conn:\n"
    '    package = "Connector:USB_C"\n'
    "    pin DP: role=bidirectional, pad=3\n"
    "    pin DM: role=bidirectional, pad=2\n"
    "module Sub:\n"
    "    pwr = io(Power)\n"
    "    r1 = R()\n"
    "    r2 = R()\n"
    "    pwr.v ~ r1.P1\n"
    "    r1.P2 ~ r2.P1\n"
    "    pwr.gnd ~ r2.P2\n"
    "module Board:\n"
    "    pwr = io(Power)\n"
    "    j = Conn()\n"
    "    ra = R()\n"
    "    rb = R()\n"
    "    sub = Sub()\n"
    "    pwr.v ~ sub.pwr.v\n"
    "    pwr.gnd ~ sub.pwr.gnd\n"
    "    j.DP ~ ra.P1\n"
    "    j.DM ~ rb.P1\n"
    "    ra.P2 ~ pwr.gnd\n"
    "    rb.P2 ~ pwr.gnd\n"
    "    constraint fixed(j, 50mm, 40mm, 90)\n"
    "    constraint near(rb, ra)\n"
    "    constraint diffpair(j.DP, j.DM)\n"
    '    constraint netclass(pwr.v, "power")\n'
    "    constraint keepout(pwr.v, 10mm, 10mm, 20mm, 5mm)\n"
)


@pytest.fixture(scope="module")
def low():
    result = compile_source(SRC, "board.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    return result.low


@pytest.fixture(scope="module")
def pcb(low):
    return emit_kicad_pcb(low, "board.ohm")


def footprints(pcb_text):
    """ohm_path -> (footprint header line, full block text)."""
    out = {}
    for m in re.finditer(r'  \(footprint [^\n]*\n(?:    [^\n]*\n)*  \)', pcb_text):
        block = m.group(0)
        path = re.search(r'\(property "ohm_path" "([^"]+)"\)', block).group(1)
        out[path] = block
    return out


def pose(block):
    m = re.search(r'\(at (-?[\d.]+) (-?[\d.]+)(?: (-?[\d.]+))?\)', block)
    return Decimal(m.group(1)), Decimal(m.group(2)), Decimal(m.group(3) or 0)


# ---------- fixed: exact pose + locked ----------
def test_fixed_part_exact_pose_and_locked(pcb):
    fps = footprints(pcb)
    block = fps["j"]
    assert " locked " in block.splitlines()[0]
    assert pose(block) == (Decimal(50), Decimal(40), Decimal(90))
    others = [b for p, b in fps.items() if p != "j"]
    assert all(" locked " not in b.splitlines()[0] for b in others)


# ---------- auto placement: distinct, non-overlapping ----------
def test_unfixed_parts_distinct_and_non_overlapping(pcb):
    fps = footprints(pcb)
    poses = {p: pose(b) for p, b in fps.items() if p != "j"}
    assert len(set(poses.values())) == len(poses)  # all distinct
    # spot-check: two CHIP0603 courtyards (2.94 x 1.40) don't intersect
    cw, ch = build("CHIP0603", {}).courtyard_w, build("CHIP0603", {}).courtyard_h
    (xa, ya, _), (xb, yb, _) = poses["sub.r1"], poses["sub.r2"]
    assert abs(xa - xb) >= cw or abs(ya - yb) >= ch


# ---------- near: adjacent in the same row ----------
def test_near_places_part_adjacent(pcb, low):
    """`near(a, b, d)` puts a within d of b — EDGE to edge, which is what the
    constraint means and what a layout engineer reads it as.

    The old contract was "the next cell along", which quantised a declared 2mm
    to the 2mm placement grid plus the courtyard padding and delivered 2.9mm.
    The distance the author wrote is the one number here that is not an
    approximation."""
    fps = footprints(pcb)
    (xa, ya, _), (xb, yb, _) = pose(fps["ra"]), pose(fps["rb"])
    # this fixture's near declares no distance, so the default cell gap applies
    want = next((parse_mm(p) for c in low.constraints if c.kind == "near"
                 for p in c.params if parse_mm(p) is not None), _CELL_GAP)
    half = build("CHIP0603", {}).courtyard_w / 2
    gap = max(abs(xb - xa) - half * 2, abs(yb - ya) - half * 2)
    assert gap <= want, f"declared {want}mm, placed {gap}mm apart"


# ---------- groups ----------
def test_groups_present_with_correct_members(pcb):
    fps = footprints(pcb)
    (group,) = re.findall(r'  \(group "([^"]+)" \(id "[^"]+"\) \(members ([^)]*)\)\)', pcb)
    assert group[0] == "sub"
    member_ids = set(re.findall(r'"([0-9a-f-]+)"', group[1]))
    expected = {re.search(r'\(tstamp "([0-9a-f-]+)"\)', fps[p]).group(1)
                for p in ("sub.r1", "sub.r2")}
    assert member_ids == expected


# ---------- ohm_path + pads with net bindings ----------
def test_ohm_path_and_pad_net_bindings(pcb, low):
    fps = footprints(pcb)
    assert set(fps) == {p.ref for p in low.parts}
    ra = fps["ra"]
    assert '(pad "1" smd roundrect' in ra and '(pad "2" smd roundrect' in ra
    assert re.search(r'\(pad "2" [^\n]*\(net \d+ "PWR_GND"\)', ra)
    assert "(pad" not in fps["j"]  # string-footprint stub: no pads
    assert fps["j"].splitlines()[0].startswith('  (footprint "Connector:USB_C"')


# ---------- diffpair rename: kicad files only ----------
def test_diffpair_renamed_only_in_kicad_files(pcb, low):
    sch = emit_kicad_sch(low, "board.ohm")
    for text in (pcb, sch):
        assert '"J_DP_P"' in text and '"J_DP_N"' in text
        assert '"J_DP"' not in text and '"J_DM"' not in text
    net = emit_netlist(low, "board.ohm")
    # The renamed pair must not leak into the canonical netlist. Name the two
    # renames exactly: a bare "_P" substring also matches any net that honestly
    # ends in a P-pin (`SUB_R1_P2`), which says nothing about the diffpair.
    assert '"J_DP"' in net and '"J_DM"' in net
    assert '"J_DP_P"' not in net and '"J_DP_N"' not in net
    doc = json.loads(emit_constraints(low, "board.ohm"))
    dp = next(c for c in doc["constraints"] if c["kind"] == "diffpair")
    assert dp["nets"] == ["J_DP", "J_DM"]  # canonical names in the seam


# ---------- constraints JSON: placement + groups ----------
def test_constraints_json_placement_and_groups(low):
    doc = json.loads(emit_constraints(low, "board.ohm"))
    assert doc["placement"] == [
        {"ref": "j", "x_mm": "50", "y_mm": "40", "rot": "90", "side": "top",
         "fixed": True}]
    assert {g["name"]: g["members"] for g in doc["groups"]} == {
        "root": ["j", "ra", "rb"], "sub": ["sub.r1", "sub.r2"]}
    near = next(c for c in doc["constraints"] if c["kind"] == "near")
    assert near["nets"] == ["rb", "ra"]  # part refs ride the constraints list


# ---------- .kicad_pro: netclass + diffpair classes ----------
def test_kicad_pro_classes(low):
    doc = json.loads(emit_kicad_pro(low, "board.ohm"))
    classes = {c["name"]: c for c in doc["net_settings"]["classes"]}
    assert set(classes) == {"Default", "DiffPair", "power"}
    assert classes["DiffPair"]["track_width"] == 0.4
    assert classes["DiffPair"]["diff_pair_gap"] == 0.4
    patterns = {(p["netclass"], p["pattern"])
                for p in doc["net_settings"]["netclass_patterns"]}
    assert ("power", "PWR_V") in patterns
    assert ("DiffPair", "J_DP_P") in patterns and ("DiffPair", "J_DP_N") in patterns


# ---------- keepout zone with geometry ----------
def test_keepout_zone_emitted(pcb):
    assert "(keepout (tracks not_allowed) (vias not_allowed)" in pcb
    assert "(xy 10 10) (xy 30 10) (xy 30 15) (xy 10 15)" in pcb


# ---------- kicad-cli parses the board ----------
@requires_kicad_cli
def test_kicad_cli_parses_board(pcb, tmp_path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text(pcb)
    r = subprocess.run(
        [KICAD_CLI, "pcb", "export", "pos", "-o", str(tmp_path / "b.pos"), str(board)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


# ---------- determinism ----------
def test_double_build_byte_identical():
    a = compile_source(SRC, "board.ohm").low
    b = compile_source(SRC, "board.ohm").low
    assert emit_kicad_pcb(a, "x").encode() == emit_kicad_pcb(b, "x").encode()
    assert emit_kicad_pro(a, "x").encode() == emit_kicad_pro(b, "x").encode()


# ---------- lowering: placement refs are part refs; bad refs warn I007 ----------
def test_placement_constraint_lowers_to_part_refs(low):
    fixed = next(c for c in low.constraints if c.kind == "fixed")
    assert fixed.nets == ("j",) and fixed.params == ("50mm", "40mm", "90")


def test_placement_ref_must_be_component_instance():
    bad = SRC.replace("constraint near(rb, ra)", "constraint near(rb, sub)")
    result = compile_source(bad, "board.ohm")
    assert any(d.code == "I007" and "component instance" in d.message
               for d in result.diagnostics)


# ---------- CLI writes the .kicad_pro ----------
def test_cli_emits_kicad_pro(tmp_path):
    from ohm.cli import main

    src = tmp_path / "board.ohm"
    src.write_text(SRC)
    out = tmp_path / "out"
    assert main(["build", str(src), "-o", str(out), "--emit", "kicad"]) == 0
    pro = json.loads((out / "board.kicad_pro").read_text())
    assert pro["meta"]["filename"] == "board.kicad_pro"
    assert (out / "board.kicad_pcb").exists()


# ---------- the placer flows; it does not run off the edge of the world ----------
FLOW_SRC = """use std.interfaces
use std.passives

component Ic:
    package = generate(SOIC, pins=8)
    pin VDD: role=power_in, pad=1, current=100mA
    pin GND: role=ground, pad=2
    pin A: role=input, pad=3
    pin B: role=input, pad=4
    pin C: role=input, pad=5
    pin D: role=input, pad=6
    pin E: role=input, pad=7
    pin F: role=input, pad=8

module Board:
    pwr = io(Power)
    u = Ic()
"""


def _many(n: int) -> str:
    src = FLOW_SRC + "".join(f"    c{i} = Capacitor(value=100nF)\n" for i in range(n))
    src += "    pwr.v ~ u.VDD\n    pwr.gnd ~ u.GND\n"
    src += "".join(f"    pwr.v ~ c{i}.P1\n    pwr.gnd ~ c{i}.P2\n" for i in range(n))
    return src


def test_the_placer_wraps_instead_of_running_off_in_one_line():
    """The old placer only advanced x, so 116 parts landed on a single 500mm
    line — wider than any panel, and a placement no router can use."""
    from ohm.emit_kicad import placement

    result = compile_source(_many(60), "flow.ohm")
    assert result.low is not None, [d.machine() for d in result.diagnostics]
    pos = placement(result.low)
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    assert len(set(ys)) > 1, "every part is still on one row"
    # a 61-part board is not 400mm wide
    assert max(xs) - min(xs) < 200, f"spread {max(xs) - min(xs)}mm across"
    assert min(xs) >= 0 and min(ys) >= 0, "placed off the board at a negative coordinate"


def test_no_two_auto_placed_parts_overlap():
    from ohm.emit_kicad import _courtyard, placement

    result = compile_source(_many(40), "flow.ohm")
    pos = placement(result.low)
    pkg = {p.ref: p.package for p in result.low.parts}
    boxes = []
    for ref, (x, y, _r, _s, _l) in sorted(pos.items()):
        w, h, _m = _courtyard(pkg[ref])
        boxes.append((ref, x - w / 2, y - h / 2, x + w / 2, y + h / 2))
    for i, (ra, ax1, ay1, ax2, ay2) in enumerate(boxes):
        for rb, bx1, by1, bx2, by2 in boxes[i + 1:]:
            assert not (ax1 < bx2 and ax2 > bx1 and ay1 < by2 and ay2 > by1), \
                f"{ra} overlaps {rb}"


def test_near_is_honoured_against_a_FIXED_target():
    """`near(cap, u, 2mm)` with the MCU pinned is the most load-bearing near on
    a board, and it used to be dropped outright because the target was not in
    the flow — the cap landed wherever the row happened to reach."""
    from ohm.emit_kicad import _courtyard, placement

    src = _many(3).replace(
        "module Board:",
        "module Board:") + (
        '    constraint fixed(u, 30mm, 25mm, 0, "top")\n'
        "    constraint near(c0, u, 2mm)\n")
    result = compile_source(src, "anchor.ohm")
    assert result.low is not None, [d.machine() for d in result.diagnostics]
    pos = placement(result.low)
    pkg = {p.ref: p.package for p in result.low.parts}
    (ux, uy, *_), (cx, cy, *_) = pos["u"], pos["c0"]
    uw, uh, _ = _courtyard(pkg["u"])
    cw, ch, _ = _courtyard(pkg["c0"])
    gap = max(abs(cx - ux) - uw / 2 - cw / 2, abs(cy - uy) - uh / 2 - ch / 2)
    assert gap <= _CELL_GAP, f"declared 2mm from a fixed part, placed {gap}mm away"


# ---------- placement() is TOTAL over low.parts ----------
_CYCLE_SRC = (
    "component R:\n"
    "    package = generate(CHIP0603)\n"
    '    designator_prefix = "R"\n'
    "    pin P1: role=passive, pad=1\n"
    "    pin P2: role=passive, pad=2\n"
    "module Board:\n"
    "    ra = R()\n"
    "    rb = R()\n"
    "    ra.P2 ~ rb.P1\n"
    "    constraint near(ra, rb, 2mm)\n"
    "    constraint near(rb, ra, 2mm)\n"
)


def _low(src: str):
    result = compile_source(src, "cycle.ohm")
    assert result.ok, [d.machine() for d in result.diagnostics]
    return result


@pytest.mark.parametrize("design", sorted(
    [p for p in (Path(__file__).parent / "golden").glob("*.ohm")]
    + [p for p in (Path(__file__).parent.parent / "examples").glob("*.ohm")],
    key=lambda p: p.name), ids=lambda p: p.stem)
def test_placement_places_every_part(design):
    """`placement()` must return a pose for EVERY part, on every design.

    This is the invariant, not a courtesy. `placement()` is on the ANALYSIS path
    as well as the emit path: `emit_kicad_pcb` indexes it directly, M012 derives
    the board outline from it, and S011 checks overlaps in it. A partial map
    therefore does not merely crash the emitter — it makes two checkers report
    confidently about parts they never saw. Pinning totality here is what makes
    the `KeyError` structurally impossible, instead of hiding it behind a
    `.get()` that would emit a board with a part silently missing and let
    `ohm signoff` report V001 about it — a design verdict for a compiler bug.
    """
    from ohm.emit_kicad import placement

    result = compile_source(design.read_text(encoding="utf-8"), design.name)
    assert result.low is not None, [d.machine() for d in result.diagnostics]
    poses = placement(result.low)
    missing = {p.ref for p in result.low.parts} - set(poses)
    assert not missing, f"placement() returned no pose for {sorted(missing)}"


def test_a_near_cycle_still_places_both_parts():
    """`near(a, b)` plus `near(b, a)` used to place NEITHER part.

    Each was the other's follower, so neither was in the flow, and the recovery
    loop skipped both because neither target was down yet. `placement()` returned
    `{}`; `ohm build` died with `KeyError: 'ra'` out of `emit_kicad_pcb` AFTER the
    .kicad_sch had been written, leaving a half-populated output directory; and
    M012 reported "the design places no parts" about a design that declares two.
    """
    from ohm.emit_kicad import placement

    low = _low(_CYCLE_SRC).low
    poses = placement(low)
    assert set(poses) == {p.ref for p in low.parts} == {"ra", "rb"}


def test_a_near_cycle_is_actually_honoured():
    """...and it is not merely placed, it is placed where the source asked.

    A 2-cycle reads as unsatisfiable and is not: two parts CAN each sit beside
    the other. One member gives up its adjacency preference and flows, the other
    seats beside it, and the resulting separation satisfies BOTH declarations —
    which is why S013 is a measurement rather than a check on the shape of the
    constraint graph.
    """
    from ohm.emit_kicad import _courtyard, placement

    low = _low(_CYCLE_SRC).low
    poses = placement(low)
    (ax, ay, *_), (bx, by, *_) = poses["ra"], poses["rb"]
    w, h, _model = _courtyard(low.parts[0].package)
    gap = max(abs(ax - bx) - w, abs(ay - by) - h)
    assert gap <= Decimal("2"), f"declared 2mm, placed {gap}mm apart"


def test_a_near_cycle_emits_a_board_with_both_footprints():
    """The end-to-end shape of the crash: `emit_kicad_pcb` raised `KeyError`."""
    low = _low(_CYCLE_SRC).low
    pcb = emit_kicad_pcb(low, "cycle.ohm")
    assert pcb.count('(property "ohm_path"') == 2
